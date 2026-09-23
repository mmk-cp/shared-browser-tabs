"""Site Copy buttons -> local clipboard; only temporary QA users/sites."""
import asyncio
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
from tests.e2e_remote import Remote, navigate_fixture

HTML = '''<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<style>body{font:18px sans-serif;padding:20px}button{display:block;margin:12px;padding:12px}iframe{width:400px;height:100px}</style>
<h2>Clipboard QA / کپی</h2>
<button id=modern onclick="navigator.clipboard.writeText('متن دکمه سایت — English 42').then(()=>this.dataset.done='yes')">Copy text</button>
<button id=rich onclick="navigator.clipboard.write([new ClipboardItem({'text/plain':new Blob(['متن ClipboardItem'],{type:'text/plain'})})]).then(()=>this.dataset.done='yes')">Copy item</button>
<button id=html onclick="navigator.clipboard.write([new ClipboardItem({'text/html':new Blob(['<b>متن HTML</b>'],{type:'text/html'})})]).then(()=>this.dataset.done='yes')">Copy HTML as text</button>
<button id=legacy onclick="legacyCopy()">Legacy copy</button>
<button id=custom onclick="customCopy()">Custom copy handler</button>
<iframe id=frame src='/clipboard-frame'></iframe>
<script>
function legacyCopy(){const el=document.createElement('textarea');el.value='متن کپی قدیمی';document.body.append(el);el.select();document.execCommand('copy');el.remove()}
function customCopy(){document.addEventListener('copy',event=>{event.clipboardData.setData('text/plain','متن رویداد کپی');event.preventDefault()},{once:true});document.execCommand('copy')}
</script>'''
FRAME = '''<!doctype html><meta charset=utf-8><button id=frame-copy onclick="navigator.clipboard.writeText('متن داخل قاب')">Copy in frame</button>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.end_headers()
        content = FRAME if self.path == '/clipboard-frame' else '<textarea id="outside"></textarea>' if self.path == '/outside' else HTML
        # Different hostname: exercise an actual cross-origin child frame.
        content = content.replace("src='/clipboard-frame'", f"src='http://localhost:{self.server.server_port}/clipboard-frame'")
        self.wfile.write(content.encode())

    def log_message(self, *args):
        pass


async def main():
    fixture=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=fixture.serve_forever,daemon=True).start()
    names=['qa_copy_'+secrets.token_hex(6) for _ in range(2)]
    password=secrets.token_urlsafe(20)
    with SessionLocal() as db:
        db.add_all([User(username=name,password_hash=hash_password(password)) for name in names])
        db.commit()
    clients, sockets=[], []
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            views, remotes=[], []
            for name in names:
                client=httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=30)
                clients.append(client)
                (await client.post('/api/auth/login',json={'username':name,'password':password})).raise_for_status()
                client.headers['X-CSRF-Token']=client.cookies['shared_browser_csrf']
                (await client.get('/api/tabs/me')).raise_for_status()
                url=f'http://127.0.0.1:{fixture.server_port}/{name}'
                await navigate_fixture(client, url)
                context=await browser.new_context(viewport={'width':1200,'height':900},permissions=['clipboard-read','clipboard-write'])
                await context.add_cookies([{'name':k,'value':v,'url':'http://127.0.0.1:8000'} for k,v in client.cookies.items()])
                view=await context.new_page()
                await view.goto('http://127.0.0.1:8000/dashboard')
                await view.wait_for_selector('#online-dot.connected',timeout=20000)
                await view.wait_for_function("document.querySelector('#vnc-screen canvas')?.width>500")
                targets=(await client.get('http://127.0.0.1:9222/json')).json()
                target=next(t for t in targets if t.get('url')==url)
                socket=await websockets.connect(target['webSocketDebuggerUrl'])
                sockets.append(socket)
                views.append(view); remotes.append(Remote(socket))
            view, remote=views[0], remotes[0]
            await view.bring_to_front()

            async def wait_clipboard(expected, page=None):
                page = page or view
                deadline = asyncio.get_running_loop().time() + 5
                # wait_for_function treats returned Promises as truthy in
                # some Playwright versions; explicitly await clipboard reads.
                while asyncio.get_running_loop().time() < deadline:
                    if await page.evaluate('navigator.clipboard.readText()') == expected:
                        return
                    await asyncio.sleep(.05)
                raise AssertionError('Local clipboard did not receive expected QA text')

            async def click(selector):
                box=await remote.box(selector)
                canvas=await view.locator('#vnc-screen canvas').bounding_box()
                size=await remote.evaluate('({w:innerWidth,h:innerHeight})')
                await view.mouse.click(canvas['x']+(box['x']+box['width']/2)*canvas['width']/size['w'],canvas['y']+(box['y']+box['height']/2)*canvas['height']/size['h'])

            for selector, text in [('#modern','متن دکمه سایت — English 42'),('#rich','متن ClipboardItem'),('#html','متن HTML'),('#legacy','متن کپی قدیمی'),('#custom','متن رویداد کپی')]:
                await click(selector)
                await wait_clipboard(text)
                assert await views[1].locator('#clipboard-text').input_value() == ''
                print(f'PASS site copy {selector}: local clipboard and per-user isolation',flush=True)

            outside=await view.context.new_page()
            await outside.goto(f'http://127.0.0.1:{fixture.server_port}/outside')
            await outside.bring_to_front()
            await outside.locator('#outside').click()
            await wait_clipboard('متن رویداد کپی', outside)
            await outside.keyboard.press('Control+v')
            await outside.wait_for_function("document.querySelector('#outside').value==='متن رویداد کپی'",timeout=5000)
            await outside.close()
            await view.bring_to_front()
            print('PASS paste into a separate local page outside the remote viewer',flush=True)

            # The init script and binding also operate in child frames.
            # The fixture's button starts at (8,8) inside the frame. Its DOM
            # isn't accessible to the main document across origins.
            box=await remote.evaluate("() => {const f=document.querySelector('#frame'),r=f.getBoundingClientRect();return {x:r.x+f.clientLeft+40,y:r.y+f.clientTop+20}}")
            canvas=await view.locator('#vnc-screen canvas').bounding_box()
            size=await remote.evaluate('({w:innerWidth,h:innerHeight})')
            await view.mouse.click(canvas['x']+box['x']*canvas['width']/size['w'],canvas['y']+box['y']*canvas['height']/size['h'])
            await wait_clipboard('متن داخل قاب')
            print('PASS copy from cross-origin child frame',flush=True)

            # Reload must install the bridge before the site's scripts run.
            (await clients[0].post('/api/tabs/me/reload')).raise_for_status()
            await remote.wait_for_function("!!document.querySelector('#modern')")
            await click('#modern')
            await wait_clipboard('متن دکمه سایت — English 42')
            print('PASS bridge survives page navigation/reload',flush=True)

            # Simulate a local permission denial. The text must still arrive,
            # with an explicit retry button, not a false 'copied' confirmation.
            await view.evaluate("() => {window.savedWrite=navigator.clipboard.writeText.bind(navigator.clipboard);navigator.clipboard.writeText=()=>Promise.reject(new DOMException('Denied','NotAllowedError'));}")
            await click('#legacy')
            await view.wait_for_selector('#clipboard-panel.open')
            await view.wait_for_function("document.querySelector('#clipboard-text').value==='متن کپی قدیمی'",timeout=5000)
            await view.evaluate("() => {navigator.clipboard.writeText=window.savedWrite;}")
            await view.click('#copy-local-clipboard')
            await wait_clipboard('متن کپی قدیمی')
            print('PASS permission-denied fallback and explicit local-copy button',flush=True)

            # An HTTP-origin viewer may not expose navigator.clipboard at all.
            await view.evaluate("() => {navigator.clipboard.writeText=()=>Promise.reject(new DOMException('Unavailable','NotAllowedError'));}")
            await view.locator('#clipboard-text').fill('متن کپی با روش جایگزین')
            await view.click('#copy-local-clipboard')
            await wait_clipboard('متن کپی با روش جایگزین')
            await view.evaluate("() => {navigator.clipboard.writeText=window.savedWrite;}")
            await view.set_viewport_size({'width':390,'height':844})
            assert await view.evaluate('document.documentElement.scrollWidth<=innerWidth')
            artifacts=Path('/tmp/sbt-qa'); artifacts.mkdir(exist_ok=True)
            await view.screenshot(path=str(artifacts/'clipboard-fallback-mobile.png'))
            print('PASS legacy local copy fallback and responsive clipboard panel',flush=True)
            await browser.close()
    finally:
        for socket in sockets: await socket.close()
        for client in clients:
            await client.delete('/api/tabs/me')
            await client.aclose()
        with SessionLocal() as db:
            for user in db.query(User).filter(User.username.in_(names)).all():
                db.query(BrowserTab).filter(BrowserTab.user_id==user.id).delete()
                db.delete(user)
            db.commit()
        fixture.shutdown()


if __name__=='__main__':
    asyncio.run(main())
