import { renderDashboard } from './dashboard.js';
import { renderPLC, startPlcRuntime, stopPlcRuntime } from './plc/connection.js';
import { renderPTM, startPtmRuntime, stopPtmRuntime } from './ptm/connection.js';
import { renderVision, startVisionRuntime, stopVisionRuntime } from './vision/connection.js';
import { renderServices } from './service.js';
import { renderProcess } from './process_manager.js';
import {
  ensureProjectFilename,
  parseProject,
  serializeProject,
} from './project_file.js';
import {
  DATA,
  state,
  viewRoot,
  pageTitle,
  pageActions,
  titles,
  categoryMap,
  esc,
  showToast,
  priorityClass,
  statusBadge,
  apiRequest,
  coreCommand,
  moduleProcessName,
  isConfiguredProcessName,
  buildModulePatch,
  buildProcessesPatch,
  mergeModulePatchIntoLocal,
  loadCoreState,
  updateConnectionBadge,
  shouldAutoRefreshView,
  recordCommand,
  getPlcBase,
  renderSettingsCategory,
} from './global.js';

function renderAllSettings() {
  const cats = [...new Set(state.settings.map(x => x['대분류']))];
  return `<div class="searchbar">
    <input id="settingsSearch" placeholder="설정 항목, 설명, 기본값 검색...">
    <select id="categoryFilter"><option value="">전체 대분류</option>${cats.map(c => `<option>${esc(c)}</option>`).join('')}</select>
    <select id="priorityFilter"><option value="">전체 중요도</option><option>필수</option><option>권장</option><option>선택</option><option>고급</option></select>
    <span class="count-pill" id="settingsCount">${state.settings.length} items</span>
  </div>
  <div class="card table-wrap"><table id="settingsTable">
    <thead><tr><th>대분류</th><th>중분류</th><th>설정 항목</th><th>예시/기본값</th><th>중요도</th><th>UI 형태</th><th>설명</th><th>적용 범위</th></tr></thead>
    <tbody>${state.settings.map(settingRow).join('')}</tbody>
  </table></div>`;
}

function settingRow(s) {
  const search = Object.values(s).join(' ').toLowerCase();
  return `<tr data-cat="${esc(s['대분류'])}" data-priority="${esc(s['중요도'])}" data-search="${esc(search)}"><td>${esc(s['대분류'])}</td><td>${esc(s['중분류'])}</td><td><strong>${esc(s['설정 항목'])}</strong></td><td class="${/^D\d+$/i.test(String(s['예시/기본값'])) ? 'mono-cell' : ''}">${esc(s['예시/기본값'])}</td><td><span class="priority ${priorityClass(s['중요도'])}">${esc(s['중요도'])}</span></td><td>${esc(s['UI 형태'])}</td><td>${esc(s['설명'])}</td><td>${esc(s['적용 범위'])}</td></tr>`;
}

