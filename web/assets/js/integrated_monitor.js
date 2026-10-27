import { state, esc, apiRequest, showToast } from './global.js';
import { browserReachableUrl } from './external_web_frame.js';
import { startVisionCamera } from './vision_camera.js';
import { renderPlcDialog, bindPlcDialog, openPlcDialog, updatePlcDialog } from './plc_monitor_dialog.js';

const controllerNames = {
  INIT: '초기화', STANDBY: '운전 대기', READY: '운전 준비 완료', HOMING: '원점 복귀 중',
  TARGET_MOVING: '목표 위치로 이동', TRACKING: '추적 중', STOPPED: '운전 정지',
  FAULT: '오류 확인 필요', TEACH_CALIBRATION: '티칭 / 캘리브레이션',
  TESTING: '테스트 중', MANUAL: '수동 조작 중',
};
const commandNames = { IDLE: '대기', RECEIVED: '수신', BUSY: '처리 중', COMPLETE: '완료', REJECTED: '거부', ERROR: '오류' };
const plcCommands = {0: '명령 없음', 1: '레시피·포인트 적용 및 이동', 2: '원점 복귀', 3: '알람 리셋',
  4: 'Core 재시작', 5: '너트러너 측정 데이터 요청', 6: '체결 완료 통보'};
const writeNames = {
  disabled: '비활성', waiting_gateway: 'Gateway 대기', disconnected: '공유메모리 단절',
  waiting_commit: '기록 대기', pending: '응답 대기', transmitting: '전송 중 · 응답 정상',
  confirmed: '전송 확인', error: '쓰기 오류',
};
const alarmStates = { active: '발생 중', latched: '원인 복구 · 리셋 대기', recovered: '복구됨', unknown: '원인 미확인' };
let snapshot = null;
let receivedAt = 0;
let pollError = '';
let runtime = null;
const alarmFilter = { mode: 'active', severity: '', search: '' };
const history = { entries: [], total: 0, next: null, loading: false, error: '' };

const number = (value, digits = 0, suffix = '') => typeof value === 'number' && Number.isFinite(value)
  ? `${value.toFixed(digits)}${suffix}` : '—';
const idText = value => Number.isInteger(value) && value > 0 ? String(value) : '—';
const pill = (text, tone = 'muted') => `<span class="im-pill im-${tone}">${esc(text)}</span>`;
const pair = (label, value) => `<div class="im-pair"><span>${esc(label)}</span><strong>${esc(value ?? '—')}</strong></div>`;
const stamp = value => value && !Number.isNaN(new Date(value).getTime()) ? new Date(value).toLocaleString('ko-KR', { hour12: false }) : '—';

function fresh() {
  const data = snapshot;
  if (!data || pollError || !receivedAt || !data.runtime?.loop_running) return false;
  const age = data.monitor?.state_age_ms;
  return typeof age === 'number' && Number.isFinite(age) && age >= 0
    && Math.max(age, data.core?.status_age_ms || 0) + Date.now() - receivedAt < 3000;
}

function deviceReady(id, online) {
  const device = snapshot?.devices?.[id];
  return fresh() && online === true && device?.bypassed !== true
    && (device?.age_ms === undefined || (typeof device.age_ms === 'number'
      && device.age_ms + Date.now() - receivedAt < Math.max(3000, (device.poll_interval_ms || 200) * 5)));
}

function target() {
  const data = snapshot || {};
  const context = data.runtime?.target_context;
  return context?.schema_version === 1 ? context.applied : {
    recipe_id: data.runtime?.active_recipe_id, point_id: data.runtime?.active_point_id,
  };
}

