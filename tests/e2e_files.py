"""Local upload and real device-clipboard paste, using disposable QA users only."""
import asyncio
import base64
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
from tests.e2e_remote import Remote, navigate_fixture

HTML = '''<!doctype html><meta charset=utf-8>
<style>body{padding:24px;font:18px sans-serif;background:#eef3fa}button,input{padding:14px;margin:12px}#editor{padding:20px;border:1px solid;height:90px}iframe{height:90px}</style>
<h2>File and image QA / آزمایش فایل و عکس</h2>
<button id=upload onclick="document.querySelector('#file').click()">Upload one</button>
<input id=file type=file hidden><button id=multi onclick="document.querySelector('#files').click()">Upload multiple</button>
<input id=files type=file multiple hidden><input id=text placeholder=Text>
<div id=editor contenteditable=true>Paste image here / عکس</div><img id=preview width=80>
<iframe src='/frame'></iframe>
<script>
window.uploads=[];window.pastes=[];window.cancels=0;
window.addEventListener('message', e=>{if(e.data.frameUpload)window.frameUpload=e.data.frameUpload;if(e.data.framePaste)window.framePaste=e.data.framePaste});
async function describe(files){return Promise.all([...files].map(async f=>({name:f.name,type:f.type,size:f.size,data:btoa(String.fromCharCode(...new Uint8Array(await f.arrayBuffer())))})))}
for(const el of document.querySelectorAll('input[type=file]')){
el.onchange=async()=>{window.uploads.push(await describe(el.files))};el.oncancel=()=>window.cancels++;
}
document.querySelector('#editor').onpaste=async e=>{e.preventDefault();const files=[...e.clipboardData.files];window.pastes.push(await describe(files));if(files[0])document.querySelector('#preview').src=URL.createObjectURL(files[0])};
</script>'''
FRAME = '''<!doctype html><button id=frame-upload onclick="document.querySelector('input').click()">Frame upload</button><input type=file hidden onchange="parent.postMessage({frameUpload:this.files[0].name},'*')">
<div contenteditable=true style="padding:8px" onpaste="event.preventDefault();parent.postMessage({framePaste:event.clipboardData.files[0]?.type},'*')">Frame paste</div>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        html = FRAME if self.path == '/frame' else HTML.replace("src='/frame'", f"src='http://localhost:{self.server.server_port}/frame'")
        self.wfile.write(html.encode())

    def log_message(self, *args):
        pass


async def main():
    fixture = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    names = ['qa_files_'+secrets.token_hex(6) for _ in range(2)]
    password = secrets.token_urlsafe(20)
    with SessionLocal() as db:
        db.add_all([User(username=n, password_hash=hash_password(password)) for n in names])
        db.commit()
    clients, sockets = [], []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
            views, remotes = [], []
            for name in names:
                client = httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=30)
                clients.append(client)
                (await client.post('/api/auth/login', json={'username':name,'password':password})).raise_for_status()
                client.headers['X-CSRF-Token'] = client.cookies['shared_browser_csrf']
                (await client.get('/api/tabs/me')).raise_for_status()
                url = f'http://127.0.0.1:{fixture.server_port}/{name}'
                await navigate_fixture(client, url)
                context = await browser.new_context(viewport={'width':1200,'height':900}, permissions=['clipboard-read','clipboard-write'])
                await context.add_cookies([{'name':k,'value':v,'url':'http://127.0.0.1:8000'} for k,v in client.cookies.items()])
                view = await context.new_page()
                await view.goto('http://127.0.0.1:8000/dashboard')
                await view.wait_for_selector('#online-dot.connected', timeout=20000)
                await view.wait_for_function("document.querySelector('#vnc-screen canvas')?.width>500")
                target = next(t for t in (await client.get('http://127.0.0.1:9222/json')).json() if t.get('url') == url)
                socket = await websockets.connect(target['webSocketDebuggerUrl'])
                sockets.append(socket)
                views.append(view); remotes.append(Remote(socket))
            view, remote = views[0], remotes[0]
            await view.bring_to_front()
            tokens = []
            view.on('websocket', lambda ws: ws.on('framereceived', lambda data: tokens.append(json.loads(data)['token']) if isinstance(data,str) and '"type":"filechooser"' in data else None))
            # Reconnect so the inspector observes chooser tokens for security tests.
            await view.reload()
            await view.wait_for_selector('#online-dot.connected')
            await view.wait_for_function("document.querySelector('#vnc-screen canvas')?.width>500")

            async def click(selector, button='left'):
                box = await remote.box(selector)
                canvas = await view.locator('#vnc-screen canvas').bounding_box()
                size = await remote.evaluate('({w:innerWidth,h:innerHeight})')
                await view.mouse.click(canvas['x']+(box['x']+box['width']/2)*canvas['width']/size['w'], canvas['y']+(box['y']+box['height']/2)*canvas['height']/size['h'],button=button)

            async def choose(selector='#upload'):
                await click(selector)
                await view.wait_for_selector('#file-panel.open', timeout=5000)
                assert not await views[1].locator('#file-panel').evaluate("e=>e.classList.contains('open')")

            payload = {'name':'آزمایش.txt','mimeType':'text/plain','buffer':'سلام Upload'.encode()}
            await choose()
            token = tokens[-1]
            await view.locator('#local-files').set_input_files(payload)
            await remote.wait_for_function('uploads.length===1')
            files = await remote.evaluate('uploads[0]')
            assert files == [{'name':payload['name'],'type':payload['mimeType'],'size':len(payload['buffer']),'data':base64.b64encode(payload['buffer']).decode()}], files
            await view.wait_for_selector('#file-panel.open', state='hidden')
            assert (await clients[0].post('/api/tabs/me/files',headers={'X-Upload-Token':token},json={'files':[]})).status_code == 409
            print('PASS local file chooser, exact bytes, user isolation and single-use token', flush=True)

            await choose()
            await view.click('#cancel-files')
            await remote.wait_for_function('cancels===1')
            assert await remote.evaluate("document.querySelector('#file').files[0].name") == payload['name']
            await choose('#multi')
            await view.locator('#local-files').set_input_files([payload, {'name':'second.txt','mimeType':'text/plain','buffer':b'second'}])
            await remote.wait_for_function('uploads.length===2 && uploads[1].length===2')
            await view.wait_for_selector('#file-panel.open', state='hidden')
            print('PASS cancel preserves selection, reopen and multiple files', flush=True)

            await choose()
            token = tokens[-1]
            body = {'files':[{'name':'test.txt','type':'text/plain','data':'aGk='}]}
            assert (await clients[1].post('/api/tabs/me/files',headers={'X-Upload-Token':token},json=body)).status_code == 409
            assert (await clients[0].post('/api/tabs/me/files',headers={'X-Upload-Token':token,'X-CSRF-Token':''},json=body)).status_code == 403
            assert (await clients[0].post('/api/tabs/me/files',headers={'X-Upload-Token':token},json={'files':body['files']*2})).status_code == 400
            assert (await clients[0].post('/api/tabs/me/files',headers={'X-Upload-Token':token},json={'files':[{'path':'/etc/passwd'}]})).status_code == 400
            await view.click('#cancel-files')
            print('PASS foreign token, missing CSRF, single-file count and server-path rejection', flush=True)

            await click('#text')
            await view.evaluate("navigator.clipboard.writeText('سلام paste English 42')")
            await view.keyboard.press('Control+v')
            await remote.wait_for_function("document.querySelector('#text').value==='سلام paste English 42'")
            print('PASS native Ctrl+V text regression', flush=True)

            # Generate a real PNG through the local browser, then use its system clipboard.
            png = await view.evaluate("() => {const c=document.createElement('canvas');c.width=32;c.height=32;const x=c.getContext('2d');x.fillStyle='#43a080';x.fillRect(0,0,32,32);return c.toDataURL().split(',')[1]}")
            await click('#editor')
            await view.evaluate("async data => {const blob=await (await fetch('data:image/png;base64,'+data)).blob();await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);}", png)
            await view.keyboard.press('Control+v')
            await remote.wait_for_function('pastes.length===1 && pastes[0].length===1')
            pasted = (await remote.evaluate('pastes[0]'))[0]
            assert pasted['type'] == 'image/png' and pasted['size'] > 0
            await remote.wait_for_function("document.querySelector('#preview').naturalWidth===32")
            assert await remotes[1].evaluate('pastes.length') == 0
            print('PASS real device image clipboard Ctrl+V, image rendering and user isolation', flush=True)

            await view.locator('#local-paste-image').set_input_files({'name':'fallback.png','mimeType':'image/png','buffer':base64.b64decode(png)})
            await remote.wait_for_function('pastes.length===2')
            assert (await remote.evaluate('pastes[1]'))[0]['data'] == png
            print('PASS image-picker fallback with exact PNG bytes', flush=True)
            await click('#editor', button='right')
            await view.wait_for_selector('#context-menu:not([hidden])')
            await view.click('[data-command=paste]')
            await remote.wait_for_function('pastes.length===3')
            print('PASS right-click image paste using local Clipboard API', flush=True)

            # Open an actual cross-origin frame's file input through viewer coordinates.
            point = await remote.evaluate("() => {const f=document.querySelector('iframe'),r=f.getBoundingClientRect();return {x:r.x+50,y:r.y+20}}")
            canvas = await view.locator('#vnc-screen canvas').bounding_box()
            size = await remote.evaluate('({w:innerWidth,h:innerHeight})')
            await view.mouse.click(canvas['x']+point['x']*canvas['width']/size['w'],canvas['y']+point['y']*canvas['height']/size['h'])
            await view.wait_for_selector('#file-panel.open')
            await view.locator('#local-files').set_input_files(payload)
            await view.wait_for_selector('#file-panel.open',state='hidden')
            await remote.wait_for_function("window.frameUpload==='آزمایش.txt'")
            await view.mouse.click(canvas['x']+point['x']*canvas['width']/size['w'],canvas['y']+(point['y']+38)*canvas['height']/size['h'])
            await view.keyboard.press('Control+v')
            await remote.wait_for_function("window.framePaste==='image/png'")
            print('PASS cross-origin iframe upload and image paste', flush=True)

            await choose()
            token = tokens[-1]
            artifacts = Path('/tmp/sbt-qa'); artifacts.mkdir(exist_ok=True)
            await view.screenshot(path=str(artifacts/'file-upload-desktop.png'))
            await view.set_viewport_size({'width':390,'height':844})
            await remote.wait_for_function('innerWidth<400 && innerHeight>700')
            await view.wait_for_function("document.querySelector('#vnc-screen canvas').width<400")
            assert await view.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await view.screenshot(path=str(artifacts/'file-upload-mobile.png'))
            (await clients[0].post('/api/tabs/me/reload')).raise_for_status()
            await view.wait_for_selector('#file-panel.open',state='hidden')
            assert (await clients[0].post('/api/tabs/me/files',headers={'X-Upload-Token':token},json=body)).status_code == 409
            print('PASS mobile layout and navigation invalidation', flush=True)

            cookie = '; '.join(f'{k}={v}' for k,v in clients[0].cookies.items())
            async with websockets.connect('ws://127.0.0.1:8000/ws/input', origin='http://127.0.0.1:8000', additional_headers={'Cookie':cookie}) as commands:
                async def command(event):
                    await commands.send(json.dumps({**event,'id':'qa-command'}))
                    while True:
                        message = json.loads(await asyncio.wait_for(commands.recv(),10))
                        if message.get('id') == 'qa-command':
                            assert 'error' not in message, message
                            return message['result']
                await remote.evaluate("document.querySelector('#editor').focus()")
                target = await command({'type':'prepare_paste'})
                # Changing focus while bytes are uploading must not retarget them.
                await remote.evaluate("document.querySelector('#text').focus()")
                image_body = {'files':[{'name':'photo.png','type':'image/png','data':png}]}
                response = await clients[0].post('/api/tabs/me/files',headers={'X-Upload-Token':target['token']},json=image_body)
                response.raise_for_status()
                assert response.json()['handled']
                await remote.wait_for_function('pastes.length===1')
                target = await command({'type':'prepare_paste'})
                rejected = await clients[0].post('/api/tabs/me/files',headers={'X-Upload-Token':target['token']},json=body)
                assert rejected.status_code == 415
                # Login replacement must reject both old cookies and the old
                # transfer token, even when used with the new session cookie.
                async with httpx.AsyncClient(base_url='http://127.0.0.1:8000') as replacement:
                    (await replacement.post('/api/auth/login',json={'username':names[0],'password':password})).raise_for_status()
                    assert (await clients[0].post('/api/tabs/me/files',headers={'X-Upload-Token':target['token']},json=image_body)).status_code == 401
                    clients[0].cookies.clear()
                    clients[0].cookies.update(replacement.cookies)
                    clients[0].headers['X-CSRF-Token'] = clients[0].cookies['shared_browser_csrf']
                    assert (await clients[0].post('/api/tabs/me/files',headers={'X-Upload-Token':target['token']},json=image_body)).status_code == 409
            print('PASS captured paste target, non-image rejection and replacement-session revocation', flush=True)
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


if __name__ == '__main__':
    asyncio.run(main())
