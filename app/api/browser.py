import asyncio
import logging
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import protected_admin, current_user, admin_user
from app.services.proxy_manager import proxy_manager, vless_config, normalize_vless_uri
from app.services.browser_dns import DNSSettings
from app.db import get_db, SessionLocal
from app.models import User
from app.services.browser_manager import browser_manager
from app.services.tab_manager import tab_manager
from app.services.stream_manager import stream_manager

router = APIRouter(prefix="/api/browser", tags=["browser"])
logger = logging.getLogger(__name__)
maintenance_task = None
proxy_test_lock = asyncio.Lock()
proxy_last_test = 0.0


async def maintenance(action, user, proxy_config=None):
    global maintenance_task
    if proxy_test_lock.locked() or (maintenance_task and not maintenance_task.done()):
        raise HTTPException(409, 'یک عملیات مرورگر در حال انجام است؛ کمی صبر کنید.')

    async def run():
        browser_manager.maintenance_owner = asyncio.current_task()
        try:
            with SessionLocal() as db:
                fresh = db.get(User, user.id)
                if not fresh or not fresh.is_active or not fresh.is_admin or fresh.session_id != user.session_id:
                    raise HTTPException(401, 'نشست شما پایان یافته است.')
                removed = None
                try:
                    # Drain existing page creation; new viewers cannot create
                    # windows while the shared profile is being deleted.
                    async with tab_manager._lock:
                        await stream_manager.stop_all()
                        if action == 'proxy':
                            await browser_manager.stop()
                            try:
                                await proxy_manager.apply(proxy_config)
                            except ValueError as error:
                                raise HTTPException(400, str(error)) from None
                            finally:
                                await browser_manager.start()
                        elif action == 'clear': removed = await browser_manager.clear_browser_data()
                        elif action == 'restart': await browser_manager.restart()
                        else: await browser_manager.start()
                finally:
                    if browser_manager.running:
                        await tab_manager.restore_pages(db)
                logger.info('BROWSER_MAINTENANCE_COMPLETE action=%s admin_id=%s', action, user.id)
                return {'ok':True, 'running':True, 'removed':removed, 'pages':len(browser_manager.pages)}
        except HTTPException:
            raise
        except Exception:
            logger.exception('BROWSER_MAINTENANCE_FAILED action=%s', action)
            raise HTTPException(500, 'عملیات کامل نشد؛ وضعیت مرورگر را بررسی کنید.' if action != 'clear' else 'عملیات کامل نشد؛ ممکن است بخشی از داده‌ها پاک شده باشد. وضعیت مرورگر و لاگ سرور را بررسی کنید.')
        finally:
            browser_manager.maintenance_owner = None

    maintenance_task = asyncio.create_task(run())
    # Closing the admin page must not interrupt a half-deleted profile.
    maintenance_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
    return await asyncio.shield(maintenance_task)


class ClearConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation: Literal['DELETE_BROWSER_DATA']


class ProxySettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    uri: str = Field(default='', max_length=16384)
    dns: DNSSettings | None = None


@router.get('/proxy')
def proxy_status(user: User = Depends(admin_user)):
    return proxy_manager.status()


@router.put('/proxy')
async def update_proxy(payload: ProxySettings, user: User = Depends(protected_admin)):
    uri = normalize_vless_uri(payload.uri) or proxy_manager.saved['uri']
    if payload.enabled:
        try:
            vless_config(uri)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
    elif payload.uri.strip():
        try:
            vless_config(uri)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
    candidate = {'enabled': payload.enabled, 'uri': uri}
    if payload.dns is not None:
        candidate['dns'] = payload.dns.model_dump()
    elif 'dns' in proxy_manager.saved:
        candidate['dns'] = proxy_manager.saved['dns']
    await maintenance('proxy', user, candidate)
    return proxy_manager.status()


@router.post('/proxy/test')
async def test_proxy(user: User = Depends(protected_admin)):
    global proxy_last_test
    if proxy_test_lock.locked() or (maintenance_task and not maintenance_task.done()):
        raise HTTPException(409, 'عملیات دیگری در حال انجام است؛ کمی صبر کنید.')
    status = proxy_manager.status()
    if not status['enabled'] or not status['running']:
        raise HTTPException(400, 'ابتدا VPN را ذخیره و روشن کنید؛ سرویس پروکسی باید در حال اجرا باشد.')
    if time.monotonic() - proxy_last_test < 10:
        raise HTTPException(429, 'بین دو تست حداقل ۱۰ ثانیه فاصله بگذارید.')
    async with proxy_test_lock:
        proxy_last_test = time.monotonic()
        return await proxy_manager.test_connection()

@router.get("/status")
def status(user: User = Depends(current_user)):
    return {"running": browser_manager.running, "pages": len(browser_manager.pages)}

@router.post("/start")
async def start(user: User = Depends(protected_admin), db: Session = Depends(get_db)):
    return await maintenance('start', user)

@router.post("/restart")
async def restart(user: User = Depends(protected_admin), db: Session = Depends(get_db)):
    return await maintenance('restart', user)


@router.post("/clear-data")
async def clear_data(payload: ClearConfirmation, user: User = Depends(protected_admin)):
    """Admin-only destructive reset of shared Chromium browsing data."""
    return await maintenance('clear', user)
