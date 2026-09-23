import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from playwright.async_api import Page
from app.models import BrowserTab, User
from app.services.browser_manager import BrowserManager
from app.services.stream_manager import StreamManager

logger = logging.getLogger(__name__)


class TabManager:
    def __init__(self, browser: BrowserManager, streams: StreamManager) -> None:
        self.browser = browser
        self.streams = streams
        self._lock = asyncio.Lock()

    async def _wire_page(self, tab: BrowserTab, page: Page) -> None:
        self.browser.pages[tab.page_id] = page

        async def navigated(frame):
            if frame != page.main_frame:
                return
            try:
                from app.db import SessionLocal
                with SessionLocal() as local:
                    record = local.get(BrowserTab, tab.id)
                    if record:
                        record.url = page.url
                        record.title = await page.title()
                        record.last_seen = datetime.now(timezone.utc)
                        local.commit()
                await self.streams.broadcast_json(tab.page_id, {"type": "state", "url": page.url, "title": await page.title()})
            except Exception as exc:
                logger.debug("NAVIGATION_EVENT_FAILED tab=%s error=%s", tab.id, exc)

        async def closed(_page=None):
            if self.browser.get_page(tab.page_id) != page:
                return
            self.browser.pages.pop(tab.page_id, None)
            self.browser.window_ids.pop(tab.page_id, None)
            self.browser.window_slots.pop(tab.page_id, None)
            self.browser.window_sizes.pop(tab.page_id, None)
            await self.streams.stop(tab.page_id)

        page.on("framenavigated", navigated)
        page.on("close", closed)

    async def restore_pages(self, db: Session) -> None:
        """Recreate active DB mappings after a process/browser restart."""
        async with self._lock:
            for tab in db.query(BrowserTab).filter(BrowserTab.is_active.is_(True)).all():
                old = self.browser.get_page(tab.page_id)
                if old and not old.is_closed():
                    continue
                page = await self.browser.create_page(tab.page_id, tab.url or "about:blank")
                await self._wire_page(tab, page)
                try:
                    tab.url = page.url
                    # Title evaluation can hang while a just-restored SPA is
                    # still booting; never block application startup on it.
                    tab.title = await asyncio.wait_for(page.title(), timeout=3)
                except Exception:
                    tab.title = tab.title or "Shared tab"
            db.commit()

    async def get_or_create(self, db: Session, user: User) -> BrowserTab:
        async with self._lock:
            tab = db.query(BrowserTab).filter(BrowserTab.user_id == user.id, BrowserTab.is_active.is_(True)).first()
            if tab:
                page = self.browser.get_page(tab.page_id)
                if not page or page.is_closed():
                    page = await self.browser.create_page(tab.page_id, tab.url or "about:blank")
                    await self._wire_page(tab, page)
                return tab
            tab = BrowserTab(user_id=user.id, page_id=str(uuid.uuid4()), url="about:blank", title="New tab")
            db.add(tab)
            db.commit()
            db.refresh(tab)
            page = await self.browser.create_page(tab.page_id)
            await self._wire_page(tab, page)
            logger.info("TAB_CREATED user=%s tab=%s", user.username, tab.id)
            return tab

    def get_page(self, tab: BrowserTab) -> Optional[Page]:
        return self.browser.get_page(tab.page_id)

    async def reset(self, db: Session, user: User) -> None:
        tab = db.query(BrowserTab).filter(BrowserTab.user_id == user.id).first()
        if not tab:
            return
        await self.streams.stop(tab.page_id)
        await self.browser.close_page(tab.page_id)
        db.delete(tab)
        db.commit()


tab_manager: Optional[TabManager] = None
