import { state, esc, coreCommand } from '../global.js';
import {
  renderExternalWebFrame,
  startExternalWebFrame,
  stopExternalWebFrame,
} from '../external_web_frame.js';
import { renderPlcMapViewer } from './datamap.js';
import {
  renderSharedMemoryMonitor,
  startSharedMemoryMonitoring,
  stopSharedMemoryMonitoring,
} from './shared_memory.js';

const PLC_TABS = ['연결/통신', 'Data Map', '공유 메모리'];

function plcTabs(active) {
  return `<div class="tabs">${PLC_TABS.map(tab => `<button class="tab ${tab === active ? 'active' : ''}" data-subgroup="${esc(tab)}">${esc(tab)}</button>`).join('')}</div>`;
}

function configuredWebUrl() {
  const web = state.coreConfig?.xgt?.web;
  if (!web?.host || !web?.port) return '';
  const host = ['0.0.0.0', '::'].includes(String(web.host)) ? '127.0.0.1' : String(web.host);
  return `http://${host.includes(':') && !host.startsWith('[') ? `[${host}]` : host}:${web.port}/`;
}

function renderConnection() {
  return renderExternalWebFrame({
    id: 'xgt',
    name: 'XGT Share Memory',
    title: 'XGT Share Memory Web UI',
    url: configuredWebUrl(),
  });
}

export function renderPLC() {
  const active = PLC_TABS.includes(state.activeSubgroup.plc)
    ? state.activeSubgroup.plc
    : PLC_TABS[0];
  state.activeSubgroup.plc = active;
  let content = renderConnection();
  if (active === 'Data Map') content = renderPlcMapViewer();
  if (active === '공유 메모리') content = renderSharedMemoryMonitor();
  return `${plcTabs(active)}${content}`;
}

export function startPlcRuntime() {
  stopPlcRuntime();
  if (state.activeSubgroup.plc === '공유 메모리') {
    startSharedMemoryMonitoring();
    return;
  }
  if (state.activeSubgroup.plc !== '연결/통신') return;
  startExternalWebFrame({
    id: 'xgt',
    name: 'XGT',
    command: 'xgt.web_status',
    commandRequest: coreCommand,
    configuredUrl: configuredWebUrl,
    unavailableTitle: 'XGT Web UI 연결 불가',
  });
}

export function stopPlcRuntime() {
  stopExternalWebFrame('xgt');
  stopSharedMemoryMonitoring();
}
