const form=document.getElementById('login-form'), error=document.getElementById('error');
const reason=new URLSearchParams(location.search).get('reason');
if(reason==='session-ended') error.textContent='نشست شما پایان یافته است؛ ممکن است این حساب در دستگاه دیگری وارد شده باشد. دوباره وارد شوید.';
if(reason==='password-changed') { error.classList.add('success'); error.textContent='رمز عبور تغییر کرد. با رمز جدید وارد شوید.'; }
form.addEventListener('submit',async event=>{
  event.preventDefault(); error.textContent=''; error.classList.remove('success');
  const button=form.querySelector('button'); button.disabled=true;
  try {
    const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('username').value,password:document.getElementById('password').value})});
    if(response.ok) { location.href='/dashboard'; return; }
    const data=await response.json().catch(()=>({}));
    error.textContent=response.status===401?'نام کاربری یا رمز عبور صحیح نیست.':typeof data.detail==='string'?data.detail:'ورود انجام نشد؛ دوباره تلاش کنید.';
  } catch { error.textContent='ارتباط برقرار نشد؛ دوباره تلاش کنید.'; }
  finally { button.disabled=false; }
});
