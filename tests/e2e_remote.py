"""Run inside the app container: python -m tests.e2e_remote.

Temporary users and a loopback fixture avoid changing the user's browser tab
or sending test text to third-party websites. Screenshots go to /tmp/sbt-qa.
"""
import asyncio
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import websockets
from playwright.async_api import async_playwright

from app.db import SessionLocal
from app.models import User, BrowserTab
from app.services.auth_service import hash_password

FIXTURE = """<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Remote browser QA</title><style>*{box-sizing:border-box}body{margin:0;background:#f6f8fc;font:18px 'Noto Sans Arabic',sans-serif;padding:22px;color:#12243a}
h1{font-size:25px}input,textarea{width:100%;font:20px 'Noto Sans Arabic',sans-serif;padding:12px;margin:12px 0;border:1px solid #8fa2bc;border-radius:8px}section{height:1200px}button{padding:15px}</style>
<h1>سلام دنیا · Shared browser</h1><p id=copy>متن فارسی برای کپی — English 123</p><input id=editor placeholder='فارسی / English'><textarea id=notes></textarea>
<a href='/next'>پیوند آزمایشی</a><button id=click onclick="this.textContent='Clicked'">Click / لمس</button><section>Scroll / پیمایش</section>"""

class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(FIXTURE.encode())

    def log_message(self, *args):
        pass


class Remote:
    """Inspect only the QA target, never attach to the user's other pages.

    An unrelated page with a pending document can block Playwright's global
    connect_over_cdp initialization. A target-specific CDP connection also
    avoids disturbing the user's existing inspector sessions.
    """
    def __init__(self, socket):
        self.socket = socket
        self.request_id = 0

    async def evaluate(self, expression):
        self.request_id += 1
        expression = expression.strip()
        if expression.startswith('() =>'):
            expression = f'({expression})()'
        await self.socket.send(json.dumps({'id':self.request_id, 'method':'Runtime.evaluate',
            'params':{'expression':expression,'returnByValue':True,'awaitPromise':True}}))
        while True:
            response = json.loads(await asyncio.wait_for(self.socket.recv(), 8))
            if response.get('id') == self.request_id:
                assert 'error' not in response, response
                assert 'exceptionDetails' not in response['result'], response
                return response['result']['result'].get('value')

    async def box(self, selector):
        return await self.evaluate(f"(() => {{const r=document.querySelector({json.dumps(selector)}).getBoundingClientRect();return {{x:r.x,y:r.y,width:r.width,height:r.height}}}})()")

    async def value(self, selector):
        return await self.evaluate(f'document.querySelector({json.dumps(selector)}).value')

    async def wait_for_function(self, expression, timeout=10000):
        end = asyncio.get_running_loop().time() + timeout/1000
        while asyncio.get_running_loop().time() < end:
            if await self.evaluate(expression):
                return
            await asyncio.sleep(.05)
        raise AssertionError(f'Timed out: {expression}')


