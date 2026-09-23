import secrets
from typing import Optional
import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from fastapi import Request, Response
from sqlalchemy.orm import Session
from app.config import get_settings
from app.models import User


settings = get_settings()
SESSION_COOKIE = "shared_browser_session"
CSRF_COOKIE = "shared_browser_csrf"
serializer = URLSafeTimedSerializer(settings.secret_key, salt="session")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def set_auth_cookies(response: Response, user: User) -> None:
    token = serializer.dumps({"user_id": user.id, "session_id": user.session_id})
    csrf = secrets.token_urlsafe(32)
    secure = settings.cookie_secure or settings.app_env == "production"
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.session_max_age, httponly=True,
                        secure=secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=settings.session_max_age, httponly=False,
                        secure=secure, samesite="lax", path="/")


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def get_user_from_request(request: Request, db: Session) -> Optional[User]:
    return get_user_from_token(request.cookies.get(SESSION_COOKIE), db)


def get_user_from_token(token: str | None, db: Session) -> Optional[User]:
    if not token:
        return None
    try:
        data = serializer.loads(token, max_age=settings.session_max_age)
        session_id = data.get("session_id")
        user_id = int(data.get("user_id", 0))
    except (BadSignature, SignatureExpired, ValueError, TypeError, AttributeError):
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    user = db.get(User, user_id)
    if not user or not user.is_active or not user.session_id:
        return None
    return user if secrets.compare_digest(session_id, user.session_id) else None


def csrf_valid(request: Request) -> bool:
    # Safe methods and WebSocket handshakes do not carry a CSRF header. WebSocket
    # connections are separately protected by authentication and Origin checks.
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("x-csrf-token")
    return bool(cookie and header and secrets.compare_digest(cookie, header))
