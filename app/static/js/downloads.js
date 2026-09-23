// One-shot server response -> local Blob. The fallback link needs no server file.
export function createDownloads({csrf, api, notify, closePanels}) {
  const $ = id => document.getElementById(id);
  const panel = $('downloads-panel'), list = $('downloads-list'), items = new Map();
  const show = () => { closePanels(); panel.classList.add('open'); panel.scrollTop = 0; $('downloads-toggle').classList.add('active'); };
  function closePanel() { panel.classList.remove('open'); $('downloads-toggle').classList.remove('active'); }
  $('downloads-toggle').onclick = () => panel.classList.contains('open') ? closePanel() : show();
  function render() {
    list.replaceChildren(); $('downloads-empty').hidden = items.size > 0;
    for (const item of [...items.values()].reverse()) {
      const row = document.createElement('li'), name = document.createElement('strong');
      name.textContent = item.name; name.dir = 'auto'; row.append(name);
      const status = document.createElement('small');
      status.textContent = item.error || ({receiving:'در حال دریافت از سایت…', ready:'آمادهٔ دریافت؛ تا دو دقیقه فرصت دارید.',
        fetching:'در حال انتقال به دستگاه شما…', sending:'در حال دریافت در یکی از پنجره‌های شما…',
        local:'فایل به این دستگاه رسید. اگر ذخیره شروع نشد، دکمهٔ زیر را بزنید.'}[item.state] || '');
      row.append(status);
      const actions = document.createElement('div'); actions.className = 'panel-actions';
      if (item.url) {
        const link = document.createElement('a'); link.href = item.url; link.download = item.name;
        link.className = 'accent-button'; link.textContent = 'ذخیره در دستگاه'; actions.append(link);
      } else if (item.state === 'ready') {
        const receive = document.createElement('button'); receive.className = 'accent-button';
        receive.textContent = 'دریافت در دستگاه'; receive.onclick = () => transfer(item); actions.append(receive);
      }
      const cancel = document.createElement('button'); cancel.className = 'secondary-button';
      cancel.textContent = item.url || item.error ? 'بستن' : 'لغو'; cancel.onclick = () => dismiss(item);
      actions.append(cancel); row.append(actions); list.append(row);
    }
  }
  function dismiss(item) {
    items.delete(item.token); item.controller?.abort();
    if (item.url) URL.revokeObjectURL(item.url);
    else api(`/api/tabs/me/downloads/${encodeURIComponent(item.token)}`, {method:'DELETE'}).catch(() => {});
    clearTimeout(item.timer); render();
  }
  async function transfer(item) {
    if (item.state !== 'ready') return;
    // Release an old Blob before buffering a new response, limiting peak
    // memory as well as retained memory on phones and desktops.
    const old = [...items.values()].filter(entry => entry.url);
    while (old.length >= 2) dismiss(old.shift());
    item.state = 'fetching'; item.controller = new AbortController(); render();
    try {
      const response = await fetch(`/api/tabs/me/downloads/${encodeURIComponent(item.token)}`, {
        method:'POST', headers:{'X-CSRF-Token':csrf()}, signal:item.controller.signal, cache:'no-store',
      });
      if (!response.ok) throw Error('Download failed');
      const blob = await response.blob();
      if (!items.has(item.token)) return;
      // Bound device memory, too. The fallback links last five minutes.
      item.url = URL.createObjectURL(blob); item.state = 'local'; item.controller = null;
      item.timer = setTimeout(() => dismiss(item), 5 * 60 * 1000); render();
      const link = document.createElement('a'); link.href = item.url; link.download = item.name;
      document.body.append(link); link.click(); link.remove();
      notify('فایل به دستگاه رسید؛ اگر ذخیره شروع نشد، از پنل دانلود «ذخیره در دستگاه» را بزنید.');
    } catch (error) {
      if (!items.has(item.token)) return;
      item.state = 'error'; item.error = 'انتقال کامل نشد و نسخهٔ موقت پاک می‌شود؛ دوباره از سایت دانلود کنید.';
      api(`/api/tabs/me/downloads/${encodeURIComponent(item.token)}`, {method:'DELETE'}).catch(() => {});
      notify(item.error); render();
    }
  }
  function message(event) {
    if (event.type === 'download_error') {
      const item = items.get(event.token);
      if (item) { item.state = 'error'; item.error = event.message; render(); }
      notify(event.message); return true;
    }
    if (event.type === 'download_removed') {
      const item = items.get(event.token);
      if (item && !['local','fetching','error'].includes(item.state)) {
        item.state = 'error'; item.error = 'نسخهٔ موقت پاک شد؛ در صورت نیاز دوباره از سایت دانلود کنید.'; render();
      }
      return true;
    }
    if (event.type !== 'download') return false;
    let item = items.get(event.token);
    if (item && ['fetching','local','error'].includes(item.state)) return true;
    if (!item) {
      if (items.size >= 8) dismiss(items.values().next().value);
      item = {...event}; items.set(item.token,item);
    } else Object.assign(item,event);
    render();
    if (document.visibilityState === 'visible') show();
    if (item.state === 'ready' && document.hasFocus()) transfer(item);
    return true;
  }
  // Disconnect cleanup also works if a phone never delivers its unload event.
  window.addEventListener('pagehide', () => {
    for (const item of items.values()) { item.controller?.abort(); if (item.url) URL.revokeObjectURL(item.url); }
  });
  return {message, closePanel};
}
