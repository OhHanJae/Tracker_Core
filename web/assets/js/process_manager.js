import {
  state,
  esc,
  statusBadge,
  moduleProcessName,
  processForModule,
  isManagedModule,
} from './global.js';

export function renderProcess() {
  const modules = state.modules.filter(isManagedModule);
  return `<div class="module-grid watchdog-grid">
    ${modules.map(module => {
    const processName = moduleProcessName(module);
    const proc = processForModule(module);
    const watchdogState = module.autoRestart ? (proc?.watchdog?.state || 'idle') : 'disabled';
    return `<div class="card module-card watchdog-card">
      <div class="module-head">
        <div><strong>${esc(module.name || processName)}</strong><small>${esc(processName || '-')}</small></div>
        ${statusBadge(watchdogState)}
      </div>
      <div class="module-body">
        <label><span>Watchdog Auto Restart</span><span class="switch"><input type="checkbox" data-module-id="${esc(module.id)}" data-module-field="autoRestart" ${module.autoRestart ? 'checked' : ''}><span class="slider"></span></span></label>
        <label><span>Process Status</span><input value="${esc(proc?.status || (proc?.running ? 'Running' : 'Stopped'))}" readonly></label>
        <label><span>Consecutive Failures</span><input value="${esc(proc?.watchdog?.consecutive_failures ?? 0)}" readonly></label>
      </div>
      <div class="module-actions">
        <button class="mini-btn primary-lite" data-module-action="apply" data-module-id="${esc(module.id)}">Watchdog 저장</button>
      </div>
    </div>`;
  }).join('')}
  </div>`;
}
