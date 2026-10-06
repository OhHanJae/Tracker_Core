import { renderDashboard } from './dashboard.js';
import { renderPLC, startPlcRuntime, stopPlcRuntime } from './plc/connection.js';
import { renderPTM, startPtmRuntime, stopPtmRuntime } from './ptm/connection.js';
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
  const externalFrameView = ['plc', 'ptm'].includes(state.view);
  document.documentElement.classList.toggle('external-frame-view', externalFrameView);
  document.body.classList.toggle('external-frame-view', externalFrameView);
  const [title, desc] = titles[state.view];
  pageTitle.textContent = title;
  pageActions.innerHTML = state.view === 'services' ? `<button class="secondary" data-action="restart-all">전체 재시작</button>` : '';

  if (state.view === 'dashboard') viewRoot.innerHTML = renderDashboard();
  else if (state.view === 'plc') viewRoot.innerHTML = renderPLC();
  else if (state.view === 'ptm') viewRoot.innerHTML = renderPTM();
  else if (state.view === 'process') viewRoot.innerHTML = renderProcess();
  else if (state.view === 'services') viewRoot.innerHTML = renderServices();
  else if (state.view === 'settings') viewRoot.innerHTML = renderAllSettings();
  else if (state.view === 'scope') viewRoot.innerHTML = renderScope();
  else viewRoot.innerHTML = renderSettingsCategory(categoryMap[state.view]);

  bindDynamicEvents();
  if (state.view === 'plc') startPlcRuntime();
  if (state.view === 'ptm') startPtmRuntime();
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

document.getElementById('saveBtn').addEventListener('click', async () => pushCommand('SET_CONFIG', { patch: buildProcessesPatch() }));
document.getElementById('applyBtn').addEventListener('click', async () => {
  await pushCommand('SET_CONFIG', { patch: buildProcessesPatch() });
  await pushCommand('APPLY_CONFIG', { target: 'all' });
});

const backdrop = document.getElementById('modalBackdrop');
const controllerResults = document.getElementById('controllerResults');
const controllers = [
  { name: 'BPC_LINE_01', id: 'TRK-A8F21C', ip: '192.168.0.210', mac: 'A4:11:62:9C:2D:01', version: '1.0.3', state: 'ONLINE' },
  { name: 'BPC_LINE_02', id: 'TRK-C7D312', ip: '192.168.0.211', mac: 'A4:11:62:9C:2D:02', version: '1.0.3', state: 'ONLINE' },
  { name: 'TRACKER_DEV', id: 'TRK-D19F04', ip: '192.168.0.220', mac: 'A4:11:62:9C:2D:0A', version: '1.0.2', state: 'ONLINE' },
];

function renderControllers() {
  controllerResults.innerHTML = controllers.map((c, i) => `<label class="controller-result ${state.selectedController === i ? 'selected' : ''}"><input type="radio" name="ctrl" ${state.selectedController === i ? 'checked' : ''} value="${i}"><div><strong>${c.name}</strong><small>${c.id} · ${c.mac}</small></div><div><strong>${c.ip}</strong><small>Core v${c.version}</small></div>${statusBadge(c.state)}</label>`).join('');
  controllerResults.querySelectorAll('input').forEach(r => r.addEventListener('change', () => {
    state.selectedController = Number(r.value);
    renderControllers();
  }));
}

function openDiscovery() {
  renderControllers();
  backdrop.classList.remove('hidden');
}

document.getElementById('discoverBtn').addEventListener('click', openDiscovery);
document.getElementById('changeControllerBtn').addEventListener('click', openDiscovery);
document.getElementById('closeModalBtn').addEventListener('click', () => backdrop.classList.add('hidden'));
document.getElementById('rescanBtn').addEventListener('click', () => { renderControllers(); showToast('UDP Broadcast 재검색을 시뮬레이션했습니다.'); });
document.getElementById('selectControllerBtn').addEventListener('click', () => { backdrop.classList.add('hidden'); showToast(`${controllers[state.selectedController].name} 컨트롤러를 선택했습니다.`); });
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

loadCoreState({ silent: true }).finally(() => {
  connectBtn.textContent = state.apiOnline ? '연결 해제' : '연결';
  render();
});
setInterval(() => {
  if (!state.apiOnline && !state.connected) return;
  if (!shouldAutoRefreshView()) return;
  loadCoreState({ silent: true }).then(updated => {
    if (updated && shouldAutoRefreshView()) render();
  });
}, 1500);
