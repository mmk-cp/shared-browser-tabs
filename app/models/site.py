from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class Site(Base):
    __tablename__ = 'sites'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(String(2048))
    icon: Mapped[str] = mapped_column(String(16), default='')
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