function currentAlarms() {
  const data = snapshot || {};
  const entries = [...(data.alarm_history?.entries || [])];
  const add = (key, severity, message, description = '') => entries.push({
    occurrence_id: key, severity, message, description, device: 'core_monitor', device_name: '시스템 상태',
    status: 'active', cause_active: true, clear_policy: '상태가 회복되면 자동 해제',
  });
  if (pollError) add('monitor-connection', 'Fault', 'Core 모니터링 연결 끊김', pollError);
  else if (receivedAt && !fresh()) add('monitor-stale', 'Fault', 'Core 운전 상태 확인 필요',
    data.runtime?.loop_running ? '상태 수신이 지연되었습니다. 표시된 값은 마지막 수신 값입니다.' : 'Main Core 운전 주기가 정지했거나 현재 상태를 받을 수 없습니다.');
  if (data.runtime?.last_cycle_error) add('cycle-error', 'Fault', 'Core 처리 주기 오류', data.runtime.last_cycle_error);
  if (data.runtime?.force_stop) add('force-stop', 'Fault', 'PLC 강제 정지 입력', 'FORCE_STOP이 활성화되어 있습니다.');
  if (data.runtime?.safe_stop_pending) add('safe-stop', 'Warning', '안전 정지 처리 중');
  if (data.vision?.work?.last_error) add('vision-target', 'Warning', 'Vision 대상 전달 확인 필요', data.vision.work.last_error);
  if (['ERROR', 'REJECTED'].includes(data.command?.status)) add('command-result', 'Warning',
    `최근 PLC 명령 ${commandNames[data.command.status]}`, `${data.command.result || ''} · ${data.command.message || ''}`);
  const laser = data.devices?.ptm?.status?.raw?.laser;
  if (laser?.manual_allowed === false && laser.manual_block_reason && !data.runtime?.force_stop)
    add('manual-block', 'Warning', '레이저 수동 제어 제한', laser.manual_block_reason);
  for (const [key, label] of [['memory', '메모리'], ['disk', '디스크']]) {
    const metric = data.core?.resources?.[key];
    if (metric?.warning) add('resource-' + key, 'Warning', `${label} 사용량 경고`, `${number(metric.value, 1, '%')} / 기준 ${metric.warning_threshold_percent}%`);
  }
  if (data.alarms?.fault_summary && !entries.some(entry => entry.severity === 'Fault' && !entry.suppressed))
    add('fault-summary', 'Fault', `장치 오류 코드 ${data.alarms.primary_fault_code}`, '상세 원인을 수신하지 못했습니다. 운전 모니터에서 장치 상태를 확인하세요.');
  if (data.alarms?.warning_summary && !entries.some(entry => entry.severity === 'Warning' && !entry.suppressed))
    add('warning-summary', 'Warning', `장치 경고 코드 ${data.alarms.primary_warning_code}`);
  const recording = data.alarm_history?.recording;
  if (recording?.last_error) add('alarm-recording', 'Warning', '알람 이력 기록 확인 필요',
    typeof recording.last_error === 'string' ? recording.last_error : JSON.stringify(recording.last_error));
  return entries.sort((a, b) => Number(Boolean(a.suppressed)) - Number(Boolean(b.suppressed))
    || Number(b.severity === 'Fault') - Number(a.severity === 'Fault'));
}

function alarmCounts(entries) {
  return { faults: entries.filter(entry => entry.severity === 'Fault' && !entry.suppressed).length,
    warnings: entries.filter(entry => entry.severity === 'Warning' && !entry.suppressed).length };
}

function updateHtml(id, html) {
  const element = document.getElementById(id);
  if (element && element.innerHTML !== html) element.innerHTML = html;
}

export function renderIntegratedMonitor() {
  return `<div class="integrated-monitor" id="integratedMonitor">
    <header class="im-hero"><div class="im-heading"><span class="im-eyebrow">TRACK-EYE · OPERATION CENTER</span><h2>통합 모니터링</h2></div>
      <div id="imSystemState" class="im-system-state">${pill('상태 수신 중')}</div>
      <div class="im-hero-actions"><span id="imUpdated">1초 갱신</span><button type="button" id="imFullscreen" title="전체화면">⛶ <span>전체화면</span></button></div></header>
    <div class="im-flow" id="imFlow" aria-label="현재 작업 흐름"></div>
    <div class="im-workspace">
      <section class="im-panel im-vision"><div class="im-panel-head"><div><span class="im-live-dot"></span><h3>VISION <span>CAMERA</span></h3><span id="imVisionConnection"></span></div>
        <div class="im-vision-actions"><a id="imVisionOpen" target="_blank" rel="noopener" hidden>새 창 ↗</a><button type="button" id="imVisionReload" aria-label="Vision RUN 새로고침">↻</button></div></div>
        <div class="im-vision-metrics" id="imVisionMetrics"></div>
        <div class="im-vision-viewport"><img id="imVisionImage" alt="Vision 카메라 최신 영상"><div class="camera-message" id="camera-message" role="status">카메라 최신 프레임 연결 중</div></div></section>
      <aside class="im-side"><section class="im-panel"><div class="im-panel-head"><h3>현재 작업</h3><span id="imTargetBadge"></span></div><div id="imTarget" class="im-panel-body"></div></section>
        <section class="im-panel"><div class="im-panel-head"><h3>PTM · 레이저</h3><span id="imPtmConnection"></span></div><div id="imPtm" class="im-panel-body"></div></section>
        <section class="im-panel im-plc-panel"><div class="im-panel-head"><h3>PLC 운전 / 통신</h3><button class="im-text-button" type="button" id="imPlcCheck">PLC 통신 확인 ↗</button></div><div id="imPlc" class="im-panel-body"></div></section></aside>
    </div>
    <button type="button" class="im-alarm-bar" id="imAlarmBar" data-open-alarms aria-label="현재 오류·경고 상세 알람 목록 열기"></button>
    ${renderPlcDialog()}
  </div>`;
}

