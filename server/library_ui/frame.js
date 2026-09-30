'use strict';
let initialized = false;
const parentOrigin = new URL(location.href).origin;
const notify = (type, value) => parent.postMessage({silverdict: true, type, value}, parentOrigin);
const audioButtons = new Map();
const speakerIcon = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/></svg>';
const pauseIcon = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 5v14M16 5v14"/></svg>';
function enhanceAudio() {
  for (const audio of document.querySelectorAll('audio')) {
    if (audioButtons.has(audio)) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'silverdict-audio-button';
    let loading = false;
    let requestVersion = 0;
    function state(mode) {
      loading = mode === 'loading';
      const playing = mode === 'playing';
      const label = playing || loading ? 'Pause pronunciation' : mode === 'error' ? 'Audio unavailable. Retry pronunciation' : 'Play pronunciation';
      button.innerHTML = playing ? pauseIcon : speakerIcon;
      button.title = label;
      button.setAttribute('aria-label', label);
      button.setAttribute('aria-pressed', String(playing || loading));
      button.dataset.state = mode;
    }
    audioButtons.set(audio, button);
    audio.controls = false;
    audio.hidden = true;
    button.addEventListener('click', async () => {
      const version = ++requestVersion;
      if (!audio.paused || loading) { audio.pause(); state('idle'); return; }
      state('loading');
      try { await audio.play(); }
      catch (error) { if (version === requestVersion) state(error.name === 'AbortError' ? 'idle' : 'error'); }
    });
    audio.addEventListener('play', () => {
      for (const other of audioButtons.keys()) if (other !== audio) other.pause();
      state('loading');
    });
    audio.addEventListener('playing', () => state('playing'));
    audio.addEventListener('waiting', () => { if (!audio.paused) state('loading'); });
    audio.addEventListener('pause', () => state('idle'));
    audio.addEventListener('ended', () => state('idle'));
    audio.addEventListener('error', () => state('error'));
    audio.before(button);
    state('idle');
  }
}
window.addEventListener('message', async event => {
  if (initialized || event.source !== parent || event.origin !== parentOrigin || event.data?.type !== 'article') return;
  const {html, id} = event.data;
  if (typeof html !== 'string' || !/^[_a-zA-Z0-9-]+$/.test(id)) return;
  initialized = true;
  const base = document.createElement('base');
  base.href = `${parentOrigin}/api/cache/${encodeURIComponent(id)}/`;
  document.head.prepend(base);
  const style = document.createElement('style');
  style.textContent = 'body{font-family:system-ui,sans-serif;font-size:16px;line-height:1.6;margin:20px;color:#273b35;overflow-wrap:anywhere}img,video{max-width:100%;height:auto}audio[hidden]{display:none!important}a{color:#174d45}pre{white-space:pre-wrap}' +
    '.silverdict-audio-button{display:inline-flex!important;position:relative;align-items:center;justify-content:center;vertical-align:middle;width:28px;height:28px;padding:5px;margin:0 3px;border:1px solid #174d4526;border-radius:50%;background:#edf4ef;color:#174d45;cursor:pointer;box-sizing:border-box;line-height:1;flex-shrink:0}.silverdict-audio-button:hover{background:#dcebe1}.silverdict-audio-button:focus-visible{outline:2px solid #174d45;outline-offset:2px}.silverdict-audio-button svg{display:block;width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.silverdict-audio-button[data-state=error]{color:#943d30;background:#fff0eb}.silverdict-audio-button[data-state=loading] svg{opacity:.35}';
  document.head.append(style);
  const fonts = document.createElement('link');
  fonts.rel = 'stylesheet';
  fonts.href = `${parentOrigin}/library-fonts/fonts.css`;
  const fontsReady = new Promise(resolve => {
    const timer = setTimeout(resolve, 3000);
    const done = () => { clearTimeout(timer); resolve(); };
    fonts.addEventListener('load', done, {once:true});
    fonts.addEventListener('error', done, {once:true});
  });
  document.head.append(fonts);
  document.body.id = id;
  document.body.innerHTML = html;
  enhanceAudio();
  new MutationObserver(enhanceAudio).observe(document.body, {childList:true, subtree:true});
  await fontsReady;
  // Scripts run only in this opaque-origin sandbox, never in the library document.
  for (const old of document.body.querySelectorAll('script')) {
    const script = document.createElement('script');
    for (const attr of old.attributes) script.setAttribute(attr.name, attr.value);
    script.textContent = old.textContent;
    script.async = false;
    if (script.src) {
      // An inline initializer must wait for the external dependency before it.
      await new Promise(resolve => {
        script.addEventListener('load', resolve, {once: true});
        script.addEventListener('error', resolve, {once: true});
        old.replaceWith(script);
      });
    } else {
      old.replaceWith(script);
    }
  }
  const resize = () => {
    const margins = getComputedStyle(document.body);
    const height = Math.max(document.body.scrollHeight, document.body.getBoundingClientRect().height);
    notify('height', Math.ceil(height + parseFloat(margins.marginTop || 0) + parseFloat(margins.marginBottom || 0)));
  };
  new ResizeObserver(resize).observe(document.body);
  resize();
});
document.addEventListener('click', event => {
  const a = event.target.closest('a');
  if (!a) return;
  const raw = a.getAttribute('href') || '';
  if (raw.startsWith('#')) {
    event.preventDefault();
    const name = decodeURIComponent(raw.slice(1));
    const target = document.getElementById(name) || document.getElementsByName(name)[0];
    target?.scrollIntoView({block:'start'});
    return;
  }
  event.preventDefault();
  const url = new URL(a.href, location.href);
  const match = url.pathname.match(/^\/api\/(?:lookup|query)\/[^/]+\/(.+)$/);
  if (url.origin === parentOrigin && match) notify('lookup', decodeURIComponent(match[1]));
  else if (raw.startsWith('entry://') || raw.startsWith('bword://')) notify('lookup', decodeURIComponent(raw.split('://')[1]));
});
