import logging
from urllib.parse import urlsplit
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.db import SessionLocal
from app.models import BrowserTab
from app.services.auth_service import SESSION_COOKIE, serializer, settings
from app.services.tab_manager import tab_manager
from app.services.stream_manager import stream_manager

logger = logging.getLogger(__name__)
router = APIRouter()


async def authenticated_user(websocket: WebSocket):
    token = websocket.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        data = serializer.loads(token, max_age=settings.session_max_age)
    except Exception:
        return None
    with SessionLocal() as db:
        from app.models import User
        return db.get(User, int(data.get("user_id", 0)))


@router.websocket("/ws/browser")
async def browser_socket(websocket: WebSocket):
    user = await authenticated_user(websocket)
    if not user or not user.is_active:
        await websocket.close(code=4401)
        return
    # Browsers send Origin automatically for WS. Reject cross-site attempts.
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "")
    if origin and urlsplit(origin).netloc != host:
        # Host/origin checks are intentionally conservative; deployments behind a
        # proxy can omit Origin or configure the proxy to preserve same-origin.
        logger.warning("WS_ORIGIN_REJECTED user=%s origin=%s", user.username, origin)
        await websocket.close(code=4403)
        return
    with SessionLocal() as db:
        tab = await tab_manager.get_or_create(db, user)
        tab_id, page_id, url, title = tab.id, tab.page_id, tab.url, tab.title
    page = tab_manager.browser.get_page(page_id)
    if not page:
        await websocket.close(code=1011)
        return
    await websocket.accept()
    await stream_manager.add_socket(page_id, page, websocket)
    await websocket.send_json({"type": "state", "url": page.url, "title": await page.title(),
                               "width": settings.default_width, "height": settings.default_height})
    logger.info("WS_CONNECTED user=%s tab=%s", user.username, tab_id)
    try:
        while True:
            event = await websocket.receive_json()
            kind = event.get("type")
            if kind == "mouse_move":
                await page.mouse.move(float(event.get("x", 0)), float(event.get("y", 0)))
            elif kind in {"mouse_down", "mouse_up"}:
                method = getattr(page.mouse, kind.removeprefix("mouse_"))
                await method(button=event.get("button", "left"))
            elif kind == "mouse_wheel":
                await page.mouse.wheel(float(event.get("delta_x", 0)), float(event.get("delta_y", 0)))
            elif kind in {"click", "double_click"}:
                x, y = float(event.get("x", 0)), float(event.get("y", 0))
                if kind == "click": await page.mouse.click(x, y, button=event.get("button", "left"))
                else: await page.mouse.dblclick(x, y, button=event.get("button", "left"))
            elif kind in {"keyboard_down", "keyboard_up"}:
                key = event.get("key") or event.get("code")
                if key:
                    method = page.keyboard.down if kind == "keyboard_down" else page.keyboard.up
                    await method(key)
            elif kind == "text_input":
                await page.keyboard.insert_text(str(event.get("text", "")))
            elif kind == "resize":
                width, height = int(event.get("width", settings.default_width)), int(event.get("height", settings.default_height))
                if 320 <= width <= 3840 and 200 <= height <= 2160:
                    await page.set_viewport_size({"width": width, "height": height})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.info("WS_CLOSED user=%s error=%s", user.username, exc)
    finally:
        await stream_manager.remove_socket(page_id, websocket)
        logger.info("WS_DISCONNECTED user=%s tab=%s", user.username, tab_id)
