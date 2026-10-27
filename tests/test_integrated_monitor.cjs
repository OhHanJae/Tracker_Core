// Real browser, real Core frontend, simulated read-only Core and Vision responses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 900}});
  page.setDefaultTimeout(8000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const commands = [];
  let visionLoads = 0;
  let snapshots = 0;
  let fail = false;
  let cameraFail = false;
  let cameraFrames = 0;
  const input = Array(100).fill(0);
  Object.assign(input, {0: 5, 7: 218, 10: 3, 11: 15, 12: 2, 13: 41});
  const written = Array(80).fill(0);
  written[15] = 0;
  const data = {
    core: {running: true, status_age_ms: 100, resources: {}}, processes: {},
    runtime: {loop_running: true, controller_state: 'TRACKING', system_ready: true, run_enable: true,
      last_cycle_age_ms: 50, target_context: {schema_version: 1,
        requested: {recipe_id: 3, point_id: 15, command_seq: 41}, applied: {recipe_id: 3, point_id: 15}}},
    command: {status: 'COMPLETE', result: 'OK', last_code: 2, last_seq: 41},
    ptm: {enabled: true, online: true, serial_connected: true, pan_deg: 123.45, tilt_deg: -12.34, moving: false},
    vision: {enabled: true, online: true, tracker_valid: true, result_ready: true, point_match: true, position_error_mm: 0.42,
      result_age_seconds: 0.1, work: {target_synced: true, target: {recipe_id: 3, point_id: 15}}},
    devices: {ptm: {online: true, age_ms: 50, status: {raw: {laser: {on: false}}}}, vision: {online: true, age_ms: 50,
      status: {raw: {run_position: {simulation: false, target: {recipe_id: 3, point_id: 15}, measurement_age_ms: 30,
        error_mm: 0.42, measurement: {data_valid: true, coordinate_frame: 'PRODUCT_CAD_STEP_NATIVE',
          socket_reference_xyz_mm: [10, 20, 30], target_reference_xyz_mm: [10.1, 20.2, 30.3],
          inclination_valid: true, inclination_deg: 2.5, tightening_angle_deg: 12.3}}}}}},
    plc: {enabled: true, heartbeat: 218, heartbeat_age_ms: 100, input_words_preview: input, write: {state: 'confirmed',
      acknowledged_sequence: 512, last_committed_words_preview: written}},
    plc_memory: {connected: true, read: {base_address: 'D2000', words: input, source_age_ms: 100}, write: {base_address: 'D3000', words: written}}, laser: {on: false}, bypass: {}, alarms: {},
    alarm_history: {entries: [], recording: {running: true, retention_days: 30}},
    monitor: {schema_version: 1, state_age_ms: 50, endpoints: {vision: {http: {host: '127.0.0.1', port: 8767}}}},
  };
  const fault = {occurrence_id: 'fault-1', severity: 'Fault', device: 'pan_tilt_motor', device_name: 'PTM', code: 4001,
    message: '모터 보호 오류', description: '모터 상태와 배선을 확인하세요.', clear_policy: '원인 해제 후 수동 리셋',
    status: 'active', cause_active: true, cause_observed: true, relative_address: 'D+63', bit: 1,
    occurred_at: '2026-10-21T03:00:00+00:00'};
  const warning = {occurrence_id: 'warning-1', severity: 'Warning', device: 'plc_communication', device_name: 'PLC',
    code: 6000, message: '하트비트 지연', description: 'PLC 응답 주기를 확인하세요.', status: 'active', cause_active: true,
    occurred_at: '2026-10-21T03:01:00+00:00'};
  const old = {...fault, occurrence_id: 'old-1', status: 'recovered', cause_active: false,
    recovered_at: '2026-10-21T03:03:00+00:00'};
  const webRoot = path.resolve(__dirname, '../web');
  const fixture = '<!doctype html><html><body style="margin:0;background:#101b23;color:#afc4d1;font-family:Segoe UI,sans-serif"><header style="padding:12px;border-bottom:1px solid #30414c;font-size:13px">FVP　 RUN · 운전 화면 <span style="float:right;color:#58dbaf">위치 OK</span></header><div style="padding:18px;text-align:center"><svg viewBox="0 0 700 290" style="width:100%;max-height:320px"><path d="M110 55H570L610 100V240H80V100Z" fill="#84929c" stroke="#d1e0e8" stroke-width="3"/><path d="M150 100H530V195H150Z" fill="#273a47" stroke="#597585" stroke-width="3"/><g fill="#b7c8d2" stroke="#172934" stroke-width="6"><circle cx="120" cy="100" r="12"/><circle cx="570" cy="100" r="12"/><circle cx="120" cy="210" r="12"/><circle cx="570" cy="210" r="12"/><circle cx="240" cy="75" r="12"/><circle cx="430" cy="75" r="12"/></g><circle cx="430" cy="75" r="24" fill="none" stroke="#28d2a0" stroke-width="3"/><path d="M390 75h80M430 35v80" stroke="#28d2a0"/><text x="465" y="45" fill="#7debc9" font-size="18">P015</text></svg><p style="font-size:12px">레시피 3 / 포인트 15　·　목표 대비 오차 0.42 mm</p><small style="color:#698593">브라우저 검증용 모의 Vision 화면</small></div></body></html>';
  await page.route('http://core.test:8767/**', route => {visionLoads++; return route.fulfill({contentType: 'text/html; charset=utf-8', body: fixture});});
  await page.route('http://core.test/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const json = result => route.fulfill({json: {ok: true, result}});
    if (url.pathname === '/api/monitor/vision/frame') {
      cameraFrames++;
      if (cameraFail) return route.fulfill({status: 503, body: 'Preview unavailable'});
      return route.fulfill({contentType: 'image/svg+xml', headers: {'X-Frame-Id': String(cameraFrames)},
        body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 700 420"><rect width="700" height="420" fill="#101b23"/><path d="M80 80H580L630 130V340H60V130Z" fill="#7b919e" stroke="#c3d3dd" stroke-width="3"/><path d="M160 145H530V280H160Z" fill="#223944"/><g fill="#b5c7d2" stroke="#223944" stroke-width="5"><circle cx="110" cy="135" r="15"/><circle cx="580" cy="135" r="15"/><circle cx="110" cy="295" r="15"/><circle cx="580" cy="295" r="15"/></g><text x="350" y="395" fill="#86a3b5" font-size="14" text-anchor="middle">브라우저 검증용 모의 카메라 영상</text></svg>'});
    }
    if (url.pathname === '/api/status') return json(data);
    if (url.pathname === '/api/config') return json({processes: {}, vision: {web_enabled: true, web_host: '127.0.0.1', web_port: 8767}});
    if (url.pathname === '/api/devices') return json(data.devices);
    if (url.pathname === '/api/command') {
      const {command, params} = request.postDataJSON();
      commands.push(command);
      if (command === 'monitor.snapshot') {
        snapshots++;
        if (fail) return route.fulfill({status: 503, json: {ok: false, error: {message: 'Test connection lost'}}});
        return json(data);
      }
      if (command === 'alarms.history') {
        const all = [fault, warning, old].filter(row => (!params.severity || row.severity === params.severity)
          && (!params.search || JSON.stringify(row).includes(params.search)));
        return json({entries: params.cursor ? all.slice(1) : all.slice(0, 1), total: all.length,
          next_cursor: !params.cursor && all.length > 1 ? 'older' : null});
      }
      throw Error('Unexpected command: ' + command);
    }
    const file = path.resolve(webRoot, '.' + (url.pathname === '/' ? '/index.html' : url.pathname));
    if (!file.startsWith(webRoot + path.sep) || !fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
    const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
    return route.fulfill({contentType: type + '; charset=utf-8', body: fs.readFileSync(file)});
  });
  const waitText = (selector, text) => page.waitForFunction(({selector, text}) =>
    document.querySelector(selector)?.textContent.includes(text), {selector, text});
  const openIntegrated = () => page.locator('#sidebarNav [data-view="integrated"]').click();
  try {
    await page.goto('http://core.test/');
    await openIntegrated();
    await waitText('#imPtm', '123.45');
    await page.waitForFunction(() => document.querySelector('#imVisionImage')?.src.startsWith('blob:'));
    assert.equal(await page.locator('#imVisionFrame').count(), 0, 'only camera pixels and status are loaded');
    assert.equal(await page.locator('main.main > header').count(), 0);
    for (const id of ['saveBtn', 'applyBtn', 'fileMenuBtn', 'discoverBtn', 'connectBtn'])
      assert.equal(await page.locator('#appSidebar #' + id).count(), 1, id + ' is in the sidebar');
    const frame = await page.locator('#imVisionImage').elementHandle();
    const count = snapshots;
    await page.waitForTimeout(2200);
    assert.ok(snapshots >= count + 2, 'cached status polls continuously');
    assert.equal(visionLoads, 0, 'the complete Vision page is never requested');
    assert.equal(await frame.evaluate(element => element === document.getElementById('imVisionImage')), true);
    assert.match(await page.locator('#imPlc').innerText(), /2 · 원점 복귀/);
    assert.match(await page.locator('#imPlc').innerText(), /41 \/ 41/);
    assert.match(await page.locator('#imPlc').innerText(), /정상/);
    assert.match(await page.locator('#imPlc').innerText(), /512/);
    assert.match(await page.locator('#imFlow').innerText(), /대상 동기화됨/);
    assert.match(await page.locator('#imVisionMetrics').innerText(), /X 10.0 \/ Y 20.0 \/ Z 30.0/);
    assert.match(await page.locator('#imVisionMetrics').innerText(), /2.5°/);
    assert.match(await page.locator('#imVisionMetrics').innerText(), /12.3°/);
    data.devices.vision.status.raw.run_position.measurement.data_valid = false;
    await page.waitForFunction(() => document.querySelectorAll('#imVisionMetrics strong')[1].textContent === '—');
    data.devices.vision.status.raw.run_position.measurement.data_valid = true;
    await waitText('#imVisionMetrics', '0.42 mm');
    cameraFail = true;
    await waitText('#camera-message', 'HTTP 503');
    cameraFail = false;
    await page.waitForFunction(() => document.querySelector('#camera-message').hidden);
    data.plc.heartbeat_age_ms = 1500;
    await waitText('#imPlc', '지연');
    data.plc.heartbeat_age_ms = 3500;
    await waitText('#imPlc', '끊김');
    data.plc.heartbeat_age_ms = 100;
    data.command.status = 'BUSY';
    await waitText('#imPlc', '원점 복귀 처리 중');
    data.command.status = 'COMPLETE';
    await page.locator('#imPlcCheck').click();
    await page.waitForFunction(() => document.querySelector('#imPlcDialog').open);
    assert.equal(await page.locator('.im-plc-data-pane').count(), 2, 'wide modal displays both directions together');
    const runRow = page.getByRole('button', {name: 'RUN_ENABLE 전체 설명', exact: true});
    assert.match(await runRow.innerText(), /D2000.0/);
    assert.equal(await runRow.locator('td strong').innerText(), '1');
    const before = await page.locator('[data-direction="write"][data-plc-page="1"]').evaluate(element => element.parentElement.textContent);
    await page.locator('[data-direction="write"][data-plc-page="1"]').click();
    assert.notEqual(await page.locator('[data-direction="write"][data-plc-page="1"]').evaluate(element => element.parentElement.textContent), before);
    await runRow.click();
    await waitText('.im-plc-row-detail', 'Controller→PLC 데이터 쓰기 허용');
    await page.locator('[data-plc-back]').click();
    assert.equal(await page.locator('#imPlcModalBody').evaluate(element => element.scrollHeight > element.clientHeight + 1), false);
    if (process.env.MONITOR_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.MONITOR_SCREENSHOT_DIR, 'plc-communication.png')});
    await page.keyboard.press('Escape');
    data.runtime.run_enable = false;
    input[0] &= ~1;
    data.plc.write.state = 'paused';
    await waitText('#imPlc', 'RUN_ENABLE OFF · 쓰기 중지');
    await waitText('#imSystemState', '추적 중');
    assert.equal(await page.locator('#imAlarmBar').getAttribute('data-level'), 'clear');
    data.runtime.run_enable = true;
    input[0] |= 1;
    data.plc.write.state = 'confirmed';
    if (process.env.MONITOR_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.MONITOR_SCREENSHOT_DIR, 'integrated-monitor.png')});
    await page.setViewportSize({width: 1366, height: 768});
    const rect = await page.locator('#imAlarmBar').boundingBox();
    assert.ok(rect.y + rect.height <= 768, 'alarm bar fits on a 1366 by 768 desktop');
    for (const selector of ['.im-target-request', '.im-device-foot']) {
      assert.equal(await page.locator(selector).evaluate(element =>
        element.getBoundingClientRect().bottom <= element.closest('.im-panel').getBoundingClientRect().bottom), true,
      selector + ' remains visible on the compact desktop');
    }

    data.alarm_history.entries = [fault, warning];
    await waitText('#imAlarmBar', '모터 보호 오류');
    assert.equal(await page.locator('#imAlarmBar').getAttribute('data-level'), 'fault');
    if (process.env.MONITOR_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.MONITOR_SCREENSHOT_DIR, 'integrated-monitor-alarm.png')});
    await page.locator('#imAlarmBar').click();
    await waitText('#imAlarmRows', '모터 보호 오류');
    assert.equal(await page.locator('#sidebarNav [data-view="alarm_list"]').getAttribute('class'), 'nav-item active');
    await page.locator('#imAlarmRows summary').first().click();
    await page.waitForTimeout(1250);
    assert.equal(await page.locator('#imAlarmRows details').first().getAttribute('open'), '', 'expanded alarm details survive refresh');
    assert.match(await page.locator('#imAlarmRows').innerText(), /수동 리셋/);
    await page.locator('#imAlarmSeverity').selectOption('Warning');
    assert.equal(await page.locator('#imAlarmRows tr').count(), 1);
    assert.match(await page.locator('#imAlarmRows').innerText(), /하트비트 지연/);
    await page.locator('#imAlarmSeverity').selectOption('');
    data.alarm_history.entries = [{...fault, status: 'latched', cause_active: false, recovered_at: old.recovered_at},
      {...warning, suppressed: true, cause_observed: false}];
    await waitText('#imAlarmRows', '원인 복구 · 리셋 대기');
    assert.match(await page.locator('#imAlarmRows').innerText(), /바이패스 · 원인 미확인/);
    if (process.env.MONITOR_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.MONITOR_SCREENSHOT_DIR, 'alarm-list.png')});
    await page.locator('[data-alarm-mode="history"]').click();
    await waitText('#imAlarmCount', '1건 / 전체 3건');
    await page.locator('#imAlarmMore').click();
    await waitText('#imAlarmRows', '복구됨');
    assert.equal(await page.locator('#imAlarmRows tr').count(), 3);
    await page.locator('#imAlarmSeverity').selectOption('Warning');
    await waitText('#imAlarmCount', '1건 / 전체 1건');
    assert.match(await page.locator('#imAlarmRows').innerText(), /하트비트 지연/);
    await page.locator('#imAlarmSearch').fill('없는 값');
    await waitText('#imAlarmRows', '조회 조건에 해당하는 알람이 없습니다.');

    await page.locator('#imBackToMonitor').click();
    data.alarm_history.entries = [];
    data.runtime.target_context.applied = null;
    data.ptm.pan_deg = null;
    await page.waitForFunction(() => document.querySelector('#imTarget .im-target-ids').textContent.includes('—'));
    assert.match(await page.locator('#imTarget .im-target-ids').innerText(), /RECIPE\s+—\s+\/\s+POINT\s+—/);
    await waitText('#imPtm', '—');
    assert.doesNotMatch(await page.locator('#imFlow').innerText(), /대상 동기화됨/);
    data.runtime.loop_running = false;
    data.monitor.state_age_ms = null;
    await waitText('#imSystemState', '운전 상태 미확인');
    assert.equal(await page.locator('#integratedMonitor').getAttribute('data-stale'), 'true');
    assert.doesNotMatch(await page.locator('#imPtmConnection').innerText(), /통신 정상/);
    data.runtime.loop_running = true;
    data.monitor.state_age_ms = 6000;
    await waitText('#imAlarmBar', '상태 수신이 지연');
    data.monitor.state_age_ms = 50;
    fail = true;
    await waitText('#imAlarmBar', 'Test connection lost');
    fail = false;
    await page.waitForFunction(() => document.querySelector('#integratedMonitor').dataset.stale === 'false');
    await waitText('#imAlarmBar', '현재 보고된 오류·경고가 없습니다.');
    data.bypass.vision = {active: true};
    const framesBeforeBypass = cameraFrames;
    await waitText('#imFlow', '바이패스');
    assert.ok(cameraFrames > framesBeforeBypass, 'bypass keeps the camera preview available');
    const framesBeforeRefresh = cameraFrames;
    await page.locator('#imVisionReload').click();
    await page.waitForTimeout(250);
    assert.ok(cameraFrames > framesBeforeRefresh);
    await page.locator('#sidebarNav [data-view="dashboard"]').click();
    await page.waitForTimeout(200);
    const stopped = snapshots;
    await page.waitForTimeout(1200);
    assert.equal(snapshots, stopped, 'leaving the view stops its polling');
    await page.evaluate(() => localStorage.setItem('track-eye.sidebar-pinned', 'false'));
    await page.setViewportSize({width: 390, height: 844});
    await page.reload();
    await page.locator('#sidebarRevealBtn').click();
    await openIntegrated();
    await page.keyboard.press('Escape');
    await waitText('#imPtm', '-12.34');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'mobile has no horizontal page overflow');
    await page.locator('#imPlcCheck').click();
    await page.locator('[data-plc-mode="write"]').click();
    assert.equal(await page.locator('.im-plc-data-pane').count(), 1);
    assert.match(await page.locator('.im-plc-data-pane h3').innerText(), /Controller → PLC/);
    assert.equal(await page.locator('#imPlcModalBody').evaluate(element => element.scrollHeight > element.clientHeight + 1 || element.scrollWidth > element.clientWidth + 1), false);
    await page.keyboard.press('Escape');
    assert.deepEqual([...new Set(commands)].sort(), ['alarms.history', 'monitor.snapshot']);
    assert.deepEqual(errors, []);
    console.log('PASS: camera-only preview and 503 recovery, live XYZ/angles, heartbeat/command status, sidebar tools, paginated bidirectional PLC modal, alarm return/history, stale/recovery, cleanup and mobile');
  } catch (error) {
    console.error({errors, commands: commands.slice(-5), text: (await page.locator('#viewRoot').innerText()).slice(0, 1800)});
    throw error;
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
