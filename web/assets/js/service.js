import {
  state,
  esc,
  statusBadge,
  processForModule,
  endpointForProcess,
  statusTextForProcess,
} from './global.js';

function communicationFields(module) {
  if (!['plc', 'ptm', 'vision'].includes(module.id)) {
    return `<label><span>Health Endpoint</span><input class="mono" data-module-id="${esc(module.id)}" data-module-field="endpoint" value="${esc(module.endpoint || '')}" placeholder="127.0.0.1:8766"></label>`;
  }
  return `
    <label><span>Server IP (TCP / Web)</span><input class="mono" data-module-id="${esc(module.id)}" data-module-field="tcpHost" value="${esc(module.tcpHost || module.webHost || '')}" placeholder="127.0.0.1"></label>
    <label><span>TCP Server Port</span><input class="mono" type="number" min="1" max="65535" data-module-id="${esc(module.id)}" data-module-field="tcpPort" value="${esc(module.tcpPort || '')}"></label>
    <label><span>Web Server Port</span><input class="mono" type="number" min="0" max="65535" data-module-id="${esc(module.id)}" data-module-field="webPort" value="${esc(module.webPort || '')}" placeholder="${module.id === 'vision' ? '미정 (0=비활성)' : ''}"></label>`;
}

export function renderServices() {
  const lastLog = state.commandLog[0];
  const logText = lastLog
    ? JSON.stringify({ request: lastLog.packet, result: lastLog.result, error: lastLog.error }, null, 2)
    : '{\n  "message": "아직 실행한 명령 없음"\n}';

  return `<div class="module-grid">
    ${state.modules.filter(m => !['config', 'main'].includes(m.id)).map(m => {
    const proc = processForModule(m);
    const status = statusTextForProcess(proc);
    const endpoint = m.endpoint || endpointForProcess(proc, m);
    const processName = String(m.process || proc?.process_name || '').trim();
    const processDisabled = processName ? '' : 'disabled';
    return `<div class="card module-card">
        <div class="module-head"><div><span class="status-dot ${proc?.online ? 'online' : 'offline'}"></span><strong>${esc(m.name)}</strong><small>${esc(endpoint)} · ${esc(m.health || m.healthType || '-')}</small></div>${statusBadge(status)}</div>
        <div class="module-body">
          <label><span>Enabled</span><span class="switch"><input type="checkbox" data-module-id="${esc(m.id)}" data-module-field="enabled" ${m.enabled !== false ? 'checked' : ''}><span class="slider"></span></span></label>
          <label><span>Process Name</span><input class="mono" data-module-id="${esc(m.id)}" data-module-field="process" value="${esc(m.process)}"></label>
          ${communicationFields(m)}
          <label><span>Start Script</span><input class="mono" data-module-id="${esc(m.id)}" data-module-field="scriptPath" value="${esc(m.scriptPath)}" placeholder="run_core.bat 또는 scripts/start.sh"></label>
          <label><span>Working Directory</span><input class="mono" data-module-id="${esc(m.id)}" data-module-field="workingDir" value="${esc(m.workingDir)}" placeholder="비우면 Start Script 폴더"></label>
        </div>
        <div class="module-actions">
          <button class="mini-btn primary-lite" data-module-action="apply" data-module-id="${esc(m.id)}">설정 저장</button>
          <button class="mini-btn" data-proc-action="status" data-proc-name="${esc(processName)}" ${processDisabled}>상태 조회</button>
          <button class="mini-btn" data-proc-action="start" data-proc-name="${esc(processName)}" ${processDisabled}>Start</button>
          <button class="mini-btn stop" data-proc-action="stop" data-proc-name="${esc(processName)}" ${processDisabled}>Stop</button>
          <button class="mini-btn restart" data-proc-action="restart" data-proc-name="${esc(processName)}" ${processDisabled}>Restart</button>
        </div>
      </div>`;
  }).join('')}
  </div>

  <div class="card command-log-card">
    <div class="api-console">
      <div class="api-console-head"><span>Last Request</span><small>${lastLog ? esc(lastLog.time) : '아직 실행한 명령 없음'}</small></div>
      <pre>${esc(logText)}</pre>
    </div>
  </div>`;
}
