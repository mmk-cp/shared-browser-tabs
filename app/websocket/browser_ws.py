import asyncio
import base64
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db import SessionLocal
from app.services.auth_service import SESSION_COOKIE, get_user_from_token
from app.services.tab_manager import tab_manager
from app.services.stream_manager import stream_manager
from app.services.input_manager import handle_input
from app.services.input_buffer import InputBuffer

logger = logging.getLogger(__name__)
router = APIRouter()


async def watch_session(websocket: WebSocket):
    """Revoke already-open streams too, including idle/background viewers."""
    while True:
        if not await authenticated_user(websocket):
            await websocket.close(code=4401, reason="Session ended")
            return
        await asyncio.sleep(0.5)


@router.websocket("/ws/input")
async def input_socket(websocket: WebSocket):
    user = await authenticated_user(websocket)
    if not user:
        await websocket.close(code=4401)
        return
    origin = websocket.headers.get("origin")
    if not user or not user.is_active or not origin or urlsplit(origin).netloc != websocket.headers.get("host"):
        await websocket.close(code=4403)
        return
    with SessionLocal() as db:
        tab = await tab_manager.get_or_create(db, user)
        page_id = tab.page_id
    page = tab_manager.browser.get_page(page_id)
    if not page:
        await websocket.close(code=1011)
        return
    await websocket.accept()
    buffer = InputBuffer()

    async def receive_events():
        while True:
            event = await websocket.receive_json()
            if isinstance(event, dict):
                await buffer.put(event)

    async def apply_events():
        while True:
            event = await buffer.get()
            # Reject queued input immediately after a replacement login,
            # without waiting for the periodic stream-revocation check.
            if not await authenticated_user(websocket):
                await websocket.close(code=4401, reason="Session ended")
                break
            try:
                if event.get("type") == "resize":
                    result = await tab_manager.browser.resize_page(page_id, int(event["width"]), int(event["height"]))
                else:
                    result = await asyncio.wait_for(handle_input(page, event), timeout=8)
                if event.get("id") is not None:
                    await websocket.send_json({"id": event["id"], "result": result})
            except Exception as exc:
                logger.warning("TAB_INPUT_FAILED type=%s error=%s", event.get("type"), exc)
                if event.get("id") is not None:
                    await websocket.send_json({"id": event["id"], "error": "این فرمان اجرا نشد؛ دوباره تلاش کنید."})

    tasks = {
        asyncio.create_task(watch_session(websocket)),
        asyncio.create_task(receive_events()),
        asyncio.create_task(apply_events()),
    }
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except WebSocketDisconnect:
                pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if not page.is_closed():
            try:
                await handle_input(page, {"type": "release"})
            except Exception:
                pass


async def authenticated_user(websocket: WebSocket):
    with SessionLocal() as db:
        return get_user_from_token(websocket.cookies.get(SESSION_COOKIE), db)


@router.websocket("/ws/vnc")
async def vnc_socket(websocket: WebSocket):
    user = await authenticated_user(websocket)
    if not user or not user.is_active:
        await websocket.close(code=4401)
        return
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "")
    if not origin or urlsplit(origin).netloc != host:
        await websocket.close(code=4403)
        return

    with SessionLocal() as db:
        tab = await tab_manager.get_or_create(db, user)
        page_id = tab.page_id
    page = tab_manager.browser.get_page(page_id)
    xid = tab_manager.browser.window_ids.get(page_id)
    if not page or not xid:
        await websocket.close(code=1011)
        return
    session = await stream_manager.ensure(page_id, page, xid)
    reader, writer = await asyncio.open_connection("127.0.0.1", session.port)
    offered = websocket.scope.get("subprotocols", [])
    await websocket.accept(subprotocol="binary" if "binary" in offered else None)
    logger.info("VNC_CONNECTED user=%s page=%s", user.username, page_id)

    async def browser_to_vnc() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            payload = message.get("bytes")
            if payload is None and message.get("text") is not None:
                # Compatibility with old noVNC clients using the base64 WS
                # subprotocol. Current clients always use binary frames.
                payload = base64.b64decode(message["text"])
            if payload:
                writer.write(payload)
                await writer.drain()

    async def vnc_to_browser() -> None:
        while data := await reader.read(65536):
            await websocket.send_bytes(data)

    tasks = {
        asyncio.create_task(browser_to_vnc()),
        asyncio.create_task(vnc_to_browser()),
        asyncio.create_task(watch_session(websocket)),
    }
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled():
                task.exception()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except (WebSocketDisconnect, ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()
        logger.info("VNC_DISCONNECTED user=%s page=%s", user.username, page_id)
