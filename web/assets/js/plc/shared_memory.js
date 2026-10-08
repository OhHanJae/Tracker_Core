import { state, esc, coreCommand } from '../global.js';

const BIT_COLUMNS = Array.from({ length: 16 }, (_, index) => 15 - index);
let monitoring = false;
let pollTimer = null;
const previousWords = { read: [], write: [] };

function configuredWordCount(area) {
  const key = area === 'read' ? 'read_words' : 'write_words';
  return Number(state.coreConfig?.xgt?.shared_memory?.[key] || 0);
}

function tableRows(area, count) {
  return Array.from({ length: count }, (_, index) => `<tr data-shm-row="${area}-${index}">
    <td class="mono-cell" data-shm-address="${area}-${index}">-</td>
    ${BIT_COLUMNS.map(bit => `<td class="shm-bit" data-shm-bit="${area}-${index}-${bit}">0</td>`).join('')}
    <td class="mono-cell" data-shm-dec="${area}-${index}">0</td>
    <td class="mono-cell" data-shm-hex="${area}-${index}">0x0000</td>
  </tr>`).join('');
}

function memoryTable(area, title, count) {
  return `<div class="card section-gap shm-monitor-card">
    <div class="card-head"><h3>${esc(title)}</h3><small><span data-shm-count="${area}">${count}</span> Words</small></div>
    <div class="table-wrap shm-table-wrap"><table class="shm-word-table">
      <thead><tr><th>Address</th>${BIT_COLUMNS.map(bit => `<th>Bit ${bit.toString(16).toUpperCase()}</th>`).join('')}<th>DEC</th><th>HEX</th></tr></thead>
      <tbody data-shm-body="${area}">${tableRows(area, count)}</tbody>
    </table></div>
  </div>`;
}

export function renderSharedMemoryMonitor() {
  const name = state.coreConfig?.xgt?.shared_memory?.name || 'N/A';
  return `<div class="card">
    <div class="card-head"><div><h3>Shared Memory Monitor</h3><small class="mono" id="shmMonitorMeta">${esc(name)} · Waiting for data</small></div><span class="badge red" id="shmMonitorStatus">Offline</span></div>
  </div>
  ${memoryTable('read', 'Read Shared Memory', configuredWordCount('read'))}
  ${memoryTable('write', 'Write Shared Memory', configuredWordCount('write'))}`;
}

function addressAt(baseAddress, index) {
  const match = /^([A-Za-z]+)(\d+)$/.exec(String(baseAddress || '').trim());
  return match ? `${match[1].toUpperCase()}${Number(match[2]) + index}` : `${baseAddress || '?'}+${index}`;
}

function ensureRows(area, count) {
  const body = document.querySelector(`[data-shm-body="${area}"]`);
  if (!body || body.children.length === count) return;
  body.innerHTML = tableRows(area, count);
  const countLabel = document.querySelector(`[data-shm-count="${area}"]`);
  if (countLabel) countLabel.textContent = String(count);
  previousWords[area] = [];
}

function updateArea(area, snapshot) {
  const words = Array.isArray(snapshot?.words) ? snapshot.words : [];
  ensureRows(area, words.length);
  words.forEach((raw, index) => {
    const word = Number(raw) & 0xFFFF;
    if (previousWords[area][index] === word) return;
    previousWords[area][index] = word;
    const address = document.querySelector(`[data-shm-address="${area}-${index}"]`);
    const dec = document.querySelector(`[data-shm-dec="${area}-${index}"]`);
    const hex = document.querySelector(`[data-shm-hex="${area}-${index}"]`);
    if (address) address.textContent = addressAt(snapshot.base_address, index);
    if (dec) dec.textContent = String(word);
    if (hex) hex.textContent = `0x${word.toString(16).toUpperCase().padStart(4, '0')}`;
    BIT_COLUMNS.forEach(bit => {
      const cell = document.querySelector(`[data-shm-bit="${area}-${index}-${bit}"]`);
      if (cell) cell.textContent = String((word >>> bit) & 1);
    });
  });
}

function setMonitorState(online, message, disabled = false) {
  const badge = document.getElementById('shmMonitorStatus');
  if (badge) {
    badge.className = `badge ${disabled ? 'blue' : online ? 'green' : 'red'}`;
    badge.textContent = disabled ? 'Disabled' : online ? 'Online' : 'Offline';
  }
  const meta = document.getElementById('shmMonitorMeta');
  if (meta) meta.textContent = message;
}

async function pollSharedMemory() {
  if (!monitoring || state.view !== 'plc' || state.activeSubgroup.plc !== '공유 메모리') return;
  let nextDelay = 500;
  try {
    const snapshot = await coreCommand('xgt.shared_memory');
    if (!monitoring) return;
    if (snapshot.disabled) {
      updateArea('read', { words: [] });
      updateArea('write', { words: [] });
      setMonitorState(false, 'PLC Communication disabled', true);
      return;
    }
    updateArea('read', snapshot.read);
    updateArea('write', snapshot.write);
    const header = snapshot.header || {};
    setMonitorState(
      true,
      `${snapshot.name} · ${snapshot.byte_order}-endian · Read offset ${header.read_offset} · Write offset ${header.write_offset}`,
    );
    nextDelay = Number(snapshot.refresh_ms) || 500;
  } catch (error) {
    setMonitorState(false, error.message || String(error));
  } finally {
    if (monitoring) pollTimer = setTimeout(pollSharedMemory, Math.max(100, nextDelay));
  }
}

export function startSharedMemoryMonitoring() {
  stopSharedMemoryMonitoring();
  monitoring = true;
  previousWords.read = [];
  previousWords.write = [];
  pollSharedMemory();
}

export function stopSharedMemoryMonitoring() {
  monitoring = false;
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
}