function updateIntegrated() {
  if (!document.getElementById('integratedMonitor')) return;
  const data = snapshot || {};
  const run = data.runtime || {};
  const ptm = data.ptm || {};
  const vision = data.vision || {};
  const applied = target();
  const requested = run.target_context?.requested;
  const entries = currentAlarms();
  const counts = alarmCounts(entries);
  const live = fresh();
  const fault = counts.faults > 0 || run.controller_state === 'FAULT';
  const tone = !live ? 'muted' : fault ? 'bad' : counts.warnings ? 'warn' : run.system_ready ? 'good' : 'info';
  const main = pollError ? 'Core 연결 확인 필요' : !receivedAt ? '상태 수신 중' : !live ? '운전 상태 미확인' : fault ? '오류 확인 필요' : controllerNames[run.controller_state] || run.controller_state || '운전 상태 미보고';
  updateHtml('imSystemState', `${pill(main, tone)}<small>${!live ? '최근 수신값 · 제어 상태 확인 필요' : fault ? `현재 오류 ${counts.faults}건 · ${esc(controllerNames[run.controller_state] || run.controller_state || '상태 확인')}` : run.power_save?.sleeping ? '자동 절전 중' : `RUN ${run.run_enable ? 'ON' : 'OFF'} · ${run.system_ready ? 'SYSTEM READY' : '준비 조건 확인'}`}</small>`);
  updateHtml('imUpdated', live ? `<i class="im-live-dot"></i>${esc(new Date(receivedAt).toLocaleTimeString('ko-KR', {hour12: false}))} · 1초 갱신` : '상태 수신 대기');
  document.getElementById('integratedMonitor').dataset.stale = String(!live);
  const ptmOnline = deviceReady('ptm', ptm.online);
  const visionOnline = deviceReady('vision', vision.online);
  const visionTarget = vision.work?.target;
  const synced = Boolean(applied?.recipe_id && applied?.point_id && vision.work?.target_synced
    && visionTarget?.recipe_id === applied.recipe_id && visionTarget?.point_id === applied.point_id);
  const flow = [
    ['PLC 수신', requested ? `R${idText(requested.recipe_id)} / P${idText(requested.point_id)}` : '요청 대기', live && Boolean(requested)],
    ['Core 대상 적용', applied?.recipe_id > 0 && applied?.point_id > 0 ? `R${idText(applied.recipe_id)} / P${idText(applied.point_id)}` : '적용 대상 없음', live && Boolean(applied?.point_id)],
    ['PTM 위치', data.bypass?.ptm?.active ? '바이패스' : !ptmOnline ? '통신 확인' : ptm.moving ? '이동 중' : '정지', ptmOnline && !ptm.moving && !data.bypass?.ptm?.active],
    ['Vision 대상 동기', data.bypass?.vision?.active ? '바이패스' : !visionOnline ? '상태 미확인' : synced ? '대상 동기화됨' : '동기 대기', visionOnline && synced && !data.bypass?.vision?.active],
  ];
  updateHtml('imFlow', flow.map(([label, value, ok], index) => `<div class="im-flow-step ${ok ? 'is-complete' : ''}"><b>${String(index + 1).padStart(2, '0')}</b><div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>${index < 3 ? '<i aria-hidden="true">›</i>' : ''}</div>`).join(''));
  updateHtml('imTargetBadge', pill(!live ? '미확인' : applied?.point_id > 0 ? '적용 대상' : '대기', !live ? 'muted' : applied?.point_id > 0 ? 'info' : 'muted'));
  updateHtml('imTarget', `<div class="im-target-ids"><div><span>RECIPE</span><strong>${idText(applied?.recipe_id)}</strong></div><b>/</b><div><span>POINT</span><strong>${idText(applied?.point_id)}</strong></div></div>
    <div class="im-target-request">PLC 요청 <strong>R${idText(requested?.recipe_id)} / P${idText(requested?.point_id)}</strong>${requested ? `<span>SEQ ${esc(requested.command_seq)}</span>` : ''}</div>`);
  updateHtml('imPtmConnection', pill(data.bypass?.ptm?.active ? '바이패스' : ptm.enabled === false ? '비활성' : ptmOnline && ptm.serial_connected === true ? '통신 정상' : '통신 확인', ptmOnline && ptm.serial_connected === true && !data.bypass?.ptm?.active ? 'good' : 'muted'));
  const laser = data.devices?.ptm?.status?.raw?.laser;
  const laserKnown = live && ptmOnline && typeof (laser?.on ?? data.laser?.on) === 'boolean';
  updateHtml('imPtm', `<div class="im-axis-values"><div><span>PAN</span><strong>${number(ptm.pan_deg, 2)}<small>°</small></strong></div><div><span>TILT</span><strong>${number(ptm.tilt_deg, 2)}<small>°</small></strong></div></div>
    <div class="im-device-foot">${pill(!ptmOnline || ptm.serial_connected !== true ? '상태 미확인' : ptm.moving ? '이동 중' : '정지', ptm.moving && ptmOnline ? 'info' : 'muted')}
      <span>레이저 명령 ${pill(laserKnown ? (laser?.on ?? data.laser?.on) ? 'ON' : 'OFF' : '미확인', laserKnown && (laser?.on ?? data.laser?.on) ? 'warn' : 'muted')}</span></div>`);
  const plc = data.plc || {};
  const memory = data.plc_memory || {};
  const inputAge = memory.read?.source_age_ms ?? memory.read?.age_ms;
  const inputFresh = live && memory.connected && typeof inputAge === 'number' && inputAge >= 0
    && inputAge + Date.now() - receivedAt < Math.max(3000, state.coreConfig?.runtime?.plc_heartbeat_fault_ms || 3000);
  const input = memory.read?.words || plc.input_words_preview || [];
  const written = plc.write?.last_committed_words_preview || [];
  const command = data.command || {};
  const write = plc.write || {};
  const heartbeatAge = plc.heartbeat_age_ms;
  const warnMs = state.coreConfig?.runtime?.plc_heartbeat_warn_ms || 1000;
  const faultMs = state.coreConfig?.runtime?.plc_heartbeat_fault_ms || 3000;
  const heartbeatKnown = inputFresh && typeof heartbeatAge === 'number' && Number.isFinite(heartbeatAge);
  const heartbeatState = !heartbeatKnown ? '미확인' : heartbeatAge >= faultMs ? '끊김' : heartbeatAge >= warnMs ? '지연' : '정상';
  const handling = command.status === 'BUSY' ? `${plcCommands[command.last_code] || '명령'} 처리 중`
    : command.status === 'RECEIVED' ? '명령 수신 · 처리 준비' : `${commandNames[command.status] || '대기'}${command.result ? ' · ' + command.result : ''}`;
  updateHtml('imPlc', `<div class="im-plc-direction"><span>하트비트</span>${pill(heartbeatState, heartbeatState === '정상' ? 'good' : heartbeatState === '끊김' ? 'bad' : heartbeatState === '지연' ? 'warn' : 'muted')}</div>
    <div class="im-plc-values">${pair('최근 갱신 / 수신값', `${heartbeatKnown ? number(heartbeatAge, 0, ' ms') : '—'} / ${number(input[7] ?? plc.heartbeat)}`)}${pair('수신 명령', `${number(input[12])} · ${plcCommands[input[12]] || '미정의 명령'}`)}${pair('현재 처리', live ? handling : '상태 미확인')}${pair('요청 / 응답 SEQ', `${number(input[13])} / ${number(command.last_seq)}`)}${pair('RUN / 추적 / 정지', `${run.run_enable ? 'ON' : 'OFF'} / ${run.tracking_enable ? 'ON' : 'OFF'} / ${run.force_stop ? 'ON' : 'OFF'}`)}</div>
    <div class="im-plc-direction"><span>Controller → PLC</span>${pill(live ? writeNames[write.state] || '확인 대기' : '미확인', live && ['confirmed', 'transmitting'].includes(write.state) ? 'good' : write.state === 'error' ? 'bad' : 'muted')}</div>
    <div class="im-plc-values">${pair('기록 결과 / 전송 ACK', `${number(written[15])} / ${number(write.acknowledged_sequence)}`)}</div>`);
  updateHtml('imVisionConnection', pill(data.bypass?.vision?.active ? '바이패스' : vision.enabled === false ? '비활성' : visionOnline ? '연결됨' : '상태 확인', visionOnline && !data.bypass?.vision?.active ? 'good' : 'muted'));
  const runPosition = data.devices?.vision?.status?.raw?.run_position;
  const measurement = runPosition?.measurement;
  const measuredTarget = runPosition?.target;
  const measurementAge = runPosition?.measurement_age_ms;
  const measurementValid = visionOnline && !data.bypass?.vision?.active && runPosition?.simulation !== true
    && measurement?.data_valid === true && measuredTarget?.expired !== true
    && applied?.recipe_id > 0 && applied?.point_id > 0
    && measuredTarget?.recipe_id === applied.recipe_id && measuredTarget?.point_id === applied.point_id
    && (!Number.isInteger(visionTarget?.command_seq) || measuredTarget?.command_seq === visionTarget.command_seq)
    && (typeof measurementAge !== 'number' || measurementAge + Date.now() - receivedAt < (state.coreConfig?.runtime?.vision_status_max_age_ms || 1500));
  const xyz = values => measurementValid && Array.isArray(values) && values.length === 3 && values.every(value => typeof value === 'number' && Number.isFinite(value))
    ? values.map((value, index) => `${['X', 'Y', 'Z'][index]} ${value.toFixed(1)}`).join(' / ') : '—';
  const metric = (label, value, wide = false) => `<div${wide ? ' class="im-metric-wide"' : ''}><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
  updateHtml('imVisionMetrics', [
    metric('공구 추적', !visionOnline ? '미확인' : measurementValid ? '측정 중' : '추적 대기'),
    metric('목표 대비 오차', measurementValid ? number(runPosition.error_mm, 2, ' mm') : '—'),
    metric('지정 축 대비 기울기', measurementValid && measurement.inclination_valid !== false ? number(measurement.inclination_deg, 1, '°') : '—'),
    metric('회전 각도', measurementValid ? number(measurement.tightening_angle_deg, 1, '°') : '—'),
    metric(`공구 XYZ · mm (${measurement?.coordinate_frame === 'PRODUCT_CAD_STEP_NATIVE' ? '제품 CAD 기준' : measurement?.coordinate_frame === 'GROUP_REFERENCE_PLANE' ? '체결면 기준' : '좌표계 미보고'})`, xyz(measurement?.socket_reference_xyz_mm), true),
    metric('지정점 XYZ · mm (공구와 동일 기준)', xyz(measurement?.target_reference_xyz_mm), true),
  ].join(''));
  updatePlcDialog(data, live);
  const messages = entries.map(entry => `${entry.suppressed ? '[바이패스 · 원인 미확인]' : entry.severity === 'Fault' ? '[오류]' : '[경고]'} ${entry.device_name || entry.device || ''} · ${entry.message}${entry.description ? ' — ' + entry.description : ''}`);
  const bar = document.getElementById('imAlarmBar');
  bar.dataset.level = counts.faults ? 'fault' : counts.warnings ? 'warning' : messages.length ? 'muted' : 'clear';
  const message = messages.join('     /     ');
  const signature = JSON.stringify([counts, message, Boolean(receivedAt)]);
  if (bar.dataset.signature !== signature) {
    bar.dataset.signature = signature;
    bar.innerHTML = `<span class="im-alarm-label"><b>${counts.faults || counts.warnings ? '!' : '✓'}</b><span>오류 ${counts.faults}<small>경고 ${counts.warnings}</small></span></span>
      <span class="im-ticker-window">${messages.length ? `<span class="im-ticker-track"><span>${esc(message)}</span><span aria-hidden="true">${esc(message)}</span></span>` : `<span class="im-ticker-clear">${receivedAt ? '현재 보고된 오류·경고가 없습니다.' : '현재 알람을 확인하고 있습니다.'}</span>`}</span><span class="im-alarm-link">알람 목록 <b>↗</b></span>`;
    const track = bar.querySelector('.im-ticker-track');
    if (track) track.style.animationDuration = `${Math.max(24, track.scrollWidth / 100)}s`;
  }
}

function visionRunUrl() {
  const config = state.coreConfig?.vision;
  const endpoint = snapshot?.monitor?.endpoints?.vision?.http;
  const host = config?.web_host || endpoint?.host;
  const port = config?.web_port || endpoint?.port;
  if (config?.web_enabled === false || !host || !port) return '';
  const hostname = ['0.0.0.0', '::'].includes(host) ? window.location.hostname : host;
  try {
    const url = new URL(browserReachableUrl(`http://${hostname.includes(':') && !hostname.startsWith('[') ? `[${hostname}]` : hostname}:${port}/`));
    url.hash = 'run';
    return url.href;
  } catch { return ''; }
}

