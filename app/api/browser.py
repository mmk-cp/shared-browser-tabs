from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import protected_admin, current_user
from app.db import get_db
from app.models import User
from app.services.browser_manager import browser_manager
from app.services.tab_manager import tab_manager
from app.services.stream_manager import stream_manager

router = APIRouter(prefix="/api/browser", tags=["browser"])

@router.get("/status")
def status(user: User = Depends(current_user)):
    return {"running": browser_manager.running, "pages": len(browser_manager.pages)}

@router.post("/start")
async def start(user: User = Depends(protected_admin), db: Session = Depends(get_db)):
    await browser_manager.start(); await tab_manager.restore_pages(db)
    return {"running": True}

@router.post("/restart")
async def restart(user: User = Depends(protected_admin), db: Session = Depends(get_db)):
    await stream_manager.stop_all()
    await browser_manager.restart(); await tab_manager.restore_pages(db)
    return {"running": True, "pages": len(browser_manager.pages)}
