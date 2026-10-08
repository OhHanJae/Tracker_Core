const DATA = window.TRACK_EYE_DATA;
const PLC_MAP = window.TRACK_EYE_PLC_MAP;

const state = {
  view: 'dashboard',
  connected: true,
  selectedController: 0,
  settings: structuredClone(DATA.settings),
  modules: structuredClone(DATA.modules || []),
  activeSubgroup: { plc: '연결/통신' },
  plcMapSheet: '01_PLC_to_Controller',
  commandLog: [],
  coreStatus: null,
  coreConfig: null,
  processes: {},
  devices: {},
  httpCommunication: {},
  apiOnline: false,
  lastApiError: '',
  coreStateLoading: false,
};

const viewRoot = document.getElementById('viewRoot');
const pageTitle = document.getElementById('pageTitle');
const pageActions = document.getElementById('pageActions');
const toast = document.getElementById('toast');

const titles = {
  dashboard: ['Dashboard', '컨트롤러, 서비스, 프로세스 상태를 한 화면에서 확인합니다.'],
  controller: ['Controller', '검색, 기본 정보, 네트워크 등 컨트롤러 전역 설정입니다.'],
  plc: ['PLC', 'XGT 전용 통신, D 디바이스 주소, PLC Data Map, 공유 메모리를 관리합니다.'],
  ptm: ['PTM', 'PTM Web 화면을 표시합니다.'],
  vision: ['Vision', 'Vision Web 화면과 연결 상태를 표시합니다.'],
  services: ['Process Manager', '프로세스 설정과 실행·중지·재시작을 관리합니다.'],
  process: ['Watchdog', '프로세스별 자동 재시작과 Watchdog 상태를 관리합니다.'],
  logging: ['Logging', 'Core 로그 레벨과 파일 보관을 설정합니다.'],
  alarm: ['Alarm / Fault', 'PLC 및 Vision 장애 판정 기준을 설정합니다.'],
  system: ['System', '메모리와 디스크 사용률 경고 기준을 설정합니다.'],
  settings: ['전체 설정 목록', '현재 Configurator에서 노출하는 설정 항목을 검색·필터링해서 확인합니다.'],
  scope: ['설정 책임 범위', '메인 컨트롤 앱과 각 Setup / Calibration 화면의 책임 범위를 구분합니다.'],
};

const categoryMap = {
  controller: '컨트롤러',
  logging: '로그',
  alarm: '알람/장애',
  system: '시스템',
};

const selectOptions = {
  '검색 방식': ['UDP Broadcast', 'mDNS', 'Manual'],
  'PLC 제조사': ['LS Electric', 'Mitsubishi', 'Siemens'],
  'PLC Series': ['XGK', 'XGI', 'XGR'],
  'Protocol': ['XGT Dedicated', 'TCP JSON', 'HTTP'],
  'Byte Order': ['Big Endian', 'Little Endian'],
  'Health Check Type': ['TCP', 'HTTP', 'Process'],
  'Log Level': ['DEBUG', 'INFO', 'WARNING', 'ERROR'],
  'Alarm Reset 조건': ['Auto', 'Manual'],
  'Create Mode': ['Main Process', 'PLC Process'],
  'Timezone': ['Asia/Seoul', 'UTC'],
};

function esc(v = '') {
  return String(v).replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
}

function showToast(msg) {
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 1900);
}

function priorityClass(p) {
  return p === '필수' ? 'required' : p === '권장' ? 'recommended' : p === '고급' ? 'advanced' : 'optional';
}

function statusBadge(text) {
  const t = String(text).toUpperCase();
  const cls = t === 'DISABLED' ? 'blue' : /^(ONLINE|CONNECTED|RUNNING|ALIVE|ON|OK|LISTEN|HEALTHY)$/.test(t) ? 'green' : /WARN|DEGRADED|IDLE/.test(t) ? 'orange' : 'red';
  const dot = cls === 'blue' ? '' : cls === 'green' ? 'online' : cls === 'orange' ? 'warn' : 'offline';
  return `<span class="badge ${cls}"><span class="status-dot ${dot}"></span>${esc(text)}</span>`;
}

function parseSwitch(v) {
  return ['ON', 'TRUE', 'YES', '1'].includes(String(v).toUpperCase());
}

