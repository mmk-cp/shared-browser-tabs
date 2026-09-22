import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Set
from fastapi import WebSocket
from playwright.async_api import CDPSession, Page
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class TabStream:
    page: Page
    cdp: CDPSession | None = None
    sockets: Set[WebSocket] = field(default_factory=set)
    started: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_frame_at: float = 0.0


class StreamManager:
    def __init__(self) -> None:
        self.streams: Dict[str, TabStream] = {}

    async def ensure(self, tab_id: str, page: Page) -> TabStream:
        stream = self.streams.get(tab_id)
        if stream and stream.page == page and stream.started:
            return stream
        if stream:
            await self.stop(tab_id)
        stream = TabStream(page=page)
        self.streams[tab_id] = stream
        stream.cdp = await page.context.new_cdp_session(page)
        stream.cdp.on("Page.screencastFrame", lambda params: asyncio.create_task(self._frame(tab_id, params)))
        await stream.cdp.send("Page.enable")
        await stream.cdp.send("Page.startScreencast", {
            "format": "jpeg", "quality": settings.browser_jpeg_quality,
            "maxWidth": settings.default_width, "maxHeight": settings.default_height,
            "everyNthFrame": 1,
        })
        stream.started = True
        return stream

    async def _frame(self, tab_id: str, params: Dict[str, Any]) -> None:
        stream = self.streams.get(tab_id)
        if not stream:
            return
        try:
            if stream.cdp:
                await stream.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            now = time.monotonic()
            interval = 1.0 / max(1, settings.browser_fps)
            if now - stream.last_frame_at < interval:
                return
            stream.last_frame_at = now
            frame = base64.b64decode(params["data"])
            stale = set()
            for ws in list(stream.sockets):
                try:
                    await ws.send_bytes(frame)
                except Exception:
                    stale.add(ws)
            stream.sockets.difference_update(stale)
        except Exception as exc:
            logger.debug("STREAM_FRAME_FAILED tab=%s error=%s", tab_id, exc)

    async def add_socket(self, tab_id: str, page: Page, websocket: WebSocket) -> TabStream:
        stream = await self.ensure(tab_id, page)
        stream.sockets.add(websocket)
        return stream

    async def remove_socket(self, tab_id: str, websocket: WebSocket) -> None:
        stream = self.streams.get(tab_id)
        if stream:
            stream.sockets.discard(websocket)

    async def broadcast_json(self, tab_id: str, message: dict) -> None:
        stream = self.streams.get(tab_id)
        if not stream:
            return
        stale = set()
        for ws in list(stream.sockets):
            try:
                await ws.send_json(message)
            except Exception:
                stale.add(ws)
        stream.sockets.difference_update(stale)

    async def stop(self, tab_id: str) -> None:
        stream = self.streams.pop(tab_id, None)
        if stream and stream.cdp:
            try:
                await stream.cdp.send("Page.stopScreencast")
                await stream.cdp.detach()
            except Exception:
                pass


stream_manager = StreamManager()
