# Shared Browser Tabs

One headed Chromium process, one persistent profile, and an authenticated browser window for each user. Cookies, login sessions and local storage are shared. Each user sees and controls their own page.

## Run

```sh
cp .env.example .env
# Set SECRET_KEY and ADMIN_PASSWORD before exposing the service.
docker compose up -d --build
```

Open http://localhost:8000. The existing `.env` is used by Compose. Admin credentials create the first account only; changing the environment does not reset an existing password. SQLite is in `data/app.db`; cookies and browser sessions are in `browser-data/chromium-profile`. Neither directory is replaced during rebuilds.

## Accounts and single-session login

Admins can open `/admin` (the gear icon in the viewer toolbar) to list accounts and create users or additional admins. This responsive page and the user-management API both enforce the admin role. Passwords require at least 8 characters and at most 72 UTF-8 bytes.

Every successful login rotates a persisted random session identifier for that account. Previous cookies immediately stop authorizing HTTP requests and new WebSocket connections. Existing input streams recheck before every command; idle input and VNC streams close within about half a second. The previous viewer returns to login; the admin page checks every two seconds. Failed logins do not revoke sessions. Logout invalidates the server-side session too. Multiple tabs using the same login cookie remain part of the same session, not separate logins.

Startup adds the nullable `users.session_id` column to existing databases without deleting accounts or browser data. Pre-upgrade cookies require one fresh login. This affects application access, not the shared Chromium website cookies/profile.

## Using the browser

The browser fills the page. A slim toolbar on the right provides URL entry, reload, clipboard, a mobile keyboard and a focus mode that hides the toolbar. Focus mode changes only this viewer, without entering the device/browser fullscreen mode. The small edge arrow restores the toolbar.

- Enter a URL in the popover, then use the page directly.
- Type Persian or English. Ctrl+A/C/V, text selection, double-click and drag are supported.
- Right-click opens a viewer menu with copy, paste, select all, link actions, back and reload. It replaces the native Chromium menu, which is a separate X11 window and cannot be captured reliably by a single-window VNC stream.
- On phones, tap and swipe to click and scroll; long-press opens the context menu. Tap the keyboard button to type using the phone's keyboard. Site layout adapts to the viewer width.
- Clipboard access depends on the local browser's permissions. HTTPS (or localhost) is needed for automatic system-clipboard access. The clipboard panel is the fallback: paste there and send, or copy the selected remote text from the panel.

Noto Arabic, Latin and emoji fonts are installed in Chromium. The application's Arabic and Latin fonts are also served locally, without a third-party font CDN.

## Transport and isolation

`BrowserManager` starts system Chromium under Xvfb and creates an app-mode window for each database tab. All windows belong to the same browser context. `StreamManager` exports each native X11 window with x11vnc; noVNC renders its Tight-compressed RFB updates over an authenticated WebSocket. There is no CDP JPEG screenshot loop.

The VNC servers bind to container loopback, run **view-only**, and disable the shared X11 clipboard. Inputs travel on a separate ordered WebSocket targeted at the authenticated Playwright Page. This avoids X11's single global focus allowing one user to type into another user's window. Copy reads only the selection in that user's page, not a desktop-wide clipboard. Clients cannot choose a page ID or VNC port. Both WebSocket routes check session, account status and same-origin headers.

The Xvfb layout reserves twelve non-overlapping window slots. The initial browser size is 1920×1080 (Full HD). Individual viewports adapt to the available viewer area, from 280×200 to 1920×1300; larger clients are scaled proportionally. A 1920×1080 viewer in focus mode receives a native 1920×1080 framebuffer without upscaling. With the toolbar visible, its width is reserved outside the browser area. Mobile clients still get a responsive viewport. Closing a tab frees its slot. This is a shared browser-session application, not a security boundary between untrusted website origins: profile storage is shared by design.

## Practical limits

Chromium is headed with a persistent profile and without Playwright's automation launcher flags. This does **not** guarantee that ChatGPT, Cloudflare or another site accepts a session. IP reputation, account rules and site-side challenges remain external factors. A timeout is reported honestly instead of returning a false success for the previously loaded page. Challenges must be completed by the user where supported.

The viewer transports pixels and input. Audio, microphone/camera forwarding, local file upload/download transfer and arbitrary native browser dialogs are not implemented. The right-click menu provides the documented page actions; it is not the complete Chromium developer/menu interface.

Use HTTPS behind a reverse proxy for deployment, with WebSocket upgrades enabled and the original Host/Origin preserved. Set `COOKIE_SECURE=true` when served over HTTPS. Do not publish CDP or VNC ports. Stop the app before backing up the profile and database together.

## Verification

```sh
docker compose exec -T app python -m tests.e2e_remote
docker compose exec -T app python -m tests.e2e_accounts
curl -fsS http://localhost:8000/health
```

The end-to-end test creates two temporary users and a loopback-only test site, checks concurrent input isolation, Persian typing, clipboard round-trips, right-click, viewer expansion, responsive phone input and shared cookies, then removes only its test accounts and tabs. It does not type into the user's active tab or submit text to external sites. Screenshots are written inside the container to `/tmp/sbt-qa/`.

FastAPI endpoints remain under `/api/auth`, `/api/tabs`, `/api/users` and `/api/browser`. Admin-only APIs create users and restart the browser. The bundled Debian noVNC client is version 1.6; `WindowRFB` confines its x11vnc encoding compatibility override to one subclass, which should be re-tested when that package is upgraded.