const PROCESS_CONFIG_KEY_MAP = {
  plc: 'plc_gateway',
  plc_gateway: 'plc_gateway',
  ptm: 'ptm',
  vision: 'vision',
  laser: 'laser',
  config: 'config',
  main: 'main',
};

function resolveApiBase() {
  if (window.location.protocol === 'file:') return 'http://127.0.0.1:8000';
  if (window.location.hostname === '0.0.0.0') {
    const port = window.location.port || '8000';
    return `${window.location.protocol}//127.0.0.1:${port}`;
  }
  return '';
}

const API_BASE = resolveApiBase();

function apiTarget(path) {
  return `${API_BASE}${path}`;
}

async function apiRequest(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  const response = await fetch(apiTarget(path), { ...options, headers });
  const text = await response.text();
  let body = {};
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { ok: response.ok, result: text };
    }
  }
  if (!response.ok || body.ok === false) {
    const error = body.error || {};
    throw new Error(error.message || error.code || `${response.status} ${response.statusText}`);
  }
  return body.result ?? body;
}

async function coreCommand(command, params = {}) {
  return apiRequest('/api/command', {
    method: 'POST',
    body: JSON.stringify({ command, params }),
  });
}

function displayModuleId(configKey) {
  return configKey === 'plc_gateway' ? 'plc' : configKey;
}

// Config keys are only used to address settings.processes entries.
// Runtime process control must always use process_name.
function moduleConfigKey(moduleOrId) {
  const raw = typeof moduleOrId === 'string'
    ? moduleOrId
    : moduleOrId?.configKey || moduleOrId?.id;
  if (!raw) return '';
  return PROCESS_CONFIG_KEY_MAP[raw] || raw;
}

function configuredProcessNameForKey(configKey) {
  const config = state.coreConfig?.processes?.[configKey];
  return String(config?.process_name || '').trim();
}

function moduleProcessName(moduleOrName) {
  if (!moduleOrName) return '';

  if (typeof moduleOrName === 'object') {
    const direct = String(moduleOrName.process || moduleOrName.process_name || '').trim();
    if (direct) return direct;
    return configuredProcessNameForKey(moduleConfigKey(moduleOrName));
  }

  const raw = String(moduleOrName).trim();
  if (!raw) return '';

  if (Object.prototype.hasOwnProperty.call(state.processes || {}, raw)) return raw;

  const module = state.modules.find(m =>
    m.process === raw ||
    m.process_name === raw ||
    m.name === raw ||
    m.id === raw ||
    m.configKey === raw ||
    moduleConfigKey(m) === raw
  );
  if (module) {
    const direct = String(module.process || module.process_name || '').trim();
    if (direct) return direct;
    const configured = configuredProcessNameForKey(moduleConfigKey(module));
    if (configured) return configured;
  }

  const configured = configuredProcessNameForKey(moduleConfigKey(raw));
  if (configured) return configured;

  const runtimeMatch = Object.entries(state.processes || {}).find(([processName, proc]) =>
    processName === raw || proc?.process_name === raw || proc?.name === raw
  );
  return runtimeMatch?.[0] || '';
}

function isConfiguredProcessName(processName) {
  const target = String(processName || '').trim();
  if (!target) return false;

  const configured = state.coreConfig?.processes;
  if (configured && Object.keys(configured).length) {
    return Object.values(configured).some(config =>
      String(config?.process_name || '').trim() === target
    );
  }

  return state.modules.some(module => moduleProcessName(module) === target);
}

function moduleForProcessName(name) {
  const raw = String(name || '').trim();
  if (!raw) return null;

  const exact = state.modules.find(m =>
    m.name === raw || m.process === raw || m.process_name === raw
  );
  if (exact) return exact;

  const text = raw.toLowerCase();
  let preferredId = '';
  if (text.includes('plc')) preferredId = 'plc';
  else if (text.includes('ptm')) preferredId = 'ptm';
  else if (text.includes('vision')) preferredId = 'vision';
  else if (text.includes('laser')) preferredId = 'laser';
  else if (text.includes('config')) preferredId = 'config';
  else if (text.includes('tracker main')) preferredId = 'main';

  if (!preferredId) return null;
  const preferredConfigKey = moduleConfigKey(preferredId);
  return state.modules.find(m =>
    m.id === preferredId || moduleConfigKey(m) === preferredConfigKey
  ) || null;
}

