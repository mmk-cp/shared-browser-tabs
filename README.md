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

Public registration is available at `/register`, linked from the login page. It always creates a regular account with `is_active=false` and no session or browser tab. A correct password still cannot log in until an admin approves the account from `/admin`. Pending accounts appear first in the list. Admin-created accounts are approved immediately; existing active accounts remain approved without a database migration.

Authenticated users (including admins) can change their password at `/account`, linked from the viewer toolbar and admin header. The current password is required. A successful change invalidates all existing sessions and requires a fresh login with the new password. Password updates and logins check the previous credential state to prevent a concurrent request from reviving stale credentials. This is not a forgotten-password reset flow.

Admins can delete pending or approved regular users after a confirmation dialog. Deletion removes the application account and its tab mapping, revokes its session, and closes its native browser window/VNC stream. It does **not** clear cookies, website logins or storage in the shared Chromium profile, as those belong to all users. Deletion is permanent unless recovered from a database backup. Self-deletion and deletion of administrator accounts are rejected. For public internet deployment, apply signup/login rate limits at the reverse proxy in addition to HTTPS.

Every successful login rotates a persisted random session identifier for that account. Previous cookies immediately stop authorizing HTTP requests and new WebSocket connections. Existing input streams recheck before every command; idle input and VNC streams close within about half a second. The previous viewer returns to login; the admin page checks every two seconds. Failed logins do not revoke sessions. Logout invalidates the server-side session too. Multiple tabs using the same login cookie remain part of the same session, not separate logins.

Startup adds the nullable `users.session_id` column to existing databases without deleting accounts or browser data. Pre-upgrade cookies require one fresh login. This affects application access, not the shared Chromium website cookies/profile.

## Using the browser

The browser fills the page. A slim toolbar on the right provides a site picker (with manual URL entry for admins only), reload, clipboard, downloads, a mobile keyboard and a focus mode that hides the toolbar. Focus mode changes only this viewer, without entering the device/browser fullscreen mode. The small edge arrow restores the toolbar.

Chromium's native password-save bubble is disabled by policy and startup flags. This prevents a browser-chrome dialog from covering the stream and becoming impossible to dismiss through page-scoped input. Existing prompts disappear after the browser container is recreated; this does not delete passwords already stored in the shared profile.

- Select a card in the **انتخاب سایت** popover, then use the page directly. Admins can also enter a custom URL there.
- Type Persian or English. Ctrl+A/C/V, text selection, double-click and drag are supported.
- Right-click opens a viewer menu with copy, paste, select all, link actions, back and reload. It replaces the native Chromium menu, which is a separate X11 window and cannot be captured reliably by a single-window VNC stream.
- On phones, tap and swipe to click and scroll; long-press opens the context menu. Tap the keyboard button to type using the phone's keyboard. Site layout adapts to the viewer width.
- Clipboard access depends on the local browser's permissions. HTTPS (or localhost) is needed for automatic system-clipboard access. The clipboard panel is the fallback: paste there and send, or copy the selected remote text from the panel.
- Site Copy buttons are also relayed: text from `navigator.clipboard.writeText`, text ClipboardItems and legacy `execCommand('copy')` is delivered to that user's viewer, including child frames and after navigation. The focused viewer tries to write it to the device clipboard. If permissions or browser activation rules prevent this, the text appears in the panel; click **کپی در دستگاه من** (Copy to my device), or manually copy the selected text. The explicit button includes a legacy fallback for plain HTTP. A site's own “Copied” indicator means it handed text to the viewer; use the viewer's confirmation to know whether the device clipboard was updated.

Noto Arabic, Latin and emoji fonts are installed in Chromium. The application's Arabic and Latin fonts are also served locally, without a third-party font CDN.

### Admin-managed sites

Open **مدیریت سایت‌ها** from `/admin`, or go to `/admin/sites`. Admins can add, edit, hide/reactivate and delete catalog entries. Each entry has a required title (up to 80 characters), a complete HTTP(S) URL (up to 2048 characters, no embedded username/password), and an optional emoji/short text icon (up to 16 characters). The icon chooser and live card preview work on mobile too. Without an icon, the title's first character is used. Icons render as text; no remote favicons, external image requests, HTML or SVG uploads are used.

