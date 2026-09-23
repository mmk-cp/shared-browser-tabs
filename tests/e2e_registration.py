"""Registration/approval/password/deletion QA, using temporary accounts only."""
import asyncio
import secrets
from pathlib import Path

import httpx
import websockets
from playwright.async_api import async_playwright

from app.db import SessionLocal
from app.models import User, BrowserTab
from app.services.auth_service import hash_password, SESSION_COOKIE
from tests.e2e_accounts import login, expect_revoked

BASE='http://127.0.0.1:8000'


async def main():
    suffix=secrets.token_hex(5)
    admin_name, member_name, pending_name=[f'qa_signup_{suffix}_{role}' for role in ('admin','member','pending')]
    names=[admin_name, member_name, pending_name]
    password, new_password=secrets.token_urlsafe(20), secrets.token_urlsafe(20)
    with SessionLocal() as db:
        db.add(User(username=admin_name,password_hash=hash_password(password),is_admin=True))
        db.commit()
    admin, member, public=[httpx.AsyncClient(base_url=BASE,timeout=30) for _ in range(3)]
    socket=None
    artifacts=Path('/tmp/sbt-qa'); artifacts.mkdir(exist_ok=True)
    try:
        await login(admin,admin_name,password)
        admin_id=(await admin.get('/api/auth/me')).json()['id']
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            signup_context=await browser.new_context(viewport={'width':390,'height':844})
            signup=await signup_context.new_page()
            await signup.goto(BASE+'/login')
            await signup.get_by_role('link',name='ثبت‌نام',exact=True).click()
            assert await signup.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await signup.locator('#username').fill(member_name)
            await signup.locator('#password').fill(password)
            await signup.locator('#confirm-password').fill('wrong-confirmation')
            await signup.locator('button[type=submit]').click()
            await signup.wait_for_function("document.querySelector('#register-message').textContent.includes('یکسان نیست')")
            await signup.locator('#confirm-password').fill(password)
            await signup.screenshot(path=str(artifacts/'register-mobile.png'),full_page=True)
            await signup.locator('button[type=submit]').click()
            await signup.wait_for_selector('#register-message.success')
            assert not any(c['name']==SESSION_COOKIE for c in await signup_context.cookies())
            for extra in ({'is_admin':True},{'is_active':True},{'session_id':'forged'}):
                assert (await public.post('/api/auth/register',json={'username':pending_name,'password':password,**extra})).status_code==422
            assert (await public.post('/api/auth/register',json={'username':member_name,'password':password})).status_code==409
            assert (await public.post('/api/auth/register',json={'username':'  ','password':password})).status_code==422
            assert (await public.post('/api/auth/register',json={'username':pending_name,'password':'ف'*40})).status_code==422
            assert (await public.post('/api/auth/register',headers={'Origin':'https://untrusted.example'},json={'username':pending_name,'password':password})).status_code==403
            assert (await member.post('/api/auth/login',json={'username':member_name,'password':'wrong'})).status_code==401
            assert (await member.post('/api/auth/login',json={'username':member_name,'password':password})).status_code==403
            assert (await member.get('/api/tabs/me')).status_code==401
            with SessionLocal() as db:
                user=db.query(User).filter(User.username==member_name).one()
                member_id=user.id
                assert not user.is_admin and not user.is_active and user.session_id is None and user.browser_tab is None
            await signup.goto(BASE+'/login')
            await signup.locator('#username').fill(member_name)
            await signup.locator('#password').fill(password)
            await signup.locator('button[type=submit]').click()
            await signup.wait_for_function("document.querySelector('#error').textContent.includes('تأیید نشده')")
            print('PASS public signup, pending login blocked, validation and privilege escalation denied',flush=True)

            admin_context=await browser.new_context(viewport={'width':1280,'height':900})
            await admin_context.add_cookies([{'name':k,'value':v,'url':BASE} for k,v in admin.cookies.items()])
            panel=await admin_context.new_page()
            await panel.goto(BASE+'/admin')
            row=panel.locator(f'li[data-user-id="{member_id}"]')
            await row.locator('.approve-user').wait_for()
            assert (await member.post(f'/api/users/{member_id}/approve')).status_code==401
            assert (await admin.post(f'/api/users/{member_id}/approve',headers={'X-CSRF-Token':'bad'})).status_code==403
            await panel.screenshot(path=str(artifacts/'admin-pending.png'))
            await row.locator('.approve-user').click()
            await row.locator('.user-badge.active').wait_for()
            await signup.locator('button[type=submit]').click()
            await signup.wait_for_url('**/dashboard')
            await signup.wait_for_selector('#online-dot.connected',timeout=20000)
            assert await signup.locator('.account-link').count()==1
            for cookie in await signup_context.cookies(): member.cookies.set(cookie['name'],cookie['value'])
            member.headers['X-CSRF-Token']=member.cookies['shared_browser_csrf']
            assert (await member.post(f'/api/users/{member_id}/approve')).status_code==403
            assert (await member.delete(f'/api/users/{admin_id}')).status_code==403
            assert (await admin.delete(f'/api/users/{admin_id}')).status_code==400
            print('PASS admin approval enables login; regular-user administration denied',flush=True)

            account=await signup_context.new_page()
            await account.goto(BASE+'/account')
            payload={'current_password':password,'new_password':new_password}
            assert (await member.post('/api/auth/password',headers={'X-CSRF-Token':'bad'},json=payload)).status_code==403
            assert (await member.post('/api/auth/password',json={**payload,'new_password':'short'})).status_code==422
            assert (await member.post('/api/auth/password',json={**payload,'new_password':password})).status_code==400
            await account.locator('#current-password').fill('wrong-current')
            await account.locator('#new-password').fill(new_password)
            await account.locator('#confirm-password').fill(new_password)
            await account.locator('button[type=submit]').click()
            await account.wait_for_function("document.querySelector('#password-message').textContent.includes('فعلی صحیح نیست')")
            assert (await member.get('/api/auth/me')).status_code==200
            await account.locator('#current-password').fill(password)
            await account.screenshot(path=str(artifacts/'change-password-mobile.png'),full_page=True)
            await account.locator('button[type=submit]').click()
            await account.wait_for_url('**/login?reason=password-changed')
            await signup.wait_for_url('**/login?reason=session-ended',timeout=7000)
            assert (await member.get('/api/auth/me')).status_code==401
            assert (await public.post('/api/auth/login',json={'username':member_name,'password':password})).status_code==401
            member.cookies.clear()
            await login(member,member_name,new_password)
            assert (await member.get('/api/auth/me')).status_code==200
            print('PASS current-password check, password change, old-password rejection and session revocation',flush=True)

            # Delete a pending registration without ever creating a browser tab.
            (await public.post('/api/auth/register',json={'username':pending_name,'password':password})).raise_for_status()
            pending_id=next(u['id'] for u in (await admin.get('/api/users')).json() if u['username']==pending_name)
            await panel.click('#refresh-users')
            await panel.set_viewport_size({'width':390,'height':844})
            pending_row=panel.locator(f'li[data-user-id="{pending_id}"]')
            await pending_row.locator('.delete-user').wait_for()
            assert await panel.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await panel.screenshot(path=str(artifacts/'admin-pending-mobile.png'),full_page=True)
            panel.once('dialog',lambda dialog:dialog.accept())
            await pending_row.locator('.delete-user').click()
            await pending_row.wait_for(state='detached')
            assert (await public.post('/api/auth/login',json={'username':pending_name,'password':password})).status_code==401
            print('PASS pending-user deletion and mobile admin layout',flush=True)

            # Deleting an active account also closes its native window and
            # already-open input stream without touching shared browser data.
            await signup_context.add_cookies([{'name':k,'value':v,'url':BASE} for k,v in member.cookies.items()])
            await signup.goto(BASE+'/dashboard')
            await signup.wait_for_selector('#online-dot.connected',timeout=20000)
            socket=await websockets.connect('ws://127.0.0.1:8000/ws/input',origin=BASE,additional_headers={'Cookie':f'{SESSION_COOKIE}={member.cookies[SESSION_COOKIE]}'})
            with SessionLocal() as db:
                marker='shared-tab-'+db.query(BrowserTab).filter(BrowserTab.user_id==member_id).one().page_id
            assert (await admin.delete(f'/api/users/{member_id}',headers={'X-CSRF-Token':'bad'})).status_code==403
            panel.once('dialog',lambda dialog:dialog.dismiss())
            await row.locator('.delete-user').click()
            assert (await member.get('/api/auth/me')).status_code==200
            panel.once('dialog',lambda dialog:dialog.accept())
            await row.locator('.delete-user').click()
            await row.wait_for(state='detached')
            await expect_revoked(socket)
            await signup.wait_for_url('**/login?reason=session-ended',timeout=7000)
            assert (await member.get('/api/auth/me')).status_code==401
            assert (await public.post('/api/auth/login',json={'username':member_name,'password':new_password})).status_code==401
            targets=(await admin.get('http://127.0.0.1:9222/json')).json()
            assert not any(t.get('title')==marker for t in targets)
            with SessionLocal() as db:
                assert db.get(User,member_id) is None
                assert db.query(BrowserTab).filter(BrowserTab.user_id==member_id).first() is None
            print('PASS confirmed deletion revokes account/streams and removes only its tab',flush=True)
            await browser.close()
    finally:
        if socket: await socket.close()
        for name in (member_name,pending_name):
            with SessionLocal() as db:
                user=db.query(User).filter(User.username==name).first()
                target_id=user.id if user else None
            if target_id: await admin.delete(f'/api/users/{target_id}')
        for client in (admin,member,public): await client.aclose()
        with SessionLocal() as db:
            for user in db.query(User).filter(User.username.in_(names)).all():
                db.query(BrowserTab).filter(BrowserTab.user_id==user.id).delete()
                db.delete(user)
            db.commit()


if __name__=='__main__':
    asyncio.run(main())
