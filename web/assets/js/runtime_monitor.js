import { state, esc, statusBadge } from './global.js';

const controllerLabels = {
  INIT: '초기화', STANDBY: '대기', READY: '운전 준비', HOMING: '원점 복귀 중',
  TARGET_MOVING: '목표 이동 중', TRACKING: '추적 중', STOPPED: '정지',
  FAULT: '오류', TEACH_CALIBRATION: '티칭 / 캘리브레이션',
};
const commandLabels = {
  IDLE: '명령 대기', RECEIVED: '수신', BUSY: '처리 중',
  COMPLETE: '완료', REJECTED: '거부', ERROR: '실패',
};
const writeLabels = {
  disabled: '비활성', waiting_gateway: 'Gateway 설정 조회 대기', disconnected: '공유메모리 단절',
  waiting_commit: '첫 기록 대기', pending: 'PLC 응답 대기',
  transmitting: '전송 중 · PLC 응답 정상', confirmed: '최신 기록 전송 확인', error: '쓰기 오류',
};

function age(value) {
  return value === null || value === undefined ? 'N/A' : `${Math.max(0, Number(value))} ms`;
}

function word(value) {
  return value === null || value === undefined ? 'N/A' : `${value} / 0x${Number(value).toString(16).toUpperCase().padStart(4, '0')}`;
}

function address(base, offset) {
  const match = /^D(\d+)$/i.exec(base || '');
  return match ? `D${Number(match[1]) + offset}` : `${base || 'N/A'} + ${offset} word`;
}

function details(title, rows, note = '') {
  return `<div class="card"><div class="card-head"><h3>${esc(title)}</h3><small>${esc(note)}</small></div>
    <div class="table-wrap"><table><tbody>${rows.map(([label, value]) =>
      `<tr><th>${esc(label)}</th><td>${esc(value ?? 'N/A')}</td></tr>`).join('')}</tbody></table></div></div>`;
}

