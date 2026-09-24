"""Isolated database and mocked browser: cannot erase the real profile."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.models import User
from app.services.auth_service import serializer, SESSION_COOKIE, CSRF_COOKIE
from app.api import browser as routes


class CleanupAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions=sessionmaker(bind=self.engine,expire_on_commit=False)
        with self.sessions() as db:
            for name,admin in [('qa_admin',True),('qa_member',False)]:
                db.add(User(username=name,password_hash='test-only',session_id=name,is_admin=admin))
            db.commit()
        def database():
            with self.sessions() as db: yield db
        self.app=FastAPI(); self.app.include_router(routes.router); self.app.dependency_overrides[get_db]=database
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://qa.test')
        self.browser=SimpleNamespace(maintenance_owner=None,running=True,pages={},
            clear_browser_data=AsyncMock(return_value={'files':1,'directories':1}),start=AsyncMock(),stop=AsyncMock(),restart=AsyncMock())
        self.tabs=SimpleNamespace(_lock=asyncio.Lock(),restore_pages=AsyncMock())
        self.patches=[patch.object(routes,'browser_manager',self.browser),patch.object(routes,'tab_manager',self.tabs),
            patch.object(routes,'SessionLocal',self.sessions),patch.object(routes,'stream_manager',SimpleNamespace(stop_all=AsyncMock())),
            patch.object(routes,'maintenance_task',None)]
        for p in self.patches: p.start()

    async def asyncTearDown(self):
        if routes.maintenance_task: await asyncio.gather(routes.maintenance_task,return_exceptions=True)
        await self.client.aclose()
        for p in reversed(self.patches): p.stop()
        self.engine.dispose()

    def login(self,admin=True):
        name='qa_admin' if admin else 'qa_member'
        self.client.cookies.set(SESSION_COOKIE,serializer.dumps({'user_id':1 if admin else 2,'session_id':name}))
        self.client.cookies.set(CSRF_COOKIE,'qa-csrf'); self.client.headers['X-CSRF-Token']='qa-csrf'

    async def test_auth_role_csrf_confirmation_and_success(self):
        body={'confirmation':'DELETE_BROWSER_DATA'}
        self.assertEqual((await self.client.post('/api/browser/clear-data',json=body)).status_code,401)
        self.login(False)
        self.assertEqual((await self.client.post('/api/browser/clear-data',json=body)).status_code,403)
        self.login()
        self.assertEqual((await self.client.post('/api/browser/clear-data',json=body,headers={'X-CSRF-Token':''})).status_code,403)
        for invalid in [{},{'confirmation':'no'},{**body,'path':'/'}]:
            self.assertEqual((await self.client.post('/api/browser/clear-data',json=invalid)).status_code,422)
        self.browser.clear_browser_data.assert_not_called()
        self.assertEqual((await self.client.post('/api/browser/clear-data',json=body)).status_code,200)
        self.browser.clear_browser_data.assert_awaited_once(); self.tabs.restore_pages.assert_awaited_once()
        self.assertIsNone(self.browser.maintenance_owner)
        with self.sessions() as db: self.assertEqual(db.query(User).count(),2)

    async def test_concurrent_operation_rejected(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def clear(): entered.set(); await release.wait(); return {'files':1,'directories':1}
        self.browser.clear_browser_data.side_effect=clear; self.login()
        first=asyncio.create_task(self.client.post('/api/browser/clear-data',json={'confirmation':'DELETE_BROWSER_DATA'}))
        try:
            await asyncio.wait_for(entered.wait(),2)
            self.assertEqual((await self.client.post('/api/browser/restart')).status_code,409)
            # Cancelling the request must not cancel the maintenance task.
            first.cancel(); await asyncio.gather(first,return_exceptions=True)
            self.assertFalse(routes.maintenance_task.done())
        finally: release.set()
        await asyncio.wait_for(routes.maintenance_task,2)
        self.tabs.restore_pages.assert_awaited_once()

    async def test_partial_failure_still_restores_browser_pages(self):
        self.login(); self.browser.clear_browser_data.side_effect=OSError('QA cleanup error')
        response=await self.client.post('/api/browser/clear-data',json={'confirmation':'DELETE_BROWSER_DATA'})
        self.assertEqual(response.status_code,500)
        self.tabs.restore_pages.assert_awaited_once()
        self.assertIsNone(self.browser.maintenance_owner)

    async def test_proxy_admin_only_csrf_and_apply(self):
        proxy=SimpleNamespace(saved={'enabled':False,'uri':''},apply=AsyncMock(),status=lambda:{'enabled':False})
        body={'enabled':True,'uri':'vless://00000000-0000-4000-8000-000000000001@example.com:443?security=tls&type=ws'}
        with patch.object(routes,'proxy_manager',proxy):
            self.assertEqual((await self.client.get('/api/browser/proxy')).status_code,401)
            self.login(False)
            self.assertEqual((await self.client.get('/api/browser/proxy')).status_code,403)
            self.assertEqual((await self.client.put('/api/browser/proxy',json=body)).status_code,403)
            self.login()
            self.assertEqual((await self.client.put('/api/browser/proxy',json=body,headers={'X-CSRF-Token':''})).status_code,403)
            self.assertEqual((await self.client.put('/api/browser/proxy',json={'enabled':True,'uri':'invalid'})).status_code,400)
            proxy.apply.assert_not_called()
            self.assertEqual((await self.client.put('/api/browser/proxy',json=body)).status_code,200)
            proxy.apply.assert_awaited_once_with(body)
            self.browser.stop.assert_awaited_once(); self.browser.start.assert_awaited_once()
            self.tabs.restore_pages.assert_awaited_once()


if __name__=='__main__': unittest.main()
