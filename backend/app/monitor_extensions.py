from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.app_settings import get_app_setting, set_app_setting
from backend.app.db import get_session
from backend.app.models import PublicPriceSnapshot, Sub2Config, Sub2ChannelRate
from backend.app.time_utils import api_datetime, coerce_aware_utc, utc_now

router = APIRouter(prefix="/api")


class AdvancedSettings(BaseModel):
    checker_enabled: bool = True
    check_interval_seconds: int = Field(default=60, ge=10, le=86400)
    check_retry_delay_seconds: int = Field(default=5, ge=0, le=120)
    request_timeout_seconds: float = Field(default=20, ge=1, le=120)
    api_probe_model_fallback_enabled: bool = True
    api_probe_fallback_models: str = Field(default="gpt-5.4,gpt-5.4-mini", max_length=2000)
    outage_repeat_checks: int = Field(default=10, ge=2, le=1000)
    recovery_confirm_checks: int = Field(default=2, ge=1, le=100)
    hide_status_targets: bool = True


def apply_advanced_settings(session, settings):
    raw = get_app_setting(session, "monitor.advanced")
    if raw:
        values = AdvancedSettings.model_validate_json(raw)
        for key, value in values.model_dump().items():
            if hasattr(settings, key):
                setattr(settings, key, value)


@router.get("/monitoring/advanced")
def advanced_settings(request: Request):
    settings = request.app.state.settings
    return AdvancedSettings(**{key: getattr(settings, key) for key in AdvancedSettings.model_fields})


@router.put("/monitoring/advanced")
def update_advanced_settings(data: AdvancedSettings, request: Request, session: Session = Depends(get_session)):
    set_app_setting(session, "monitor.advanced", data.model_dump_json())
    apply_advanced_settings(session, request.app.state.settings)
    return data


class PublicRule(BaseModel):
    config_id: int
    platform: str = Field(max_length=64)
    group_key: str = Field(max_length=128)
    visible: bool = False


class PublicSettings(BaseModel):
    enabled: bool = False
    grouping_mode: Literal["upstream", "group"] = "upstream"
    rules: list[PublicRule] = Field(default_factory=list, max_length=10000)


def read_public_settings(session):
    return PublicSettings.model_validate_json(get_app_setting(session, "monitor.public") or "{}")


@router.get("/public-settings")
def public_settings(session: Session = Depends(get_session)):
    settings = read_public_settings(session)
    visibility = {(r.config_id, r.platform, r.group_key): r.visible for r in settings.rules}
    candidates = []
    for rate, config in session.execute(select(Sub2ChannelRate, Sub2Config).join(Sub2Config)):
        candidates.append({"config_id": config.id, "config_name": config.name, "platform": rate.platform,
                           "group_key": rate.group_key, "group_name": rate.group_name,
                           "visible": visibility.get((config.id, rate.platform, rate.group_key), False)})
    return {**settings.model_dump(), "candidates": candidates}


@router.put("/public-settings")
def update_public_settings(data: PublicSettings, session: Session = Depends(get_session)):
    ids = set(session.scalars(select(Sub2Config.id)))
    if any(rule.config_id not in ids for rule in data.rules):
        raise HTTPException(422, "无效的上游配置")
    set_app_setting(session, "monitor.public", data.model_dump_json())
    return public_settings(session)


def save_public_catalog(session, config_id, catalog):
    snapshot = session.get(PublicPriceSnapshot, config_id)
    if snapshot is None:
        snapshot = PublicPriceSnapshot(config_id=config_id)
        session.add(snapshot)
    snapshot.updated_at = utc_now()
    snapshot.prices = [asdict(price) for price in catalog.model_prices]
    session.commit()


@router.get("/public-status")
def public_status(session: Session = Depends(get_session)):
    settings = read_public_settings(session)
    if not settings.enabled:
        raise HTTPException(404, "公开状态页未启用")
    allowed = {(r.config_id, r.platform, r.group_key) for r in settings.rules if r.visible}
    sections = {}
    snapshots = {row.config_id: row for row in session.scalars(select(PublicPriceSnapshot))}
    for rate, config in session.execute(select(Sub2ChannelRate, Sub2Config).join(Sub2Config)):
        if (config.id, rate.platform, rate.group_key) not in allowed:
            continue
        stale = utc_now() - coerce_aware_utc(rate.last_seen_at) > timedelta(minutes=10)
        state = "paused" if not config.enabled else "error" if config.last_error else "stale" if stale else "normal"
        key = f"{rate.platform}:{rate.group_key}" if settings.grouping_mode == "group" else str(config.id)
        section = sections.setdefault(key, {"title": rate.group_name if settings.grouping_mode == "group" else "", "rows": []})
        snapshot = snapshots.get(config.id)
        # Deliberately omit upstream identity, credentials, targets and raw errors.
        section["rows"].append({"platform": rate.platform, "group_name": rate.group_name,
                                "ratio": rate.rate_multiplier, "status": state,
                                "last_checked_at": api_datetime(config.last_checked_at),
                                "last_success_at": api_datetime(rate.last_seen_at),
                                "prices": [price for price in snapshot.prices if price.get("platform") == rate.platform] if snapshot else []})
    return {"grouping_mode": settings.grouping_mode, "sections": list(sections.values())}