function updateCameraLinks() {
  const link = document.getElementById('imVisionOpen');
  if (!link) return;
  const url = visionRunUrl();
  link.hidden = !url;
  link.href = url || '#';
}

export function renderAlarmList() {
  return `<section id="imAlarmList" class="im-alarm-list">
    <div class="im-alarm-list-heading"><div><span class="im-eyebrow">ALARM CENTER</span><h2>오류·경고 상세</h2><p>발생 원인, 복구 상태와 리셋 대기 여부를 확인합니다.</p></div><button class="primary" id="imBackToMonitor" type="button" data-monitor-view="integrated">← 통합 모니터링으로</button></div>
    <div id="imAlarmSummary" class="im-alarm-summary"></div>
    <div class="im-alarm-toolbar"><div class="tabs" role="group" aria-label="알람 조회 범위"><button type="button" class="tab ${alarmFilter.mode === 'active' ? 'active' : ''}" data-alarm-mode="active">현재 알람</button><button type="button" class="tab ${alarmFilter.mode === 'history' ? 'active' : ''}" data-alarm-mode="history">발생·복구 이력</button></div>
      <label>레벨<select id="imAlarmSeverity"><option value="">전체</option><option value="Fault" ${alarmFilter.severity === 'Fault' ? 'selected' : ''}>오류</option><option value="Warning" ${alarmFilter.severity === 'Warning' ? 'selected' : ''}>경고</option></select></label>
      <label class="im-alarm-search">검색<input id="imAlarmSearch" value="${esc(alarmFilter.search)}" placeholder="장치, 코드, 메시지 검색" type="search"></label><button type="button" class="secondary" id="imAlarmRefresh">새로고침</button></div>
    <p id="imAlarmNotice" class="im-alarm-notice" role="status"></p>
    <div class="im-panel im-alarm-table-wrap"><table class="im-alarm-table"><thead><tr><th>레벨 / 장치</th><th>알람 내용 · 펼치면 상세</th><th>상태</th><th>발생 시간</th></tr></thead><tbody id="imAlarmRows"></tbody></table></div>
    <div class="im-alarm-list-foot"><span id="imAlarmCount"></span><button type="button" class="secondary" id="imAlarmMore" hidden>이전 이력 더 보기</button><button class="im-text-button" type="button" data-monitor-view="alarm">장애 판정 설정 ↗</button></div>
  </section>`;
}

