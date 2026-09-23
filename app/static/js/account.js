const form=document.getElementById('password-form'), message=document.getElementById('password-message');
const csrf=()=>decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('shared_browser_csrf='))?.split('=')[1]||'');
let changing=false, leaving=false;
function ended() { leaving=true; location.replace('/login?reason=session-ended'); }
form.addEventListener('submit',async event=>{
  event.preventDefault(); message.textContent='';
  const password=document.getElementById('new-password').value;
  if(password!==document.getElementById('confirm-password').value) { message.textContent='تکرار رمز عبور یکسان نیست.'; return; }
  if(new TextEncoder().encode(password).length>72) { message.textContent='رمز جدید حداکثر ۷۲ بایت باشد.'; return; }
  const button=form.querySelector('button'); button.disabled=true; changing=true;
  try {
    const response=await fetch('/api/auth/password',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify({current_password:document.getElementById('current-password').value,new_password:password})});
    if(response.status===401||response.status===409) { ended(); return; }
    const data=await response.json().catch(()=>({}));
    if(!response.ok) throw Error(typeof data.detail==='string'?data.detail:'رمز تغییر نکرد؛ اطلاعات واردشده را بررسی کنید.');
    leaving=true; location.replace('/login?reason=password-changed');
  } catch(error) { message.textContent=error.message; }
  finally { changing=false; button.disabled=false; }
});
async function checkSession() {
  if(changing||leaving) return;
  try { const response=await fetch('/api/auth/me',{cache:'no-store'}); if(response.status===401&&!changing) ended(); } catch {}
}
setInterval(checkSession,2000); window.addEventListener('focus',checkSession);
