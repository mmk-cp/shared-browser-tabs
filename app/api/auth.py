import logging
import secrets
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from urllib.parse import urlsplit
from app.account_schemas import NewCredentials, PasswordChange
from app.api.deps import current_user, protected_user
from app.db import get_db
from app.models import User
from app.services.auth_service import clear_auth_cookies, set_auth_cookies, verify_password, hash_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username.strip()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="حساب شما هنوز توسط ادمین تأیید نشده است.")
    # Every successful login replaces the previous session, including logins
    # from the same device. Failed logins must never revoke a valid session.
    session_id = secrets.token_hex(32)
    updated = db.query(User).filter(User.id == user.id, User.is_active.is_(True),
                                   User.password_hash == user.password_hash).update(
        {User.session_id: session_id}, synchronize_session=False)
    db.commit()
    if not updated:
        raise HTTPException(status_code=401, detail="حساب یا رمز عبور تغییر کرده است؛ دوباره وارد شوید.")
    user.session_id = session_id
    set_auth_cookies(response, user)
    logger.info("USER_LOGIN user=%s", user.username)
    return {"ok": True, "user": {"id": user.id, "username": user.username, "is_admin": user.is_admin}}

@router.post("/logout")
def logout(response: Response, user: User = Depends(protected_user), db: Session = Depends(get_db)):
    # A late logout request must not revoke a newer concurrent login.
    db.query(User).filter(User.id == user.id, User.session_id == user.session_id).update(
        {User.session_id: None}, synchronize_session=False)
    db.commit()
    clear_auth_cookies(response)
    logger.info("USER_LOGOUT user=%s", user.username)
    return {"ok": True}

@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}


@router.post("/register", status_code=201)
def register(payload: NewCredentials, request: Request, db: Session = Depends(get_db)):
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.headers.get('host'):
        raise HTTPException(status_code=403, detail="درخواست از مبدأ نامعتبر است.")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="این نام کاربری قبلاً ثبت شده است.")
    # Public registration never accepts role, approval, or session fields.
    user = User(username=payload.username, password_hash=hash_password(payload.password),
                is_active=False, is_admin=False, session_id=None)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="این نام کاربری قبلاً ثبت شده است.")
    logger.info("USER_REGISTERED_PENDING user=%s", user.username)
    return {"ok": True, "status": "pending", "message": "ثبت‌نام انجام شد؛ پس از تأیید ادمین می‌توانید وارد شوید."}


@router.post("/password")
def change_password(payload: PasswordChange, response: Response,
                    user: User = Depends(protected_user), db: Session = Depends(get_db)):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="رمز عبور فعلی صحیح نیست.")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="رمز جدید باید با رمز فعلی متفاوت باشد.")
    updated = db.query(User).filter(User.id == user.id, User.is_active.is_(True),
        User.session_id == user.session_id, User.password_hash == user.password_hash).update(
            {User.password_hash: hash_password(payload.new_password), User.session_id: None},
            synchronize_session=False)
    db.commit()
    if not updated:
        raise HTTPException(status_code=409, detail="نشست یا حساب تغییر کرده است؛ دوباره وارد شوید.")
    clear_auth_cookies(response)
    logger.info("USER_PASSWORD_CHANGED user=%s", user.username)
    return {"ok": True}
