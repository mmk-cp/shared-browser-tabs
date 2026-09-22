from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import User
from app.services.auth_service import csrf_valid, get_user_from_request


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_user_from_request(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def protected_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if not csrf_valid(request):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def protected_admin(user: User = Depends(protected_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user