Regular users see searchable, keyboard-accessible cards, never the manual URL form. `Ctrl/Cmd+L` opens the picker. A blank/new browser opens the picker automatically; choosing a site closes it and restores the browser area. The catalog refreshes whenever the picker is opened; empty, no-match, loading and error states are displayed explicitly.

This is enforced server-side: `POST /api/tabs/me/navigate` is admin-only. Members use `POST /api/tabs/me/open-site` with **only `site_id`**; the server resolves its current URL and rejects hidden/deleted entries and extra URL fields. `GET /api/sites` lists active entries to signed-in users. `/api/sites/manage` and all catalog mutations require admin authorization; writes additionally require CSRF. Direct API calls cannot bypass these role checks. Deleting the highest-numbered entry does not recycle its ID on SQLite.

### Admin browser-data reset

The red **پاک‌سازی کامل داده‌های مرورگر** action on `/admin` is intentionally destructive: expand its warning, type **پاک شود**, then press the final delete button. It stops the shared Chromium process and resets its complete user-data directory, including history/download history, cache, cookies, saved passwords, local/IndexedDB/service-worker storage, permissions, sessions, bookmarks, extensions and browser settings. It then restarts Chromium and reopens application tabs at their recorded URLs. Unsaved work is lost and website logins are cleared. **Application accounts/passwords, application sessions, the admin site catalog and database are preserved**; recorded tab URLs are retained. Reopened sites can immediately create fresh history/cache/cookies. Downloaded files already saved on users' devices and external backups are not erased, and forensic secure erasure is not promised.

The operation requires admin authorization, CSRF and an explicit confirmation body; clients cannot supply paths. Before deletion, profile markers, path scope, symlink roots and database overlap are checked. Directory symlinks inside the profile are not followed. Maintenance blocks concurrent window creation and conflicting browser operations. Closing the admin page does not interrupt the operation. On a cleanup error Chromium startup is attempted, available tabs are restored, and a partial-failure warning is returned. There is no automatic backup of the deleted data. Tests exercise disposable profiles only, never the real shared profile.

The `sites` table is created additively on startup. Existing users, sessions, browser tabs, cookies and profile data are preserved; no demo sites are inserted. Existing tabs are not closed when a catalog entry is hidden or deleted. **This is a launcher/manual-address policy, not a domain/network allowlist or a locked-down kiosk**: links inside sites, redirects, sign-in flows, history and already-open pages remain usable. Enforcing a domain allowlist would require a separate navigation/network policy and could affect login, uploads and third-party resources.

## Transport and isolation

`BrowserManager` starts system Chromium under Xvfb and creates an app-mode window for each database tab. All windows belong to the same browser context. `StreamManager` exports each native X11 window with x11vnc; noVNC renders its Tight-compressed RFB updates over an authenticated WebSocket. There is no CDP JPEG screenshot loop.

The VNC servers bind to container loopback, run **view-only**, and disable the shared X11 clipboard. Inputs travel on a separate ordered WebSocket targeted at the authenticated Playwright Page. This avoids X11's single global focus allowing one user to type into another user's window. Copy reads only the selection in that user's page, not a desktop-wide clipboard. Clients cannot choose a page ID or VNC port. Both WebSocket routes check session, account status and same-origin headers.

Site-copy transport uses a per-Page binding and a bounded, ephemeral queue for that page's authenticated input sockets; it never reads the shared X11 clipboard. It requires input to that page within the previous five seconds. Old sessions are revalidated before delivery, and background viewers don't automatically overwrite the local clipboard. Text is limited to one million characters. Rich HTML is reduced to text; images/files and sites that bypass or replace the injected clipboard methods are not supported by this relay. Local clipboard reads are still explicit paste actions, not a background synchronization service.

### Responsiveness

The old `BROWSER_FPS` and `BROWSER_JPEG_QUALITY` variables belong to the retired screenshot transport; they do not control VNC. The current stream polls at a 16 ms interval with a 5 ms update deferral (not a guaranteed frame rate). Automatic VNC idle naps and blank-screen throttling are disabled because input arrives through CDP, which VNC cannot observe. CPU use while idle can consequently be higher. Tight uses compression level 2 and quality 8; photographic regions may use high-quality JPEG, while flat-color regions can use lossless encodings. WebSocket deflate is disabled to avoid compressing the already-compressed VNC stream again.

