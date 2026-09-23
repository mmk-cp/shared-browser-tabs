from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    secret_key: str = "change-this-in-production"
    database_url: str = "sqlite:///./data/app.db"
    browser_data_dir: str = "/browser-data/chromium-profile"
    browser_headless: bool = False
    browser_connect_over_cdp: bool = True
    browser_debug_port: int = 9222
    browser_executable_path: str | None = None
    browser_fps: int = 15
    browser_jpeg_quality: int = 70
    default_width: int = 1920
    default_height: int = 1080
    vnc_base_port: int = 5900
    vnc_view_width: int = 1920
    vnc_view_height: int = 1080
    admin_username: str = "admin"
    admin_password: str = "change-me"
    cookie_secure: bool = False
    session_max_age: int = 60 * 60 * 24 * 7

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