function processNameFromDisplayName(name) {
  const module = moduleForProcessName(name);
  if (module) return moduleProcessName(module);

  const raw = String(name || '').trim();
  const runtimeMatch = Object.entries(state.processes || {}).find(([processName, proc]) =>
    processName === raw || proc?.name === raw
  );
  return runtimeMatch?.[0] || '';
}

function processForModule(moduleOrName) {
  const processName = moduleProcessName(moduleOrName);
  return processName ? state.processes?.[processName] || null : null;
}

function endpointForProcess(proc, module = null) {
  if (proc?.communication?.endpoint) return proc.communication.endpoint;
  if (proc?.health?.endpoint) return proc.health.endpoint;
  const host = proc?.health_host || module?.healthHost;
  const port = proc?.health_port || module?.healthPort;
  if (host && port) return `${host}:${port}`;
  return module?.endpoint || '-';
}

function statusTextForProcess(proc, fallback = 'OFFLINE') {
  if (!proc) return state.apiOnline ? 'UNKNOWN' : fallback;
  if (proc.status) return proc.status;
  if (proc.enabled === false) return 'Disabled';
  if (!proc.running) return 'Offline: process dead';
  if (!proc.communication?.online) return 'Communication Error';
  return 'Online';
}

function normalizeHealthType(value) {
  const text = String(value || '').toLowerCase();
  if (text.includes('http')) return 'http';
  if (text.includes('tcp')) return 'tcp';
  if (text.includes('process')) return 'process';
  return text || 'none';
}

function splitEndpoint(endpoint = '') {
  const value = String(endpoint || '').trim();
  const match = /^(.*):(\d+)$/.exec(value);
  if (!match) return { host: '', port: 0 };
  return { host: match[1], port: Number(match[2]) };
}

function normalizeConnectHost(host) {
  const value = String(host || '').trim();
  return value === '0.0.0.0' || value === '::' ? '127.0.0.1' : value;
}

function buildModulePatch(module) {
  const configKey = moduleConfigKey(module);
  if (!configKey) throw new Error('module config key is missing.');

  const current = state.coreConfig?.processes?.[configKey] || {};
  const processName = String(module.process || current.process_name || '').trim();
  if (!processName) throw new Error('process_name is required.');

  const endpoint = splitEndpoint(module.endpoint);
  const tcpHost = normalizeConnectHost(module.tcpHost || endpoint.host || module.healthHost || current.health_host || '127.0.0.1');
  const tcpPort = Number(module.tcpPort || endpoint.port || module.healthPort || current.health_port || 0);
  const patch = {
    processes: {
      [configKey]: {
        name: module.name || current.name || processName,
        enabled: module.enabled ?? current.enabled ?? true,
        process_name: processName,
        start_scripts: module.scriptPath || '',
        stop_script: module.stopScript || '',
        working_dir: module.workingDir || '',
        health_type: module.healthType || current.health_type || normalizeHealthType(module.health),
        health_host: tcpHost,
        health_port: tcpPort,
        auto_restart: module.autoRestart ?? current.auto_restart ?? false,
      },
    },
  };

  if (configKey === 'plc_gateway') {
    patch.xgt = {
      control: { host: tcpHost, port: tcpPort },
      web: {
        host: tcpHost,
        port: Number(module.webPort || state.coreConfig?.xgt?.web?.port || 5051),
      },
    };
  } else if (configKey === 'ptm') {
    patch.motor = {
      host: tcpHost,
      port: tcpPort,
      web_host: tcpHost,
      web_port: Number(module.webPort || state.coreConfig?.motor?.web_port || 8080),
    };
  } else if (configKey === 'vision') {
    const webPort = Number(module.webPort || 0);
    patch.vision = {
      host: tcpHost,
      port: tcpPort,
      enabled: module.enabled !== false,
      web_host: tcpHost,
      web_port: webPort,
      web_enabled: webPort > 0,
    };
  }
  return patch;
}

