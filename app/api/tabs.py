import asyncio
import logging
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.requests import ClientDisconnect
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from sqlalchemy.orm import Session
from app.api.deps import current_user, protected_user, protected_admin
from app.db import get_db, SessionLocal
from app.models import User, BrowserTab, Site
from app.services.tab_manager import tab_manager
from app.services.input_manager import selection_text
from app.services.auth_service import SESSION_COOKIE, get_user_from_token
from app.services.file_transfer import file_transfers, decode_files, TransferError, MAX_BODY, UPLOAD_SLOTS
from app.services.download_manager import downloads
from app.services.download_response import DownloadResponse

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

class TextInputRequest(BaseModel):
    text: str

@router.post("/me/navigate")
async def navigate(payload: NavigateRequest, user: User = Depends(protected_admin), db: Session = Depends(get_db)):
    return await navigate_page(payload.url, user, db)


class SiteSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    site_id: int = Field(gt=0, le=9223372036854775807, strict=True)


@router.post('/me/open-site')
async def open_site(payload: SiteSelection, user: User = Depends(protected_user), db: Session = Depends(get_db)):
    site = db.get(Site, payload.site_id)
    if not site or not site.is_active:
        raise HTTPException(404, 'این سایت حذف یا غیرفعال شده است؛ فهرست را تازه‌سازی کنید.')
    # Resolve the URL exclusively on the server; clients send only an ID.
    return await navigate_page(site.url, user, db)


async def navigate_page(url: str, user: User, db: Session):
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Only http(s) URLs are allowed")
    tab = own_tab(db, user)
    page = tab_manager.get_page(tab)
    if not page:
        tab = await tab_manager.get_or_create(db, user)
        page = tab_manager.get_page(tab)
    try:
        # Respond when the document starts; subresources can load afterwards.
        await page.goto(url.strip(), wait_until="commit", timeout=15000)
    except PlaywrightTimeoutError as exc:
        raise HTTPException(status_code=504, detail="سایت هنوز پاسخ نداده است. صفحه باز می‌ماند؛ کمی صبر کنید یا دوباره تلاش کنید.") from exc
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
        logger.warning("PAGE_ACTION_COMMIT_TIMEOUT action=%s current=%s", action, page.url)
        raise HTTPException(status_code=504, detail="بارگذاری سایت طول کشید؛ کمی صبر کنید.")
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

@router.post("/me/text")
async def insert_unicode_text(payload: TextInputRequest, user: User = Depends(protected_user), db: Session = Depends(get_db)):
    """Compatibility endpoint for inserting Unicode into the user's page."""
    if len(payload.text) > 1_000_000:
        raise HTTPException(status_code=413, detail="Clipboard text is too large")
    tab = own_tab(db, user)
    page = tab_manager.get_page(tab)
    if not page:
        raise HTTPException(status_code=503, detail="Browser page unavailable")
    await page.keyboard.insert_text(payload.text)
    return {"ok": True}

@router.get("/me/clipboard")
async def read_browser_clipboard(user: User = Depends(protected_user), db: Session = Depends(get_db)):
    tab = own_tab(db, user)
    page = tab_manager.get_page(tab)
    if not page:
        raise HTTPException(status_code=503, detail="Browser page unavailable")
    try:
        text = await asyncio.wait_for(selection_text(page), timeout=5)
    except Exception as exc:
        logger.warning("CLIPBOARD_READ_FAILED user=%s error=%s", user.username, exc)
        text = ""
    return {"text": text}

@router.delete("/me")
async def delete_my_tab(user: User = Depends(protected_user), db: Session = Depends(get_db)):
    await tab_manager.reset(db, user)
    return {"ok": True}


@router.post('/me/files')
async def upload_files(request: Request, user: User = Depends(protected_user), db: Session = Depends(get_db)):
    tab = own_tab(db, user)
    page = tab_manager.get_page(tab)
    token = request.headers.get('x-upload-token', '')
    try:
        file_transfers.require(page, user.session_id, token)
        try:
            length = int(request.headers.get('content-length', '0'))
        except ValueError:
            raise HTTPException(status_code=400, detail='اندازهٔ درخواست نامعتبر است.')
        if length > MAX_BODY:
            raise HTTPException(status_code=413, detail='مجموع فایل‌ها باید حداکثر ۲۰ مگابایت باشد.')
        async with UPLOAD_SLOTS:
            async def read_body():
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY:
                        raise HTTPException(status_code=413, detail='مجموع فایل‌ها باید حداکثر ۲۰ مگابایت باشد.')
                    body.extend(chunk)
                return bytes(body)
            body = await asyncio.wait_for(read_body(), timeout=180)
            files = await asyncio.to_thread(decode_files, body)
            # Revalidate after a slow upload; logout/login replacement may
            # have happened while bytes were arriving.
            with SessionLocal() as fresh:
                current = get_user_from_token(request.cookies.get(SESSION_COOKIE), fresh)
                if not current or current.id != user.id or current.session_id != user.session_id:
                    raise HTTPException(status_code=401, detail='نشست شما پایان یافته است.')
            return await asyncio.wait_for(file_transfers.deliver(page, user.session_id, token, files), timeout=20)
    except TransferError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail='انتقال فایل بیش از حد طول کشید؛ دوباره تلاش کنید.')
    except ClientDisconnect:
        raise HTTPException(status_code=400, detail='انتقال فایل قطع شد.')


@router.post('/me/downloads/{token}')
async def receive_download(token: str, request: Request, user: User = Depends(protected_user), db: Session = Depends(get_db)):
    page = tab_manager.get_page(own_tab(db, user))
    item = downloads.claim(page, user.session_id, token)
    return DownloadResponse(item, request.cookies.get(SESSION_COOKIE))


@router.delete('/me/downloads/{token}')
async def cancel_download(token: str, user: User = Depends(protected_user), db: Session = Depends(get_db)):
    page = tab_manager.get_page(own_tab(db, user))
    await downloads.remove(downloads.require(page, user.session_id, token))
    return {'ok': True}


@router.api_route("/{tab_id}", methods=["GET", "POST", "DELETE"])
def reject_foreign_tab(tab_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Never let a client address another user's tab by database ID."""
    tab = db.get(BrowserTab, tab_id)
    if tab and tab.user_id != user.id:
        raise HTTPException(status_code=403, detail="Tab does not belong to the authenticated user")
    raise HTTPException(status_code=404, detail="Tab not found")
