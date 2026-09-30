'use strict';
let initialized = false;
const parentOrigin = new URL(location.href).origin;
const notify = (type, value) => parent.postMessage({silverdict: true, type, value}, parentOrigin);
window.addEventListener('message', event => {
  if (initialized || event.source !== parent || event.origin !== parentOrigin || event.data?.type !== 'article') return;
  const {html, id} = event.data;
  if (typeof html !== 'string' || !/^[_a-zA-Z0-9-]+$/.test(id)) return;
  initialized = true;
  const base = document.createElement('base');
  base.href = `${parentOrigin}/api/cache/${encodeURIComponent(id)}/`;
  document.head.prepend(base);
  const style = document.createElement('style');
  style.textContent = 'body{font-family:system-ui,sans-serif;font-size:16px;line-height:1.6;margin:20px;color:#273b35;overflow-wrap:anywhere}img,video{max-width:100%;height:auto}audio{max-width:100%;height:32px}a{color:#174d45}pre{white-space:pre-wrap}';
  document.head.append(style);
  document.body.id = id;
  document.body.innerHTML = html;
  // Scripts run only in this opaque-origin sandbox, never in the library document.
  for (const old of document.body.querySelectorAll('script')) {
    const script = document.createElement('script');
    for (const attr of old.attributes) script.setAttribute(attr.name, attr.value);
    script.textContent = old.textContent;
    script.async = false;
    old.replaceWith(script);
  }
  new ResizeObserver(() => notify('height', Math.ceil(document.documentElement.scrollHeight))).observe(document.body);
  notify('height', document.documentElement.scrollHeight);
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