function buildProcessesPatch() {
  const configured = state.coreConfig?.processes || {};
  const hasCoreConfig = Object.keys(configured).length > 0;

  return state.modules.reduce((patch, module) => {
    const configKey = moduleConfigKey(module);
    if (!configKey) return patch;
    if (hasCoreConfig && !Object.prototype.hasOwnProperty.call(configured, configKey)) return patch;

    const item = buildModulePatch(module).processes[configKey];
    patch.processes[configKey] = item;
    return patch;
  }, { processes: {} });
}

function mergeModulePatchIntoLocal(patch) {
  if (!patch?.processes) return;
  state.coreConfig = state.coreConfig || {};
  state.coreConfig.processes = {
    ...(state.coreConfig.processes || {}),
    ...patch.processes,
  };
  // Do not write config keys into state.processes.
  // state.processes is runtime state and is keyed only by process_name.
}

function syncModulesFromCore() {
  const configs = state.coreConfig?.processes || {};
  const processState = state.processes || {};
  if (!Object.keys(configs).length && !Object.keys(processState).length) return;

  const previousByConfigKey = new Map(
    state.modules.map(module => [moduleConfigKey(module), module])
  );
  const previousByProcessName = new Map(
    state.modules
      .map(module => [moduleProcessName(module), module])
      .filter(([processName]) => processName)
  );

  const consumedProcessNames = new Set();
  const configuredModules = Object.entries(configs)
    .filter(([configKey]) => !['config', 'main'].includes(configKey))
    .map(([configKey, config]) => {
    const processName = String(config?.process_name || '').trim();
    const proc = processName ? processState[processName] || {} : {};
    const old = previousByConfigKey.get(configKey) || previousByProcessName.get(processName) || {};
    if (processName) consumedProcessNames.add(processName);

    const endpoint = config.health_host && config.health_port
      ? `${config.health_host}:${config.health_port}`
      : endpointForProcess(proc, old);
    const tcpConfig = configKey === 'plc_gateway'
      ? state.coreConfig?.xgt?.control
      : configKey === 'ptm' ? state.coreConfig?.motor
        : configKey === 'vision' ? state.coreConfig?.vision : null;
    const webConfig = configKey === 'plc_gateway'
      ? state.coreConfig?.xgt?.web
      : configKey === 'ptm' ? state.coreConfig?.motor
        : configKey === 'vision' ? state.coreConfig?.vision : null;
    return {
      ...old,
      id: displayModuleId(configKey),
      configKey,
      name: config.name || proc.name || old.name || processName || configKey,
      process: processName,
      endpoint,
      health: String(config.health_type || proc.health_type || old.health || 'none').toUpperCase(),
      healthType: config.health_type || proc.health_type || old.healthType || 'none',
      healthHost: config.health_host || proc.health_host || old.healthHost || '',
      healthPort: config.health_port || proc.health_port || old.healthPort || 0,
      scriptPath: config.start_scripts ?? proc.start_scripts ?? old.scriptPath ?? '',
      stopScript: config.stop_script ?? proc.stop_script ?? old.stopScript ?? '',
      workingDir: config.working_dir ?? proc.working_dir ?? old.workingDir ?? '',
      enabled: config.enabled ?? proc.enabled ?? old.enabled ?? true,
      autoRestart: config.auto_restart ?? proc.auto_restart ?? old.autoRestart ?? false,
      tcpHost: tcpConfig?.host || config.health_host || old.tcpHost || '',
      tcpPort: tcpConfig?.port || config.health_port || old.tcpPort || 0,
      webHost: webConfig?.web_host || (configKey === 'plc_gateway' ? webConfig?.host : '') || old.webHost || '',
      webPort: webConfig?.web_port || (configKey === 'plc_gateway' ? webConfig?.port : 0) || old.webPort || 0,
    };
  });

  const runtimeOnlyModules = Object.entries(processState)
    .filter(([processName]) => !consumedProcessNames.has(processName))
    .map(([processName, proc]) => {
      const old = previousByProcessName.get(processName) || {};
      const endpoint = endpointForProcess(proc, old);
      return {
        ...old,
        id: old.id || processName,
        configKey: old.configKey || '',
        name: proc.name || old.name || processName,
        process: processName,
        endpoint,
        health: String(proc.health_type || old.health || 'none').toUpperCase(),
        healthType: proc.health_type || old.healthType || 'none',
        healthHost: proc.health_host || old.healthHost || '',
        healthPort: proc.health_port || old.healthPort || 0,
        scriptPath: proc.start_scripts ?? old.scriptPath ?? '',
        stopScript: proc.stop_script ?? old.stopScript ?? '',
        workingDir: proc.working_dir ?? old.workingDir ?? '',
        enabled: proc.enabled ?? old.enabled ?? true,
        autoRestart: proc.auto_restart ?? old.autoRestart ?? false,
      };
    });

  state.modules = [...configuredModules, ...runtimeOnlyModules];
}