function updateAlarmList() {
  if (!document.getElementById('imAlarmList')) return;
  const entries = currentAlarms();
  const counts = alarmCounts(entries);
  updateHtml('imAlarmSummary', `<div><span>현재 오류</span><strong class="im-text-bad">${counts.faults}</strong></div><div><span>현재 경고</span><strong class="im-text-warn">${counts.warnings}</strong></div><div><span>상태 수신</span><strong>${fresh() ? '실시간' : '미확인'}</strong></div><div><span>이력 보관</span><strong>${snapshot?.alarm_history?.recording?.retention_days ?? 30}<small>일</small></strong></div>`);
  const active = alarmFilter.mode === 'active';
  let rows = active ? entries : history.entries;
  const query = alarmFilter.search.trim().toLowerCase();
  if (active) rows = rows.filter(row => (!alarmFilter.severity || row.severity === alarmFilter.severity)
    && (!query || [row.device, row.device_name, row.code, row.message, row.description].join(' ').toLowerCase().includes(query)));
  const openDetails = new Set([...document.querySelectorAll('#imAlarmRows details[open]')].map(detail => detail.closest('tr').dataset.alarmKey));
  updateHtml('imAlarmRows', rows.length ? rows.map(row => `<tr data-alarm-key="${esc(row.occurrence_id || '')}">
    <td>${pill(row.severity === 'Fault' ? '오류' : '경고', row.suppressed ? 'muted' : row.severity === 'Fault' ? 'bad' : 'warn')}<strong>${esc(row.device_name || row.device || 'Core')}</strong><small>CODE ${esc(row.code ?? '—')}</small></td>
    <td><details${openDetails.has(row.occurrence_id) ? ' open=""' : ''}><summary>${esc(row.message || '상세 알람')}</summary><div class="im-alarm-detail"><p>${esc(row.description || '추가 설명이 없습니다.')}</p><dl>
      <dt>원인 상태</dt><dd>${row.suppressed || row.cause_observed === false ? '관측 중단 · 현재 원인 미확인' : row.cause_active === true ? '원인 발생 중' : row.cause_active === false ? '원인 복구됨' : '확인 불가'}</dd>
      <dt>해제 기준</dt><dd>${esc(row.clear_policy || '—')}</dd><dt>PLC 주소</dt><dd>${esc(row.relative_address || '—')}${row.bit === undefined ? '' : ` · bit ${esc(row.bit)}`}</dd>
      <dt>복구 시간</dt><dd>${esc(stamp(row.recovered_at))}</dd><dt>마지막 리셋</dt><dd>${esc(stamp(row.reset_result?.timestamp))}</dd></dl></div></details></td>
    <td>${esc(row.suppressed ? '바이패스 · 원인 미확인' : alarmStates[row.status] || row.status || '미확인')}</td><td>${esc(stamp(row.occurred_at))}</td></tr>`).join('')
    : `<tr><td colspan="4" class="im-empty-row">${!fresh() && active ? '현재 상태를 확인할 수 없습니다. 연결 상태와 마지막 수신 알람을 확인하세요.' : '조회 조건에 해당하는 알람이 없습니다.'}</td></tr>`);
  updateHtml('imAlarmNotice', esc(active ? !fresh() ? pollError || 'Core 운전 상태 미확인 · 마지막 수신 알람을 표시합니다.' : '실시간 갱신 · 항목을 펼치면 원인과 해제 기준을 볼 수 있습니다.'
    : history.loading ? '이력을 불러오는 중…' : history.error || '최근 발생 순서 · 이력의 복구 상태는 조회 시점 기준입니다.'));
  updateHtml('imAlarmCount', `${rows.length}건${active ? '' : ` / 전체 ${history.total}건`}`);
  const more = document.getElementById('imAlarmMore');
  more.hidden = active || !history.next;
  more.disabled = history.loading;
}

