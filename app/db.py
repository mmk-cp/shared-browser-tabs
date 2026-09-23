from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def initialize_database():
    Base.metadata.create_all(bind=engine)
    # Additive migration for existing installations; keep accounts and tabs.
    if "session_id" not in {column["name"] for column in inspect(engine).get_columns("users")}:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE users ADD COLUMN session_id VARCHAR(64)"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
