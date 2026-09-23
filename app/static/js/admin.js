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
  if (response.status === 409) throw Error('این نام کاربری قبلاً ثبت شده است.');
  if (response.status === 422) throw Error('نام کاربری و طول رمز عبور را بررسی کنید.');
  if (!response.ok) throw Error('درخواست انجام نشد؛ دوباره تلاش کنید.');
  return response.json();
}

async function loadUsers() {
  $('refresh-users').disabled = true;
  $('users-message').textContent = 'در حال دریافت کاربران…';
  try {
    const users = await request('/api/users');
    const rows = users.map(user => {
      const row = document.createElement('li');
      const identity = document.createElement('div');
      const name = document.createElement('strong'); name.textContent = user.username; name.dir = 'auto';
      const id = document.createElement('small'); id.textContent = `شناسه ${user.id.toLocaleString('fa-IR')}`;
      identity.append(name, id);
      const badges = document.createElement('div'); badges.className = 'user-badges';
      const role = document.createElement('span'); role.className = 'user-badge'; role.textContent = user.is_admin ? 'ادمین' : 'کاربر';
      const active = document.createElement('span'); active.className = `user-badge ${user.is_active ? 'active' : ''}`; active.textContent = user.is_active ? 'فعال' : 'غیرفعال';
      badges.append(role, active); row.append(identity, badges);
      return row;
    });
    $('users-list').replaceChildren(...rows);
    $('user-count').textContent = users.length.toLocaleString('fa-IR');
    $('users-message').textContent = users.length ? '' : 'هنوز کاربری ثبت نشده است.';
  } catch (error) { $('users-message').textContent = error.message; }
  finally { $('refresh-users').disabled = false; }
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
$('admin-logout').onclick = async () => {
  try { await request('/api/auth/logout', {method:'POST'}); leaving = true; location.replace('/login'); }
  catch (error) { $('users-message').textContent = error.message; }
};
// This page has no VNC stream to notify it of session revocation.
async function checkSession() { if (!leaving) await request('/api/auth/me').catch(() => {}); }
setInterval(checkSession, 2000);
window.addEventListener('focus', checkSession);
loadUsers();
