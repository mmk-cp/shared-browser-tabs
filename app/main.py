import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import User
from app.services.auth_service import hash_password, get_user_from_request
from app.services.browser_manager import browser_manager
from app.services.stream_manager import stream_manager
import app.services.tab_manager as tab_module
from app.services.tab_manager import TabManager

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

# Set the singleton before importing route modules, which import this binding.
tab_module.tab_manager = TabManager(browser_manager, stream_manager)
async def restore_browser_tabs():
    with SessionLocal() as restore_db:
        await tab_module.tab_manager.restore_pages(restore_db)
browser_manager.restore_callback = restore_browser_tabs
from app.api import auth, browser, tabs, users
from app.websocket import browser_ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.query(User).first():
            admin = User(username=settings.admin_username, password_hash=hash_password(settings.admin_password), is_admin=True)
            db.add(admin); db.commit()
            logger.info("INITIAL_ADMIN_CREATED user=%s", settings.admin_username)
        await browser_manager.start()
        await tab_module.tab_manager.restore_pages(db)
    yield
    await stream_manager.stop_all()
    await browser_manager.stop()


app = FastAPI(title="Shared Browser Tabs", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.mount("/novnc", StaticFiles(directory="/usr/share/novnc"), name="novnc")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
app.include_router(auth.router)
app.include_router(browser.router)
app.include_router(tabs.router)
app.include_router(users.router)
app.include_router(browser_ws.router)


@app.get("/health")
def health():
    database = "ok"
    try:
        with SessionLocal() as db: db.execute(text("SELECT 1"))
    except Exception:
        database = "error"
    return {"status": "ok" if database == "ok" else "degraded", "browser": "running" if browser_manager.running else "stopped", "database": database}


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    with SessionLocal() as db:
        user = get_user_from_request(request, db)
    return RedirectResponse("/dashboard" if user else "/login")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    with SessionLocal() as db:
        if get_user_from_request(request, db): return RedirectResponse("/dashboard")
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    with SessionLocal() as db:
        user = get_user_from_request(request, db)
    if not user: return RedirectResponse("/login")
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})
