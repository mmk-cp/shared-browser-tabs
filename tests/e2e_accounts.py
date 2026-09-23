"""Run inside Compose; only creates/revokes temporary QA accounts."""
import asyncio
import secrets
from pathlib import Path
from unittest.mock import patch

import httpx
import websockets
from playwright.async_api import async_playwright
from sqlalchemy import create_engine, inspect, text

from app.db import SessionLocal, initialize_database
from app.models import User, BrowserTab
from app.services.auth_service import SESSION_COOKIE, hash_password, serializer

BASE = 'http://127.0.0.1:8000'


def test_migration():
    isolated = create_engine('sqlite://')
    with isolated.begin() as connection:
        connection.execute(text('''CREATE TABLE users (id INTEGER PRIMARY KEY,
            username VARCHAR(120), password_hash VARCHAR(255), is_active BOOLEAN,
            is_admin BOOLEAN, created_at DATETIME)'''))
        connection.execute(text("INSERT INTO users (id,username) VALUES (1,'preserved')"))
    with patch('app.db.engine', isolated):
        initialize_database()
        initialize_database()
    assert 'session_id' in {c['name'] for c in inspect(isolated).get_columns('users')}
    with isolated.connect() as connection:
        assert connection.execute(text('SELECT username FROM users WHERE id=1')).scalar() == 'preserved'
    isolated.dispose()
    print('PASS non-destructive, repeatable legacy database migration', flush=True)


async def login(client, username, password):
    response = await client.post('/api/auth/login', json={'username':username,'password':password})
    response.raise_for_status()
    client.headers['X-CSRF-Token'] = client.cookies['shared_browser_csrf']


async def expect_revoked(socket):
    try:
        while True:
            await asyncio.wait_for(socket.recv(), 4)
    except websockets.exceptions.ConnectionClosed as error:
        assert error.rcvd.code == 4401, error


