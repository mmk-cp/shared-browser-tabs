"""Session-scoped downloads, temporarily staged in Linux tmpfs, never the profile.

The browser consumes a one-shot response into a local Blob; the server removes
its artifact even on interrupted transfers. Only task-owned staging directories
are swept on startup. No client-supplied filesystem paths are accepted.
"""
import asyncio
import logging
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException
from playwright.async_api import Download, Page

from app.db import SessionLocal
from app.models import User

logger = logging.getLogger(__name__)
ROOT = Path('/dev/shm/shared-browser-downloads')
MAX_BYTES = 100 * 1024 * 1024
MAX_PER_PAGE = 1
MAX_TOTAL = 2
READY_TTL = 120
TRANSFER_TTL = 300
DISCONNECT_GRACE = 10


def live_sessions():
    with SessionLocal() as db:
        return {s for (s,) in db.query(User.session_id).filter(User.is_active.is_(True)).all() if s}


def memory_pressure():
    """Avoid adding download buffers near the container's hard memory limit."""
    for usage, limit in [('/sys/fs/cgroup/memory.current','/sys/fs/cgroup/memory.max'),
                         ('/sys/fs/cgroup/memory/memory.usage_in_bytes','/sys/fs/cgroup/memory/memory.limit_in_bytes')]:
        try:
            used, maximum = int(Path(usage).read_text()), int(Path(limit).read_text())
            return used > maximum * .85 or maximum - used < 256 * 1024 * 1024
        except (OSError, ValueError):
            continue
    return False


def safe_name(name):
    name = re.sub(r'[\x00-\x1f\x7f/\\]', '_', name).strip()[:180]
    return name if name and name not in {'.', '..'} else 'download'


@dataclass
class DownloadItem:
    token: str
    page: Page
    session: str
    download: Download
    name: str
    state: str = 'receiving'
    path: Path | None = None
    size: int = 0
    expires: float = field(default_factory=lambda: time.monotonic() + TRANSFER_TTL)
    task: asyncio.Task | None = None

    def message(self):
        return {'type':'download', 'token':self.token, 'name':self.name,
                'state':self.state, 'size':self.size}


