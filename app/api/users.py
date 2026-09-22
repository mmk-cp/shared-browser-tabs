from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import admin_user, protected_admin
from app.db import get_db
from app.models import User
from app.services.auth_service import hash_password

router = APIRouter(prefix="/api/users", tags=["users"])

class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8)
    is_admin: bool = False

@router.get("")
def list_users(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [{"id": u.id, "username": u.username, "is_active": u.is_active, "is_admin": u.is_admin,
             "tab": u.browser_tab.id if u.browser_tab else None} for u in db.query(User).order_by(User.id).all()]

@router.post("")
def create_user(payload: UserCreate, _: User = Depends(protected_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == payload.username.strip()).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(username=payload.username.strip(), password_hash=hash_password(payload.password), is_admin=payload.is_admin)
    db.add(user); db.commit(); db.refresh(user)
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}