Input is consumed from a bounded queue. Consecutive pending hover positions are replaced by the newest position; compatible wheel deltas are summed. Click/key/text ordering, drag paths and command acknowledgements are retained. This prevents stale hover events from holding up later typing. Actual responsiveness also depends on server load and network latency/bandwidth. The remote resolution is capped at 1600×900 to reduce pixel processing and transfer compared with Full HD.

The Xvfb layout reserves twelve non-overlapping window slots. The initial browser size is 1600×900. Individual viewports adapt to the available viewer area, from 280×200 to 1600×900; larger clients display the framebuffer scaled proportionally. A 1920×1080 viewer in focus mode receives a 1600×900 framebuffer that fills the available area, processing about 31% fewer pixels than native Full HD. With the toolbar visible, its width is reserved outside the browser area. Mobile clients still get a responsive viewport. Closing a tab frees its slot. This is a shared browser-session application, not a security boundary between untrusted website origins: profile storage is shared by design.

## Practical limits

Chromium is headed with a persistent profile and without Playwright's automation launcher flags. This does **not** guarantee that ChatGPT, Cloudflare or another site accepts a session. IP reputation, account rules and site-side challenges remain external factors. A timeout is reported honestly instead of returning a false success for the previously loaded page. Challenges must be completed by the user where supported.

The viewer transports pixels and input. Audio, microphone/camera forwarding and arbitrary native browser dialogs are not implemented. The right-click menu provides the documented page actions; it is not the complete Chromium developer/menu interface.

### Local files and image paste

- Click a site's normal upload button, then **انتخاب فایل** in the viewer panel. Select files on your own computer/phone, not on the server. HTML file pickers (including cross-origin frames) are intercepted before a native server dialog can cover the streamed window. **لغو** / Escape closes the panel without clearing previously selected files.
- Click the site's editor and press **Ctrl+V / Cmd+V** to paste an image from your device clipboard. Text paste still works. The context-menu Paste action also reads image clipboard items on supported browsers with permission. On phones, paste into the keyboard/clipboard panel, or use **انتخاب عکس برای Paste** in the clipboard panel.
- Up to **8 files, 20 MiB total** per transfer; the site's single/multiple selection and file-type filter are retained. Image paste supports PNG, JPEG, GIF and WebP. Sites must handle an image `paste` event; synthetic paste is not accepted by every site. If it does not attach the image, use that site's upload button instead. Clipboard API reads require HTTPS/localhost and browser permission; native keyboard paste and local file selection are the alternatives.
- Each transfer has an expiring, single-use token bound to the authenticated session and its own page/input. Navigating or cancelling invalidates the target. Logout/replacement login rejects old transfers. File bytes are supplied directly to that input; the API never accepts server file paths or reads the shared desktop clipboard. Selecting files sends them to the remote site, just like its normal uploader.
- Folder selection, `showOpenFilePicker` / File System Access API dialogs, and image copy **from remote to local** are not covered by this feature. The existing remote-to-local copy relay remains text-only.

### Downloads and server cleanup

Click a site's download link/button normally. Standard attachments, Blob downloads and download links opening a new window are sent to the authenticated user's device. The **↓** toolbar panel shows progress and a **ذخیره در دستگاه** fallback link if the device browser blocks automatic saving. This link uses a Blob already received on that device, not another server copy. Mobile saving/opening behavior depends on the device browser. Files that a site opens inline (e.g. a PDF viewer) require that site's actual Download button.

- Maximum **100 MiB per file**, one pending download per user, two across the service. Server-to-device responses are streamed in 64 KiB chunks; the backend does not load the whole file into Python memory. The device buffers the completed file for local saving; at most two fallback Blobs remain, each for five minutes.
- Chromium stages download artifacts only under `/dev/shm/shared-browser-downloads`, mounted as a **256 MiB tmpfs** by Compose. This is not a persistent Docker volume or profile directory. Staging requires Linux tmpfs and fails closed if mounted on a disk filesystem. tmpfs can use swap on a general Linux host; the supplied Compose prevents this container from using additional swap through equal RAM and RAM+swap limits.
- Successful transfers and interrupted HTTP transfers delete the server artifact. Cancelling, closing the owned tab, logout/replacement login, or losing all viewers also clean it up (disconnect grace: 10 seconds). Ready files not fetched expire after two minutes; receiving/sending has a five-minute limit. Cleanup runs every second, including size/memory-pressure checks. Startup removes stale files only from this feature's task-owned staging directories. Stopped/recreated containers lose their tmpfs contents.
- Transfers are one-shot, require login and CSRF, and are bound to the initiating session and its own page. They cannot address server paths or another user's downloads. No-store headers prevent response caching. After an incomplete transfer, download again from the original site; the server deliberately does not retain a retry copy.
- This removes downloaded-file artifacts, not the shared site's cookies/history or data that websites themselves store in the persistent profile. No claim of forensic secure erasure is made.

