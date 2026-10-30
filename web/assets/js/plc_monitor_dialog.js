import { PLC_MAP, esc, getPlcBase } from './global.js';

let data = {};
let live = false;
let mode = 'read';
let pageSize = 10;
let pages = {read: 0, write: 0};
let reserved = false;
let selected = null;
let detailPage = 0;

export function renderPlcDialog() {
  return `<dialog id="imPlcDialog" class="im-plc-dialog" aria-labelledby="imPlcDialogTitle">
    <div class="im-plc-modal-head"><div><span class="im-eyebrow">PLC COMMUNICATION</span><h2 id="imPlcDialogTitle">PLC 통신 확인</h2><small id="imPlcDialogState"></small></div><button type="button" class="secondary" data-plc-close aria-label="PLC 통신 확인 닫기">닫기 ×</button></div>
    <div class="im-plc-modal-toolbar"><div class="tabs" role="group" aria-label="통신 방향"><button type="button" class="tab" data-plc-mode="both">양방향</button><button type="button" class="tab" data-plc-mode="read">PLC → Controller</button><button type="button" class="tab" data-plc-mode="write">Controller → PLC</button></div><label><input type="checkbox" id="imPlcReserved">예약 항목 포함</label></div>
    <div id="imPlcModalBody" class="im-plc-modal-body"></div><div class="im-plc-modal-note">현재 공유메모리 수신·기록값 · 값은 자동 갱신 · 항목을 선택하면 전체 설명 표시</div>
  </dialog>`;
}

function metadata(direction) {
  const sheet = PLC_MAP.sheets.find(item => item.base === direction && item.key.startsWith(direction === 'read' ? '01_' : '02_'));
  const rows = (sheet?.rows || []).map(row => ({relative: row[1], bit: row[2], item: row[3], description: [row[4], row[5]].filter(Boolean).join('\n')}));
  if (direction === 'write') {
    const alarms = PLC_MAP.sheets.find(item => item.key === '03_Device_Alarm_Detail');
    rows.push(...(alarms?.rows || []).map(row => ({relative: row[2], bit: row[3], item: row[4], description: [row[8], '해제: ' + row[6]].join('\n')})));
  }
  return rows.flatMap(row => {
    const match = /^D\+(\d+)(?:~D\+(\d+))?$/.exec(row.relative || '');
    if (!match) return [];
    const start = Number(match[1]);
    const end = match[2] ? Number(match[2]) : start;
    return Array.from({length: Math.max(0, Math.min(100, end - start + 1))}, (_, index) => ({...row, offset: start + index, direction}));
  });
}
const maps = {read: metadata('read'), write: metadata('write')};

function rowsFor(direction) {
  return reserved ? maps[direction] : maps[direction].filter(row => !/reserved|예약/i.test(row.item));
}

function rowValue(row) {
  const memory = data.plc_memory?.[row.direction];
  const words = memory?.words || (row.direction === 'read' ? data.plc?.input_words_preview : data.plc?.write?.last_committed_words_preview) || [];
  const word = words[row.offset];
  const base = memory?.base_address || `D${getPlcBase(row.direction)}`;
  const match = /^D(\d+)$/i.exec(base);
  let address = match ? `D${Number(match[1]) + row.offset}` : `${base}+${row.offset}`;
  const raw = Number.isInteger(word) ? word & 0xffff : null;
  let value = raw;
  const bits = /^([0-9a-f])(?:~([0-9a-f]))?$/i.exec(String(row.bit));
  if (bits) {
    const first = parseInt(bits[1], 16);
    const last = bits[2] ? parseInt(bits[2], 16) : first;
    if (raw !== null) value = (raw >>> first) & (0xffff >>> (15 - last + first));
    address += '.' + row.bit;
  }
  return {address, value: value === null ? '—' : String(value), hex: raw === null ? '' : '0x' + raw.toString(16).toUpperCase().padStart(4, '0')};
}

function pane(direction) {
  const rows = rowsFor(direction);
  const count = Math.max(1, Math.ceil(rows.length / pageSize));
  pages[direction] = Math.max(0, Math.min(pages[direction], count - 1));
  const start = pages[direction] * pageSize;
  return `<section class="im-plc-data-pane"><h3>${direction === 'read' ? 'PLC → Controller' : 'Controller → PLC'}<small>${rows.length} 항목</small></h3>
    <table class="im-plc-live-table"><colgroup><col class="im-col-address"><col class="im-col-item"><col class="im-col-value"><col></colgroup><thead><tr><th>주소</th><th>항목</th><th>값</th><th>설명</th></tr></thead><tbody>${rows.slice(start, start + pageSize).map((row, index) => {
      const value = rowValue(row);
      return `<tr tabindex="0" role="button" data-plc-direction="${direction}" data-plc-index="${start + index}" aria-label="${esc(row.item)} 전체 설명"><td>${esc(value.address)}</td><td title="${esc(row.item)}"><span>${esc(row.item)}</span></td><td><strong>${esc(value.value)}</strong><small>${esc(value.hex)}</small></td><td><span>${esc(row.description)}</span></td></tr>`;
    }).join('') || '<tr><td colspan="4">표시할 항목이 없습니다.</td></tr>'}</tbody></table>
    <div class="im-plc-pagination"><button type="button" class="secondary" data-plc-page="-1" data-direction="${direction}" ${pages[direction] === 0 ? 'disabled' : ''} aria-label="${direction} 이전 페이지">‹</button><span>${pages[direction] + 1} / ${count} <small>· ${start + 1}–${Math.min(rows.length, start + pageSize)}</small></span><button type="button" class="secondary" data-plc-page="1" data-direction="${direction}" ${pages[direction] + 1 >= count ? 'disabled' : ''} aria-label="${direction} 다음 페이지">›</button></div></section>`;
}

