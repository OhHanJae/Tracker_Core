import { esc } from './global.js';

const runtimes = new Map();

export function renderExternalWebFrame({ id, name, url, title }) {
  return `<div class="card external-frame-card">
    <div class="external-frame-wrap">
      <iframe id="${esc(id)}Frame" title="${esc(title)}" src="about:blank"></iframe>
      <div class="external-frame-toolbar">
        <strong>${esc(name)}</strong>
        <a class="external-frame-url mono" id="${esc(id)}WebUrl" href="${esc(url || '#')}" data-url="${esc(url || '')}" target="_blank" rel="noopener">${esc(url || 'Web URL is not configured')}</a>
        <button class="secondary" id="${esc(id)}FrameRefresh" type="button">Refresh</button>
      </div>
      <div class="external-frame-state" id="${esc(id)}FrameState">
        <strong>연결 확인 중...</strong>
        <small>Web Server 상태를 확인하고 있습니다.</small>
      </div>
    </div>
  </div>`;
}

function showFrameState(id, title, detail, visible = true) {
  const stateBox = document.getElementById(`${id}FrameState`);
  if (!stateBox) return;
  stateBox.classList.toggle('hidden', !visible);
  stateBox.innerHTML = `<strong>${esc(title)}</strong><small>${esc(detail || '')}</small>`;
}

function closeExternalWebModal() {
  const modal = document.getElementById('externalWebModal');
  const frame = document.getElementById('externalWebModalFrame');
  modal?.classList.add('hidden');
  if (frame) frame.src = 'about:blank';
}

function bindExternalWebModal() {
  const modal = document.getElementById('externalWebModal');
  if (!modal || modal.dataset.bound === '1') return;
  modal.dataset.bound = '1';
  document.getElementById('externalWebModalClose')?.addEventListener('click', closeExternalWebModal);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeExternalWebModal();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) closeExternalWebModal();
  });
}

function openExternalWebModal(name, url) {
  const modal = document.getElementById('externalWebModal');
  const frame = document.getElementById('externalWebModalFrame');
  if (!modal || !frame || !url) return;
  document.getElementById('externalWebModalTitle').textContent = `${name} Web UI`;
  document.getElementById('externalWebModalUrl').textContent = url;
  frame.src = url;
  modal.classList.remove('hidden');
}

function browserReachableUrl(url) {
  if (!url) return url;
  try {
    const parsed = new URL(url);
    const loopbackHosts = new Set(['127.0.0.1', 'localhost', '[::1]']);
    const browserHost = window.location.hostname;
    const wildcardHosts = new Set(['0.0.0.0', '[::]']);
    if (loopbackHosts.has(parsed.hostname) && browserHost && !loopbackHosts.has(browserHost)) {
      parsed.hostname = wildcardHosts.has(browserHost) ? '127.0.0.1' : browserHost;
    }
    return parsed.href;
  } catch {
    return url;
  }
}

function showProbeFailure(options, frame, detail) {
  const runtime = runtimes.get(options.id);
  if (!runtime) return;
  runtime.statusFailures = (runtime.statusFailures || 0) + 1;
  if (!frame.dataset.loadedUrl || runtime.statusFailures >= 3) {
    if (!frame.dataset.loadedUrl) frame.src = 'about:blank';
    showFrameState(options.id, options.unavailableTitle, detail);
  }
}

