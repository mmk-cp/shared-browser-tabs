import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from typing import Dict

from playwright.async_api import Page

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class VncSession:
    page: Page
    xid: int
    port: int
    process: asyncio.subprocess.Process


class StreamManager:
    """Exports one native Chromium app window per user through local VNC."""

    def __init__(self) -> None:
        self.streams: Dict[str, VncSession] = {}
        self._lock = asyncio.Lock()
        self._next_port = settings.vnc_base_port

    async def ensure(self, page_id: str, page: Page, xid: int) -> VncSession:
        current = self.streams.get(page_id)
        if current and current.page == page and current.process.returncode is None:
            return current
        async with self._lock:
            current = self.streams.get(page_id)
            if current and current.page == page and current.process.returncode is None:
                return current
            if current:
                await self.stop(page_id)
            port = self._allocate_port()
            args = [
                "x11vnc", "-display", os.environ.get("DISPLAY", ":99"),
                "-id", hex(xid), "-rfbport", str(port), "-localhost",
                "-forever", "-shared", "-nopw", "-viewonly", "-nosel",
                "-xrandr", "resize", "-noxdamage", "-noscr", "-quiet",
            ]
            process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await self._wait_for_port(port, process)
            session = VncSession(page=page, xid=xid, port=port, process=process)
            self.streams[page_id] = session
            logger.info("VNC_WINDOW_STARTED page=%s xid=%s port=%s", page_id, hex(xid), port)
            return session

    def _allocate_port(self) -> int:
        for _ in range(1000):
            port = self._next_port
            self._next_port += 1
            if self._next_port > 65000:
                self._next_port = settings.vnc_base_port
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    return port
        raise RuntimeError("No free local VNC port")

    async def _wait_for_port(self, port: int, process: asyncio.subprocess.Process) -> None:
        for _ in range(80):
            if process.returncode is not None:
                raise RuntimeError(f"x11vnc exited with code {process.returncode}")
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                await asyncio.sleep(0.1)
        process.terminate()
        raise RuntimeError("x11vnc did not start in time")

    async def broadcast_json(self, page_id: str, message: dict) -> None:
        # VNC transports the window directly; navigation state is returned by
        # the normal authenticated API rather than mixed into the RFB stream.
        return None

    async def stop(self, page_id: str) -> None:
        session = self.streams.pop(page_id, None)
        if session and session.process.returncode is None:
            session.process.terminate()
            try:
                await asyncio.wait_for(session.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                session.process.kill()
                await session.process.wait()

    async def stop_all(self) -> None:
        for page_id in list(self.streams):
            await self.stop(page_id)


stream_manager = StreamManager()
