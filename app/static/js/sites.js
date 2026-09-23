export const iconChoices = ['🌐','💬','🤖','🔎','📚','🎨','💻','✉️','🎵','📊','🛠️','⭐'];

export function siteCard(site, onSelect) {
  const card = document.createElement(onSelect ? 'button' : 'div');
  card.className = 'site-card';
  if (onSelect) { card.type = 'button'; card.onclick = () => onSelect(site); }
  const icon = document.createElement('span'); icon.className = 'site-icon'; icon.setAttribute('aria-hidden','true');
  icon.textContent = site.icon || [...site.title.trim()][0] || '🌐';
  const detail = document.createElement('span'); detail.className = 'site-identity';
  const title = document.createElement('strong'); title.textContent = site.title; title.dir = 'auto';
  const host = document.createElement('small'); host.dir = 'ltr';
  try { host.textContent = new URL(site.url).host; } catch { host.textContent = 'example.com'; }
  detail.append(title,host); card.append(icon,detail);
  return card;
}

export function createSitePicker({api, onOpen}) {
  const $ = id => document.getElementById(id);
  let sites = [], busy = false, generation = 0;
  function render() {
    const query = $('site-search').value.trim().toLocaleLowerCase();
    const matches = sites.filter(site => `${site.title} ${new URL(site.url).host}`.toLocaleLowerCase().includes(query));
    $('site-grid').replaceChildren(...matches.map(site => {
      const card = siteCard(site, select); card.disabled = busy; card.dataset.siteId = site.id; return card;
    }));
    $('sites-empty').hidden = matches.length > 0;
    $('sites-empty').textContent = sites.length ? 'سایتی با این نام پیدا نشد؛ جست‌وجو را تغییر دهید.' : document.body.dataset.admin === 'true' ? 'هنوز سایتی فعال نیست؛ از لینک مدیریت زیر، اولین سایت را اضافه کنید.' : 'هنوز سایتی در دسترس نیست. از ادمین بخواهید یک سایت اضافه کند.';
    $('sites-count').textContent = sites.length.toLocaleString('fa-IR');
  }
  async function load() {
    const current = ++generation;
    $('refresh-sites').disabled = true; $('site-message').textContent = 'در حال دریافت سایت‌ها…';
    try {
      const result = await api('/api/sites', {cache:'no-store'});
      if (current !== generation) return;
      sites = result; render(); $('site-message').textContent = '';
    } catch (error) {
      if (current === generation) { sites = []; render(); $('site-message').textContent = error.message; }
    } finally { if (current === generation) $('refresh-sites').disabled = false; }
  }
  async function select(site) {
    if (busy) return;
    busy = true; render(); $('site-message').textContent = `در حال باز کردن ${site.title}…`;
    try { await onOpen(site); $('site-message').textContent = ''; }
    catch (error) { await load(); $('site-message').textContent = error.message; }
    finally { busy = false; render(); }
  }
  $('site-search').addEventListener('input', render);
  $('refresh-sites').onclick = load;
  return {load};
}
