from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.api.deps import admin_user, protected_admin
from app.db import get_db
from app.models import User
from app.services.auth_service import hash_password
from app.account_schemas import NewCredentials
from app.services.tab_manager import tab_manager

router = APIRouter(prefix="/api/users", tags=["users"])

class UserCreate(NewCredentials):
    is_admin: bool = False

@router.get("")
def list_users(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [{"id": u.id, "username": u.username, "is_active": u.is_active, "is_admin": u.is_admin,
             "tab": u.browser_tab.id if u.browser_tab else None} for u in db.query(User).order_by(User.is_active.asc(), User.id.desc()).all()]

@router.post("", status_code=201)
def create_user(payload: UserCreate, _: User = Depends(protected_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == payload.username.strip()).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(username=payload.username.strip(), password_hash=hash_password(payload.password), is_admin=payload.is_admin)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username already exists")
    db.refresh(user)
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}


@router.post("/{user_id}/approve")
def approve_user(user_id: int, _: User = Depends(protected_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="کاربر پیدا نشد.")
    if not user.is_active:
        updated = db.query(User).filter(User.id == user.id, User.username == user.username,
            User.created_at == user.created_at, User.is_active.is_(False)).update(
                {User.is_active: True, User.session_id: None}, synchronize_session=False)
        db.commit()
        if not updated:
            raise HTTPException(status_code=409, detail="حساب تغییر کرده است؛ فهرست را تازه‌سازی کنید.")
    return {"ok": True, "id": user.id, "is_active": True}


@router.delete("/{user_id}")
async def delete_user(user_id: int, admin: User = Depends(protected_admin), db: Session = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="نمی‌توانید حساب خودتان را حذف کنید.")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="کاربر پیدا نشد.")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="حذف حساب‌های ادمین از این بخش مجاز نیست.")
    await tab_manager.delete_user(db, user)
    return {"ok": True}
