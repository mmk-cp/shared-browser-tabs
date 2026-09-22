# Shared Browser Tabs

A FastAPI application that runs one persistent Chromium profile and gives each authenticated user an isolated Playwright Page, CDP JPEG stream, and input channel. Cookies, local storage, and logins are shared by all pages in the profile; pixels and input are not.

## Run

```bash
cp .env.example .env
# Change SECRET_KEY and ADMIN_PASSWORD before production use
docker compose up -d --build
docker compose logs -f
```

Open <http://localhost:8000>. The initial admin credentials come from `ADMIN_USERNAME` and `ADMIN_PASSWORD`. Users can be created through the admin API (`POST /api/users`) using the admin session.

## Important configuration

`BROWSER_DATA_DIR` is the persistent Chromium profile. Back it up while the app is stopped (for example, archive `./browser-data`). To reset the browser, stop the app, remove or move `./browser-data/chromium-profile`, then start it again. The SQLite database is in `./data/app.db`.

The default cookie is HttpOnly and SameSite=Lax; set `APP_ENV=production` (or `COOKIE_SECURE=true`) when serving over HTTPS. Put Nginx or Traefik in front for TLS, rate limiting, and a single public origin. Chromium is only reachable inside the app container.

## Architecture

`BrowserManager` launches one ordinary headed Chromium under Xvfb with a remote CDP port, then attaches Playwright using `connect_over_cdp`. This keeps `navigator.webdriver` false and avoids Playwright's automation launch flags, which makes interactive anti-bot challenges behave much closer to a normal browser. `TabManager` maps one database tab to one Playwright Page. `StreamManager` starts a CDP `Page.startScreencast` per page and sends frames only to that tab's authenticated WebSocket. WebSocket events never accept a client-supplied page ID, so authorization is enforced server-side.

The default database URL is SQLite. For PostgreSQL later, install a PostgreSQL SQLAlchemy driver (for example `psycopg[binary]`) and set `DATABASE_URL=postgresql+psycopg://user:password@host/db`.

## API highlights

`POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`, `GET /api/tabs/me`, `POST /api/tabs/me/navigate`, `POST /api/tabs/me/{back|forward|reload}`, `DELETE /api/tabs/me`, and `GET /health`. Browser start/restart and user management are admin-only.
