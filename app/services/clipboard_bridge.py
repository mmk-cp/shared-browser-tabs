"""Relay site Copy actions from the owning page, never the X11 clipboard."""
import asyncio
import json
import secrets
import time
from pathlib import Path

from playwright.async_api import Page

MAX_CLIPBOARD_TEXT = 1_000_000
SCRIPT = (Path(__file__).with_name('clipboard_bridge.js')).read_text()


class PageClipboard:
    def __init__(self):
        self.last_input = float('-inf')
        self.subscribers: set[asyncio.Queue] = set()

    def publish(self, text) -> bool:
        if not isinstance(text, str) or not text or len(text) > MAX_CLIPBOARD_TEXT:
            return False
        # Don't allow background page scripts to silently replace the user's
        # clipboard. Copy buttons may complete asynchronously after a click.
        if time.monotonic() - self.last_input > 5 or not self.subscribers:
            return False
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(text)
        return True


class ClipboardBridge:
    def __init__(self):
        self.pages: dict[Page, PageClipboard] = {}

    async def attach(self, page: Page):
        state = PageClipboard()
        self.pages[page] = state
        page.on('close', lambda *_: self.pages.pop(page, None))
        binding = '__sharedCopy_' + secrets.token_hex(16)

        def copied(source, text):
            return source['page'] == page and self.pages.get(page) is state and state.publish(text)

        await page.expose_binding(binding, copied)
        script = SCRIPT.replace('__BINDING_NAME__', json.dumps(binding))
        await page.add_init_script(script=script)
        # The initial app-mode marker page already exists at attachment time.
        await page.evaluate(script)

    def interacted(self, page: Page, event: dict):
        if event.get('type') in {'down', 'up', 'key_down', 'key_up', 'copy', 'select_all'}:
            state = self.pages.get(page)
            if state:
                state.last_input = time.monotonic()

    def subscribe(self, page: Page) -> asyncio.Queue:
        state = self.pages[page]
        queue = asyncio.Queue(maxsize=1)
        state.subscribers.add(queue)
        return queue

    def unsubscribe(self, page: Page, queue: asyncio.Queue):
        state = self.pages.get(page)
        if state:
            state.subscribers.discard(queue)


clipboard_bridge = ClipboardBridge()