async def main():
    test_migration()
    suffix = secrets.token_hex(5)
    admin_name, member_name = f'qa_{suffix}_admin', f'qa_{suffix}_member'
    password = secrets.token_urlsafe(18)
    with SessionLocal() as db:
        db.add(User(username=admin_name, password_hash=hash_password(password), is_admin=True))
        db.commit()
    clients = [httpx.AsyncClient(base_url=BASE, timeout=30) for _ in range(4)]
    admin, first, second, anonymous = clients
    sockets = []
    artifacts = Path('/tmp/sbt-qa')
    artifacts.mkdir(exist_ok=True)
    try:
        await login(admin, admin_name, password)
        assert (await anonymous.get('/admin')).status_code == 307
        assert (await anonymous.get('/api/users')).status_code == 401
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])

            async def view_for(client, **options):
                context = await browser.new_context(**options)
                await context.add_cookies([{'name':k,'value':v,'url':BASE} for k,v in client.cookies.items()])
                return await context.new_page()

            panel = await view_for(admin, viewport={'width':1280,'height':900})
            await panel.goto(BASE+'/admin')
            await panel.locator('#new-username').fill(member_name)
            await panel.locator('#new-password').fill(password)
            await panel.locator('#create-user').click()
            await panel.wait_for_selector('#create-message.success')
            await panel.get_by_text(member_name, exact=True).wait_for()
            await panel.screenshot(path=str(artifacts/'admin-desktop.png'))
            await panel.set_viewport_size({'width':390,'height':844})
            assert await panel.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await panel.screenshot(path=str(artifacts/'admin-mobile.png'), full_page=True)
            users = (await admin.get('/api/users')).json()
            member = next(u for u in users if u['username'] == member_name)
            assert member['is_admin'] is False
            assert (await admin.post('/api/users', json={'username':member_name,'password':password})).status_code == 409
            for invalid in [dict(username='  ', password=password), dict(username='valid', password='ف'*40)]:
                assert (await admin.post('/api/users', json=invalid)).status_code == 422
            assert (await admin.post('/api/users', headers={'X-CSRF-Token':'invalid'}, json={'username':'unused','password':password})).status_code == 403
            print('PASS admin creation/list, mobile layout, duplicate/validation/CSRF checks', flush=True)

            await login(first, member_name, password)
            assert (await first.get('/admin')).status_code == 403
            assert (await first.get('/api/users')).status_code == 403
            assert (await first.post('/api/users', json={'username':'unused','password':password})).status_code == 403
            assert (await second.post('/api/auth/login', json={'username':member_name,'password':'wrong'})).status_code == 401
            assert (await first.get('/api/auth/me')).status_code == 200
            print('PASS regular user access denied; failed login preserves existing session', flush=True)

            viewer = await view_for(first, viewport={'width':1000,'height':700})
            await viewer.goto(BASE+'/dashboard')
            await viewer.wait_for_selector('#online-dot.connected', timeout=20000)
            assert await viewer.locator('.admin-link').count() == 0
            for route in ('input','vnc'):
                socket = await websockets.connect(f'ws://127.0.0.1:8000/ws/{route}', origin=BASE,
                    additional_headers={'Cookie':f'{SESSION_COOKIE}={first.cookies[SESSION_COOKIE]}'})
                sockets.append(socket)
            old_token = first.cookies[SESSION_COOKIE]
            await login(second, member_name, password)
            assert second.cookies[SESSION_COOKIE] != old_token
            assert (await first.get('/api/auth/me')).status_code == 401
            assert (await first.post('/api/tabs/me/text', json={'text':'must not type'})).status_code == 401
            assert (await first.post('/api/auth/logout')).status_code == 401
            assert (await second.get('/api/auth/me')).status_code == 200
            await asyncio.gather(*(expect_revoked(socket) for socket in sockets))
            await viewer.wait_for_url('**/login?reason=session-ended', timeout=7000)
            assert 'نشست' in await viewer.locator('#error').inner_text()
            for route in ('input','vnc'):
                try:
                    async with websockets.connect(f'ws://127.0.0.1:8000/ws/{route}', origin=BASE,
                        additional_headers={'Cookie':f'{SESSION_COOKIE}={old_token}'}):
                        raise AssertionError('Revoked cookie accepted by WebSocket')
                except websockets.exceptions.InvalidStatus as error:
                    assert error.response.status_code == 403
            print('PASS second login revokes old HTTP, input/VNC streams, handshakes and viewer UI', flush=True)

            # Same-session tabs remain allowed; a second admin login also
            # returns an already-open administration page to the login form.
            same_session = await view_for(admin)
            await same_session.goto(BASE+'/admin')
            assert (await admin.get('/api/auth/me')).status_code == 200
            await same_session.close()
            await login(anonymous, admin_name, password)
            await panel.wait_for_url('**/login?reason=session-ended', timeout=7000)
            print('PASS multiple tabs in one session; replacement admin login redirects old panel', flush=True)

            # Clean our remote window while its current cookie is still valid.
            (await second.delete('/api/tabs/me')).raise_for_status()
            saved_cookie = second.cookies[SESSION_COOKIE]
            (await second.post('/api/auth/logout')).raise_for_status()
            second.cookies.set(SESSION_COOKIE, saved_cookie)
            assert (await second.get('/api/auth/me')).status_code == 401
            second.cookies.set(SESSION_COOKIE, serializer.dumps({'user_id':member['id'],'username':member_name}))
            assert (await second.get('/api/auth/me')).status_code == 401
            print('PASS logout revokes replay; legacy stateless cookies rejected', flush=True)
            await browser.close()
    finally:
        for socket in sockets:
            await socket.close()
        # Only these random QA accounts are in scope, never the real admin.
        for name in (admin_name, member_name):
            async with httpx.AsyncClient(base_url=BASE, timeout=30) as cleanup:
                response = await cleanup.post('/api/auth/login', json={'username':name,'password':password})
                if response.status_code == 200:
                    cleanup.headers['X-CSRF-Token'] = cleanup.cookies['shared_browser_csrf']
                    await cleanup.delete('/api/tabs/me')
        for client in clients:
            await client.aclose()
        with SessionLocal() as db:
            for user in db.query(User).filter(User.username.in_([admin_name,member_name])).all():
                db.query(BrowserTab).filter(BrowserTab.user_id == user.id).delete()
                db.delete(user)
            db.commit()


if __name__ == '__main__':
    asyncio.run(main())
