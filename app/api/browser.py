import asyncio
import logging
from typing import Literal
from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import protected_admin, current_user
from app.db import get_db, SessionLocal
from app.models import User
from app.services.browser_manager import browser_manager
from app.services.tab_manager import tab_manager
from app.services.stream_manager import stream_manager

router = APIRouter(prefix="/api/browser", tags=["browser"])
logger = logging.getLogger(__name__)
maintenance_task = None


async def maintenance(action, user):
    global maintenance_task
    if maintenance_task and not maintenance_task.done():
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
                        if action == 'clear': removed = await browser_manager.clear_browser_data()
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
            raise HTTPException(500, 'عملیات کامل نشد؛ ممکن است بخشی از داده‌ها پاک شده باشد. وضعیت مرورگر و لاگ سرور را بررسی کنید.')
        finally:
            browser_manager.maintenance_owner = None

    maintenance_task = asyncio.create_task(run())
    # Closing the admin page must not interrupt a half-deleted profile.
    maintenance_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
    return await asyncio.shield(maintenance_task)


class ClearConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation: Literal['DELETE_BROWSER_DATA']

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