function updateConnectionBadge() {
  const badge = document.querySelector('.connection-badge');
  if (!badge) return;
  badge.innerHTML = `<span class="status-dot ${state.apiOnline ? 'online' : 'offline'}"></span>${state.apiOnline ? 'CONNECTED' : 'DISCONNECTED'}`;
}

function isEditingControl() {
  const el = document.activeElement;
  if (!el || el === document.body) return false;
  return Boolean(el.closest('input, textarea, select, [contenteditable="true"]'));
}

function shouldAutoRefreshView() {
  return state.view === 'dashboard' && !isEditingControl();
}

async function loadCoreState({ silent = false, force = false } = {}) {
  if (state.coreStateLoading && !force) return false;
  state.coreStateLoading = true;
  try {
    const [status, config, devices] = await Promise.all([
      apiRequest('/api/status'),
      apiRequest('/api/config'),
      apiRequest('/api/devices'),
    ]);
    state.coreStatus = status;
    state.coreConfig = config;
    state.processes = status?.processes || {};
    state.devices = devices || status?.devices || {};
    state.httpCommunication = status?.http_communication || {};
    state.apiOnline = true;
    state.connected = true;
    state.lastApiError = '';
    syncModulesFromCore();
    return true;
  } catch (error) {
    state.apiOnline = false;
    state.connected = false;
    state.lastApiError = error.message;
    if (!silent) showToast(`Core API 연결 실패: ${error.message}`);
    return false;
  } finally {
    state.coreStateLoading = false;
    updateConnectionBadge();
  }
}

function recordCommand(packet, result = null, error = null) {
  state.commandLog.unshift({
    time: new Date().toLocaleTimeString('ko-KR'),
    ok: !error,
    packet,
    result,
    error: error ? String(error.message || error) : null,
  });
  state.commandLog = state.commandLog.slice(0, 20);
}

function getSetting(name, subgroup = null) {
  return state.settings.find(x => x['설정 항목'] === name && (!subgroup || x['중분류'] === subgroup));
}

function getSettingValue(name, fallback = '') {
  return getSetting(name)?.['예시/기본값'] ?? fallback;
}

function parseDAddress(v, fallback) {
  const m = /^D(\d+)$/i.exec(String(v || '').trim());
  return m ? Number(m[1]) : fallback;
}

function getPlcBase(direction) {
  if (direction === 'read') {
    return parseDAddress(getSettingValue('Read Base', getSettingValue('Read Start Address', 'D1000')), 1000);
  }
  return parseDAddress(getSettingValue('Write Base', getSettingValue('Write Start Address', 'D1100')), 1100);
}

function convertRelativeAddresses(value, direction) {
  if (typeof value !== 'string' || !direction || !value.includes('D+')) return value;
  const base = getPlcBase(direction);
  let out = value;

  // D+50/52/54/56 -> D1150/D1152/D1154/D1156
  out = out.replace(/D\+(\d+)((?:\/\d+)+)/g, (_, first, rest) => {
    const nums = [first, ...rest.slice(1).split('/')];
    return nums.map(n => `D${base + Number(n)}`).join('/');
  });

  // D+00~D+19 -> D1100~D1119
  out = out.replace(/D\+(\d+)~D\+(\d+)/g, (_, a, b) => `D${base + Number(a)}~D${base + Number(b)}`);

  // D+20.1 -> D1120.1, D+07 -> D1107
  out = out.replace(/D\+(\d+)(\.[0-9A-F]+)?/gi, (_, n, bit = '') => `D${base + Number(n)}${bit}`);
  return out;
}

function isDeviceAddressItem(item) {
  const n = item['설정 항목'] || '';
  return /Address|Base/i.test(n) && /^D\d+$/i.test(String(item['예시/기본값'] || '').trim());
}

