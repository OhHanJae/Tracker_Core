import { state, esc, coreCommand } from '../global.js';
import { renderBypassControl, bindBypassControl, startBypassStatus } from '../device_bypass.js';
import {
  renderExternalWebFrame,
  setExternalWebFrameBypassed,
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

let stopStatus = null;
function nutrunnerResult(status) {
  const vision = status?.vision || {};
  const result = vision.nutrunner_result;
  if (!result) return '너트러너 데이터 없음 · 각도 / 기울기 / 체결좌표 / 타임스탬프 전송 기능은 Vision 앱에서 추후 구현합니다.';
  const request = `레시피 ${result.recipe_id} · 포인트 ${result.point_id}`;
  if (result.supported === false) return `${request} · Vision 앱의 너트러너 데이터 기능 미지원 · 추후 구현 예정`;
  if (result.data_valid === false) return `${request} · 유효한 측정 데이터 없음`;
  const coordinate = result.fastening_coordinate_mm;
  const value = item => item === null || item === undefined ? '미보고' : String(item);
  return `${request} · 각도: ${value(result.tightening_angle_deg)}° · 기울기: ${value(result.inclination_deg)}° · 체결좌표 (mm): X ${value(coordinate?.x)}, Y ${value(coordinate?.y)}, Z ${value(coordinate?.z)} · 타임스탬프 (ms): ${value(result.timestamp_ms)}${result.coordinate_frame ? ` · 좌표계: ${result.coordinate_frame}` : ''}`;
}

export function renderVision() {
  return /* renderBypassControl('vision', 'vision', 'Vision', '활성화 시 Vision 동작과 Vision 오류를 차단합니다.')+*/ `<div class="card" style="flex:none;margin-bottom:12px"><div class="card-body"><strong>최근 너트러너 데이터</strong><p id="visionNutrunnerResult">${esc(nutrunnerResult(state.coreStatus))}</p></div></div>`
    + renderExternalWebFrame({ ...frameOptions, url: configuredWebUrl() });
}

export function startVisionRuntime() {
  if (stopStatus) stopStatus();
  const updateFrame = status => {
    const bypass = status?.bypass?.vision;
    const active = bypass?.source === 'device' ? false : bypass?.active;
    const pendingEnable = document.getElementById('visionBypass');
    setExternalWebFrameBypassed(frameOptions,
      Boolean((pendingEnable?.disabled && pendingEnable.checked) || active || (active === undefined && state.coreConfig?.vision?.bypass)));
  };
  bindBypassControl('vision', 'vision', () => setExternalWebFrameBypassed(frameOptions, true));
  updateFrame(state.coreStatus);
  stopStatus = startBypassStatus('vision', status => {
    updateFrame(status);
    const result = document.getElementById('visionNutrunnerResult');
    if (result) result.textContent = nutrunnerResult(status);
  });
}

export function stopVisionRuntime() {
  if (stopStatus) stopStatus();
  stopStatus = null;
  stopExternalWebFrame(frameOptions.id);
}
