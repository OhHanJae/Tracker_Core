import {
  state,
  esc,
  statusBadge,
  processForModule,
  endpointForProcess,
  statusTextForProcess,
} from './global.js';

function formatBytes(value) {
  if (!Number.isFinite(Number(value))) return 'N/A';
  const gb = Number(value) / (1024 ** 3);
  return `${gb.toFixed(gb >= 10 ? 1 : 2)} GB`;
}

function formatUptime(seconds) {
  if (!Number.isFinite(Number(seconds))) return 'N/A';
  const total = Math.max(0, Math.floor(Number(seconds)));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `${days}d ${hours}h ${minutes}m`;
}

function resourceCard(name, metric, detail = '') {
  const failed = !metric || metric.available === false;
  const hasValue = !failed && metric.value !== null && metric.value !== undefined;
  const value = hasValue ? metric.value : failed ? 'Error' : 'N/A';
  const percent = hasValue && metric.unit === '%' ? Math.max(0, Math.min(100, Number(metric.value))) : 0;
  const suffix = hasValue && metric.unit === '%' ? '%' : '';
  const meta = failed ? metric?.error || 'Value unavailable' : detail || metric.message || '';
  return `<div class="resource ${metric?.warning ? 'resource-warning' : ''}">
    <div class="resource-head"><span>${esc(name)}</span><strong>${esc(value)}${suffix}</strong></div>
    <div class="progress"><i style="width:${percent}%"></i></div>
    <div class="resource-meta"><span>${esc(meta)}</span><span>${failed ? 'Error' : metric?.warning ? `Warning ≥ ${esc(metric.warning_threshold_percent)}%` : hasValue ? 'Measured' : 'N/A'}</span></div>
  </div>`;
}

function processHealthText(proc) {
  if (!proc) return 'UNKNOWN';
  if (proc.enabled === false) return 'Disabled';
  return proc.health?.online ? 'ALIVE' : 'DEAD';
}

export function renderDashboard() {
  const tcp = state.coreStatus?.core?.tcp;
  const coreEndpoint = tcp ? `${tcp.host}:${tcp.port}` : 'N/A';
  const managedModules = state.modules.filter(module => !['config', 'main'].includes(module.id));
  const topStatuses = [
    ['Controller', state.apiOnline && state.coreStatus?.core?.running !== false ? 'Online' : 'Offline', coreEndpoint],
    ...managedModules.map(module => {
      const proc = processForModule(module);
      return [module.name, statusTextForProcess(proc), endpointForProcess(proc, module)];
    }),
    ['Main Process', state.apiOnline && state.coreStatus?.core?.running !== false ? 'Online' : 'Offline', 'Core loop'],
    ['Config Server', state.apiOnline ? 'Online' : 'Offline', coreEndpoint],
  ];
  const resources = state.coreStatus?.core?.resources || {};

  return `<div class="grid status">
      ${topStatuses.map(([name, status, meta]) => `<div class="card status-card"><div class="kicker">${esc(name)}</div><strong>${statusBadge(status)}</strong><small>${esc(meta)}</small></div>`).join('')}
    </div>
    <div class="grid two section-gap">
      <div class="card">
        <div class="card-head"><h3>Process Health</h3><small>OS process identity · 1s refresh</small></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Order</th><th>Process</th><th>Status</th><th>Detail</th><th>Watchdog</th></tr></thead>
          <tbody>${managedModules.map((module, index) => {
            const proc = processForModule(module);
            return `<tr><td>${index + 1}</td><td><strong>${esc(module.name)}</strong><br><small>${esc(module.process || '-')}</small></td><td>${statusBadge(processHealthText(proc))}</td><td>${esc(proc?.health?.message || 'N/A')}</td><td>${esc(proc?.watchdog?.state || (module.autoRestart ? 'idle' : 'disabled'))}</td></tr>`;
          }).join('')}</tbody>
        </table></div>
      </div>
      <div class="card">
        <div class="card-head"><h3>Controller Resources</h3><small>${esc(resources.platform?.value || resources.platform?.error || 'N/A')}</small></div>
        <div class="card-body"><div class="resource-row">
          ${resourceCard('CPU', resources.cpu, resources.cpu?.logical_cores ? `${resources.cpu.logical_cores} logical cores` : '')}
          ${resourceCard('Memory', resources.memory, resources.memory?.available ? `${formatBytes(resources.memory.used_bytes)} / ${formatBytes(resources.memory.total_bytes)}` : '')}
          ${resourceCard('Disk', resources.disk, resources.disk?.available ? `${formatBytes(resources.disk.used_bytes)} / ${formatBytes(resources.disk.total_bytes)} · ${resources.disk.path || ''}` : '')}
          ${resourceCard('Uptime', resources.uptime, resources.uptime?.available ? formatUptime(resources.uptime.value) : '')}
          ${resourceCard('Platform', resources.platform, resources.platform?.available ? String(resources.platform.value || '') : '')}
        </div></div>
      </div>
    </div>
    <div class="grid two section-gap">
      <div class="card">
        <div class="card-head"><h3>TCP Communication</h3><small>Core command channel · Application Ping/Pong</small></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Service</th><th>Endpoint</th><th>Latency</th><th>Status</th><th>Failures</th></tr></thead>
          <tbody>${managedModules.map(module => {
            const proc = processForModule(module);
            const communication = proc?.communication;
            const latency = communication?.latency_ms;
            return `<tr><td>${esc(module.name)}</td><td>${esc(endpointForProcess(proc, module))}</td><td>${latency === null || latency === undefined ? 'N/A' : `${esc(latency)} ms`}</td><td>${statusBadge(statusTextForProcess(proc))}</td><td>${esc(communication?.consecutive_failures ?? 0)}</td></tr>`;
          }).join('')}</tbody>
        </table></div>
      </div>
      <div class="card">
        <div class="card-head"><h3>HTTP Communication</h3><small>Setup / Calibration Web UI</small></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Service</th><th>Endpoint</th><th>Status</th><th>Detail</th></tr></thead>
          <tbody>${[
            ['PLC Web', state.httpCommunication?.plc],
            ['PTM Web', state.httpCommunication?.ptm],
          ].map(([name, item]) => `<tr><td>${esc(name)}</td><td>${esc(item?.url || 'N/A')}</td><td>${statusBadge(item?.online ? 'Online' : 'Offline')}</td><td>${esc(item?.error || (item?.online ? 'Reachable' : 'N/A'))}</td></tr>`).join('')}</tbody>
        </table></div>
      </div>
    </div>`;
}