class Downloads:
    def __init__(self):
        self.directory: Path | None = None
        self.items: dict[str, DownloadItem] = {}
        self.pages: dict[Page, dict] = {}
        self.sweeper = None

    def prepare(self):
        # This application is Linux/X11-only. Do not fall back to persistent
        # /tmp if tmpfs is unavailable: failing closed preserves this promise.
        if not Path('/dev/shm').is_dir():
            raise RuntimeError('Download staging requires /dev/shm (tmpfs)')
        mounts = [line.split() for line in Path('/proc/mounts').read_text().splitlines()]
        storage = max((len(parts[1]), parts[2]) for parts in mounts
                      if Path(parts[1]) == ROOT or Path(parts[1]) in ROOT.parents)[1]
        if storage != 'tmpfs':
            raise RuntimeError('Download staging must be mounted as tmpfs')
        ROOT.mkdir(mode=0o700, exist_ok=True)
        if ROOT.is_symlink():
            raise RuntimeError('Download staging must not be a symlink')
        ROOT.chmod(0o700)
        for path in ROOT.iterdir():
            if path.name.startswith('run-') and path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        self.directory = Path(tempfile.mkdtemp(prefix='run-', dir=ROOT))
        self.sweeper = asyncio.create_task(self._sweep())
        return str(self.directory)

    def attach(self, page):
        self.pages[page] = {'session':None, 'subscribers':{}, 'offline':time.monotonic()}
        page.on('download', lambda download: self._started(page, download))
        page.on('close', lambda: asyncio.create_task(self.close_page(page)))

    def watch_context(self, context):
        def watch(page):
            async def unowned(download):
                if page in self.pages:
                    return  # The managed page's listener handles it.
                owner = await page.opener()
                while owner and owner not in self.pages:
                    owner = await owner.opener()
                if owner:
                    self._started(owner, download)
                else:
                    await self._discard(download)
            page.on('download', unowned)
        context.on('page', watch)
        for page in context.pages:
            watch(page)

    def interacted(self, page, session):
        if page in self.pages:
            self.pages[page]['session'] = session

    def subscribe(self, page, session):
        state = self.pages[page]
        queue = asyncio.Queue(maxsize=16)
        state['subscribers'][queue] = session
        for item in self.items.values():
            if item.page == page and item.session == session:
                queue.put_nowait(item.message())
        return queue

    def unsubscribe(self, page, queue):
        state = self.pages.get(page)
        if state:
            state['subscribers'].pop(queue, None)
            state['offline'] = time.monotonic()

    def emit(self, page, session, message):
        for queue, owner in self.pages.get(page, {}).get('subscribers', {}).items():
            if owner == session:
                if queue.full(): queue.get_nowait()
                queue.put_nowait(message)

    def _started(self, page, download):
        state = self.pages.get(page)
        session = state['session'] if state else None
        count = sum(item.page == page for item in self.items.values())
        if not session or count >= MAX_PER_PAGE or len(self.items) >= MAX_TOTAL or memory_pressure():
            asyncio.create_task(self._discard(download))
            if session:
                self.emit(page, session, {'type':'download_error', 'message':'ظرفیت دانلود یا حافظهٔ موقت پر است؛ کمی صبر کنید و دوباره دانلود را بزنید.'})
            return
        item = DownloadItem(secrets.token_urlsafe(24), page, session, download, safe_name(download.suggested_filename))
        self.items[item.token] = item
        self.emit(page, session, item.message())
        item.task = asyncio.create_task(self._receive(item))

    async def _discard(self, download):
        try:
            await asyncio.wait_for(download.cancel(), timeout=5)
        except Exception:
            pass  # Already cancelled/completed downloads can reject cancel.
        try:
            await asyncio.wait_for(download.delete(), timeout=5)
        except Exception:
            # Playwright rejects delete() for a cancelled artifact because
            # pathAfterFinished raises "canceled". Chromium removes its partial
            # file; the orphan sweep remains a fallback for any leftover.
            try:
                if await asyncio.wait_for(download.failure(), timeout=1) == 'canceled':
                    return
            except Exception:
                pass
            logger.warning('DOWNLOAD_DISCARD_FAILED; staging sweep will retry')

    async def _receive(self, item):
        try:
            path = await asyncio.wait_for(item.download.path(), timeout=TRANSFER_TTL)
            if self.items.get(item.token) is not item:
                return
            if not path or not self.directory or Path(path).parent != self.directory:
                raise ValueError('Unexpected download artifact')
            item.path = Path(path)
            item.size = item.path.stat().st_size
            if item.size > MAX_BYTES:
                raise ValueError('Download too large')
            item.state, item.expires = 'ready', time.monotonic() + READY_TTL
            self.emit(item.page, item.session, item.message())
        except asyncio.CancelledError:
            raise
        except Exception:
            self.emit(item.page, item.session, {'type':'download_error', 'token':item.token,
                'message':'دانلود ناموفق بود یا از سقف ۱۰۰ مگابایت گذشت؛ دوباره از سایت دانلود کنید.'})
            await self.remove(item)

    def require(self, page, session, token):
        item = self.items.get(token)
        if not item or item.page != page or item.session != session or item.expires <= time.monotonic():
            raise HTTPException(404, 'دانلود موجود نیست یا منقضی شده است؛ دوباره از سایت دانلود کنید.')
        return item

    def claim(self, page, session, token):
        item = self.require(page, session, token)
        if item.state != 'ready':
            raise HTTPException(409, 'این دانلود هنوز آماده نیست یا قبلاً دریافت شده است.')
        item.state, item.expires = 'sending', time.monotonic() + TRANSFER_TTL
        return item

    async def remove(self, item):
        if self.items.pop(item.token, None) is None:
            return
        if item.task and item.task is not asyncio.current_task() and not item.task.done():
            item.task.cancel()
            await asyncio.gather(item.task, return_exceptions=True)
        await self._discard(item.download)
        if item.path:
            item.path.unlink(missing_ok=True)
        self.emit(item.page, item.session, {'type':'download_removed','token':item.token})

    async def close_page(self, page):
        for item in list(self.items.values()):
            if item.page == page:
                await self.remove(item)
        self.pages.pop(page, None)

    async def _sweep(self):
        while True:
            await asyncio.sleep(1)
            try:
                await self.sweep_once()
            except Exception:
                logger.exception('DOWNLOAD_SWEEP_FAILED; retrying')

    async def sweep_once(self):
        now, sessions, pressure = time.monotonic(), live_sessions(), memory_pressure()
        # Cancel growing downloads before they exhaust the bounded tmpfs.
        oversized = False
        if self.directory:
            known = {item.path for item in self.items.values()}
            receiving = any(item.state == 'receiving' for item in self.items.values())
            for path in self.directory.iterdir():
                try:
                    if not path.is_file(): continue
                    stat = path.stat()
                    oversized |= stat.st_size > MAX_BYTES
                    # Retry cleanup for artifacts whose browser delete
                    # failed, but never touch an in-progress writer.
                    if not receiving and path not in known and time.time() - stat.st_mtime > DISCONNECT_GRACE:
                        path.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass
        for item in list(self.items.values()):
            state = self.pages.get(item.page)
            online = state and item.session in state['subscribers'].values()
            offline = not state or (not online and now - state['offline'] > DISCONNECT_GRACE)
            if item.session not in sessions or now > item.expires or offline or ((oversized or pressure) and item.state == 'receiving'):
                await self.remove(item)

    async def stop(self):
        if self.sweeper:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
            self.sweeper = None
        for item in list(self.items.values()):
            await self.remove(item)
        self.pages.clear()
        if self.directory:
            shutil.rmtree(self.directory, ignore_errors=True)
            self.directory = None


downloads = Downloads()
