import logging
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.api.deps import current_user, protected_user
from app.db import get_db
from app.models import User
from app.services.auth_service import clear_auth_cookies, set_auth_cookies, verify_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username.strip()).first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    set_auth_cookies(response, user)
    logger.info("USER_LOGIN user=%s", user.username)
    return {"ok": True, "user": {"id": user.id, "username": user.username, "is_admin": user.is_admin}}

@router.post("/logout")
def logout(response: Response, user: User = Depends(protected_user)):
    clear_auth_cookies(response)
    logger.info("USER_LOGOUT user=%s", user.username)
    return {"ok": True}

@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}
