import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from app.services.download_manager import Downloads, DownloadItem, safe_name
from app.services.download_response import DownloadResponse


class DownloadsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qa-downloads-', dir='/dev/shm')
        self.manager = Downloads()
        self.manager.directory = Path(self.temp.name)
        self.page = object()
        self.manager.pages[self.page] = {'session':'session','subscribers':{asyncio.Queue():'session'},'offline':0}
        self.path = self.manager.directory/'artifact'
        self.path.touch()
        self.download = AsyncMock()
        self.item = DownloadItem('token', self.page, 'session', self.download, 'test.txt',
                                 state='ready', path=self.path, size=0)
        self.manager.items['token'] = self.item

    async def asyncTearDown(self):
        await self.manager.stop()
        self.temp.cleanup()

    def test_safe_names(self):
        self.assertEqual(safe_name('../dir\\bad\nname.txt'), '.._dir_bad_name.txt')
        self.assertEqual(safe_name('..'), 'download')
        self.assertEqual(safe_name('آزمایش.txt'), 'آزمایش.txt')

    def test_claim_is_bound_and_single_use(self):
        for page,session,token in [(object(),'session','token'),(self.page,'other','token'),(self.page,'session','wrong')]:
            with self.assertRaises(HTTPException): self.manager.claim(page,session,token)
        self.assertIs(self.manager.claim(self.page,'session','token'),self.item)
        with self.assertRaises(HTTPException): self.manager.claim(self.page,'session','token')

    async def test_remove(self):
        await self.manager.remove(self.item)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.manager.items)
        self.download.cancel.assert_awaited_once()
        self.download.delete.assert_awaited_once()

    async def test_delete_even_if_cancel_fails(self):
        self.download.cancel.side_effect = RuntimeError('Already cancelled')
        await self.manager.remove(self.item)
        self.download.delete.assert_awaited_once()
        self.assertFalse(self.path.exists())

    async def test_cancelled_artifact_still_cleans_local_path(self):
        self.download.delete.side_effect = RuntimeError('canceled')
        self.download.failure.return_value = 'canceled'
        await self.manager.remove(self.item)
        self.assertFalse(self.path.exists())

    async def test_memory_pressure_stops_growing_download(self):
        self.item.state='receiving'
        with patch('app.services.download_manager.live_sessions',return_value={'session'}), \
             patch('app.services.download_manager.memory_pressure',return_value=True):
            await self.manager.sweep_once()
        self.assertFalse(self.path.exists())

    async def test_expired_cleanup(self):
        self.item.expires=time.monotonic()-1
        with patch('app.services.download_manager.live_sessions',return_value={'session'}):
            await self.manager.sweep_once()
        self.assertFalse(self.path.exists())

    async def test_revoked_session_cleanup(self):
        with patch('app.services.download_manager.live_sessions',return_value=set()):
            await self.manager.sweep_once()
        self.assertFalse(self.path.exists())

    async def test_disconnect_cleanup(self):
        self.manager.pages[self.page]['subscribers'].clear()
        with patch('app.services.download_manager.live_sessions',return_value={'session'}):
            await self.manager.sweep_once()
        self.assertFalse(self.path.exists())

    async def test_oversized_cleanup(self):
        self.item.state='receiving'
        with self.path.open('wb') as f: f.truncate(101*1024*1024)
        with patch('app.services.download_manager.live_sessions',return_value={'session'}):
            await self.manager.sweep_once()
        self.assertFalse(self.path.exists())

    async def test_response_cleanup_if_disconnect_before_body(self):
        response = DownloadResponse(self.item,'cookie')
        scope = {'type':'http','asgi':{'spec_version':'2.4'}}
        async def receive():
            # A real ASGI receive blocks until input arrives. An immediate
            # AsyncMock here creates a busy loop and unbounded call history.
            await asyncio.Event().wait()
        async def send(message):
            raise ConnectionError('test disconnect')
        with patch('app.services.download_response.downloads',self.manager):
            with self.assertRaises(Exception):
                await asyncio.wait_for(response(scope,receive,send),timeout=2)
        self.assertFalse(self.path.exists())

    async def test_startup_removes_only_owned_staging(self):
        # A dedicated isolated fixture, not any actual app download directory.
        with tempfile.TemporaryDirectory(prefix='qa-root-',dir='/dev/shm') as root:
            old=Path(root)/'run-old'; old.mkdir(); (old/'partial').touch()
            untouched=Path(root)/'unrelated'; untouched.touch()
            manager=Downloads()
            with patch('app.services.download_manager.ROOT',Path(root)):
                manager.prepare()
            self.assertFalse(old.exists()); self.assertTrue(untouched.exists())
            await manager.stop()


if __name__=='__main__': unittest.main()
