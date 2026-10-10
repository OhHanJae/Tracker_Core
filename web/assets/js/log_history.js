import { state, esc, coreCommand, showToast } from './global.js';

const history = { entries: [], total: 0, level: '', source: '', limit: 200 };
let requestVersion = 0;

function timestampText(entry) {
  const value = entry.timestamp || entry.timestamp_ms;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || '-') : date.toLocaleString('ko-KR', { hour12: false, timeZoneName: 'short' });
}

function historyRows() {
  return history.entries.length ? history.entries.map(entry => `<tr>
    <td class="log-time" title="${esc(entry.timestamp || '')}">${esc(timestampText(entry))}</td>
    <td><span class="badge ${['ERROR', 'CRITICAL'].includes(entry.level) ? 'red' : entry.level === 'WARNING' ? 'orange' : 'blue'}">${esc(entry.level || '-')}</span></td>
    <td class="mono-cell">${esc(entry.source || '-')}</td>
    <td class="log-message">${esc(entry.message || '')}${entry.event ? `<small>${esc(entry.event)}</small>` : ''}${entry.details && Object.keys(entry.details).length ? `<details><summary>상세</summary><pre>${esc(JSON.stringify(entry.details, null, 2))}</pre></details>` : ''}</td>
  </tr>`).join('') : '<tr><td colspan="4" class="empty">조건에 해당하는 이력이 없습니다.</td></tr>';
}

export function renderLogHistory() {
  return `<div class="card section-gap log-history-card">
    <div class="card-head"><div><h3>프로세스 오류 이력</h3><small>통신 장애, 실행 실패, 프로세스 종료와 오류 로그 · 시간대 포함</small></div><small id="logHistoryCount">${history.entries.length} / ${history.total}건</small></div>
    <div class="log-history-toolbar">
      <label>레벨<select id="logHistoryLevel">${[['', '전체'], ['WARNING', 'WARNING'], ['ERROR', 'ERROR'], ['CRITICAL', 'CRITICAL']].map(([value, label]) => `<option value="${value}" ${history.level === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
      <label>출처<input id="logHistorySource" value="${esc(history.source)}" placeholder="예: core_runtime 또는 ptm"></label>
      <label>조회 수<select id="logHistoryLimit">${[100, 200, 500, 1000].map(value => `<option ${history.limit === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label>
      <button class="secondary" id="refreshLogHistory">새로고침</button>
      <button class="secondary" id="exportLogHistory">전체 이력 저장</button>
      <button class="secondary" id="importLogHistory">이력 불러오기</button>
      <input id="logHistoryFile" type="file" accept=".json,application/json" hidden>
    </div>
    <p class="log-history-status" id="logHistoryStatus" aria-live="polite"></p>
    <div class="table-wrap log-history-table-wrap"><table class="log-history-table"><thead><tr><th>타임스탬프</th><th>레벨</th><th>출처</th><th>내용</th></tr></thead><tbody id="logHistoryBody">${historyRows()}</tbody></table></div>
  </div>`;
}

async function refreshHistory() {
  const version = ++requestVersion;
  const status = document.getElementById('logHistoryStatus');
  if (!status) return;
  status.textContent = '이력 조회 중...';
  try {
    const result = await coreCommand('logs.history', { limit: history.limit, level: history.level, source: history.source });
    if (version !== requestVersion || state.view !== 'logging') return;
    history.entries = Array.isArray(result.entries) ? result.entries : [];
    history.total = result.total ?? history.entries.length;
    const body = document.getElementById('logHistoryBody');
    if (body) body.innerHTML = historyRows();
    document.getElementById('logHistoryCount').textContent = `${history.entries.length} / ${history.total}건`;
    status.textContent = '최근 이력부터 표시합니다. 저장은 조회 조건과 관계없이 전체 이력을 내보냅니다.';
  } catch (error) {
    if (version === requestVersion && state.view === 'logging') status.textContent = `이력 조회 실패: ${error.message}`;
  }
}

export function bindLogHistoryEvents(saveTextFile) {
  document.getElementById('logHistorySource')?.addEventListener('input', event => { history.source = event.target.value.trim(); });
  document.getElementById('logHistorySource')?.addEventListener('keydown', event => { if (event.key === 'Enter') refreshHistory(); });
  document.getElementById('logHistoryLevel')?.addEventListener('change', event => { history.level = event.target.value; refreshHistory(); });
  document.getElementById('logHistoryLimit')?.addEventListener('change', event => { history.limit = Number(event.target.value); refreshHistory(); });
  document.getElementById('refreshLogHistory')?.addEventListener('click', refreshHistory);
  document.getElementById('exportLogHistory')?.addEventListener('click', async () => {
    try {
      const result = await coreCommand('logs.export');
      const filename = await saveTextFile(JSON.stringify(result, null, 2), {
        suggestedName: `core_error_history_${new Date().toISOString().replace(/[:.]/g, '-')}.json`,
        description: 'Core Error History', extension: '.json',
      });
      showToast(`${filename} 파일로 오류 이력을 저장했습니다.`);
    } catch (error) {
      if (error?.name !== 'AbortError') showToast(`이력 저장 실패: ${error.message}`);
    }
  });
  document.getElementById('importLogHistory')?.addEventListener('click', () => document.getElementById('logHistoryFile').click());
  document.getElementById('logHistoryFile')?.addEventListener('change', async event => {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const entries = Array.isArray(data) ? data : data.entries;
      if (!Array.isArray(entries)) throw new Error('entries 배열이 포함된 이력 JSON 파일이 필요합니다.');
      const result = await coreCommand('logs.import', { entries });
      showToast(`${result.imported ?? entries.length}건의 이력을 불러왔습니다.`);
      await refreshHistory();
    } catch (error) { showToast(`이력 불러오기 실패: ${error.message}`); }
  });
  refreshHistory();
}
