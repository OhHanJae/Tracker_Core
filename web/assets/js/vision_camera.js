// Core relays only the latest JPEG; no Vision navigation or settings are loaded.
export function startVisionCamera() {
  const image = document.getElementById('imVisionImage');
  const message = document.getElementById('camera-message');
  if (!image || !message) return () => {};
  let stopped = false;
  let timer = null;
  let request = null;
  let currentUrl = '';
  let frameId = '';
  let frameAt = 0;
  const show = text => { message.textContent = text; message.hidden = !text; };
  async function nextFrame() {
    if (stopped) return;
    if (document.hidden) { timer = setTimeout(nextFrame, 300); return; }
    request = new AbortController();
    const timeout = setTimeout(() => request.abort(), 2500);
    let delay = 100;
    let nextUrl = '';
    try {
      const base = window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : window.location.href;
      const url = new URL('/api/monitor/vision/frame', base);
      if (frameId) url.searchParams.set('after', frameId);
      const response = await fetch(url, {signal: request.signal, cache: 'no-store'});
      if (stopped) return;
      if (response.status === 204) {
        if (!frameAt || Date.now() - frameAt > 1500) show('카메라 최신 프레임 대기 중');
        return;
      }
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const blob = await response.blob();
      nextUrl = URL.createObjectURL(blob);
      const preview = new Image();
      preview.src = nextUrl;
      await preview.decode();
      if (stopped) return;
      const id = response.headers.get('X-Frame-Id') || '';
      if (!frameAt || !id || frameId !== id) frameAt = Date.now();
      frameId = id;
      image.src = nextUrl;
      if (currentUrl) URL.revokeObjectURL(currentUrl);
      currentUrl = nextUrl;
      nextUrl = '';
      show(Date.now() - frameAt > 1500 ? '카메라 최신 프레임 대기 중' : '');
    } catch (error) {
      if (!stopped) show(`카메라 최신 프레임 재시도 중 · ${error.name === 'AbortError' ? '응답 시간 초과' : error.message}`);
      delay = 1000;
    } finally {
      clearTimeout(timeout);
      if (nextUrl) URL.revokeObjectURL(nextUrl);
      if (!stopped) timer = setTimeout(nextFrame, delay);
    }
  }
  show('카메라 최신 프레임 연결 중');
  nextFrame();
  return () => {
    stopped = true;
    clearTimeout(timer);
    request?.abort();
    if (currentUrl) URL.revokeObjectURL(currentUrl);
    image.removeAttribute('src');
  };
}