async function probeFrame(options, forceReload = false, initial = false) {
  const { id, command, configuredUrl, unavailableTitle } = options;
  const frame = document.getElementById(`${id}Frame`);
  const pendingRuntime = runtimes.get(id);
  if (!frame || !pendingRuntime) return;
  if (initial || forceReload) {
    showFrameState(id, '연결 확인 중...', `${options.name} Web Server 상태를 확인하고 있습니다.`);
  }
  try {
    const status = await options.commandRequest(command);
    if (runtimes.get(id) !== pendingRuntime) return;
    const url = browserReachableUrl(status?.url || configuredUrl());
    const urlLabel = document.getElementById(`${id}WebUrl`);
    if (urlLabel) {
      urlLabel.textContent = url || 'Web URL is not configured';
      urlLabel.href = url || '#';
      urlLabel.dataset.url = url || '';
    }
    if (!status?.online || !url) {
      showProbeFailure(options, frame, status?.error || 'Web Server가 실행 중인지 확인하세요.');
      return;
    }
    const runtime = runtimes.get(id);
    if (runtime) runtime.statusFailures = 0;
    if (!forceReload) {
      if (frame.dataset.loadedUrl === url) {
        showFrameState(id, '', '', false);
        return;
      }
      if (frame.dataset.requestedUrl === url) return;
    }
    frame.onload = () => {
      if (runtimes.get(id) !== pendingRuntime) return;
      if (frame.src !== 'about:blank') {
        frame.dataset.loadedUrl = url;
        showFrameState(id, '', '', false);
      }
      const runtime = runtimes.get(id);
      if (runtime?.loadTimer) clearTimeout(runtime.loadTimer);
    };
    frame.onerror = () => {
      if (runtimes.get(id) === pendingRuntime) showFrameState(id, `${options.name} Web UI 로드 실패`, 'Refresh 버튼으로 다시 시도하세요.');
    };
    frame.dataset.requestedUrl = url;
    frame.src = url;
    const activeRuntime = runtimes.get(id) || {};
    activeRuntime.loadTimer = setTimeout(() => {
      if (runtimes.get(id) !== pendingRuntime) return;
      showFrameState(id, `${options.name} Web UI 응답 지연`, '서버 상태를 다시 확인하거나 Refresh를 누르세요.');
    }, 8000);
    runtimes.set(id, activeRuntime);
  } catch (error) {
    if (runtimes.get(id) === pendingRuntime) showProbeFailure(options, frame, error.message || String(error));
  }
}

export function startExternalWebFrame(options) {
  stopExternalWebFrame(options.id);
  bindExternalWebModal();
  const runtime = { statusTimer: null, loadTimer: null, statusFailures: 0 };
  runtimes.set(options.id, runtime);
  runtime.refreshButton = document.getElementById(`${options.id}FrameRefresh`);
  runtime.urlLabel = document.getElementById(`${options.id}WebUrl`);
  runtime.refreshHandler = () => probeFrame(options, true);
  runtime.urlHandler = event => {
    event.preventDefault();
    const url = event.currentTarget.dataset.url;
    if (!url) return;
    if (event.ctrlKey || event.metaKey) {
      window.open(url, '_blank', 'noopener');
      return;
    }
    openExternalWebModal(options.name, url);
  };
  runtime.refreshButton?.addEventListener('click', runtime.refreshHandler);
  runtime.urlLabel?.addEventListener('click', runtime.urlHandler);
  probeFrame(options, false, true);
  runtime.statusTimer = setInterval(() => probeFrame(options), 5000);
}

export function stopExternalWebFrame(id) {
  const runtime = runtimes.get(id);
  if (!runtime) return;
  if (runtime.statusTimer) clearInterval(runtime.statusTimer);
  if (runtime.loadTimer) clearTimeout(runtime.loadTimer);
  runtime.refreshButton?.removeEventListener('click', runtime.refreshHandler);
  runtime.urlLabel?.removeEventListener('click', runtime.urlHandler);
  closeExternalWebModal();
  runtimes.delete(id);
}

export function setExternalWebFrameBypassed(options, active) {
  const { id, name } = options;
  const frame = document.getElementById(`${id}Frame`);
  if (!frame) return;
  const wasBypassed = frame.dataset.bypassed === '1';
  if (active && wasBypassed) return;
  const refresh = document.getElementById(`${id}FrameRefresh`);
  const link = document.getElementById(`${id}WebUrl`);
  frame.dataset.bypassed = active ? '1' : '0';
  frame.style.pointerEvents = active ? 'none' : '';
  frame.inert = active;
  frame.tabIndex = active ? -1 : 0;
  if (refresh) refresh.disabled = active;
  if (link) {
    link.style.pointerEvents = active ? 'none' : '';
    link.tabIndex = active ? -1 : 0;
    link.href = active ? '#' : link.dataset.url || '#';
    link.setAttribute('aria-disabled', active ? 'true' : 'false');
  }
  if (active) {
    stopExternalWebFrame(id);
    closeExternalWebModal();
    frame.onload = null;
    frame.onerror = null;
    frame.src = 'about:blank';
    delete frame.dataset.loadedUrl;
    delete frame.dataset.requestedUrl;
    showFrameState(id, `${name} 바이패스 활성화`, '바이패스를 해제하면 장치 Web UI를 다시 연결합니다.');
  } else if (wasBypassed || !runtimes.has(id)) {
    startExternalWebFrame(options);
  }
}
