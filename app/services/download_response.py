import asyncio
import time
from urllib.parse import quote

from fastapi.responses import StreamingResponse

from app.db import SessionLocal
from app.services.auth_service import get_user_from_token
from app.services.download_manager import downloads, TRANSFER_TTL


class DownloadResponse(StreamingResponse):
    """Always clean the single-use artifact, including pre-body disconnects."""

    def __init__(self, item, cookie):
        self.item = item

        async def chunks():
            checked = 0
            with item.path.open('rb') as source:
                while downloads.items.get(item.token) is item:
                    if time.monotonic() - checked >= 1:
                        with SessionLocal() as db:
                            user = get_user_from_token(cookie, db)
                            if not user or user.session_id != item.session:
                                return
                        checked = time.monotonic()
                    data = source.read(64 * 1024)
                    if not data:
                        break
                    yield data
                    await asyncio.sleep(0)  # Let disconnect/revocation cleanup run.

        super().__init__(chunks(), media_type='application/octet-stream', headers={
            'Content-Length': str(item.size),
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(item.name, safe=''),
            'Cache-Control':'no-store, private',
            'X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"default-src 'none'; sandbox",
        })

    async def __call__(self, scope, receive, send):
        try:
            async with asyncio.timeout(TRANSFER_TTL):
                await super().__call__(scope, receive, send)
        finally:
            async def cleanup():
                try:
                    # Explicitly close the generator's descriptor even when
                    # send() failed before it could resume after a yield.
                    await self.body_iterator.aclose()
                finally:
                    await downloads.remove(self.item)
            await asyncio.shield(cleanup())
