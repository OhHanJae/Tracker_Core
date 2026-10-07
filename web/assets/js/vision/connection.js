import { state, coreCommand } from '../global.js';
import {
  renderExternalWebFrame,
  startExternalWebFrame,
  stopExternalWebFrame,
} from '../external_web_frame.js';

function configuredWebUrl() {
  const vision = state.coreConfig?.vision;
  if (!vision?.web_enabled || !vision?.web_host || !vision?.web_port) return '';
  const host = ['0.0.0.0', '::'].includes(String(vision.web_host))
    ? '127.0.0.1' : String(vision.web_host);
  const urlHost = host.includes(':') && !host.startsWith('[') ? `[${host}]` : host;
  return `http://${urlHost}:${vision.web_port}/`;
}

const frameOptions = {
  id: 'vision',
  name: 'Vision',
  title: 'Vision Web UI',
  command: 'vision.web_status',
  commandRequest: coreCommand,
  configuredUrl: configuredWebUrl,
  unavailableTitle: 'Vision Web UI 미연결',
};

export function renderVision() {
  return renderExternalWebFrame({ ...frameOptions, url: configuredWebUrl() });
}

export function startVisionRuntime() {
  startExternalWebFrame(frameOptions);
}

export function stopVisionRuntime() {
  stopExternalWebFrame(frameOptions.id);
}