async def main():
    fixture = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    fixture_base = f'http://127.0.0.1:{fixture.server_port}'
    suffix = secrets.token_hex(5)
    names = [f'qa_{suffix}_{i}' for i in range(2)]
    password = secrets.token_urlsafe(20)
    with SessionLocal() as db:
        db.add_all([User(username=n, password_hash=hash_password(password)) for n in names])
        db.commit()
    clients = []
    inspect_sockets = []
    artifacts = Path('/tmp/sbt-qa')
    artifacts.mkdir(exist_ok=True)
    try:
        async with async_playwright() as pw:
            test_browser = await pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
            views, remotes = [], []
            for i, name in enumerate(names):
                client = httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=30)
                clients.append(client)
                response = await client.post('/api/auth/login', json={'username':name, 'password':password})
                response.raise_for_status()
                client.headers['X-CSRF-Token'] = client.cookies['shared_browser_csrf']
                (await client.get('/api/tabs/me')).raise_for_status()
                url = f'{fixture_base}/user-{i}'
                (await client.post('/api/tabs/me/navigate', json={'url':url})).raise_for_status()
                context = await test_browser.new_context(viewport={'width':1366, 'height':900}, permissions=['clipboard-read','clipboard-write'])
                await context.add_cookies([{'name':k,'value':v,'url':'http://127.0.0.1:8000'} for k,v in client.cookies.items()])
                view = await context.new_page()
                view.on('pageerror', lambda e: print('JS_ERROR', str(e), flush=True))
                await view.goto('http://127.0.0.1:8000/dashboard')
                await view.wait_for_selector('#online-dot.connected', timeout=20000)
                await view.wait_for_function("() => {const c=document.querySelector('#vnc-screen canvas'); return c?.width>500 && c.getContext('2d').getImageData(50,50,1,1).data[3]===255}", timeout=20000)
                # noVNC sets an inline cursor:none when the stream does not
                # supply cursor shapes. The local pointer must stay visible.
                await view.locator('#vnc-screen canvas').hover()
                assert await view.locator('#vnc-screen canvas').evaluate("el => getComputedStyle(el).cursor") == 'default'
                targets = (await client.get('http://127.0.0.1:9222/json')).json()
                target = next(t for t in targets if t.get('url') == url)
                socket = await websockets.connect(target['webSocketDebuggerUrl'])
                inspect_sockets.append(socket)
                remote = Remote(socket)
                views.append(view); remotes.append(remote)

            async def click_remote(view, remote, selector, button='left'):
                box = await remote.box(selector)
                cv = await view.locator('#vnc-screen canvas').bounding_box()
                size = await remote.evaluate('({w:innerWidth,h:innerHeight})')
                await view.mouse.click(cv['x']+(box['x']+box['width']/2)*cv['width']/size['w'],
                                       cv['y']+(box['y']+box['height']/2)*cv['height']/size['h'], button=button)

            print('PASS visible local mouse cursor on both viewers', flush=True)
            for i in range(2):
                await click_remote(views[i], remotes[i], '#editor')
            await asyncio.gather(views[0].keyboard.type('Alpha 123'), views[1].keyboard.type('Beta 456'))
            await remotes[0].wait_for_function("document.querySelector('#editor').value==='Alpha 123'")
            await remotes[1].wait_for_function("document.querySelector('#editor').value==='Beta 456'")
            print('PASS simultaneous input isolation', flush=True)
            await views[0].keyboard.press('Control+a')
            for char in 'سلام فارسی English 123':
                await views[0].locator('#vnc-screen').dispatch_event('keydown', {'key':char,'bubbles':True})
            await remotes[0].wait_for_function("document.querySelector('#editor').value==='سلام فارسی English 123'")
            await views[0].keyboard.press('Control+a')
            await views[0].keyboard.press('Control+c')
            await views[0].wait_for_function("navigator.clipboard.readText().then(t=>t==='سلام فارسی English 123')")
            print('PASS Persian typing and copy to local clipboard', flush=True)
            await views[0].evaluate("navigator.clipboard.writeText('چسباندن فارسی + Paste')")
            await views[0].keyboard.press('Control+v')
            await remotes[0].wait_for_function("document.querySelector('#editor').value==='چسباندن فارسی + Paste'")
            print('PASS Unicode paste', flush=True)
            await click_remote(views[0], remotes[0], '#editor', 'right')
            await views[0].wait_for_selector('#context-menu:not([hidden])')
            await views[0].screenshot(path=str(artifacts/'desktop-context.png'))
            assert await remotes[1].value('#editor') == 'Beta 456'
            print('PASS right click menu / second user unchanged', flush=True)
            await views[0].keyboard.press('Escape')
            await views[0].click('#maximize')
            await views[0].wait_for_function("document.querySelector('#vnc-screen').clientWidth===innerWidth")
            await views[0].click('#restore-tools')
            print('PASS in-page maximize', flush=True)
            # Full HD must be actual remote/canvas pixels, not a stretched
            # lower-resolution image. Exercise input beyond the old 1800 cap.
            await views[0].set_viewport_size({'width':1920,'height':1080})
            await views[0].click('#maximize')
            await remotes[0].wait_for_function('innerWidth === 1920 && innerHeight === 1080')
            await views[0].wait_for_function("() => {const c=document.querySelector('#vnc-screen canvas');return c?.width===1920 && c.height===1080 && c.getBoundingClientRect().width===1920}")
            await remotes[0].evaluate("""() => {
                const button = document.createElement('button'); button.id = 'fhd-edge';
                button.style.cssText = 'position:fixed;right:8px;bottom:8px;width:64px;height:40px;background:rgb(0,200,100);border:0';
                button.onclick = () => button.dataset.clicked = 'yes';
                document.body.append(button);
            }""")
            await views[0].wait_for_function("() => {const c=document.querySelector('#vnc-screen canvas'); const p=c.getContext('2d').getImageData(1880,1052,1,1).data; return p[0]<40 && p[1]>150 && p[2]<140}")
            await click_remote(views[0], remotes[0], '#fhd-edge')
            await remotes[0].wait_for_function("document.querySelector('#fhd-edge').dataset.clicked==='yes'")
            await views[0].screenshot(path=str(artifacts/'full-hd.png'))
            await remotes[0].evaluate("document.querySelector('#fhd-edge').remove()")
            await views[0].click('#restore-tools')
            await remotes[0].wait_for_function('innerWidth === 1874 && innerHeight === 1080')
            print('PASS native 1920x1080 framebuffer and right-edge mouse input', flush=True)
            # Mobile viewport and touch session.
            mobile_context = await test_browser.new_context(viewport={'width':390,'height':844}, is_mobile=True, has_touch=True, device_scale_factor=2)
            await mobile_context.add_cookies(await views[0].context.cookies())
            await views[0].close()
            mobile = await mobile_context.new_page()
            await mobile.goto('http://127.0.0.1:8000/dashboard')
            await mobile.wait_for_selector('#online-dot.connected',timeout=20000)
            await remotes[0].wait_for_function('innerWidth <= 390', timeout=15000)
            assert await mobile.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await mobile.wait_for_function("() => document.querySelector('#vnc-screen canvas')?.width <= 390",timeout=15000)
            box=await remotes[0].box('#editor'); cv=await mobile.locator('#vnc-screen canvas').bounding_box()
            size=await remotes[0].evaluate('({w:innerWidth,h:innerHeight})')
            await mobile.touchscreen.tap(cv['x']+(box['x']+30)*cv['width']/size['w'],cv['y']+(box['y']+20)*cv['height']/size['h'])
            await mobile.click('#keyboard-toggle')
            await mobile.locator('#mobile-input').fill(' گوشی Mobile')
            await remotes[0].wait_for_function("document.querySelector('#editor').value.includes('گوشی Mobile')")
            await mobile.screenshot(path=str(artifacts/'mobile.png'))
            print('PASS responsive phone, touch and Unicode mobile input', flush=True)
            # Shared context cookie and rejection of unauthenticated input.
            await remotes[0].evaluate("document.cookie='sbt_qa=shared;path=/'")
            assert 'sbt_qa=shared' in await remotes[1].evaluate('document.cookie')
            await remotes[0].evaluate("document.cookie='sbt_qa=;Max-Age=0;path=/'")
            print('PASS shared cookie session', flush=True)
            await test_browser.close()
    finally:
        for socket in inspect_sockets:
            await socket.close()
        for client in clients:
            try:
                await client.delete('/api/tabs/me')
            finally:
                await client.aclose()
        with SessionLocal() as db:
            for user in db.query(User).filter(User.username.in_(names)).all():
                db.query(BrowserTab).filter(BrowserTab.user_id==user.id).delete()
                db.delete(user)
            db.commit()
        fixture.shutdown()

if __name__ == '__main__':
    asyncio.run(main())
