from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import AppSetting


def get_app_setting(session: Session, key: str) -> str | None:
    row = session.scalar(select(AppSetting).where(AppSetting.key == key))
    return row.value if row is not None else None


def set_app_setting(session: Session, key: str, value: str) -> AppSetting:
    row = session.scalar(select(AppSetting).where(AppSetting.key == key))
    if row is None:
        row = AppSetting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    session.commit()
    session.refresh(row)
    return row
