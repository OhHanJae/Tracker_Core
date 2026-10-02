import { state, coreCommand } from '../global.js';
import {
  renderExternalWebFrame,
  startExternalWebFrame,
  stopExternalWebFrame,
} from '../external_web_frame.js';

function configuredWebUrl() {
  const motor = state.coreConfig?.motor;
  if (!motor?.web_host || !motor?.web_port) return '';
  const host = ['0.0.0.0', '::'].includes(String(motor.web_host))
    ? '127.0.0.1'
    : String(motor.web_host);
  const urlHost = host.includes(':') && !host.startsWith('[') ? `[${host}]` : host;
  return `http://${urlHost}:${motor.web_port}/`;
}

const frameOptions = {
  id: 'ptm',
  name: 'PTM',
  title: 'PTM Web UI',
  command: 'ptm.web_status',
  commandRequest: coreCommand,
  configuredUrl: configuredWebUrl,
  unavailableTitle: 'PTM Web UI 연결 불가',
};

export function renderPTM() {
  return renderExternalWebFrame({ ...frameOptions, url: configuredWebUrl() });
}

export function startPtmRuntime() {
  startExternalWebFrame(frameOptions);
}

export function stopPtmRuntime() {
  stopExternalWebFrame(frameOptions.id);
}
