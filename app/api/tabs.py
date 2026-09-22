import asyncio
import logging
from urllib.parse import urlparse
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from sqlalchemy.orm import Session
from app.api.deps import current_user, protected_user
from app.db import get_db
from app.models import User, BrowserTab
from app.services.tab_manager import tab_manager

router = APIRouter(prefix="/api/tabs", tags=["tabs"])
logger = logging.getLogger(__name__)

def own_tab(db: Session, user: User) -> BrowserTab:
    tab = db.query(BrowserTab).filter(BrowserTab.user_id == user.id, BrowserTab.is_active.is_(True)).first()
    if not tab:
        raise HTTPException(status_code=404, detail="Tab not found")
    return tab

def tab_response(tab: BrowserTab):
    return {"id": tab.id, "url": tab.url, "title": tab.title, "is_active": tab.is_active}

@router.get("/me")
async def get_my_tab(user: User = Depends(current_user), db: Session = Depends(get_db)):
    tab = await tab_manager.get_or_create(db, user)
    return tab_response(tab)

@router.post("")
async def create_tab(user: User = Depends(protected_user), db: Session = Depends(get_db)):
    return tab_response(await tab_manager.get_or_create(db, user))

class NavigateRequest(BaseModel):
    url: str

@router.post("/me/navigate")
async def navigate(payload: NavigateRequest, user: User = Depends(protected_user), db: Session = Depends(get_db)):
    parsed = urlparse(payload.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Only http(s) URLs are allowed")
    tab = own_tab(db, user)
    page = tab_manager.get_page(tab)
    if not page:
        tab = await tab_manager.get_or_create(db, user)
        page = tab_manager.get_page(tab)
    try:
        # `commit` is enough to start displaying the document.  Waiting for
        # `domcontentloaded` is unreliable for long-lived SPAs (ChatGPT and
        # challenge pages may keep network activity open indefinitely).
        await page.goto(payload.url.strip(), wait_until="commit", timeout=15000)
    except PlaywrightTimeoutError as exc:
        # A timeout after the navigation has started is not a failed
        # navigation.  The page is still usable and its frame events will
        # update the URL/title in the background.
        if page.url not in {"about:blank", ""}:
            logger.warning("NAVIGATION_COMMIT_TIMEOUT url=%s current=%s", payload.url.strip(), page.url)
        else:
            raise HTTPException(status_code=504, detail=f"Navigation timed out before the page started: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Navigation failed: {exc}")
    tab.url = page.url
    try:
        tab.title = await asyncio.wait_for(page.title(), timeout=3)
    except Exception:
        tab.title = tab.title or "Shared tab"
    db.commit()
    return tab_response(tab)

async def page_action(action: str, user: User, db: Session):
    tab = own_tab(db, user)
    page = tab_manager.get_page(tab)
    if not page:
        raise HTTPException(status_code=503, detail="Browser page unavailable")
    try:
        if action == "back": await page.go_back(wait_until="commit", timeout=15000)
        elif action == "forward": await page.go_forward(wait_until="commit", timeout=15000)
        else: await page.reload(wait_until="commit", timeout=15000)
    except PlaywrightTimeoutError:
        # The document may already have navigated even when a SPA does not
        # reach the requested load milestone in time.
        logger.warning("PAGE_ACTION_COMMIT_TIMEOUT action=%s current=%s", action, page.url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    tab.url = page.url
    try:
        tab.title = await asyncio.wait_for(page.title(), timeout=3)
    except Exception:
        tab.title = tab.title or "Shared tab"
    db.commit()
    return tab_response(tab)

@router.post("/me/back")
async def back(user: User = Depends(protected_user), db: Session = Depends(get_db)): return await page_action("back", user, db)
@router.post("/me/forward")
async def forward(user: User = Depends(protected_user), db: Session = Depends(get_db)): return await page_action("forward", user, db)
@router.post("/me/reload")
async def reload(user: User = Depends(protected_user), db: Session = Depends(get_db)): return await page_action("reload", user, db)

@router.delete("/me")
async def delete_my_tab(user: User = Depends(protected_user), db: Session = Depends(get_db)):
    await tab_manager.reset(db, user)
    return {"ok": True}


@router.api_route("/{tab_id}", methods=["GET", "POST", "DELETE"])
def reject_foreign_tab(tab_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Never let a client address another user's tab by database ID."""
    tab = db.get(BrowserTab, tab_id)
    if tab and tab.user_id != user.id:
        raise HTTPException(status_code=403, detail="Tab does not belong to the authenticated user")
    raise HTTPException(status_code=404, detail="Tab not found")
