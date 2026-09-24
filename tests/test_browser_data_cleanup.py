import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import asyncio
from pathlib import Path
from app.services.browser_manager import _clear_profile_browsing_data, BrowserManager


class BrowserDataCleanupTests(unittest.TestCase):
    def test_resets_entire_profile_without_touching_siblings(self):
        with tempfile.TemporaryDirectory(prefix='qa-profile-', dir='/dev/shm') as root:
            sibling=Path(root)/'app.db'; sibling.write_text('application data')
            profile=Path(root)/'chromium-profile'; profile.mkdir()
            (profile/'Local State').write_text('{}')
            default=profile/'Default'; default.mkdir()
            (default/'History').write_text('history')
            (default/'Cookies').write_text('cookies')
            (default/'Login Data').write_text('passwords')
            (default/'Preferences').write_text('keep')
            (default/'Bookmarks').write_text('keep')
            (default/'Cache').mkdir(); (default/'Cache'/'item').write_text('cache')
            (default/'IndexedDB').mkdir(); (default/'IndexedDB'/'site').write_text('site')
            result=_clear_profile_browsing_data(str(profile))
            self.assertGreaterEqual(result['files'],1)
            self.assertGreaterEqual(result['directories'],1)
            self.assertFalse((default/'Preferences').exists())
            self.assertFalse((default/'Bookmarks').exists())
            self.assertEqual(sibling.read_text(),'application data')
            self.assertEqual(list(profile.iterdir()),[])
            self.assertFalse((default/'History').exists())
            self.assertFalse((default/'Cookies').exists())
            self.assertFalse((default/'Cache').exists())
            self.assertFalse((default/'IndexedDB').exists())

    def test_refuses_broad_paths(self):
        with self.assertRaises(RuntimeError): _clear_profile_browsing_data('/')
        with self.assertRaises(RuntimeError): _clear_profile_browsing_data('/tmp')

    def test_requires_profile_markers_and_rejects_symlink_root(self):
        with tempfile.TemporaryDirectory(prefix='qa-profile-',dir='/dev/shm') as root:
            profile=Path(root)/'profile'; profile.mkdir()
            with self.assertRaises(RuntimeError): _clear_profile_browsing_data(str(profile))
            (profile/'Local State').write_text('{}'); (profile/'Default').mkdir()
            alias=Path(root)/'alias'; alias.symlink_to(profile,target_is_directory=True)
            with self.assertRaises(RuntimeError): _clear_profile_browsing_data(str(alias))
            outside=Path(root)/'outside'; outside.mkdir(); (outside/'secret').write_text('keep')
            (profile/'Default'/'linked-cache').symlink_to(outside,target_is_directory=True)
            _clear_profile_browsing_data(str(profile))
            self.assertEqual((outside/'secret').read_text(),'keep')

    def test_rejects_database_overlap(self):
        with tempfile.TemporaryDirectory(prefix='qa-profile-',dir='/dev/shm') as root:
            profile=Path(root); (profile/'Local State').write_text('{}'); (profile/'Default').mkdir()
            with patch('app.services.browser_manager.settings.database_url',f'sqlite:///{root}/app.db'):
                with self.assertRaises(RuntimeError): _clear_profile_browsing_data(root)
            self.assertTrue((profile/'Default').exists())


class MaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_competing_browser_start(self):
        manager=BrowserManager(); manager.maintenance_owner=object()
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught: await manager.start()
        self.assertEqual(caught.exception.status_code,503)

    async def test_restart_attempted_after_cleanup_failure(self):
        manager=BrowserManager(); manager.stop=AsyncMock(); manager.start=AsyncMock()
        with patch('app.services.browser_manager._clear_profile_browsing_data',side_effect=OSError('test failure')):
            with self.assertRaises(OSError): await manager.clear_browser_data()
        manager.stop.assert_awaited_once(); manager.start.assert_awaited_once()


if __name__=='__main__': unittest.main()