### Memory limits and safe testing

Compose enforces a default **3 GiB total container memory cap**, an equal memory+swap cap (no additional swap), and 512 processes/threads. `APP_MEMORY_LIMIT=3g` in `.env` changes the RAM cap; choose a value that leaves memory for the OS and other services. Shared memory and tmpfs consumption count toward this cap. New downloads are rejected, and growing downloads cancelled, near 85% usage or with less than 256 MiB headroom. Browser tabs can still exhaust the budget: the kernel may kill a process inside this container; these limits protect the host, not guarantee that every heavy site stays running. Xvfb startup now removes only its stale display lock after an unclean stop and has a bounded readiness wait.

Run test suites **sequentially**, under the Compose memory cap and with a wall-clock timeout; browser E2E suites create an additional Chromium and temporarily need more RAM. Unit tests can also run with a 512 MiB per-process virtual-memory cap. Do not remove resource caps to make a failing test pass. Check `docker stats --no-stream` and `/sys/fs/cgroup/memory.events` while testing. The interrupted-download test uses a blocking ASGI receive, not a spinning mock that accumulates call history.

Use HTTPS behind a reverse proxy for deployment, with WebSocket upgrades enabled and the original Host/Origin preserved. Set `COOKIE_SECURE=true` when served over HTTPS. Do not publish CDP or VNC ports. Stop the app before backing up the profile and database together.

## Admin VLESS proxy

In `/admin`, enable **پروکسی مرورگر · VLESS**, paste a raw `vless://` URI, and save/confirm the browser restart. Supports WS (including custom headers), TCP and gRPC with TLS or Reality; unsupported transport/encryption parameters are rejected. A blank URI retains the saved one. Uncheck and save to return to direct access. This affects every shared-browser user, not the web panel, host, or other containers. Existing cookies/logins survive the restart; unsaved work does not.

