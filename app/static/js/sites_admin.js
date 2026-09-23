import {siteCard, iconChoices} from './sites.js';
const $ = id => document.getElementById(id);
let sites = [], editing = null, saving = false, leaving = false;
const csrf = () => decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('shared_browser_csrf='))?.split('=')[1] || '');
async function api(path, options={}) {
  const response = await fetch(path,{cache:'no-store',...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()}});
  if (response.status===401 || response.status===403) {
    leaving=true; document.querySelector('.admin-shell').hidden=true;
    location.replace(response.status===401 ? '/login?reason=session-ended' : '/dashboard');
    throw Error('دسترسی شما پایان یافته است.');
  }
  const data = await response.json().catch(()=>({}));
  if (!response.ok) throw Error(typeof data.detail==='string' ? data.detail : 'عنوان، آدرس کامل سایت و طول آیکون را بررسی کنید.');
  return data;
}
function preview() {
  $('site-preview').replaceChildren(siteCard({title:$('site-title').value || 'عنوان سایت',url:$('site-url').value,icon:$('site-icon').value}));
  for (const button of $('icon-choices').children) button.setAttribute('aria-pressed',String(button.textContent===$('site-icon').value));
}
for (const icon of iconChoices) {
  const button=document.createElement('button'); button.type='button'; button.textContent=icon;
  button.setAttribute('aria-label',`انتخاب آیکون ${icon}`);
  button.onclick=()=>{ $('site-icon').value=icon; preview(); }; $('icon-choices').append(button);
}
for (const id of ['site-title','site-url','site-icon']) $(id).addEventListener('input',preview);
function reset() {
  editing=null; $('site-form').reset(); $('cancel-site-edit').hidden=true;
  $('site-form-heading').textContent='افزودن سایت'; $('save-site').textContent='افزودن سایت'; preview();
}
function edit(site) {
  if (saving) return;
  editing=site.id; $('site-title').value=site.title; $('site-url').value=site.url;
  $('site-icon').value=site.icon; $('site-active').checked=site.is_active;
  $('site-form-heading').textContent='ویرایش سایت'; $('save-site').textContent='ذخیرهٔ تغییرات';
  $('cancel-site-edit').hidden=false; $('site-form-message').textContent=''; preview();
  $('site-form-heading').scrollIntoView({behavior:'smooth',block:'start'}); $('site-title').focus({preventScroll:true});
}
function render() {
  const query=$('manage-site-search').value.trim().toLocaleLowerCase();
  const matches=sites.filter(site=>`${site.title} ${site.url}`.toLocaleLowerCase().includes(query));
  $('site-total').textContent=sites.length.toLocaleString('fa-IR');
  $('manage-sites').replaceChildren(...matches.map(site=>{
    const row=document.createElement('li'); row.dataset.siteId=site.id;
    const card=siteCard(site); const url=document.createElement('small'); url.className='managed-site-url'; url.dir='ltr'; url.textContent=site.url;
    const state=document.createElement('span'); state.className=`user-badge ${site.is_active?'active':'pending'}`; state.textContent=site.is_active?'نمایش به کاربران':'پنهان از فهرست';
    const actions=document.createElement('div'); actions.className='panel-actions';
    const change=document.createElement('button'); change.className='secondary-button edit-site'; change.textContent='ویرایش'; change.disabled=saving; change.onclick=()=>edit(site);
    const remove=document.createElement('button'); remove.className='secondary-button delete-user delete-site'; remove.textContent='حذف'; remove.disabled=saving;
    remove.onclick=async()=>{
      if (!confirm(`«${site.title}» از فهرست حذف شود؟ تب‌های باز و کوکی‌های مرورگر پاک نمی‌شوند.`)) return;
      remove.disabled=true;
      try { await api(`/api/sites/${site.id}`,{method:'DELETE'}); if(editing===site.id) reset(); await load(); }
      catch(error){ $('site-list-message').textContent=error.message; remove.disabled=false; }
    };
    actions.append(state,change,remove); row.append(card,url,actions); return row;
  }));
  $('site-list-message').textContent=matches.length ? '' : sites.length ? 'نتیجه‌ای پیدا نشد.' : 'اولین سایت را از فرم کنار این بخش اضافه کنید.';
}
async function load() {
  $('reload-site-list').disabled=true; $('site-list-message').textContent='در حال دریافت سایت‌ها…';
  try { sites=await api('/api/sites/manage'); render(); }
  catch(error){ $('site-list-message').textContent=error.message; }
  finally { $('reload-site-list').disabled=false; }
}
$('site-form').onsubmit=async event=>{
  event.preventDefault(); if(saving) return;
  const payload={title:$('site-title').value.trim(),url:$('site-url').value.trim(),icon:$('site-icon').value.trim(),is_active:$('site-active').checked};
  if(!payload.title){ $('site-form-message').textContent='عنوان سایت را وارد کنید.'; return; }
  saving=true; $('save-site').disabled=true; $('cancel-site-edit').disabled=true; render();
  $('site-form-message').classList.remove('success'); $('site-form-message').textContent='در حال ذخیره…';
  try {
    await api(editing===null?'/api/sites':`/api/sites/${editing}`,{method:editing===null?'POST':'PUT',body:JSON.stringify(payload)});
    reset(); $('site-form-message').classList.add('success'); $('site-form-message').textContent='سایت ذخیره شد؛ فهرست کاربران با باز کردن انتخاب‌گر تازه می‌شود.';
    await load();
  } catch(error){ $('site-form-message').textContent=error.message; }
  finally{ saving=false; $('save-site').disabled=false; $('cancel-site-edit').disabled=false; render(); }
};
$('cancel-site-edit').onclick=()=>{ reset(); $('site-form-message').textContent=''; };
$('reload-site-list').onclick=load; $('manage-site-search').oninput=render;
async function checkSession(){ if(!leaving) await api('/api/auth/me').catch(()=>{}); }
setInterval(checkSession,2000); window.addEventListener('focus',checkSession);
preview(); load();
