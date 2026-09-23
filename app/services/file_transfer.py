"""User-selected bytes -> an authenticated page's file input/paste target.

Never accepts server paths or reads the desktop clipboard. Pending targets
are short-lived and bound to the login session that initiated the action.
"""
import asyncio
import base64
import binascii
import json
import re
import secrets
import time
from dataclasses import dataclass

from playwright.async_api import ElementHandle, Frame, Page

MAX_BYTES = 20 * 1024 * 1024
MAX_FILES = 8
MAX_BODY = ((MAX_BYTES + 2) // 3) * 4 + 65536
UPLOAD_SLOTS = asyncio.Semaphore(2)


async def dispose(element):
    try:
        await element.dispose()
    except Exception:
        pass  # Navigation/closing the page may already have released it.


class TransferError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def decode_files(body: bytes) -> list[dict]:
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) != {'files'}:
            raise ValueError()
        files = payload['files']
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
            raise ValueError()
        result, size = [], 0
        for item in files:
            if not isinstance(item, dict) or set(item) != {'name', 'type', 'data'}:
                raise ValueError()
            if not all(isinstance(item[k], str) for k in item):
                raise ValueError()
            if len(item['name']) > 255 or len(item['type']) > 128:
                raise ValueError()
            data = base64.b64decode(item['data'], validate=True)
            size += len(data)
            if size > MAX_BYTES:
                raise TransferError('مجموع فایل‌ها باید حداکثر ۲۰ مگابایت باشد.', 413)
            name = re.sub(r'[\x00-\x1f\x7f]', '_', item['name'].replace('\\', '/').rsplit('/', 1)[-1])
            result.append({'name': name or 'upload.bin', 'mimeType': item['type'] or 'application/octet-stream', 'buffer': data})
        return result
    except (ValueError, TypeError, KeyError, binascii.Error):
        raise TransferError('اطلاعات فایل نامعتبر است.', 400)


