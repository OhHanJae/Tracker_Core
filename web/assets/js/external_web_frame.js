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
  if (!frame) return;
  if (initial || forceReload) {
    showFrameState(id, '연결 확인 중...', `${options.name} Web Server 상태를 확인하고 있습니다.`);
  }
  try {
    const status = await options.commandRequest(command);
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
      if (frame.src !== 'about:blank') {
        frame.dataset.loadedUrl = url;
        showFrameState(id, '', '', false);
      }
      const runtime = runtimes.get(id);
      if (runtime?.loadTimer) clearTimeout(runtime.loadTimer);
    };
    frame.onerror = () => showFrameState(id, `${options.name} Web UI 로드 실패`, 'Refresh 버튼으로 다시 시도하세요.');
    frame.dataset.requestedUrl = url;
    frame.src = url;
    const activeRuntime = runtimes.get(id) || {};
    activeRuntime.loadTimer = setTimeout(() => {
      showFrameState(id, `${options.name} Web UI 응답 지연`, '서버 상태를 다시 확인하거나 Refresh를 누르세요.');
    }, 8000);
    runtimes.set(id, activeRuntime);
  } catch (error) {
    showProbeFailure(options, frame, error.message || String(error));
  }
}

export function startExternalWebFrame(options) {
  stopExternalWebFrame(options.id);
  bindExternalWebModal();
  const runtime = { statusTimer: null, loadTimer: null, statusFailures: 0 };
  runtimes.set(options.id, runtime);
  document.getElementById(`${options.id}FrameRefresh`)?.addEventListener('click', () => probeFrame(options, true));
  document.getElementById(`${options.id}WebUrl`)?.addEventListener('click', event => {
    event.preventDefault();
    const url = event.currentTarget.dataset.url;
    if (!url) return;
    if (event.ctrlKey || event.metaKey) {
      window.open(url, '_blank', 'noopener');
      return;
    }
    openExternalWebModal(options.name, url);
  });
  probeFrame(options, false, true);
  runtime.statusTimer = setInterval(() => probeFrame(options), 5000);
}

export function stopExternalWebFrame(id) {
  const runtime = runtimes.get(id);
  if (!runtime) return;
  if (runtime.statusTimer) clearInterval(runtime.statusTimer);
  if (runtime.loadTimer) clearTimeout(runtime.loadTimer);
  runtimes.delete(id);
}
