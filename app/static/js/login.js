const form=document.getElementById('login-form'), error=document.getElementById('error');
if(new URLSearchParams(location.search).get('reason')==='session-ended') {
  error.dir='rtl';
  error.textContent='نشست شما پایان یافته است؛ ممکن است این حساب در دستگاه دیگری وارد شده باشد. دوباره وارد شوید.';
}
form.addEventListener('submit',async e=>{e.preventDefault();error.textContent='';const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('username').value,password:document.getElementById('password').value})});if(r.ok)location.href='/dashboard';else{const d=await r.json().catch(()=>({}));error.textContent=d.detail||'Unable to sign in';}});