async function commandRequest(command, params, signal) {
  return apiRequest('/api/command', { method: 'POST', body: JSON.stringify({command, params}), signal });
}

async function loadHistory(run, append = false) {
  run.historyRequest?.abort();
  const request = new AbortController();
  run.historyRequest = request;
  if (!append) { history.entries = []; history.total = 0; history.next = null; }
  history.loading = true;
  history.error = '';
  updateAlarmList();
  try {
    const result = await commandRequest('alarms.history', {limit: 100, severity: alarmFilter.severity,
      search: alarmFilter.search.trim(), ...(append && history.next ? {cursor: history.next} : {})}, request.signal);
    if (runtime !== run || run.historyRequest !== request) return;
    history.entries = append ? [...history.entries, ...(result.entries || [])] : result.entries || [];
    history.total = result.total ?? history.entries.length;
    history.next = result.next_cursor;
  } catch (error) {
    if (runtime === run && run.historyRequest === request && error.name !== 'AbortError') history.error = `알람 이력 조회 실패: ${error.message}`;
  } finally {
    if (runtime === run && run.historyRequest === request) {
      history.loading = false;
      updateAlarmList();
    }
  }
}

async function poll(run) {
  if (runtime !== run || run.pending) return;
  clearTimeout(run.timer);
  if (document.hidden) { run.timer = setTimeout(() => poll(run), 1000); return; }
  run.pending = true;
  const request = new AbortController();
  run.request = request;
  const timeout = setTimeout(() => request.abort(), 4000);
  try {
    const result = await commandRequest('monitor.snapshot', {}, request.signal);
    if (runtime !== run) return;
    if (result?.monitor?.schema_version !== 1) throw new Error('현재 Core가 통합 모니터 상태를 지원하지 않습니다. Core 서버 버전을 확인하세요.');
    snapshot = result;
    receivedAt = Date.now();
    pollError = '';
  } catch (error) {
    if (runtime !== run) return;
    pollError = error.name === 'AbortError' ? 'Core 상태 응답 시간이 초과되었습니다.' : error.message;
  } finally {
    clearTimeout(timeout);
    run.pending = false;
    if (runtime === run) {
      updateIntegrated();
      updateAlarmList();
      updateCameraLinks();
      run.timer = setTimeout(() => poll(run), 1000);
    }
  }
}

