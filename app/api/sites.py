import re
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.api.deps import current_user, admin_user, protected_admin
from app.db import get_db
from app.models import Site, User

router = APIRouter(prefix='/api/sites', tags=['sites'])


class SiteFields(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    icon: str = Field(default='', max_length=16)
    is_active: bool = True

    @field_validator('url')
    @classmethod
    def valid_url(cls, value):
        if re.search(r'[\s\x00-\x1f\x7f\\]', value):
            raise ValueError('آدرس نباید فاصله یا کاراکتر کنترلی داشته باشد.')
        parsed = urlsplit(value)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError('آدرس کامل http یا https و بدون نام کاربری/رمز لازم است.')
        result = str(TypeAdapter(HttpUrl).validate_python(value))
        if len(result) > 2048: raise ValueError('آدرس بیش از حد طولانی است.')
        return result

    @field_validator('title','icon')
    @classmethod
    def valid_text(cls, value):
        if re.search(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', value):
            raise ValueError('متن شامل کاراکتر کنترلی است.')
        return value


def site_data(site):
    return {key:getattr(site,key) for key in ('id','title','url','icon','is_active')}


@router.get('')
def available_sites(response: Response, _: User = Depends(current_user), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return [site_data(site) for site in db.query(Site).filter(Site.is_active.is_(True)).order_by(Site.id).all()]


@router.get('/manage')
def managed_sites(response: Response, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return [site_data(site) for site in db.query(Site).order_by(Site.id.desc()).all()]


@router.post('', status_code=201)
def add_site(payload: SiteFields, _: User = Depends(protected_admin), db: Session = Depends(get_db)):
    site = Site(**payload.model_dump())
    db.add(site); db.commit(); db.refresh(site)
    return site_data(site)


@router.put('/{site_id}')
def edit_site(site_id: int, payload: SiteFields, _: User = Depends(protected_admin), db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if not site: raise HTTPException(404, 'سایت پیدا نشد؛ فهرست را تازه‌سازی کنید.')
    for key,value in payload.model_dump().items(): setattr(site,key,value)
    db.commit()
    return site_data(site)


@router.delete('/{site_id}')
def delete_site(site_id: int, _: User = Depends(protected_admin), db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if not site: raise HTTPException(404, 'سایت پیدا نشد؛ فهرست را تازه‌سازی کنید.')
    db.delete(site); db.commit()
    return {'ok':True}
