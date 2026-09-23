import asyncio
import logging
import os
import shutil
import socket
import subprocess
from urllib.parse import quote
from typing import Dict, Optional
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from app.config import get_settings
from app.services.clipboard_bridge import clipboard_bridge

logger = logging.getLogger(__name__)
settings = get_settings()


class BrowserManager:
    """Owns the single persistent Chromium context used by the whole service."""

    def __init__(self) -> None:
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.process: Optional[subprocess.Popen] = None
        self.pages: Dict[str, Page] = {}
        self.window_ids: Dict[str, int] = {}
        self.window_slots: Dict[str, int] = {}
        self.window_sizes: Dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._stopping = False
        self._ignore_next_close = False
        self.restore_callback = None

    @property
    def running(self) -> bool:
        return self.context is not None

    def get_context(self) -> Optional[BrowserContext]:
        return self.context

    async def start(self) -> BrowserContext:
        async with self._lock:
            if self.context is not None:
                return self.context
            os.makedirs(settings.browser_data_dir, exist_ok=True)
            self.playwright = await async_playwright().start()
            if settings.browser_connect_over_cdp and not settings.browser_headless:
                self.context = await self._start_real_chromium()
            else:
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=settings.browser_data_dir,
                    headless=settings.browser_headless,
                    viewport={"width": settings.default_width, "height": settings.default_height},
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            self.context.on("close", lambda: asyncio.create_task(self._handle_context_close()))
            logger.info("BROWSER_STARTED persistent_profile=%s mode=%s", settings.browser_data_dir,
                        "external-headful-cdp" if self.process else "playwright")
            return self.context

    async def _start_real_chromium(self) -> BrowserContext:
        """Start headed Chromium with its persistent profile and attach CDP.

        This is an ordinary desktop browser process, not a headless browser.
        Third-party bot checks can still challenge it; no fingerprint spoofing
        or CAPTCHA bypass is installed here.
        """
        assert self.playwright is not None
        executable = settings.browser_executable_path or self.playwright.chromium.executable_path
        if not os.path.exists(executable):
            executable = shutil.which(executable) or executable
        self._clear_stale_profile_locks()
        port = settings.browser_debug_port
        args = [
            executable,
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={settings.browser_data_dir}",
            "--window-size=240,160",
            "--window-position=7400,4100",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--no-sandbox",
            "--app=data:text/html,<title>Browser service</title>",
        ]
        # Never leave an unread stderr pipe: Chromium can fill it and freeze.
        self.process = subprocess.Popen(args, env=os.environ.copy(), stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, text=True)
        endpoint = f"http://127.0.0.1:{port}"
        last_error: Exception | None = None
        for _ in range(80):
            if self.process.poll() is not None:
                error = self.process.stderr.read().strip() if self.process.stderr else ""
                raise RuntimeError(f"Chromium exited with code {self.process.returncode}: {error[-1000:]}")
            try:
                self.browser = await self.playwright.chromium.connect_over_cdp(endpoint, timeout=2000)
                if self.browser.contexts:
                    return self.browser.contexts[0]
            except Exception as exc:
                last_error = exc
            await asyncio.sleep(0.25)
        raise RuntimeError(f"Could not connect to Chromium CDP: {last_error}")

    def _clear_stale_profile_locks(self) -> None:
        """Remove lock symlinks left by an abruptly killed container.

        Chromium writes Singleton* links into the profile. A Docker restart can
        leave them behind even though the owning process no longer exists. We
        only remove links whose recorded host differs from this container (or
        whose recorded PID is definitely gone), never an active same-host lock.
        """
        profile = settings.browser_data_dir
        lock = os.path.join(profile, "SingletonLock")
        try:
            target = os.readlink(lock)
        except OSError:
            return
        parts = target.rsplit("-", 1)
        current_host = socket.gethostname()
        stale = len(parts) != 2 or parts[0] != current_host
        if not stale:
            try:
                stale = not os.path.exists(f"/proc/{int(parts[1])}")
            except (ValueError, OSError):
                stale = True
        if not stale:
            return
        for name in ("SingletonCookie", "SingletonLock", "SingletonSocket"):
            try:
                os.unlink(os.path.join(profile, name))
            except FileNotFoundError:
                pass
        logger.warning("REMOVED_STALE_CHROMIUM_PROFILE_LOCK profile=%s", profile)

    async def stop(self) -> None:
        self._stopping = True
        self._ignore_next_close = self.context is not None
        async with self._lock:
            if self.context:
                if self.browser:
                    await self.browser.close()
                else:
                    await self.context.close()
            self.context = None
            self.pages.clear()
            self.window_ids.clear()
            self.window_slots.clear()
            self.window_sizes.clear()
            if self.playwright:
                await self.playwright.stop()
        self.playwright = None
        self.browser = None
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    await asyncio.to_thread(self.process.wait, 5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None
        if not self._ignore_next_close:
            self._ignore_next_close = False
        self._stopping = False

    async def _handle_context_close(self) -> None:
        if self._ignore_next_close:
            self._ignore_next_close = False
            return
        await self._recover_after_crash()

    async def restart(self) -> BrowserContext:
        await self.stop()
        return await self.start()

    async def _recover_after_crash(self) -> None:
        if self._stopping:
            return
        logger.error("BROWSER_CONTEXT_CLOSED attempting recovery")
        try:
            self.context = None
            self.browser = None
            self.pages.clear()
            self.window_ids.clear()
            self.window_slots.clear()
            self.window_sizes.clear()
            if self.process and self.process.poll() is None:
                self.process.kill()
            self.process = None
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
            await self.start()
            if self.restore_callback:
                await self.restore_callback()
        except Exception as exc:
            logger.exception("BROWSER_RECOVERY_FAILED error=%s", exc)

    async def create_page(self, page_id: str, url: str = "about:blank") -> Page:
        """Create a real Chromium app window in the shared browser process.

        Chromium forwards a second invocation that uses the same profile to
        the existing process.  That gives every user an independent native
        top-level window while cookies and storage still belong to one shared
        persistent profile.  The native window can then be exported alone by
        x11vnc, without exposing anybody else's tab or the desktop.
        """
        if not self.context:
            await self.start()
        assert self.context is not None
        slot = next((n for n in range(12) if n not in self.window_slots.values()), None)
        if slot is None:
            raise RuntimeError("All 12 browser window slots are occupied")
        executable = settings.browser_executable_path or (
            self.playwright.chromium.executable_path if self.playwright else "chromium"
        )
        marker = f"shared-tab-{page_id}"
        html = quote(f"<!doctype html><meta charset=utf-8><title>{marker}</title>")
        command = [
            executable,
            f"--user-data-dir={settings.browser_data_dir}",
            f"--app=data:text/html,{html}",
            f"--window-size={settings.vnc_view_width},{settings.vnc_view_height}",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",
        ]
        try:
            async with self.context.expect_page(timeout=12000) as page_info:
                forwarded = await asyncio.create_subprocess_exec(
                    *command,
                    env=os.environ.copy(),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(forwarded.wait(), timeout=10)
            page = await page_info.value
        except Exception:
            logger.exception("APP_WINDOW_CREATE_FAILED page=%s", page_id)
            raise
        self.pages[page_id] = page
        xid = await self._find_native_window(marker)
        self.window_ids[page_id] = xid
        self.window_slots[page_id] = slot
        await self.resize_page(page_id, settings.vnc_view_width, settings.vnc_view_height)
        await clipboard_bridge.attach(page)
        if url and url != "about:blank":
            try:
                # Startup does not wait for all subresources to load.
                await page.goto(url, wait_until="commit", timeout=5000)
            except Exception as exc:
                logger.warning("PAGE_NAVIGATION_FAILED page=%s error=%s", page_id, exc)
        return page

    async def _find_native_window(self, marker: str) -> int:
        last_output = ""
        for _ in range(60):
            process = await asyncio.create_subprocess_exec(
                "xdotool", "search", "--onlyvisible", "--name", f"^{marker}$",
                env=os.environ.copy(), stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            last_output = (stdout + stderr).decode(errors="replace").strip()
            if process.returncode == 0 and stdout.strip():
                return int(stdout.decode().splitlines()[-1])
            await asyncio.sleep(0.1)
        raise RuntimeError(f"Could not find native Chromium window {marker}: {last_output}")

    async def resize_page(self, page_id: str, width: int, height: int) -> dict:
        width, height = max(280, min(1600, width)), max(200, min(900, height))
        size = {"width": width, "height": height}
        if self.window_sizes.get(page_id) == size:
            return size
        xid, slot = self.window_ids[page_id], self.window_slots[page_id]
        x, y = (slot % 4) * 2048, (slot // 4) * 1400
        process = await asyncio.create_subprocess_exec(
            "xdotool", "windowmove", "--sync", str(xid), str(x), str(y),
            "windowsize", str(xid), str(width), str(height),
            env=os.environ.copy(), stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=5)
        await self.pages[page_id].set_viewport_size(size)
        self.window_sizes[page_id] = size
        return size

    def get_page(self, page_id: str) -> Optional[Page]:
        return self.pages.get(page_id)

    async def close_page(self, page_id: str) -> None:
        page = self.pages.pop(page_id, None)
        self.window_ids.pop(page_id, None)
        self.window_slots.pop(page_id, None)
        self.window_sizes.pop(page_id, None)
        if page and not page.is_closed():
            await page.close()


browser_manager = BrowserManager()