function renderScope() {
  const blocks = [
    ['메인 컨트롤 앱', 'Controller / PLC / Endpoint / Process / Watchdog / Log / System'],
    ['Vision Setup', 'Exposure / Gain / ROI / Trigger / ArUco'],
    ['PTM / Laser Setup', 'Serial / Baud / Pelco Address / Limit / Preset'],
    ['Calibration / Teaching', 'Camera Intrinsic/Extrinsic / Marker / Laser-PTM / Product Point'],
  ];
  return `<div class="scope-grid">${blocks.map(([h, b]) => `<div class="card scope-card"><h3>${h}</h3><ul>${b.split(' / ').map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`).join('')}</div>
    <div class="card section-gap table-wrap"><table><thead><tr><th>구분</th><th>메인 컨트롤 앱</th><th>Vision Setup</th><th>PTM/Laser Setup</th><th>Calibration/Teaching</th><th>비고</th></tr></thead><tbody>${DATA.scope.map(r => `<tr><td><strong>${esc(r['구분'])}</strong></td><td>${esc(r['메인 컨트롤 앱'])}</td><td>${esc(r['Vision Setup'])}</td><td>${esc(r['PTM/Laser Setup'])}</td><td>${esc(r['Calibration/Teaching'])}</td><td>${esc(r['비고'])}</td></tr>`).join('')}</tbody></table></div>`;
}

function render() {
  stopPlcRuntime();
  stopPtmRuntime();
  stopVisionRuntime();
  const externalFrameView = ['plc', 'ptm', 'vision'].includes(state.view);
  document.documentElement.classList.toggle('external-frame-view', externalFrameView);
  document.body.classList.toggle('external-frame-view', externalFrameView);
  const [title, desc] = titles[state.view];
  pageTitle.textContent = title;
  pageActions.innerHTML = state.view === 'services' ? `<button class="secondary" data-action="restart-all">전체 재시작</button>` : '';

  if (state.view === 'dashboard') viewRoot.innerHTML = renderDashboard();
  else if (state.view === 'plc') viewRoot.innerHTML = renderPLC();
  else if (state.view === 'ptm') viewRoot.innerHTML = renderPTM();
  else if (state.view === 'vision') viewRoot.innerHTML = renderVision();
  else if (state.view === 'process') viewRoot.innerHTML = renderProcess();
  else if (state.view === 'services') viewRoot.innerHTML = renderServices();
  else if (state.view === 'settings') viewRoot.innerHTML = renderAllSettings();
  else if (state.view === 'scope') viewRoot.innerHTML = renderScope();
  else if (state.view === 'controller') viewRoot.innerHTML = renderControllerSettings();
  else if (['logging', 'alarm', 'system'].includes(state.view)) viewRoot.innerHTML = renderOperationalSettings();
  else viewRoot.innerHTML = renderSettingsCategory(categoryMap[state.view]);

  bindDynamicEvents();
  if (state.view === 'plc') startPlcRuntime();
  if (state.view === 'ptm') startPtmRuntime();
  if (state.view === 'vision') startVisionRuntime();
}

async function pushCommand(command, payload = {}) {
  if (command === 'RESTART_CONTROLLER') {
    const ok = window.confirm('Controller 재시작 요청을 Core API로 전송할까요? 운전 중 장비라면 인터록을 먼저 확인해야 합니다.');
    if (!ok) return;
  }
  const packet = {
    command,
    request_id: `REQ-${Date.now()}`,
    params: payload,
  };
  try {
    let result;
    if (command === 'SET_CONFIG') {
      result = await apiRequest('/api/config', {
        method: 'PATCH',
        body: JSON.stringify(payload.patch || payload),
      });
      mergeModulePatchIntoLocal(payload.patch || payload);
    } else {
      result = await coreCommand(command, payload);
    }
    recordCommand(packet, result, null);
    await loadCoreState({ silent: true });
    showToast(`${command} 요청 완료`);
  } catch (error) {
    recordCommand(packet, null, error);
    showToast(`${command} 실패: ${error.message}`);
  }
  if (['dashboard', 'services', 'process'].includes(state.view)) render();
}

async function pushProcessAction(moduleOrName, action) {
  const processName = moduleProcessName(moduleOrName);
  if (!processName) {
    showToast('Process name is missing.');
    return;
  }

  const packet = {
    command: `process.${action}`,
    request_id: `REQ-${Date.now()}`,
    params: { process_name: processName },
  };

  try {
    const result = await coreCommand(`process.${action}`, { process_name: processName });
    recordCommand(packet, result, null);
    await loadCoreState({ silent: true });
    showToast(`${processName} ${action} complete`);
  } catch (error) {
    recordCommand(packet, null, error);
    showToast(`${processName} ${action} failed: ${error.message}`);
  }

  if (['dashboard', 'services', 'process'].includes(state.view)) render();
}

async function applyModuleConfig(module) {
  const patch = buildModulePatch(module);
  await pushCommand('SET_CONFIG', { patch, target: moduleProcessName(module) });
}

function bindDynamicEvents() {
  document.querySelectorAll('[data-controller-tab]').forEach(btn => btn.addEventListener('click', () => {
    state.activeSubgroup.controller = btn.dataset.controllerTab;
    render();
    if (btn.dataset.controllerTab === 'network' && !state.networkStatus) refreshNetworkSettings();
  }));
  document.getElementById('saveDiscoverySettings')?.addEventListener('click', saveDiscoverySettings);
  document.getElementById('scanDiscoverySettings')?.addEventListener('click', openDiscovery);
  document.getElementById('saveOperationalSettings')?.addEventListener('click', saveOperationalSettings);
  document.getElementById('applyNetworkSettings')?.addEventListener('click', applyNetworkSettings);
  document.getElementById('confirmNetworkSettings')?.addEventListener('click', confirmNetworkSettings);
  document.getElementById('refreshNetworkSettings')?.addEventListener('click', refreshNetworkSettings);
  document.getElementById('networkMethod')?.addEventListener('change', updateNetworkFields);
  updateNetworkFields();
  document.querySelectorAll('[data-subgroup]').forEach(btn => btn.addEventListener('click', () => {
    state.activeSubgroup[state.view] = btn.dataset.subgroup;
    render();
  }));

  document.querySelectorAll('[data-setting-key]').forEach(el => el.addEventListener('change', () => {
    const key = el.dataset.settingKey;
    const item = state.settings.find(x => `${x['대분류']}|${x['중분류']}|${x['설정 항목']}` === key);
    if (!item) return;
    let value = el.type === 'checkbox' ? (el.checked ? 'ON' : 'OFF') : el.value;
    if (el.dataset.plcAddress) {
      value = value.trim().toUpperCase();
      if (!/^D\d+$/.test(value)) {
        el.classList.add('input-invalid');
        showToast('PLC 디바이스 주소는 D1000 같은 Dnnn 형식으로 입력하세요.');
        return;
      }
      el.classList.remove('input-invalid');
      el.value = value;
    }
    item['예시/기본값'] = value;
    if (item['설정 항목'] === 'Read Base' || item['설정 항목'] === 'Write Base' || item['중분류'] === '공유 메모리') render();
  }));

  document.querySelectorAll('[data-map-sheet]').forEach(btn => btn.addEventListener('click', () => {
    state.plcMapSheet = btn.dataset.mapSheet;
    render();
  }));

  const mapSearch = document.getElementById('plcMapSearch');
  const hideReserved = document.getElementById('hideReserved');
  if (mapSearch) {
    const filterMap = () => {
      const q = mapSearch.value.trim().toLowerCase();
      const hide = hideReserved?.checked || false;
      document.querySelectorAll('#plcMapTable [data-map-search]').forEach(row => {
        const matches = !q || row.dataset.mapSearch.includes(q);
        const reservedOk = !hide || row.dataset.reserved !== '1';
        row.style.display = matches && reservedOk ? '' : 'none';
      });
      // table rows have data-map-search directly on tr
      document.querySelectorAll('#plcMapTable tbody tr[data-map-search]').forEach(row => {
        const matches = !q || row.dataset.mapSearch.includes(q);
        const reservedOk = !hide || row.dataset.reserved !== '1';
        row.style.display = matches && reservedOk ? '' : 'none';
      });
    };
    mapSearch.addEventListener('input', filterMap);
    hideReserved?.addEventListener('change', filterMap);
  }

  document.querySelectorAll('[data-server-command]').forEach(btn => btn.addEventListener('click', async () => {
    const cmd = btn.dataset.serverCommand;

    if (['START_PROCESS', 'STOP_PROCESS', 'RESTART_PROCESS'].includes(cmd)) {
      const processName = window.prompt('Process Name');
      if (!processName?.trim()) return;
      const action = cmd === 'START_PROCESS' ? 'start' : cmd === 'STOP_PROCESS' ? 'stop' : 'restart';
      await pushProcessAction(processName.trim(), action);
      return;
    }

    const payload = cmd === 'SET_CONFIG' ? { patch: buildProcessesPatch() } : cmd === 'APPLY_CONFIG' ? { target: 'all' } : {};
    await pushCommand(cmd, payload);
  }));

  document.querySelectorAll('[data-module-field]').forEach(input => input.addEventListener('input', () => {
    const module = state.modules.find(m => m.id === input.dataset.moduleId);
    if (module) {
      module[input.dataset.moduleField] = input.type === 'checkbox'
        ? input.checked
        : input.value.trim();
    }
  }));

  document.querySelectorAll('[data-module-action]').forEach(btn => btn.addEventListener('click', async () => {
    const module = state.modules.find(m => m.id === btn.dataset.moduleId);
    if (!module) return;
    if (btn.dataset.moduleAction === 'apply') await applyModuleConfig(module);
  }));

  document.querySelectorAll('[data-proc-action]').forEach(btn => btn.addEventListener('click', async () => {
    const processName = btn.dataset.procName;
    if (!processName) {
      showToast('이 항목은 현재 Core 프로세스 설정에 연결되지 않았습니다.');
      return;
    }
    await pushProcessAction(processName, btn.dataset.procAction);
  }));

  document.querySelector('[data-action="restart-all"]')?.addEventListener('click', async () => {
    const targets = state.modules
      .filter(module => !['config', 'main'].includes(module.id))
      .map(moduleProcessName)
      .filter(Boolean)
      .filter(isConfiguredProcessName);

    for (const processName of targets) {
      await pushProcessAction(processName, 'restart');
    }
  });

  const search = document.getElementById('settingsSearch');
  const cat = document.getElementById('categoryFilter');
  const pri = document.getElementById('priorityFilter');
  if (search) {
    const filter = () => {
      let count = 0;
      document.querySelectorAll('#settingsTable tbody tr').forEach(tr => {
        const ok = (!search.value || tr.dataset.search.includes(search.value.toLowerCase())) && (!cat.value || tr.dataset.cat === cat.value) && (!pri.value || tr.dataset.priority === pri.value);
        tr.style.display = ok ? '' : 'none';
        if (ok) count++;
      });
      document.getElementById('settingsCount').textContent = `${count} items`;
    };
    search.addEventListener('input', filter);
    cat.addEventListener('change', filter);
    pri.addEventListener('change', filter);
  }
}

document.getElementById('sidebarNav').addEventListener('click', e => {
  const btn = e.target.closest('.nav-item');
  if (!btn) return;
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  state.view = btn.dataset.view;
  render();
});

const fileMenuBtn = document.getElementById('fileMenuBtn');
const fileMenu = document.getElementById('fileMenu');
fileMenuBtn.addEventListener('click', () => fileMenu.classList.toggle('hidden'));
document.addEventListener('click', e => { if (!e.target.closest('.file-menu-wrap')) fileMenu.classList.add('hidden'); });

function downloadFile(text, filename) {
  const blob = new Blob([text], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function configField(label, control, detail = '') {
  return `<div class="form-field"><div class="field-label"><strong>${esc(label)}</strong>${detail ? `<small>${esc(detail)}</small>` : ''}</div><div class="field-control">${control}</div></div>`;
}

function configCard(title, fields, actions = '', note = '') {
  return `<div class="card form-card"><div class="form-section-title">${esc(title)}</div><div class="form-grid">${fields.join('')}</div>${note ? `<p class="config-note">${esc(note)}</p>` : ''}<div class="config-actions">${actions}</div></div>`;
}

function renderControllerSettings() {
  const tab = state.activeSubgroup.controller || 'basic';
  const tabs = [['basic', '기본 설정'], ['network', '네트워크'], ['discovery', 'Discovery']];
  const nav = `<div class="tabs">${tabs.map(([id, title]) => `<button class="tab ${tab === id ? 'active' : ''}" data-controller-tab="${id}">${title}</button>`).join('')}</div>`;
  const d = state.coreConfig?.discovery || {};
  if (tab === 'basic') {
    return nav + configCard('컨트롤러 기본 설정', [
      configField('장치 이름', `<input id="basicName" maxlength="80" value="${esc(d.name || '')}">`, '검색 결과에 표시되는 이름'),
      configField('Controller ID', `<input id="basicId" maxlength="80" value="${esc(d.controller_id || '')}" placeholder="비워두면 자동 생성">`, '검색 결과의 고유 식별자'),
      configField('Core 접속 주소', `<input value="${esc(`${state.coreConfig?.core?.host || ''}:${state.coreConfig?.core?.port || ''}`)}" readonly>`, '접속 포트 변경은 서버 재시작이 필요합니다.'),
    ], '<button class="primary" id="saveOperationalSettings">기본 설정 적용</button>');
  }
  if (tab === 'discovery') {
    return nav + configCard('UDP Discovery', [
      configField('검색 사용', `<label class="switch"><input id="discoveryEnabled" type="checkbox" ${d.enabled ? 'checked' : ''}><span class="slider"></span></label>`),
      configField('검색 인터페이스', `<input id="discoveryInterface" maxlength="15" value="${esc(d.interface || 'eth0')}">`, 'Linux NIC 이름'),
      configField('브로드캐스트 주소', `<input id="discoveryBroadcast" value="${esc(d.broadcast_address || '255.255.255.255')}">`),
      configField('UDP 포트', `<input id="discoveryPort" type="number" min="1" max="65535" required value="${esc(d.port ?? 37020)}">`),
      configField('검색 대기 (ms)', `<input id="discoveryTimeout" type="number" min="100" max="10000" required value="${esc(d.timeout_ms ?? 1000)}">`),
      configField('재시도', `<input id="discoveryRetries" type="number" min="1" max="5" required value="${esc(d.retries ?? 2)}">`),
    ], '<button class="primary" id="saveDiscoverySettings">Discovery 적용</button><button class="secondary" id="scanDiscoverySettings">컨트롤러 찾기</button>');
  }
  const n = state.networkStatus || {};
  const pending = n.pending ? '<span class="config-pending">60초 안에 새 주소에서 확인하세요. 미확인 시 자동 복구됩니다.</span><button class="primary" id="confirmNetworkSettings">변경 확인</button>' : '';
  const networkMessage = n.error || (n.supported === false ? 'Linux NetworkManager에서 사용할 수 없습니다.' : n.connection || '설정 조회 중');
  return nav + configCard(`네트워크 · ${n.interface || 'eth0'}`, [
    configField('MAC', `<input value="${esc(n.mac || '')}" readonly>`),
    configField('IPv4 방식', `<select id="networkMethod"><option value="manual" ${n.method === 'manual' ? 'selected' : ''}>고정 IP</option><option value="auto" ${n.method === 'auto' ? 'selected' : ''}>DHCP</option></select>`),
    configField('IP / Prefix', `<input id="networkAddress" value="${esc(n.address || '')}" placeholder="192.168.0.210/24">`),
    configField('Gateway', `<input id="networkGateway" value="${esc(n.gateway || '')}">`),
    configField('DNS', `<input id="networkDns" value="${esc((n.dns || []).join(', '))}">`, '쉼표로 구분'),
  ], `<button class="secondary" id="refreshNetworkSettings">현재 설정 조회</button><button class="primary" id="applyNetworkSettings" ${n.supported !== true || n.pending ? 'disabled' : ''}>네트워크 적용</button>${pending}`, networkMessage);
}

function renderOperationalSettings() {
  const c = state.coreConfig || {};
  if (state.view === 'logging') {
    const log = c.logging || {};
    return configCard('Core Logging', [
      configField('로그 레벨', `<select id="logLevel">${['DEBUG', 'INFO', 'WARNING', 'ERROR'].map(v => `<option ${log.level === v ? 'selected' : ''}>${v}</option>`).join('')}</select>`),
      configField('파일 저장', `<label class="switch"><input id="logFileEnabled" type="checkbox" ${log.file_enabled ? 'checked' : ''}><span class="slider"></span></label>`),
      configField('로그 폴더', `<input value="${esc(c.paths?.logs_dir || 'logs')}" readonly>`, 'Core 설정의 logs_dir 경로'),
      configField('최대 파일 크기 (MB)', `<input id="logMaxMb" type="number" min="1" max="100" required value="${esc(log.max_file_mb ?? 20)}">`),
      configField('보관 파일 수', `<input id="logBackupCount" type="number" min="1" max="30" required value="${esc(log.backup_count ?? 5)}">`),
    ], '<button class="primary" id="saveOperationalSettings">Logging 적용</button>');
  }
  if (state.view === 'alarm') {
    const runtime = c.runtime || {};
    return configCard('장애 판정', [
      configField('PLC 경고 (ms)', `<input id="plcWarnMs" type="number" min="100" max="60000" required value="${esc(runtime.plc_heartbeat_warn_ms ?? 1000)}">`),
      configField('PLC 장애 (ms)', `<input id="plcFaultMs" type="number" min="101" max="60000" required value="${esc(runtime.plc_heartbeat_fault_ms ?? 3000)}">`, '경고 시간보다 커야 합니다.'),
      configField('Vision 상태 만료 (ms)', `<input id="visionMaxAgeMs" type="number" min="100" max="60000" required value="${esc(runtime.vision_status_max_age_ms ?? 1500)}">`),
      configField('장애 유지', `<label class="switch"><input id="faultLatch" type="checkbox" ${runtime.fault_latch_enabled ? 'checked' : ''}><span class="slider"></span></label>`, '수동 리셋 전까지 장애 표시 유지'),
    ], '<button class="primary" id="saveOperationalSettings">Alarm 적용</button>');
  }
  const system = c.system || {};
  const resources = state.coreStatus?.core?.resources || {};
  return configCard('리소스 경고', [
    configField('메모리 경고 (%)', `<input id="memoryWarnPercent" type="number" min="1" max="100" required value="${esc(system.memory_warn_percent ?? 90)}">`, `현재 ${resources.memory?.value ?? '-'}%${resources.memory?.warning ? ' · 경고' : ''}`),
    configField('디스크 경고 (%)', `<input id="diskWarnPercent" type="number" min="1" max="100" required value="${esc(system.disk_warn_percent ?? 90)}">`, `현재 ${resources.disk?.value ?? '-'}%${resources.disk?.warning ? ' · 경고' : ''}`),
  ], '<button class="primary" id="saveOperationalSettings">System 적용</button>', '현재 서버의 리소스 상태에 적용됩니다.');
}

function checkedValues(ids) {
  for (const id of ids) {
    const input = document.getElementById(id);
    if (!input.checkValidity()) {
      input.reportValidity();
      return false;
    }
  }
  return true;
}

async function saveOperationalSettings() {
  const tab = state.activeSubgroup.controller || 'basic';
  let patch;
  if (state.view === 'controller' && tab === 'basic') {
    if (!checkedValues(['basicName', 'basicId'])) return;
    const name = document.getElementById('basicName').value.trim();
    if (!name) return showToast('장치 이름을 입력하세요.');
    patch = { discovery: { name, controller_id: document.getElementById('basicId').value.trim() } };
  } else if (state.view === 'logging') {
    if (!checkedValues(['logMaxMb', 'logBackupCount'])) return;
    patch = { logging: {
      level: document.getElementById('logLevel').value,
      file_enabled: document.getElementById('logFileEnabled').checked,
      max_file_mb: Number(document.getElementById('logMaxMb').value),
      backup_count: Number(document.getElementById('logBackupCount').value),
    } };
  } else if (state.view === 'alarm') {
    if (!checkedValues(['plcWarnMs', 'plcFaultMs', 'visionMaxAgeMs'])) return;
    const warn = Number(document.getElementById('plcWarnMs').value);
    const fault = Number(document.getElementById('plcFaultMs').value);
    if (fault <= warn) return showToast('PLC 장애 시간은 경고 시간보다 커야 합니다.');
    patch = { runtime: {
      plc_heartbeat_warn_ms: warn,
      plc_heartbeat_fault_ms: fault,
      vision_status_max_age_ms: Number(document.getElementById('visionMaxAgeMs').value),
      fault_latch_enabled: document.getElementById('faultLatch').checked,
    } };
  } else if (state.view === 'system') {
    if (!checkedValues(['memoryWarnPercent', 'diskWarnPercent'])) return;
    patch = { system: {
      memory_warn_percent: Number(document.getElementById('memoryWarnPercent').value),
      disk_warn_percent: Number(document.getElementById('diskWarnPercent').value),
    } };
  } else return;
  try {
    state.coreConfig = await apiRequest('/api/config', { method: 'PATCH', body: JSON.stringify(patch) });
    await loadCoreState({ silent: true });
    render();
    showToast('설정을 적용했습니다.');
  } catch (error) { showToast(`설정 적용 실패: ${error.message}`); }
}

async function saveDiscoverySettings() {
  if (!checkedValues(['discoveryInterface', 'discoveryBroadcast', 'discoveryPort', 'discoveryTimeout', 'discoveryRetries'])) return;
  try {
    const d = {
      enabled: document.getElementById('discoveryEnabled').checked,
      method: 'udp_broadcast',
      interface: document.getElementById('discoveryInterface').value.trim(),
      broadcast_address: document.getElementById('discoveryBroadcast').value.trim(),
      port: Number(document.getElementById('discoveryPort').value),
      timeout_ms: Number(document.getElementById('discoveryTimeout').value),
      retries: Number(document.getElementById('discoveryRetries').value),
    };
    state.coreConfig = await apiRequest('/api/config', { method: 'PATCH', body: JSON.stringify({ discovery: d }) });
    showToast('Discovery 설정을 적용했습니다.');
    render();
  } catch (error) { showToast(`Discovery 저장 실패: ${error.message}`); }
}

function updateNetworkFields() {
  const method = document.getElementById('networkMethod');
  if (!method) return;
  const auto = method.value === 'auto';
  for (const id of ['networkAddress', 'networkGateway', 'networkDns']) {
    document.getElementById(id).disabled = auto;
  }
  document.getElementById('networkAddress').required = !auto;
}

async function refreshNetworkSettings() {
  try {
    state.networkStatus = await coreCommand('network.status');
    if (state.view === 'controller' && state.activeSubgroup.controller === 'network') render();
  } catch (error) { showToast(`네트워크 조회 실패: ${error.message}`); }
}

async function applyNetworkSettings() {
  if (state.networkStatus?.supported !== true || state.networkStatus.pending) return showToast('네트워크 설정을 먼저 조회하세요.');
  if (!checkedValues(['networkAddress', 'networkGateway', 'networkDns'])) return;
  const manual = document.getElementById('networkMethod').value === 'manual';
  const params = {
    method: manual ? 'manual' : 'auto',
    address: manual ? document.getElementById('networkAddress').value.trim() : '',
    gateway: manual ? document.getElementById('networkGateway').value.trim() : '',
    dns: manual ? document.getElementById('networkDns').value.trim() : '',
  };
  const nextUrl = params.method === 'manual'
    ? `${window.location.protocol}//${params.address.split('/')[0]}${window.location.port ? `:${window.location.port}` : ''}${window.location.pathname}?network_pending=1`
    : '';
  try {
    const result = await coreCommand('network.apply', params);
    state.networkStatus = { ...(state.networkStatus || {}), pending: true };
    render();
    showToast('새 IP에서 60초 안에 설정을 확인하세요.');
    if (nextUrl) setTimeout(() => window.location.assign(nextUrl), 1500);
    return result;
  } catch (error) {
    if (nextUrl && error instanceof TypeError) {
      showToast('연결이 변경되었습니다. 새 IP로 이동합니다.');
      setTimeout(() => window.location.assign(nextUrl), 2000);
    } else showToast(`네트워크 적용 실패: ${error.message}`);
  }
}

async function confirmNetworkSettings() {
  try {
    await coreCommand('network.confirm');
    showToast('RDK 네트워크 설정을 확정했습니다.');
    await refreshNetworkSettings();
  } catch (error) { showToast(`네트워크 확인 실패: ${error.message}`); }
}

async function saveTextFile(text, { suggestedName, description, extension }) {
  if ('showSaveFilePicker' in window) {
    const handle = await window.showSaveFilePicker({
      suggestedName,
      types: [{ description, accept: { 'application/json': [extension] } }],
      excludeAcceptAllOption: true,
    });
    const writable = await handle.createWritable();
    await writable.write(text);
    await writable.close();
    return handle.name;
  }

  downloadFile(text, suggestedName);
  return suggestedName;
}

async function saveProject() {
  const filename = await saveTextFile(serializeProject(state.settings, state.modules), {
    suggestedName: ensureProjectFilename('trackeye_config'),
    description: 'TRACK-EYE Project',
    extension: '.tre',
  });
  showToast(`${filename} 파일로 저장했습니다.`);
}

async function exportJson() {
  const filename = await saveTextFile(serializeProject(state.settings, state.modules), {
    suggestedName: 'trackeye_config.json',
    description: 'JSON',
    extension: '.json',
  });
  showToast(`${filename} 파일로 내보냈습니다.`);
}

async function loadProjectFile(file) {
  const project = parseProject(await file.text(), file.name);
  state.settings = project.settings;
  if (Array.isArray(project.modules)) state.modules = project.modules;
  render();
  showToast(`${file.name} 파일을 불러왔습니다.`);
}

async function openProject() {
  if ('showOpenFilePicker' in window) {
    const [handle] = await window.showOpenFilePicker({
      types: [{ description: 'TRACK-EYE Project', accept: { 'application/json': ['.tre'] } }],
      excludeAcceptAllOption: true,
      multiple: false,
    });
    await loadProjectFile(await handle.getFile());
    return;
  }
  document.getElementById('projectFileInput').click();
}

document.querySelectorAll('[data-file-action]').forEach(btn => btn.addEventListener('click', async () => {
  const action = btn.dataset.fileAction;
  fileMenu.classList.add('hidden');
  try {
    if (action === 'open') await openProject();
    else if (action === 'save') await saveProject();
    else if (action === 'export') await exportJson();
    else if (action === 'new') {
      state.settings = structuredClone(DATA.settings);
      state.modules = structuredClone(DATA.modules || []);
      render();
      showToast('기본 설정으로 초기화했습니다.');
    }
  } catch (error) {
    if (error?.name !== 'AbortError') showToast(action === 'open' ? '올바른 .tre 설정 파일이 아닙니다.' : '파일을 저장하지 못했습니다.');
  }
}));

document.getElementById('projectFileInput').addEventListener('change', async e => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  try {
    await loadProjectFile(file);
  } catch {
    showToast('올바른 .tre 설정 파일이 아닙니다.');
  }
});

async function saveCurrentView() {
  if (state.view === 'controller') {
    if (state.activeSubgroup.controller === 'network') return applyNetworkSettings();
    if (state.activeSubgroup.controller === 'discovery') return saveDiscoverySettings();
    return saveOperationalSettings();
  }
  if (['logging', 'alarm', 'system'].includes(state.view)) return saveOperationalSettings();
  return pushCommand('SET_CONFIG', { patch: buildProcessesPatch() });
}

document.getElementById('saveBtn').addEventListener('click', saveCurrentView);
document.getElementById('applyBtn').addEventListener('click', async () => {
  if (['controller', 'logging', 'alarm', 'system'].includes(state.view)) return saveCurrentView();
  await pushCommand('SET_CONFIG', { patch: buildProcessesPatch() });
  await pushCommand('APPLY_CONFIG', { target: 'all' });
});

const backdrop = document.getElementById('modalBackdrop');
const controllerResults = document.getElementById('controllerResults');
let controllers = [];

function renderControllers() {
  controllerResults.innerHTML = controllers.length ? controllers.map((c, i) => `<label class="controller-result ${state.selectedController === i ? 'selected' : ''}"><input type="radio" name="ctrl" ${state.selectedController === i ? 'checked' : ''} value="${i}"><div><strong>${esc(c.name)}</strong><small>${esc(c.id)}</small></div><div><strong>${esc(c.ip)}</strong><small>Web :${esc(c.web_port || c.core_port || '')}</small></div>${statusBadge('ONLINE')}</label>`).join('') : '<p>검색된 Core가 없습니다.</p>';
  document.getElementById('selectControllerBtn').disabled = controllers.length === 0;
  controllerResults.querySelectorAll('input').forEach(r => r.addEventListener('change', () => {
    state.selectedController = Number(r.value);
    renderControllers();
  }));
}

async function scanControllers() {
  controllerResults.textContent = '검색 중...';
  document.getElementById('selectControllerBtn').disabled = true;
  try {
    controllers = await coreCommand('discovery.scan');
    state.selectedController = 0;
    renderControllers();
  } catch (error) {
    controllers = [];
    controllerResults.textContent = `검색 실패: ${error.message}`;
  }
}

function openDiscovery() {
  renderControllers();
  backdrop.classList.remove('hidden');
  scanControllers();
}

document.getElementById('discoverBtn').addEventListener('click', openDiscovery);
document.getElementById('changeControllerBtn').addEventListener('click', openDiscovery);
document.getElementById('closeModalBtn').addEventListener('click', () => backdrop.classList.add('hidden'));
document.getElementById('rescanBtn').addEventListener('click', scanControllers);
document.getElementById('selectControllerBtn').addEventListener('click', () => {
  const selected = controllers[state.selectedController];
  if (!selected) return;
  const port = Number(selected.web_port || selected.core_port);
  if (!selected.ip || !Number.isInteger(port) || port < 1 || port > 65535) return;
  window.location.assign(`http://${selected.ip}:${port}/`);
});
backdrop.addEventListener('click', e => { if (e.target === backdrop) backdrop.classList.add('hidden'); });

const connectBtn = document.getElementById('connectBtn');
connectBtn.addEventListener('click', async () => {
  if (state.apiOnline) {
    state.apiOnline = false;
    state.connected = false;
    connectBtn.textContent = '연결';
    updateConnectionBadge();
    showToast('화면 연결을 일시 해제했습니다.');
    render();
    return;
  }
  await loadCoreState({ force: true });
  connectBtn.textContent = state.apiOnline ? '연결 해제' : '연결';
  render();
});

if (new URLSearchParams(window.location.search).has('network_pending')) {
  state.view = 'controller';
  state.activeSubgroup.controller = 'network';
  document.querySelectorAll('.nav-item').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.view === 'controller'));
}
loadCoreState({ silent: true }).finally(() => {
  connectBtn.textContent = state.apiOnline ? '연결 해제' : '연결';
  render();
  if (state.view === 'controller' && state.activeSubgroup.controller === 'network') refreshNetworkSettings();
});
setInterval(() => {
  if (!state.apiOnline && !state.connected) return;
  if (!shouldAutoRefreshView()) return;
  loadCoreState({ silent: true }).then(updated => {
    if (updated && shouldAutoRefreshView()) render();
  });
}, 1500);
