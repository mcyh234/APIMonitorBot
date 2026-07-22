from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import Base, BotAdmin
from backend.app.settings import Settings, get_settings


def _sqlite_connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return
    db_path = database_url.replace("sqlite:///", "", 1)
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_ensure_sqlite_parent(settings.database_url)
engine = create_engine(
    settings.database_url,
    connect_args=_sqlite_connect_args(settings.database_url),
    future=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, future=True)


def init_db(app_settings: Settings | None = None) -> None:
    current_settings = app_settings or settings
    _ensure_sqlite_parent(current_settings.database_url)
    Base.metadata.create_all(engine)
    _ensure_schema_compatibility()
    with SessionLocal() as session:
        seed_defaults(session, current_settings)




def _ensure_schema_compatibility() -> None:
    """Apply tiny SQLite migrations for users upgrading without Alembic."""
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    with engine.begin() as connection:
        if "bot_command_settings" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("bot_command_settings")}
            if "aliases" not in columns:
                connection.execute(text("ALTER TABLE bot_command_settings ADD COLUMN aliases JSON"))
        if "sub2_configs" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("sub2_configs")}
            additions = {
                "upstream_type": "VARCHAR(24) NOT NULL DEFAULT 'sub2api'",
                "credential_mode": "VARCHAR(24) NOT NULL DEFAULT 'password'",
                "newapi_user_id": "VARCHAR(64)",
                "session_cookie_encrypted": "TEXT",
                "login_extra_params_encrypted": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE sub2_configs ADD COLUMN {name} {definition}"))

def seed_defaults(session: Session, app_settings: Settings | None = None) -> None:
    current_settings = app_settings or settings
    default_admin = str(current_settings.default_admin_qq).strip()
    if not default_admin:
        return
    exists = session.scalar(select(BotAdmin).where(BotAdmin.qq == default_admin))
    if exists is None:
        session.add(BotAdmin(qq=default_admin))
        session.commit()


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
