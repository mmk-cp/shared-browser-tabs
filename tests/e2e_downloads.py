"""Disposable QA accounts + loopback downloads. Never touches real users' files."""
import asyncio
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import httpx
import websockets
from playwright.async_api import async_playwright

from app.db import SessionLocal
from app.models import User, BrowserTab
from app.services.auth_service import hash_password
from app.services.download_manager import ROOT
from tests.e2e_remote import Remote, navigate_fixture

DATA = 'دانلود روی دستگاه — test bytes 42\n'.encode() * 3000
NAME = 'آزمایش-download.txt'
HTML = '''<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<style>body{font:20px sans-serif;padding:24px;background:#eef3fa}a,button{display:block;margin:18px;padding:12px}</style>
<h2>دانلود / Download QA</h2><a id=normal href=/file>Download attachment</a>
<a id=popup href=/file target=_blank>Download new window</a><a id=slow href=/slow>Slow download</a><a id=large href=/large>Transfer interruption</a>
<button id=blob onclick="this.dataset.clicked='yes';const a=document.createElement('a');a.href=window.URL.createObjectURL(new Blob(['blob content سلام'],{type:'text/plain'}));a.download='blob.txt';a.click()">Blob download</button>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        if self.path in ['/file','/slow','/large']:
            self.send_header('Content-Type','application/octet-stream')
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''"+quote(NAME))
            self.send_header('Content-Length',str(len(DATA) if self.path=='/file' else 8*1024*1024 if self.path=='/large' else 20*1024*1024))
            self.end_headers()
            try:
                if self.path=='/file': self.wfile.write(DATA)
                elif self.path=='/large':
                    for _ in range(128): self.wfile.write(b'x'*65536)
                else:
                    for _ in range(320):
                        self.wfile.write(b'x'*65536); self.wfile.flush(); time.sleep(.1)
            except (BrokenPipeError,ConnectionResetError): pass
        else:
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers(); self.wfile.write(HTML.encode())

    def log_message(self,*args): pass


async def main():
    fixture=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=fixture.serve_forever,daemon=True).start()
    names=['qa_download_'+secrets.token_hex(6) for _ in range(2)]
    password=secrets.token_urlsafe(20)
    with SessionLocal() as db:
        db.add_all([User(username=n,password_hash=hash_password(password)) for n in names]); db.commit()
    baseline={p for p in ROOT.rglob('*') if p.is_file()}
    clients,sockets=[],[]
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            views,remotes,events=[],[],[]
            for name in names:
                client=httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=30); clients.append(client)
                (await client.post('/api/auth/login',json={'username':name,'password':password})).raise_for_status()
                client.headers['X-CSRF-Token']=client.cookies['shared_browser_csrf']
                (await client.get('/api/tabs/me')).raise_for_status()
                url=f'http://127.0.0.1:{fixture.server_port}/{name}'
                await navigate_fixture(client, url)
                context=await browser.new_context(viewport={'width':1200,'height':900},accept_downloads=True)
                await context.add_cookies([{'name':k,'value':v,'url':'http://127.0.0.1:8000'} for k,v in client.cookies.items()])
                view=await context.new_page(); messages=[]; events.append(messages)
                def observe(socket, messages=messages):
                    def received(data):
                        if isinstance(data,str):
                            message=json.loads(data)
                            if message.get('type','').startswith('download'): messages.append(message)
                    socket.on('framereceived',received)
                view.on('websocket',observe)
                await view.goto('http://127.0.0.1:8000/dashboard')
                await view.wait_for_selector('#online-dot.connected',timeout=20000)
                await view.wait_for_function("document.querySelector('#vnc-screen canvas')?.width>500")
                target=next(t for t in (await client.get('http://127.0.0.1:9222/json')).json() if t.get('url')==url)
                socket=await websockets.connect(target['webSocketDebuggerUrl']); sockets.append(socket)
                views.append(view); remotes.append(Remote(socket))
            view,remote=views[0],remotes[0]
            await view.bring_to_front()
            await remote.evaluate("window.addEventListener('error',e=>window.qaError=e.message)")

            async def click(selector):
                # Close the floating downloads panel without changing remote focus.
                if await view.locator('#downloads-panel').evaluate("e=>e.classList.contains('open')"):
                    await view.click('#downloads-toggle')
                box=await remote.box(selector); canvas=await view.locator('#vnc-screen canvas').bounding_box()
                size=await remote.evaluate('({w:innerWidth,h:innerHeight})')
                await view.mouse.click(canvas['x']+(box['x']+box['width']/2)*canvas['width']/size['w'],canvas['y']+(box['y']+box['height']/2)*canvas['height']/size['h'])

            async def empty(timeout=15):
                deadline=asyncio.get_running_loop().time()+timeout
                while asyncio.get_running_loop().time()<deadline:
                    if not ({p for p in ROOT.rglob('*') if p.is_file()}-baseline): return
                    await asyncio.sleep(.1)
                raise AssertionError('Server download artifacts were not deleted')

            async def ready(selector='#normal'):
                previous=len(events[0]); await click(selector)
                for _ in range(100):
                    matches=[e for e in events[0][previous:] if e.get('state')=='ready']
                    if matches: return matches[-1]['token']
                    await asyncio.sleep(.1)
                raise AssertionError('Download never became ready: '+str(events[0][previous:]))

            for selector,expected in [('#blob','blob content سلام'.encode()),('#normal',DATA),('#popup',DATA)]:
                try:
                    async with view.expect_download(timeout=20000) as result:
                        await click(selector)
                except Exception:
                    await view.screenshot(path='/tmp/download-failure.png')
                    print('REMOTE', await remote.evaluate("({url:location.href,error:window.qaError,clicked:document.querySelector('#blob')?.dataset.clicked, size:[innerWidth,innerHeight]})"),flush=True)
                    print('DOWNLOAD_QA_DIAGNOSTIC', events[0], await view.evaluate('({focus:document.hasFocus(),text:document.querySelector("#downloads-list").innerText})'), flush=True)
                    raise
                download=await result.value
                assert await download.failure() is None
                assert Path(await download.path()).read_bytes()==expected
                assert download.suggested_filename == ('blob.txt' if selector=='#blob' else NAME)
                await download.delete(); await empty()
                assert not events[1], 'Other user received a download event'
                print(f'PASS {selector}: exact bytes/name on device, server artifact deleted, user isolation',flush=True)

            # Saving the local fallback must work after the server copy is gone.
            async with view.expect_download() as result:
                await view.locator('#downloads-list a').first.click()
            download=await result.value
            assert Path(await download.path()).read_bytes()==DATA
            await download.delete(); await empty()
            print('PASS fallback save uses local Blob after server deletion',flush=True)

            await view.evaluate('() => {document.hasFocus=()=>false;}')
            token=await ready()
            endpoint=f'/api/tabs/me/downloads/{token}'
            assert (await clients[1].post(endpoint)).status_code==404
            assert (await clients[0].post(endpoint,headers={'X-CSRF-Token':''})).status_code==403
            assert (await clients[0].get(endpoint)).status_code==405
            response=await clients[0].post(endpoint)
            assert response.content==DATA and response.headers['cache-control']=='no-store, private'
            assert (await clients[0].post(endpoint)).status_code==404
            await empty()
            print('PASS authenticated one-shot download, foreign token/CSRF rejection and no-store',flush=True)

            previous=len(events[0]); await click('#slow')
            for _ in range(50):
                matches=[e for e in events[0][previous:] if e.get('state')=='receiving']
                if matches: break
                await asyncio.sleep(.1)
            assert matches
            (await clients[0].delete('/api/tabs/me/downloads/'+matches[0]['token'])).raise_for_status()
            await empty()
            print('PASS cancel in-progress website download removes partial file',flush=True)

            token=await ready('#large')
            async with clients[0].stream('POST','/api/tabs/me/downloads/'+token) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    assert chunk
                    break  # Device disappears before reading the whole body.
            await empty()
            print('PASS interrupted server-to-device HTTP stream cleans artifact',flush=True)

            token=await ready()
            await view.set_viewport_size({'width':390,'height':844})
            await remote.wait_for_function('innerWidth<400')
            assert await view.evaluate('document.documentElement.scrollWidth<=innerWidth')
            artifacts=Path('/tmp/sbt-qa'); artifacts.mkdir(exist_ok=True)
            await view.screenshot(path=str(artifacts/'downloads-mobile.png'))
            await view.close(); await empty()
            assert (await clients[0].post('/api/tabs/me/downloads/'+token)).status_code==404
            print('PASS mobile UI and automatic cleanup after viewer disconnect',flush=True)
            await browser.close()
    finally:
        for socket in sockets: await socket.close()
        for client in clients:
            await client.delete('/api/tabs/me'); await client.aclose()
        with SessionLocal() as db:
            for user in db.query(User).filter(User.username.in_(names)).all():
                db.query(BrowserTab).filter(BrowserTab.user_id==user.id).delete(); db.delete(user)
            db.commit()
        fixture.shutdown()


if __name__=='__main__': asyncio.run(main())
