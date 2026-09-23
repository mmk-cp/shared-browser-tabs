"""Loopback input-to-pixel benchmark; uses and removes only a QA account.

Run: python -m tests.benchmark_latency [--idle-seconds 65]
Network delay on a deployed server is additional to these local timings.
"""
import argparse
import asyncio
import json
import secrets
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import websockets
from playwright.async_api import async_playwright

from app.db import SessionLocal
from app.models import BrowserTab, User
from app.services.auth_service import hash_password, SESSION_COOKIE
from tests.e2e_remote import navigate_fixture

HTML = b'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;background:white}button{position:absolute;left:40px;top:40px;width:200px;height:200px;border:0;background:rgb(0,200,0)}input{position:absolute;top:280px;left:40px}</style>
<button id="target" onpointerdown="this.dataset.red=this.dataset.red==='yes'?'no':'yes';this.style.background=this.dataset.red==='yes'?'rgb(200,0,0)':'rgb(0,200,0)'" data-red="no"></button><input id="editor">'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *args):
        pass


async def main(idle_seconds):
    fixture = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    name, password = 'qa_latency_' + secrets.token_hex(5), secrets.token_urlsafe(20)
    with SessionLocal() as db:
        db.add(User(username=name, password_hash=hash_password(password)))
        db.commit()
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=30) as client:
        try:
            (await client.post('/api/auth/login', json={'username':name, 'password':password})).raise_for_status()
            client.headers['X-CSRF-Token'] = client.cookies['shared_browser_csrf']
            (await client.get('/api/tabs/me')).raise_for_status()
            await navigate_fixture(client, f'http://127.0.0.1:{fixture.server_port}')
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
                context = await browser.new_context(viewport={'width':1920,'height':1080})
                await context.add_cookies([{'name':k,'value':v,'url':'http://127.0.0.1:8000'} for k,v in client.cookies.items()])
                view = await context.new_page()
                await view.goto('http://127.0.0.1:8000/dashboard')
                await view.wait_for_selector('#online-dot.connected', timeout=20000)
                await view.wait_for_function("() => {const c=document.querySelector('#vnc-screen canvas');return c?.width>100 && c.getContext('2d').getImageData(100,100,1,1).data[1]>150}")

                async def click_to_pixel():
                    # Start the clock in the viewer so protocol orchestration
                    # round-trips are not included in the measured duration.
                    return await view.evaluate("""() => new Promise((resolve,reject) => {
                        const canvas=document.querySelector('#vnc-screen canvas'), ctx=canvas.getContext('2d');
                        const before=ctx.getImageData(100,100,1,1).data[0]>100;
                        const box=canvas.getBoundingClientRect(), x=box.x+100*box.width/canvas.width, y=box.y+100*box.height/canvas.height;
                        const started=performance.now();
                        const timeout=setTimeout(()=>reject(Error('pixel update timeout')),15000);
                        canvas.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,clientX:x,clientY:y,button:0,pointerType:'mouse',pointerId:1}));
                        canvas.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,clientX:x,clientY:y,button:0,pointerType:'mouse',pointerId:1}));
                        function poll(){if((ctx.getImageData(100,100,1,1).data[0]>100)!==before){clearTimeout(timeout);resolve(performance.now()-started)}else requestAnimationFrame(poll)}
                        requestAnimationFrame(poll);
                    })""")

                # Establish a real mouse pointer for setPointerCapture used by
                # the viewer's pointer handler (synthetic events reuse it).
                await view.mouse.move(100,100)
                samples = []
                for _ in range(12):
                    samples.append(await click_to_pixel())
                    await asyncio.sleep(.1)
                print(json.dumps({'click_to_pixel_ms':{'median':round(statistics.median(samples)), 'max':round(max(samples)), 'samples':[round(s) for s in samples]}}), flush=True)
                async with websockets.connect('ws://127.0.0.1:8000/ws/input', origin='http://127.0.0.1:8000',
                    additional_headers={'Cookie':f'{SESSION_COOKIE}={client.cookies[SESSION_COOKIE]}'}) as socket:
                    started=time.perf_counter()
                    for n in range(120):
                        await socket.send(json.dumps({'type':'move','x':300+n,'y':400}))
                    await socket.send(json.dumps({'type':'key_down','key':'Shift','id':999}))
                    while True:
                        reply=json.loads(await asyncio.wait_for(socket.recv(), 20))
                        if reply.get('id') == 999:
                            assert 'error' not in reply, reply
                            break
                    print(json.dumps({'key_after_120_moves_ms':round((time.perf_counter()-started)*1000)}), flush=True)
                    await socket.send(json.dumps({'type':'release'}))
                if idle_seconds:
                    print(f'Waiting {idle_seconds}s without remote input to measure idle wake-up', flush=True)
                    await asyncio.sleep(idle_seconds)
                    print(json.dumps({'first_click_after_idle_ms':round(await click_to_pixel())}), flush=True)
                await browser.close()
        finally:
            await client.delete('/api/tabs/me')
            with SessionLocal() as db:
                user=db.query(User).filter(User.username==name).first()
                if user:
                    db.query(BrowserTab).filter(BrowserTab.user_id==user.id).delete()
                    db.delete(user)
                    db.commit()
            fixture.shutdown()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--idle-seconds',type=int,default=0)
    asyncio.run(main(parser.parse_args().idle_seconds))