function render() {
  const dialog = document.getElementById('imPlcDialog');
  if (!dialog?.open) return;
  const body = document.getElementById('imPlcModalBody');
  const available = body.clientHeight;
  pageSize = Math.max(3, Math.floor((available - 98) / 42));
  if (mode === 'both' && body.clientWidth < 1200) mode = 'read';
  document.getElementById('imPlcDialogState').textContent = live ? '실시간 캐시 · 1초 갱신' : '상태 미확인 · 마지막 수신값';
  dialog.dataset.stale = String(!live);
  dialog.querySelectorAll('[data-plc-mode]').forEach(button => {
    button.classList.toggle('active', button.dataset.plcMode === mode);
    button.disabled = button.dataset.plcMode === 'both' && body.clientWidth < 1200;
  });
  if (selected) {
    const value = rowValue(selected);
    const capacity = Math.max(250, Math.floor((available - 160) / 22) * Math.max(20, Math.floor(body.clientWidth / 14)));
    const chunks = Array.from(selected.description || '추가 설명 없음');
    const count = Math.max(1, Math.ceil(chunks.length / capacity));
    detailPage = Math.max(0, Math.min(detailPage, count - 1));
    body.dataset.mode = 'detail';
    body.innerHTML = `<section class="im-plc-row-detail"><div><button type="button" class="secondary" data-plc-back>← 목록으로</button><strong>${esc(value.address)} · ${esc(selected.item)}</strong><b>${esc(value.value)} <small>${esc(value.hex)}</small></b></div><p>${esc(chunks.slice(detailPage * capacity, (detailPage + 1) * capacity).join(''))}</p><div class="im-plc-pagination"><button type="button" class="secondary" data-plc-detail-page="-1" ${detailPage === 0 ? 'disabled' : ''}>‹</button><span>설명 ${detailPage + 1} / ${count}</span><button type="button" class="secondary" data-plc-detail-page="1" ${detailPage + 1 >= count ? 'disabled' : ''}>›</button></div></section>`;
  } else {
    body.dataset.mode = mode;
    const html = mode === 'both' ? pane('read') + pane('write') : pane(mode);
    if (body.innerHTML !== html) body.innerHTML = html;
  }
}

export function updatePlcDialog(snapshot, isLive) {
  data = snapshot || {};
  live = isLive;
  render();
}

export function bindPlcDialog(signal) {
  const dialog = document.getElementById('imPlcDialog');
  if (!dialog) return;
  const observer = new ResizeObserver(render);
  observer.observe(document.getElementById('imPlcModalBody'));
  signal.addEventListener('abort', () => { observer.disconnect(); if (dialog.open) dialog.close(); }, {once: true});
  dialog.addEventListener('click', event => {
    if (event.target.closest('[data-plc-close]')) { dialog.close(); return; }
    const tab = event.target.closest('[data-plc-mode]');
    if (tab) { mode = tab.dataset.plcMode; selected = null; }
    const page = event.target.closest('[data-plc-page]');
    if (page) pages[page.dataset.direction] += Number(page.dataset.plcPage);
    const row = event.target.closest('[data-plc-index]');
    if (row) { selected = rowsFor(row.dataset.plcDirection)[Number(row.dataset.plcIndex)]; detailPage = 0; }
    if (event.target.closest('[data-plc-back]')) selected = null;
    const detail = event.target.closest('[data-plc-detail-page]');
    if (detail) detailPage += Number(detail.dataset.plcDetailPage);
    render();
  }, {signal});
  dialog.addEventListener('change', event => {
    if (event.target.id !== 'imPlcReserved') return;
    reserved = event.target.checked;
    pages = {read: 0, write: 0};
    selected = null;
    render();
  }, {signal});
  dialog.addEventListener('keydown', event => {
    const row = event.target.closest('[data-plc-index]');
    if (row && ['Enter', ' '].includes(event.key)) { event.preventDefault(); row.click(); }
  }, {signal});
}

export function openPlcDialog() {
  const dialog = document.getElementById('imPlcDialog');
  if (!dialog || dialog.open) return;
  selected = null;
  dialog.showModal();
  mode = document.getElementById('imPlcModalBody').clientWidth >= 1200 ? 'both' : 'read';
  render();
}
