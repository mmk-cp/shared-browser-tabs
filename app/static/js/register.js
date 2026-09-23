const form=document.getElementById('register-form'), message=document.getElementById('register-message');
form.addEventListener('submit',async event=>{
  event.preventDefault(); message.classList.remove('success');
  const username=document.getElementById('username').value.trim(), password=document.getElementById('password').value;
  if(password!==document.getElementById('confirm-password').value) { message.textContent='تکرار رمز عبور یکسان نیست.'; return; }
  if(username.length<2||new TextEncoder().encode(password).length>72) { message.textContent='نام کاربری حداقل ۲ کاراکتر و رمز عبور حداکثر ۷۲ بایت باشد.'; return; }
  const button=form.querySelector('button'); button.disabled=true; message.textContent='در حال ثبت درخواست…';
  try {
    const response=await fetch('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});
    const data=await response.json().catch(()=>({}));
    if(!response.ok) throw Error(response.status===422?'نام کاربری و طول رمز عبور را بررسی کنید.':typeof data.detail==='string'?data.detail:'ثبت‌نام انجام نشد؛ دوباره تلاش کنید.');
    form.reset(); form.hidden=true; message.classList.add('success'); message.textContent=data.message;
  } catch(error) { message.textContent=error.message; }
  finally { button.disabled=false; }
});