The image includes checksum-pinned Xray v25.10.15 for amd64/arm64, chosen for compatibility with existing `allowInsecure` links (newer releases remove that option, including by date cutoff). Review this compatibility pin when updating dependencies; it is not a claim of using the latest security fixes. Its HTTP listener is **127.0.0.1:10808 inside the container only**; no new published ports, host capabilities or separate service are needed. Rebuild/recreate the app image in Portainer. Keep the existing `/app/data` volume. The URI is stored in `data/browser-proxy.json` (mode **0600**, outside Chromium's profile), not returned by the API or logged. It is a credential in plaintext on disk: protect volume backups. Runtime Xray config is in private RAM-backed `/dev/shm`. Clearing browser data does not delete proxy settings.

Chromium bypasses localhost, single-label/local names, loopback, RFC1918, link-local, CGNAT and private IPv6 literals. Xray also routes domains resolving to private addresses directly; DNS resolution may therefore use the server's resolver. `localhost` means the **container**, not the user's computer; LAN connectivity still depends on Docker/network routing. Public browser requests have no automatic direct fallback if Xray fails. QUIC and non-proxied WebRTC UDP are disabled while enabled; this is a browser proxy, **not an OS-level VPN/firewall security boundary**. `allowInsecure=true` is honored with a prominent warning. Prefer a valid TLS certificate and remove that parameter when possible.

Status reports local process readiness, not end-to-end reachability of the upstream server. Configuration failures roll back; if rollback/startup fails, public browsing stays blocked until an admin fixes or disables the proxy. Applying is admin-only, CSRF-protected and serialized with browser restart/reset. The provided example is not auto-installed and no real subscription credentials are in the repository.

### Focused proxy checks

The admin network form supports system DNS or up to three **plain DNS server IPs**, such as `8.8.8.8`, `1.1.1.1`, or a LAN resolver. DoH is disabled in Chromium; old secure templates are removed. Known prior DoH provider selections migrate to their IPs (Google → 8.8.8.8, Cloudflare → 1.1.1.1, Quad9 → 9.9.9.9). Saving restarts Chromium/Xray without clearing cookies. The selection applies to the **container's** `/etc/resolv.conf`, so browser direct access and VPN bootstrap both use it. The original Docker resolver is retained only in `/dev/shm/shared-browser-system-resolv.conf` for restoring system mode and is recaptured when the container is recreated. The host resolver is never modified; custom DNS is refused outside Docker. Standard Compose/container root privileges suffice; a read-only `/etc/resolv.conf` is not supported.

With VPN on, Xray explicitly uses the same DNS IPs for both its VLESS server address and website destinations (`ForceIP`). Plain DNS queries go directly to the selected resolver on port 53, not recursively through the VPN they need to initialize. Website traffic still goes through VLESS. No different public resolver or DoH fallback is silently substituted. The selected resolver must be reachable from the container; if blocked, choose a reachable DNS. Private IPs/localhost remain direct, and private domain names require an appropriate LAN DNS. This is not a DNS-tunneling protocol or DNS-leak anonymity feature. API DNS shape: `{"mode":"custom","servers":["8.8.8.8","1.1.1.1"]}`; system mode uses `{"mode":"system","servers":[]}`.

**تست اتصال VPN** sends one bounded HTTPS request to `api.ipify.org` through the saved local HTTP proxy, displaying the observed exit IP, HTTP status, and request duration (not ICMP ping). It does not restart Chromium or apply unsaved form fields. It is admin-only, CSRF-protected, limited to one test at a time/10-second cooldown, and has a 12-second total deadline. There is no direct fallback. Success demonstrates that this test destination was reached, not that every website/CAPTCHA will work; an HTTP error is distinguished from a transport timeout.

```sh
docker compose exec -T app sh -c 'ulimit -v 524288; exec timeout 30s python -m unittest tests.test_proxy tests.test_browser_cleanup_api'
docker compose exec -T app timeout 30s python -m tests.check_xray_config
```

## Verification

Browser-cleanup tests delete only disposable Chromium profiles. UI cleanup tests intercept every API call and never reset the live shared profile.

```sh
docker compose exec -T app sh -c 'ulimit -v 524288; exec timeout 30s python -m unittest tests.test_browser_data_cleanup tests.test_browser_cleanup_api'
docker compose exec -T app timeout 90s python -m tests.e2e_browser_cleanup
docker compose exec -T app timeout 90s python -m tests.e2e_browser_cleanup_ui
docker compose exec -T app python -m tests.e2e_remote
docker compose exec -T app python -m tests.e2e_accounts
docker compose exec -T app python -m tests.e2e_registration
docker compose exec -T app python -m tests.e2e_clipboard
docker compose exec -T app python -m tests.e2e_files
docker compose exec -T app timeout 150s python -m tests.e2e_sites
docker compose exec -T app sh -c 'ulimit -v 524288; exec timeout 30s python -m unittest tests.test_sites'
docker compose exec -T app timeout 150s python -m tests.e2e_downloads
docker compose exec -T app sh -c 'ulimit -v 524288; exec timeout 30s python -m unittest tests.test_downloads'
docker compose exec -T app python -m unittest tests.test_file_transfer
docker compose exec -T app python -m unittest tests.test_clipboard_bridge
docker compose exec -T app python -m unittest tests.test_input_buffer
docker compose exec -T app python -m tests.benchmark_latency --idle-seconds 65
curl -fsS http://localhost:8000/health
```

The end-to-end test creates two temporary users and a loopback-only test site, checks concurrent input isolation, Persian typing, clipboard round-trips, right-click, viewer expansion, responsive phone input and shared cookies, then removes only its test accounts and tabs. It does not type into the user's active tab or submit text to external sites. Screenshots are written inside the container to `/tmp/sbt-qa/`.

FastAPI endpoints remain under `/api/auth`, `/api/tabs`, `/api/users` and `/api/browser`. Admin-only APIs create users and restart the browser. The bundled Debian noVNC client is version 1.6; `WindowRFB` confines its x11vnc encoding compatibility override to one subclass, which should be re-tested when that package is upgraded.