def is_image(file: dict) -> bool:
    data, mime = file['buffer'], file['mimeType']
    return ((mime == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n')) or
            (mime == 'image/jpeg' and data.startswith(b'\xff\xd8\xff')) or
            (mime == 'image/gif' and data[:6] in (b'GIF87a', b'GIF89a')) or
            (mime == 'image/webp' and data[:4] == b'RIFF' and data[8:12] == b'WEBP'))


@dataclass
class Target:
    token: str
    session: str
    kind: str
    element: ElementHandle
    frame: Frame
    expires: float
    multiple: bool = True
    accept: str = ''

    def notification(self):
        return {'type': 'filechooser', 'token': self.token, 'multiple': self.multiple, 'accept': self.accept}


class PageTransfers:
    def __init__(self):
        self.session = None
        self.pending: Target | None = None
        self.subscribers: dict[asyncio.Queue, str] = {}

    def emit(self, message, session):
        for queue, owner in self.subscribers.items():
            if owner == session:
                if queue.full(): queue.get_nowait()
                queue.put_nowait(message)

    def clear(self):
        if self.pending:
            self.emit({'type': 'filechooser_closed', 'token': self.pending.token}, self.pending.session)
            asyncio.create_task(dispose(self.pending.element))
        self.pending = None


class FileTransfers:
    def __init__(self):
        self.pages: dict[Page, PageTransfers] = {}

    async def attach(self, page: Page):
        state = PageTransfers()
        self.pages[page] = state

        async def chooser_opened(chooser):
            # Registering this handler makes Playwright suppress the native
            # OS picker before it can obscure the captured Chromium window.
            element, session = chooser.element, state.session
            if not session:
                await dispose(element)
                return
            try:
                if await element.get_attribute('webkitdirectory') is not None:
                    state.emit({'type': 'file_error', 'message': 'انتخاب پوشه پشتیبانی نمی‌شود؛ فایل‌ها را جداگانه انتخاب کنید.'}, session)
                    await dispose(element)
                    return
                frame = await element.owner_frame()
                if not frame:
                    await dispose(element)
                    return
                state.clear()
                target = Target(secrets.token_urlsafe(24), session, 'input', element, frame,
                                time.monotonic() + 300, chooser.is_multiple(), (await element.get_attribute('accept')) or '')
                state.pending = target
                state.emit(target.notification(), target.session)
            except Exception:
                await dispose(element)
                state.emit({'type':'file_error', 'message':'انتخاب فایل انجام نشد؛ دوباره دکمهٔ آپلود سایت را بزنید.'}, session)

        def navigation(frame):
            if state.pending and (frame == page.main_frame or frame == state.pending.frame):
                state.clear()

        page.on('filechooser', chooser_opened)
        page.on('framenavigated', navigation)
        page.on('framedetached', navigation)
        def closed(*_):
            state.clear()
            self.pages.pop(page, None)
        page.on('close', closed)

    def interacted(self, page, session):
        self.pages[page].session = session

    def subscribe(self, page, session):
        state = self.pages[page]
        queue = asyncio.Queue(maxsize=2)
        state.subscribers[queue] = session
        if state.pending and state.pending.session == session and state.pending.expires > time.monotonic() and state.pending.kind == 'input':
            queue.put_nowait(state.pending.notification())
        return queue

    def unsubscribe(self, page, queue):
        if page in self.pages: self.pages[page].subscribers.pop(queue, None)

    def require(self, page, session, token):
        state = self.pages.get(page)
        target = state.pending if state else None
        if not target or target.token != token or target.session != session or target.expires <= time.monotonic():
            raise TransferError('درخواست فایل منقضی یا لغو شده است؛ دوباره آپلود یا Paste را بزنید.')
        return target

    async def cancel(self, page, session, token):
        target = self.require(page, session, token)
        self.pages[page].pending = None
        self.pages[page].emit({'type': 'filechooser_closed', 'token': token}, session)
        if target.kind == 'input':
            try:
                await target.element.evaluate("el => el.dispatchEvent(new Event('cancel', {bubbles:true}))")
            except Exception:
                pass
        await dispose(target.element)
        return {'ok': True}

    async def prepare_paste(self, page: Page, session: str):
        frame = page.main_frame
        # Follow the focus chain into cross-origin frames without reading
        # other users' pages or changing X11's global focus.
        while True:
            handle = await frame.evaluate_handle('() => {let el=document.activeElement;while(el?.shadowRoot?.activeElement)el=el.shadowRoot.activeElement;return el || document.body}')
            element = handle.as_element()
            if not element:
                await handle.dispose()
                raise TransferError('ابتدا داخل محل نوشتن سایت کلیک کنید.')
            # Inspect only the focused element. Enumerating all child frames
            # races with unrelated iframe removal/reload and can break paste.
            child = await element.content_frame()
            if not child:
                break
            await dispose(element)
            frame = child
        state = self.pages[page]
        state.clear()
        target = Target(secrets.token_urlsafe(24), session, 'paste', element, frame, time.monotonic() + 300)
        state.pending = target
        return {'token': target.token}

    async def deliver(self, page, session, token, files):
        target = self.require(page, session, token)
        if not target.multiple and len(files) > 1:
            raise TransferError('این بخش سایت فقط یک فایل می‌پذیرد.', 400)
        if target.kind == 'paste' and not all(is_image(file) for file in files):
            raise TransferError('برای Paste عکس، PNG، JPEG، GIF یا WebP معتبر انتخاب کنید.', 415)
        # Consume once before dispatch. A duplicate HTTP request must not
        # upload the same files again. Detached/navigated targets fail safely.
        self.pages[page].pending = None
        try:
            if target.kind == 'input':
                await target.element.set_input_files(files, timeout=15000)
                result = {'ok': True, 'kind': 'input'}
            else:
                encoded = [{'name':f['name'], 'type':f['mimeType'], 'data':base64.b64encode(f['buffer']).decode('ascii')} for f in files]
                handled = await target.element.evaluate('''(el, files) => {
                    if (!el.isConnected) throw Error('Paste target removed');
                    const data = new DataTransfer();
                    for (const file of files) {
                        const bytes = Uint8Array.from(atob(file.data), c => c.charCodeAt(0));
                        data.items.add(new File([bytes], file.name, {type:file.type}));
                    }
                    const event = new ClipboardEvent('paste', {clipboardData:data, bubbles:true, cancelable:true, composed:true});
                    el.dispatchEvent(event);
                    return event.defaultPrevented;
                }''', encoded)
                result = {'ok': True, 'kind': 'paste', 'handled': handled}
            return result
        except Exception as exc:
            raise TransferError('محل آپلود یا Paste تغییر کرده است؛ دوباره تلاش کنید.') from exc
        finally:
            await dispose(target.element)


file_transfers = FileTransfers()
