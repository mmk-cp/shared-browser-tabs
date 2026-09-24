const $ = id => document.getElementById(id);
const csrf = () => decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('shared_browser_csrf='))?.split('=')[1] || '');
let leaving = false;

async function request(path, options = {}) {
  const response = await fetch(path, {cache:'no-store', ...options, headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf(), ...options.headers}});
  if (response.status === 401) {
    leaving = true;
    document.querySelector('.admin-shell').hidden = true;
    location.replace('/login?reason=session-ended');
    throw Error('نشست شما پایان یافت. دوباره وارد شوید.');
  }
  if (response.status === 403) { leaving = true; location.replace('/dashboard'); throw Error('دسترسی ادمین لازم است.'); }
  if (response.status === 409 && path === '/api/users') throw Error('این نام کاربری قبلاً ثبت شده است.');
  if (response.status === 422) throw Error(path === '/api/browser/proxy' ? 'لینک VLESS یا DNS معتبر نیست. DNS باید ۱ تا ۳ آدرس IP بدون لینک و پورت باشد.' : 'نام کاربری و طول رمز عبور را بررسی کنید.');
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw Error(typeof data.detail === 'string' ? data.detail : 'درخواست انجام نشد؛ دوباره تلاش کنید.');
  }
  return response.json();
}

async function loadUsers() {
  $('refresh-users').disabled = true;
  $('users-message').textContent = 'در حال دریافت کاربران…';
  try {
    const users = await request('/api/users');
    const rows = users.map(user => {
      const row = document.createElement('li');
      row.dataset.userId = user.id;
      const identity = document.createElement('div');
      const name = document.createElement('strong'); name.textContent = user.username; name.dir = 'auto';
      const id = document.createElement('small'); id.textContent = `شناسه ${user.id.toLocaleString('fa-IR')}`;
      identity.append(name, id);
      const badges = document.createElement('div'); badges.className = 'user-badges';
      const role = document.createElement('span'); role.className = 'user-badge'; role.textContent = user.is_admin ? 'ادمین' : 'کاربر';
      const active = document.createElement('span'); active.className = `user-badge ${user.is_active ? 'active' : 'pending'}`; active.textContent = user.is_active ? 'تأییدشده' : 'در انتظار تأیید';
      badges.append(role, active);
      const actions = document.createElement('div'); actions.className = 'user-row-actions';
      if (!user.is_active) {
        const approve = document.createElement('button'); approve.className = 'secondary-button approve-user'; approve.textContent = 'تأیید کاربر';
        approve.onclick = () => perform(approve, `/api/users/${user.id}/approve`, 'POST');
        actions.append(approve);
      }
      if (!user.is_admin) {
        const remove = document.createElement('button'); remove.className = 'secondary-button delete-user'; remove.textContent = 'حذف';
        remove.onclick = () => {
          if (confirm(`حساب «${user.username}» و تب آن حذف شود؟ این کار قابل بازگشت نیست. کوکی‌های مرورگر مشترک پاک نمی‌شوند.`))
            perform(remove, `/api/users/${user.id}`, 'DELETE');
        };
        actions.append(remove);
      }
      const detail = document.createElement('div'); detail.className = 'user-row-detail'; detail.append(badges, actions);
      row.append(identity, detail);
      return row;
    });
    $('users-list').replaceChildren(...rows);
    $('user-count').textContent = users.length.toLocaleString('fa-IR');
    const pending = users.filter(user => !user.is_active).length;
    $('pending-summary').textContent = pending ? `${pending.toLocaleString('fa-IR')} درخواست در انتظار تأیید` : 'درخواست تأییدنشده‌ای وجود ندارد.';
    $('users-message').textContent = users.length ? '' : 'هنوز کاربری ثبت نشده است.';
  } catch (error) { $('users-message').textContent = error.message; }
  finally { $('refresh-users').disabled = false; }
}

async function perform(button, path, method) {
  button.disabled = true;
  try { await request(path, {method}); await loadUsers(); }
  catch (error) { $('users-message').textContent = error.message; button.disabled = false; }
}

