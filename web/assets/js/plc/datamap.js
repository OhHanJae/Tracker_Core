import {
  PLC_MAP,
  state,
  esc,
  getPlcBase,
  convertRelativeAddresses,
} from '../global.js';

export function renderPlcMapViewer() {
  const sheet = PLC_MAP.sheets.find(s => s.key === state.plcMapSheet) || PLC_MAP.sheets[0];
  const baseText = sheet.base === 'read' ? `Read Base D${getPlcBase('read')}` : sheet.base === 'write' ? `Write Base D${getPlcBase('write')}` : 'Code / Constant';

  return `<div class="card section-gap map-card">
    <div class="card-head map-head">
      <div><h3>PLC Communication Map</h3><small>${esc(PLC_MAP.sourceFile)} · ${esc(PLC_MAP.note)}</small></div>
      <div class="map-base-chips"><span class="badge blue">Read D${getPlcBase('read')}</span><span class="badge purple">Write D${getPlcBase('write')}</span></div>
    </div>
    <div class="map-toolbar">
      <div class="map-sheet-tabs">${PLC_MAP.sheets.map(s => `<button class="map-sheet-tab ${s.key === sheet.key ? 'active' : ''}" data-map-sheet="${esc(s.key)}">${esc(s.label)}</button>`).join('')}</div>
      <div class="map-filter-row">
        <input id="plcMapSearch" placeholder="항목 / 주소 / 설명 검색...">
        ${sheet.type === 'table' ? `<label class="check-inline"><input type="checkbox" id="hideReserved"> Reserved 숨기기</label>` : ''}
        <span class="count-pill">${esc(baseText)}</span>
      </div>
    </div>
    <div class="map-title-block"><strong>${esc(sheet.title)}</strong><small>${esc(sheet.subtitle)}</small></div>
    ${sheet.type === 'sections' ? renderCodeSections(sheet) : renderMapTable(sheet)}
  </div>`;
}

function renderMapTable(sheet) {
  return `<div class="table-wrap map-table-wrap"><table class="map-table" id="plcMapTable">
    <thead><tr>${sheet.headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
    <tbody>${sheet.rows.map(row => {
    const converted = row.map(v => convertRelativeAddresses(v, sheet.base));
    const search = converted.join(' ').toLowerCase();
    const reserved = /reserved|예약/i.test(search);
    return `<tr data-map-search="${esc(search)}" data-reserved="${reserved ? '1' : '0'}">${converted.map((v, i) => `<td class="${i === 1 || String(v).startsWith('D') ? 'mono-cell' : ''}">${esc(v)}</td>`).join('')}</tr>`;
  }).join('')}</tbody>
  </table></div>`;
}

function renderCodeSections(sheet) {
  return `<div class="code-section-grid" id="plcMapTable">${sheet.sections.map(sec => {
    const allText = [sec.title, ...sec.rows.flat()].join(' ').toLowerCase();
    return `<section class="code-section" data-map-search="${esc(allText)}">
      <div class="code-section-title">${esc(sec.title)} <span class="count-pill">${sec.rows.length}</span></div>
      <div class="table-wrap"><table class="map-table compact"><thead><tr>${sec.headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>
        ${sec.rows.map(row => `<tr>${row.map(v => `<td>${esc(v)}</td>`).join('')}</tr>`).join('')}
      </tbody></table></div>
    </section>`;
  }).join('')}</div>`;
}

