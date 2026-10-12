import { state, esc, apiRequest, showToast } from './global.js';

let bypassRevision = 0;

export function bypassStatusText(device) {
  const bypass = state.coreStatus?.bypass?.[device];
  if (!bypass) return 'Core 상태 확인 중';
  const source = bypass.source === 'plc' ? 'PLC' : bypass.source === 'web' ? '웹' : '없음';
  return `실제 적용: ${bypass.active ? '켜짐' : '꺼짐'} · 우선 설정: ${source}`;
}

export function renderBypassControl(device, configKey, label, detail) {
  return `<div class="card" style="flex:none;margin-bottom:12px">
    <div class="card-body" style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
      <label style="display:flex;align-items:center;gap:9px"><strong>${esc(label)} 웹 바이패스</strong>
        <span class="switch"><input id="${device}Bypass" type="checkbox" ${state.coreConfig?.[configKey]?.bypass ? 'checked' : ''}><span class="slider"></span></span>
      </label>
      <span id="${device}BypassStatus">${esc(bypassStatusText(device))}</span>
      <small>${esc(detail)} PLC 활성화가 웹 설정보다 우선합니다.</small>
    </div>
  </div>`;
}

export function bindBypassControl(device, configKey, onEnable = () => {}) {
  const control = document.getElementById(`${device}Bypass`);
  control?.addEventListener('change', async () => {
    bypassRevision += 1;
    const value = control.checked;
    if (value) onEnable();
    control.disabled = true;
    try {
      state.coreConfig = await apiRequest('/api/config', {
        method: 'PATCH', body: JSON.stringify({ [configKey]: { bypass: value } }),
      });
      showToast(`${device.toUpperCase()} 웹 바이패스 저장 완료`);
    } catch (error) {
      control.checked = Boolean(state.coreConfig?.[configKey]?.bypass);
      showToast(`바이패스 저장 실패: ${error.message}`);
    } finally {
      bypassRevision += 1;
      control.disabled = false;
    }
  });
}

export function startBypassStatus(device, update = () => {}) {
  let stopped = false;
  let pending = false;
  async function refresh() {
    if (stopped || pending) return;
    pending = true;
    const revision = bypassRevision;
    try {
      const status = await apiRequest('/api/status');
      if (stopped || revision !== bypassRevision) return;
      state.coreStatus = status;
      const label = document.getElementById(`${device}BypassStatus`);
      if (label) label.textContent = bypassStatusText(device);
      update(status);
    } catch (error) {
      if (!stopped && revision === bypassRevision) {
        const label = document.getElementById(`${device}BypassStatus`);
        if (label) label.textContent = `Core 상태 확인 실패: ${error.message}`;
      }
    } finally {
      pending = false;
    }
  }
  refresh();
  const timer = setInterval(refresh, 1500);
  return () => { stopped = true; clearInterval(timer); };
}