function controlFor(item) {
  const type = item['UI 형태'];
  const value = item['예시/기본값'];
  const key = `${item['대분류']}|${item['중분류']}|${item['설정 항목']}`;

  if (type === '스위치') {
    return `<label class="switch"><input data-setting-key="${esc(key)}" type="checkbox" ${parseSwitch(value) ? 'checked' : ''}><span class="slider"></span></label>`;
  }
  if (type === '읽기전용' || type === '상태표시' || type === '텍스트/읽기전용') {
    return `<input data-setting-key="${esc(key)}" value="${esc(value)}" readonly>`;
  }
  if (type === '콤보') {
    const opts = selectOptions[item['설정 항목']] || String(value).split(/\s*\/\s*|\s*,\s*/).filter(Boolean);
    const safeOpts = opts.length > 1 ? opts : [value, 'Auto', 'Manual'];
    return `<select data-setting-key="${esc(key)}">${[...new Set(safeOpts)].map(o => `<option ${String(o) === String(value) ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
  }
  if (type === '멀티라인') {
    return `<textarea data-setting-key="${esc(key)}">${esc(value)}</textarea>`;
  }
  if (isDeviceAddressItem(item)) {
    return `<div class="address-input-wrap"><input class="mono" data-plc-address="1" data-setting-key="${esc(key)}" value="${esc(value)}" placeholder="D1000"><span>Dnnn 형식</span></div>`;
  }
  return `<input data-setting-key="${esc(key)}" type="text" value="${esc(value)}">`;
}

function renderSettingsForm(rows, title) {
  return `<div class="card form-card">
    <div class="form-section-title">${esc(title)} <span class="count-pill">${rows.length} items</span></div>
    <div class="form-grid">
      ${rows.map(item => `<div class="form-field">
        <div class="field-label">
          <strong>${esc(item['설정 항목'])}<span class="priority ${priorityClass(item['중요도'])}">${esc(item['중요도'])}</span></strong>
          <small>${esc(item['설명'])}</small>
        </div>
        <div class="field-control">${controlFor(item)}</div>
      </div>`).join('')}
    </div>
  </div>`;
}

function renderSettingsCategory(categories, forcedSubgroup = null, hideTabs = false) {
  const cats = Array.isArray(categories) ? categories : [categories];
  const rows = state.settings.filter(x => cats.includes(x['대분류']));
  if (!rows.length) return `<div class="card empty">표시할 설정이 없습니다.</div>`;
  let subgroups = [...new Set(rows.map(x => x['중분류']))];
  if (state.view === 'controller') {
    const controllerOrder = ['기본 설정', '네트워크', 'Discovery'];
    subgroups = controllerOrder.filter(group => subgroups.includes(group));
  }
  const active = forcedSubgroup || state.activeSubgroup[state.view] || subgroups[0];
  state.activeSubgroup[state.view] = active;
  const filtered = rows.filter(x => x['중분류'] === active);
  return `${hideTabs ? '' : `<div class="tabs">${subgroups.map(s => `<button class="tab ${s === active ? 'active' : ''}" data-subgroup="${esc(s)}">${esc(state.view === 'controller' && s === '기본 설정' ? '기본설정' : s)}</button>`).join('')}</div>`}
    ${renderSettingsForm(filtered, active)}`;
}


export {
  DATA,
  PLC_MAP,
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
  displayModuleId,
  moduleConfigKey,
  moduleProcessName,
  isConfiguredProcessName,
  processNameFromDisplayName,
  moduleForProcessName,
  processForModule,
  endpointForProcess,
  statusTextForProcess,
  normalizeHealthType,
  splitEndpoint,
  normalizeConnectHost,
  buildModulePatch,
  buildProcessesPatch,
  mergeModulePatchIntoLocal,
  syncModulesFromCore,
  updateConnectionBadge,
  isEditingControl,
  shouldAutoRefreshView,
  loadCoreState,
  recordCommand,
  getSetting,
  getSettingValue,
  parseDAddress,
  getPlcBase,
  convertRelativeAddresses,
  isDeviceAddressItem,
  controlFor,
  renderSettingsForm,
  renderSettingsCategory,
};