$('create-user-form').addEventListener('submit', async event => {
  event.preventDefault();
  const username = $('new-username').value.trim(), password = $('new-password').value;
  const message = $('create-message'); message.classList.remove('success');
  if (username.length < 2 || new TextEncoder().encode(password).length > 72) {
    message.textContent = 'نام کاربری حداقل ۲ کاراکتر و رمز عبور حداکثر ۷۲ بایت باشد.'; return;
  }
  $('create-user').disabled = true; message.textContent = 'در حال ساخت کاربر…';
  try {
    const user = await request('/api/users', {method:'POST', body:JSON.stringify({username,password,is_admin:$('new-is-admin').checked})});
    $('create-user-form').reset();
    message.classList.add('success'); message.textContent = `کاربر «${user.username}» ساخته شد.`;
    await loadUsers();
  } catch (error) { message.textContent = error.message; }
  finally { $('create-user').disabled = false; }
});
$('refresh-users').onclick = loadUsers;
let proxySaved = null;
let proxyTesting = false;
const dnsProviders = {
  cloudflare:['1.1.1.1'],
  google:['8.8.8.8'],
  quad9:['9.9.9.9'],
};
function dnsFormValue() {
  const choice = $('dns-mode').value;
  return choice === 'system' ? {mode:'system',servers:[]} : {mode:'custom', servers:
    dnsProviders[choice] || $('dns-servers').value.split(/[\s,;]+/).map(x => x.trim()).filter(Boolean)};
}
$('dns-mode').onchange = () => { $('dns-custom').hidden = $('dns-mode').value !== 'custom'; $('proxy-test-result').textContent = ''; };
function showProxyStatus(status) {
  proxySaved = status;
  $('proxy-enabled').checked = status.enabled;
  const dns = status.dns || {mode:'system',servers:[]};
  $('dns-mode').value = dns.mode === 'system' ? 'system' : (Object.keys(dnsProviders).find(key => JSON.stringify(dnsProviders[key]) === JSON.stringify(dns.servers)) || 'custom');
  $('dns-servers').value = dns.servers.join('\n');
  $('dns-custom').hidden = $('dns-mode').value !== 'custom';
  $('proxy-test').disabled = proxyTesting || !status.enabled || !status.running;
  const state = status.enabled ? (status.running ? 'روشن · سرویس محلی آماده است' : 'خطا · پروکسی در دسترس نیست؛ اتصال عمومی مسدود می‌ماند') : 'خاموش · اتصال مستقیم';
  $('proxy-status').textContent = state + (status.server ? ` — ${status.server}:${status.port} (${status.transport})` : '') + (status.running ? '؛ اتصال به سرور مقصد هنوز تأیید نشده است.' : '');
  if (status.load_failed) $('proxy-status').textContent += ' تنظیمات ذخیره‌شده خوانده نشد؛ لینک را دوباره وارد کنید یا پروکسی را خاموش کنید.';
  proxyWarning();
}
function proxyWarning() {
  const uri = $('proxy-uri').value.trim();
  $('proxy-warning').hidden = !(uri ? /[?&](?:allowInsecure|insecure)=(?:true|1)(?:&|#|$)/i.test(uri) : proxySaved?.insecure);
}
async function loadProxy() {
  $('proxy-refresh').disabled = true;
  try { showProxyStatus(await request('/api/browser/proxy')); $('proxy-save').disabled = false; }
  catch (error) { $('proxy-message').textContent = error.message; }
  finally { $('proxy-refresh').disabled = false; }
}
$('proxy-refresh').onclick = loadProxy;
$('proxy-uri').oninput = proxyWarning;
$('proxy-test').onclick = async () => {
  if (proxyTesting) return;
  proxyTesting = true; $('proxy-test').disabled = true; $('proxy-save').disabled = true;
  const result = $('proxy-test-result'); result.classList.remove('success');
  result.textContent = 'در حال ارسال درخواست HTTPS از VPN… حداکثر ۱۲ ثانیه';
  try {
    const test = await request('/api/browser/proxy/test', {method:'POST'});
    result.classList.toggle('success', test.ok);
    result.textContent = test.message + (test.ip ? `\nIP خروجی: \u2068${test.ip}\u2069` : '') +
      `\nزمان درخواست: ${test.elapsed_ms.toLocaleString('fa-IR')} میلی‌ثانیه` +
      (test.http_status ? ` · HTTP ${test.http_status}` : '');
  } catch (error) { result.textContent = error.message; }
  finally {
    $('proxy-save').disabled = false;
    $('proxy-test').textContent = 'تست دوباره تا ۱۰ ثانیه دیگر';
    setTimeout(() => { proxyTesting = false; $('proxy-test').textContent = 'تست اتصال VPN'; $('proxy-test').disabled = !proxySaved?.enabled || !proxySaved?.running; }, 10000);
  }
};
$('proxy-form').onsubmit = async event => {
  event.preventDefault();
  if ($('proxy-save').disabled) return;
  const enabled = $('proxy-enabled').checked, uri = $('proxy-uri').value.trim();
  const dns = dnsFormValue();
  const message = $('proxy-message'); message.classList.remove('success');
  if (enabled && !uri && !proxySaved?.configured) { message.textContent = 'ابتدا لینک VLESS را وارد کنید.'; $('proxy-uri').focus(); return; }
  if (dns.mode === 'custom' && (!dns.servers.length || dns.servers.length > 3)) { message.textContent = 'بین ۱ تا ۳ IP سرور DNS وارد کنید.'; return; }
  if (!confirm('مرورگر همهٔ کاربران برای اعمال پروکسی راه‌اندازی مجدد می‌شود. ادامه می‌دهید؟')) return;
  for (const id of ['proxy-save','proxy-refresh','proxy-uri','proxy-enabled','dns-mode','dns-servers','proxy-test']) $(id).disabled = true;
  $('proxy-test-result').textContent = '';
  message.textContent = 'در حال اعمال تنظیمات و راه‌اندازی مجدد مرورگر…';
  try {
    const status = await request('/api/browser/proxy', {method:'PUT', body:JSON.stringify({enabled, uri, dns})});
    $('proxy-uri').value = ''; showProxyStatus(status);
    message.classList.add('success'); message.textContent = 'تنظیمات اعمال شد. تب‌ها دوباره باز شدند.';
  } catch (error) { message.textContent = error.message; }
  finally { for (const id of ['proxy-save','proxy-refresh','proxy-uri','proxy-enabled','dns-mode','dns-servers']) $(id).disabled = false; $('proxy-test').disabled = proxyTesting || !proxySaved?.enabled || !proxySaved?.running; }
};
loadProxy();
const clearButton = $('clear-browser-data');
clearButton.onclick = async () => {
  $('clear-browser-form').hidden = false; clearButton.hidden = true;
  $('clear-confirmation').value = ''; $('clear-browser-submit').disabled = true;
  $('clear-confirmation').focus();
};
$('clear-confirmation').oninput = () => { $('clear-browser-submit').disabled = $('clear-confirmation').value.trim() !== 'پاک شود'; };
$('cancel-browser-clear').onclick = () => { $('clear-browser-form').hidden = true; clearButton.hidden = false; clearButton.focus(); };
$('clear-browser-form').onsubmit = async event => {
  event.preventDefault();
  if ($('clear-confirmation').value.trim() !== 'پاک شود' || clearButton.disabled) return;
  clearButton.disabled = true; $('clear-browser-message').classList.remove('success');
  $('clear-browser-submit').disabled = true; $('cancel-browser-clear').disabled = true; $('clear-confirmation').disabled = true;
  $('clear-browser-message').textContent = 'در حال بستن مرورگر و پاک‌سازی داده‌ها…';
  try {
    await request('/api/browser/clear-data', {method:'POST', body:JSON.stringify({confirmation:'DELETE_BROWSER_DATA'})});
    $('clear-browser-message').classList.add('success');
    $('clear-browser-message').textContent = 'پروفایل مرورگر پاک و از نو ساخته شد. تب‌ها باز شدند؛ ورود مجدد به سایت‌ها لازم است.';
  } catch (error) { $('clear-browser-message').textContent = error.message; }
  finally { clearButton.disabled = false; clearButton.hidden = false; $('clear-browser-form').hidden = true; $('cancel-browser-clear').disabled = false; $('clear-confirmation').disabled = false; }
};
$('admin-logout').onclick = async () => {
  try { await request('/api/auth/logout', {method:'POST'}); leaving = true; location.replace('/login'); }
  catch (error) { $('users-message').textContent = error.message; }
};
// This page has no VNC stream to notify it of session revocation.
async function checkSession() { if (!leaving) await request('/api/auth/me').catch(() => {}); }
setInterval(checkSession, 2000);
window.addEventListener('focus', checkSession);
loadUsers();
