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
  if (response.status === 422) throw Error('نام کاربری و طول رمز عبور را بررسی کنید.');
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
