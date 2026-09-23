"""Catalog permissions and responsive admin/member UX; disposable QA data only."""
import asyncio
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

from app.db import SessionLocal
from app.models import User, BrowserTab, Site
from app.services.auth_service import hash_password
from tests.e2e_accounts import login

BASE='http://127.0.0.1:8000'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers()
        self.wfile.write('<!doctype html><meta charset=utf-8><h1>سایت آزمایشی</h1><a href=/next>Normal page link</a>'.encode())
    def log_message(self,*args): pass


async def main():
    fixture=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=fixture.serve_forever,daemon=True).start()
    suffix=secrets.token_hex(5); names=[f'qa_sites_{suffix}_{role}' for role in ('admin','member')]
    password=secrets.token_urlsafe(20); ids=[]
    url=f'http://127.0.0.1:{fixture.server_port}/start'
    title=f'دستیار آزمایشی {suffix}'
    with SessionLocal() as db:
        db.add_all([User(username=n,password_hash=hash_password(password),is_admin=i==0) for i,n in enumerate(names)]); db.commit()
    admin,member,public=[httpx.AsyncClient(base_url=BASE,timeout=30) for _ in range(3)]
    try:
        await login(admin,names[0],password); await login(member,names[1],password)
        assert (await public.get('/api/sites')).status_code==401
        assert (await member.get('/api/sites/manage')).status_code==403
        assert (await member.get('/admin/sites')).status_code==403
        assert (await member.post('/api/sites',json={'title':title,'url':url})).status_code==403
        assert (await member.post('/api/tabs/me/navigate',json={'url':url})).status_code==403
        assert (await admin.post('/api/sites',headers={'X-CSRF-Token':''},json={'title':title,'url':url})).status_code==403
        for bad in ['javascript:alert(1)','file:///etc/passwd','https://user:pass@example.com']:
            assert (await admin.post('/api/sites',json={'title':title,'url':bad})).status_code==422
        print('PASS login/admin/CSRF enforcement and URL validation',flush=True)
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            pages=[]; errors=[]
            for client in (admin,member):
                context=await browser.new_context(viewport={'width':1280,'height':900})
                await context.add_cookies([{'name':k,'value':v,'url':BASE} for k,v in client.cookies.items()])
                page=await context.new_page(); page.on('pageerror',lambda e:errors.append(str(e))); pages.append(page)
            manage,view=pages
            await view.goto(BASE+'/dashboard')
            await view.wait_for_selector('#online-dot.connected',timeout=20000)
            await view.wait_for_selector('#url-panel.open')
            assert await view.locator('#url-input').count()==0
            assert await view.locator('#manual-url-form').count()==0
            if not (await member.get('/api/sites')).json():
                await view.wait_for_selector('#sites-empty:visible')
            await manage.goto(BASE+'/admin/sites')
            await manage.locator('#site-title').fill(title)
            await manage.locator('#site-url').fill(url)
            await manage.get_by_role('button',name='انتخاب آیکون 🤖',exact=True).click()
            assert await manage.locator('#site-preview .site-icon').inner_text()=='🤖'
            async with manage.expect_response(lambda r:r.url==BASE+'/api/sites' and r.request.method=='POST') as created:
                await manage.click('#save-site')
            site=(await (await created.value).json()); ids.append(site['id'])
            await manage.wait_for_selector('#site-form-message.success')
            await manage.locator(f'li[data-site-id="{site["id"]}"]').wait_for()
            print('PASS admin form, emoji picker, live preview and persisted catalog',flush=True)
            for payload in [{'title':f'کتابخانه {suffix}','url':url.replace('/start','/library')},
                            {'title':f'پنهان {suffix}','url':url,'is_active':False}]:
                response=await admin.post('/api/sites',json=payload); response.raise_for_status(); ids.append(response.json()['id'])
            assert ids[2] not in [s['id'] for s in (await member.get('/api/sites')).json()]
            for method in ('put','delete'):
                options={'json':{'title':title,'url':url}} if method=='put' else {}
                assert (await getattr(member,method)(f'/api/sites/{ids[0]}',**options)).status_code==403
            assert (await member.post('/api/tabs/me/open-site',json={'site_id':ids[0],'url':'https://example.com'})).status_code==422
            assert (await member.post('/api/tabs/me/open-site',json={'site_id':ids[2]})).status_code==404
            assert (await member.post('/api/tabs/me/open-site',headers={'X-CSRF-Token':''},json={'site_id':ids[0]})).status_code==403
            await view.click('#refresh-sites')
            await view.locator('#site-search').fill(suffix)
            await view.locator(f'#site-grid [data-site-id="{ids[0]}"]').wait_for()
            assert await view.locator('#site-grid button').count()==2
            assert await view.locator(f'#site-grid [data-site-id="{ids[1]}"] .site-icon').inner_text()=='ک'
            artifacts=Path('/tmp/sbt-qa'); artifacts.mkdir(exist_ok=True)
            await view.screenshot(path=str(artifacts/'site-picker-desktop.png'))
            await view.locator('#site-search').fill('no-match-'+suffix)
            await view.wait_for_selector('#sites-empty:visible')
            assert await view.locator('#site-grid button').count()==0
            await view.locator('#site-search').fill(suffix)
            await view.set_viewport_size({'width':390,'height':844})
            assert await view.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await view.screenshot(path=str(artifacts/'site-picker-mobile.png'))
            async with view.expect_response(lambda r:r.url.endswith('/api/tabs/me/open-site')) as opened:
                await view.locator(f'#site-grid [data-site-id="{ids[0]}"]').click()
            assert (await opened.value).status==200
            await view.wait_for_selector('#url-panel.open',state='hidden')
            assert (await member.get('/api/tabs/me')).json()['url']==url
            print('PASS member search/cards/mobile navigation, fallback icon and server-resolved selection',flush=True)

            # Ctrl+L must open the picker, never an arbitrary URL field.
            await view.locator('#vnc-screen').focus(); await view.keyboard.press('Control+l')
            await view.wait_for_selector('#url-panel.open')
            assert await view.locator('#url-input').count()==0
            # A stale visible card cannot open a site after the admin hides it.
            row=manage.locator(f'li[data-site-id="{ids[0]}"]')
            await row.locator('.edit-site').click()
            assert await manage.locator('#site-title').input_value()==title
            await manage.locator('#site-title').fill(title+' جدید')
            await manage.locator('#site-active').uncheck()
            await manage.click('#save-site'); await manage.wait_for_selector('#site-form-message.success')
            async with view.expect_response(lambda r:r.url.endswith('/api/tabs/me/open-site')) as stale:
                await view.locator(f'#site-grid [data-site-id="{ids[0]}"]').click()
            assert (await stale.value).status==404
            await view.wait_for_function("document.querySelector('#site-message').textContent.includes('غیرفعال')")
            assert (await member.get('/api/tabs/me')).json()['url']==url
            print('PASS edit/hide, stale-card rejection and unchanged current tab',flush=True)

            # Reactivate with a new URL: current server data wins over any old card.
            updated_url=url.replace('/start','/updated')
            response=await admin.put(f'/api/sites/{ids[0]}',json={'title':title+' جدید','url':updated_url,'icon':'📚','is_active':True})
            response.raise_for_status()
            assert (await member.post('/api/tabs/me/open-site',json={'site_id':ids[0]})).json()['url']==updated_url
            # Render hostile-looking text as text, never HTML.
            response=await admin.post('/api/sites',json={'title':'<img src=x onerror=alert(1)>','url':url,'icon':'<b>'})
            response.raise_for_status(); ids.append(response.json()['id'])
            await view.locator('#site-search').fill(''); await view.click('#refresh-sites')
            await view.locator(f'#site-grid [data-site-id="{ids[-1]}"]').wait_for()
            assert await view.locator('#site-grid img, #site-grid b').count()==0
            await admin.delete(f'/api/sites/{ids[-1]}')

            await manage.click('#reload-site-list')
            await manage.set_viewport_size({'width':390,'height':844})
            assert await manage.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await manage.screenshot(path=str(artifacts/'site-management-mobile.png'),full_page=True)
            manage.on('dialog',lambda dialog:dialog.accept())
            await manage.locator(f'li[data-site-id="{ids[1]}"] .delete-site').click()
            await manage.locator(f'li[data-site-id="{ids[1]}"]').wait_for(state='detached')
            assert (await member.post('/api/tabs/me/open-site',json={'site_id':ids[1]})).status_code==404
            await manage.goto(BASE+'/dashboard')
            await manage.wait_for_selector('#online-dot.connected',timeout=20000)
            await manage.wait_for_selector('#url-panel.open')
            await manage.locator('#url-input').fill(url)
            async with manage.expect_response(lambda r:r.url.endswith('/api/tabs/me/navigate')) as manual:
                await manage.locator('#manual-url-form button').click()
            assert (await manual.value).status==200
            assert not errors, errors
            print('PASS deletion, updated destination, safe text rendering, mobile admin and admin-only manual URL',flush=True)
            await browser.close()
    finally:
        for client in (admin,member):
            await client.delete('/api/tabs/me'); await client.aclose()
        await public.aclose()
        with SessionLocal() as db:
            db.query(Site).filter(Site.id.in_(ids)).delete(synchronize_session=False)
            for user in db.query(User).filter(User.username.in_(names)).all():
                db.query(BrowserTab).filter(BrowserTab.user_id==user.id).delete(); db.delete(user)
            db.commit()
        fixture.shutdown()


if __name__=='__main__': asyncio.run(main())