export function renderRuntimeMonitor() {
  const status = state.coreStatus || {};
  const runtime = status.runtime || {};
  const plc = status.plc || {};
  const write = plc.write || {};
  const header = plc.shared_memory_header || {};
  const command = status.command || {};
  const ptm = status.ptm || {};
  const vision = status.vision || {};
  const alarms = status.alarms || {};
  const fresh = state.apiOnline;
  const loopHealthy = fresh && runtime.loop_running && runtime.last_cycle_age_ms !== null
    && runtime.last_cycle_age_ms <= Math.max(1000, (runtime.cycle_interval_ms || 50) * 20)
    && !runtime.last_cycle_error;
  const writeHealthy = fresh && ['confirmed', 'transmitting'].includes(write.state);
  const controller = controllerLabels[runtime.controller_state] || runtime.controller_state || 'N/A';
  const blockers = [];
  if (runtime.force_stop) blockers.push('FORCE_STOP');
  if (runtime.run_enable === false) blockers.push('RUN_ENABLE 꺼짐');
  if (alarms.fault_summary) blockers.push(`Fault ${alarms.primary_fault_code}`);
  if (ptm.enabled && !ptm.online) blockers.push('PTM TCP 단절');
  if (ptm.enabled && ptm.serial_connected === false) blockers.push('PTM RS-485 단절');
  if (runtime.safe_stop_pending) blockers.push('안전 정지 처리 중');
  const busyAge = command.status === 'BUSY' && command.busy_since_ms
    ? Math.max(0, (status.runtime?.sampled_ms || command.busy_since_ms) - command.busy_since_ms) : null;

  return `${!fresh ? `<div class="card monitor-notice">Core 연결 끊김 · 표시된 값은 마지막 수신 값입니다. ${esc(state.lastApiError)}</div>` : ''}
    <div class="grid status">
      ${[
        ['Core 주기', loopHealthy ? 'Online' : 'Offline', `${runtime.cycle_count ?? 0} cycles`],
        ['운전 상태', fresh ? controller : 'Offline', runtime.system_ready ? 'SYSTEM_READY' : '준비 조건 확인',
          !fresh ? 'Offline' : runtime.controller_state === 'FAULT' ? 'Error' : ['READY', 'HOMING', 'TARGET_MOVING', 'TRACKING'].includes(runtime.controller_state) ? 'Running' : 'IDLE'],
        ['현재 명령', fresh ? commandLabels[command.status] || 'N/A' : 'Offline', `SEQ ${command.last_seq ?? '-'} · CODE ${command.last_code ?? '-'}`,
          !fresh ? 'Offline' : ['ERROR', 'REJECTED'].includes(command.status) ? 'Error' : command.status === 'COMPLETE' ? 'OK' : command.status === 'BUSY' ? 'Running' : 'IDLE'],
        ['PLC 쓰기', fresh ? writeHealthy ? 'Online' : write.state === 'error' ? 'Error' : write.state === 'disabled' ? 'Disabled' : 'Waiting' : 'Offline', writeLabels[write.state] || '상태 대기',
          !fresh ? 'Offline' : writeHealthy ? 'Online' : write.state === 'error' ? 'Error' : write.state === 'disabled' ? 'Disabled' : 'IDLE'],
        ['PTM RS-485', fresh && ptm.enabled ? ptm.serial_connected === true ? 'Online' : 'Offline' : ptm.enabled === false ? 'Disabled' : 'Offline', ptm.moving ? '이동 중' : '정지'],
      ].map(([title, value, note, tone]) => `<div class="card status-card"><div class="kicker">${esc(title)}</div><strong>${statusBadge(value, tone)}</strong><small>${esc(note)}</small></div>`).join('')}
    </div>
    <div class="grid two section-gap">
      ${details('Core 처리 상태', [
        ['처리 단계', controller], ['추적 상태', runtime.tracking_state],
        ['주기 / 최근 소요 시간', `${age(runtime.cycle_interval_ms)} / ${age(runtime.last_cycle_duration_ms)}`],
        ['마지막 주기 이후', age(runtime.last_cycle_age_ms)], ['누적 처리 주기', runtime.cycle_count],
        ['운전 제한 조건', blockers.join(' · ') || (runtime.system_ready ? '없음' : '대기')],
        ['최근 주기 오류', runtime.last_cycle_error || '없음'],
      ])}
      ${details('PLC 명령 진행', [
        ['명령 / 순번', `${command.last_code ?? '-'} / ${command.last_seq ?? '-'}`],
        ['진행 상태', commandLabels[command.status] || command.status], ['처리 중 경과 시간', age(busyAge)],
        ['결과', command.result], ['상세', command.message || '없음'],
        ['적용 Recipe / Point', `${runtime.active_recipe_id ?? '-'} / ${runtime.active_point_id ?? '-'}`],
        ['현재 오류 / 경고 코드', `${alarms.primary_fault_code ?? '-'} / ${alarms.primary_warning_code ?? '-'}`],
      ])}
    </div>
    <div class="grid two section-gap">
      ${details('PLC 쓰기 확인', [
        ['쓰기 영역', write.base_address], ['전송 상태', writeLabels[write.state] || 'N/A'],
        ['Core 기록 횟수', write.commit_count], ['Core 기록 SEQ', write.committed_sequence],
        ['PLC 전송 완료 ACK', write.acknowledged_sequence], ['최신 기록 대기', write.pending ? '예' : '아니오'],
        ['최근 Core 기록 이후', age(write.last_commit_age_ms)], ['최근 ACK 진행 이후', age(write.last_ack_age_ms)],
        ['ACK 제한 시간', age(write.ack_timeout_ms)], ['최근 쓰기 오류', write.last_error || '없음'],
        ['Gateway 오류 코드', header.last_error_code],
      ], 'PLC 전송 확인은 XGT 쓰기 응답 기준')}
      ${details('PLC 입력 / 출력', [
        ['PLC 수신 하트비트', plc.heartbeat], ['하트비트 갱신 이후', age(plc.heartbeat_age_ms)],
        ['RUN_ENABLE / FORCE_STOP', `${runtime.run_enable ? 1 : 0} / ${runtime.force_stop ? 1 : 0}`],
        ['SYSTEM_READY', runtime.system_ready ? 1 : 0],
        ...[[0, '운전 플래그'], [7, '하트비트 응답'], [63, 'PTM 오류'], [71, 'PLC 통신 오류']].map(([index, label]) => [
          `${address(write.base_address, index)} · ${label}`,
          `계산 ${word(plc.output_words_preview?.[index])} · 기록 ${word(write.last_committed_words_preview?.[index])}`,
        ]),
      ], '계산값과 마지막 공유메모리 기록값 비교')}
    </div>
    <div class="section-gap">${details('Vision 연동 상태', [
      ['카메라 IP', vision.camera_ip || '미보고 · Vision Web에서 설정'],
      ['트래킹 바이패스', vision.tracking_bypass === true ? '켜짐' : vision.tracking_bypass === false ? '꺼짐' : '미보고'],
      ['바이패스 사유', vision.tracking_bypass_reason || '없음'],
      ['트래킹 / 결과 유효', `${vision.tracking_active ? '동작' : '대기'} / ${vision.tracker_valid ? '유효' : '무효'}`],
      ['위치 오차 (mm)', vision.position_error_mm],
    ], '설정 변경은 Vision Web · Core는 Vision 상태 응답을 표시')}</div>
    <div class="card section-gap"><div class="card-head"><h3>장치 상태</h3><small>상태 응답 기준 · 1.5초 갱신</small></div>
      <div class="table-wrap"><table><thead><tr><th>장치</th><th>연결</th><th>상태</th><th>응답 시간</th><th>성공 / 오류</th><th>최근 오류</th></tr></thead>
      <tbody>${Object.values(status.devices || {}).filter(device => device.id !== 'laser').map(device => `<tr><td>${esc(device.name)}</td><td>${esc(device.endpoint)}</td>
        <td>${statusBadge(device.enabled === false ? 'Disabled' : fresh && device.online ? 'Online' : 'Offline')}</td>
        <td>${esc(age(device.response_ms))}</td><td>${esc(device.ok_count)} / ${esc(device.error_count)}</td>
        <td>${esc(device.last_error || '없음')}</td></tr>`).join('')}</tbody></table></div></div>`;
}
