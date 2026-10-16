import { state, coreCommand } from '../global.js';
import { renderBypassControl, bindBypassControl, startBypassStatus } from '../device_bypass.js';
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

let stopStatus = null;

export function renderPTM() {
  return /*renderBypassControl('ptm', 'motor', 'PTM', '활성화 시 모터 동작과 PTM 오류를 차단하고 레이저도 자동 바이패스합니다.')+ */ renderExternalWebFrame({ ...frameOptions, url: configuredWebUrl() });
}

export function startPtmRuntime() {
  if (stopStatus) stopStatus();
  bindBypassControl('ptm', 'motor');
  startExternalWebFrame(frameOptions);
  stopStatus = startBypassStatus('ptm');
}

export function stopPtmRuntime() {
  if (stopStatus) stopStatus();
  stopStatus = null;
  stopExternalWebFrame(frameOptions.id);
}