export function startIntegratedRuntime(view, navigate) {
  stopIntegratedRuntime();
  const root = document.getElementById(view === 'integrated' ? 'integratedMonitor' : 'imAlarmList');
  if (!root) return;
  const run = { events: new AbortController(), pending: false };
  runtime = run;
  if (view === 'integrated') { bindPlcDialog(run.events.signal); run.cameraStop = startVisionCamera(); }
  root.addEventListener('click', async event => {
    const destination = event.target.closest('[data-monitor-view]');
    if (destination) { navigate(destination.dataset.monitorView); return; }
    if (event.target.closest('[data-open-alarms]')) {
      Object.assign(alarmFilter, {mode: 'active', severity: '', search: ''});
      navigate('alarm_list');
      return;
    }
    const mode = event.target.closest('[data-alarm-mode]');
    if (mode) {
      alarmFilter.mode = mode.dataset.alarmMode;
      root.querySelectorAll('[data-alarm-mode]').forEach(button => button.classList.toggle('active', button === mode));
      if (alarmFilter.mode === 'history') loadHistory(run);
      else { run.historyRequest?.abort(); updateAlarmList(); }
    }
    if (event.target.closest('#imVisionReload')) { run.cameraStop?.(); run.cameraStop = startVisionCamera(); }
    if (event.target.closest('#imPlcCheck')) openPlcDialog();
    if (event.target.closest('#imAlarmRefresh')) { poll(run); if (alarmFilter.mode === 'history') loadHistory(run); }
    if (event.target.closest('#imAlarmMore')) loadHistory(run, true);
    if (event.target.closest('#imFullscreen')) {
      try { if (document.fullscreenElement) await document.exitFullscreen(); else await root.requestFullscreen(); }
      catch (error) { showToast(`전체화면 전환 실패: ${error.message}`); }
    }
  }, {signal: run.events.signal});
  root.addEventListener('change', event => {
    if (event.target.id !== 'imAlarmSeverity') return;
    alarmFilter.severity = event.target.value;
    if (alarmFilter.mode === 'history') loadHistory(run); else updateAlarmList();
  }, {signal: run.events.signal});
  root.addEventListener('input', event => {
    if (event.target.id !== 'imAlarmSearch') return;
    alarmFilter.search = event.target.value;
    clearTimeout(run.searchTimer);
    if (alarmFilter.mode === 'history') run.searchTimer = setTimeout(() => loadHistory(run), 300);
    else updateAlarmList();
  }, {signal: run.events.signal});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(run); }, {signal: run.events.signal});
  updateIntegrated();
  updateAlarmList();
  updateCameraLinks();
  poll(run);
  if (view === 'alarm_list' && alarmFilter.mode === 'history') loadHistory(run);
}

export function stopIntegratedRuntime() {
  const run = runtime;
  runtime = null;
  if (!run) return;
  clearTimeout(run.timer);
  run.cameraStop?.();
  clearTimeout(run.searchTimer);
  run.events.abort();
  run.request?.abort();
  run.historyRequest?.abort();
}
