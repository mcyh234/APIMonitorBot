from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class APIConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target: str = Field(pattern=r"^[GP]\d+$")
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = Field(min_length=1)
    model_name: str = Field(min_length=1, max_length=160)
    enabled: bool = True


class APIConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    target: str | None = Field(default=None, pattern=r"^[GP]\d+$")
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = Field(default=None, min_length=1)
    model_name: str | None = Field(default=None, min_length=1, max_length=160)
    enabled: bool | None = None


class APIConfigOut(BaseModel):
    id: int
    name: str
    target_type: str
    target_id: str
    target: str
    base_url: str
    model_name: str
    enabled: bool
    status: str
    last_code: str | None
    last_error: str | None
    last_checked_at: datetime | None
    last_latency_ms: int | None
    today_availability: float
    created_at: datetime
    updated_at: datetime


class CheckRecordOut(BaseModel):
    id: int
    checked_at: datetime
    status: str
    code: str | None
    error: str | None
    latency_ms: int | None
    scheduled: bool


class StatusBucketOut(BaseModel):
    start_at: datetime
    end_at: datetime
    state: str
    ok_count: int
    down_count: int
    total_count: int


class StatusWindowOut(BaseModel):
    key: str
    label: str
    bucket_minutes: int
    buckets: list[StatusBucketOut]


class ConfigStatusBarsOut(BaseModel):
    config_id: int
    config_name: str
    target: str
    model_name: str
    status: str
    last_code: str | None
    success_rate: float
    windows: list[StatusWindowOut]


class Sub2RateHistoryPointOut(BaseModel):
    recorded_at: datetime
    rate_multiplier: float


class Sub2RateOut(BaseModel):
    platform: str
    group_key: str
    group_name: str
    rate_multiplier: float
    previous_rate: float | None
    change_percent: float | None
    last_seen_at: datetime
    history: list[Sub2RateHistoryPointOut]


class Sub2PriceBoardOut(BaseModel):
    config_id: int
    name: str
    target_type: str
    target_id: str
    target: str
    base_url: str
    enabled: bool
    last_checked_at: datetime | None
    last_error: str | None
    rates: list[Sub2RateOut]


class ReceivedMessageOut(BaseModel):
    id: int
    received_at: datetime
    message_type: str
    user_id: str
    group_id: str | None
    message: str
    triggered: bool
    trigger_type: str | None
    reply_preview: str | None


class SendRecordOut(BaseModel):
    id: int
    sent_at: datetime
    action: str
    target_type: str
    target_id: str
    message_preview: str
    ok: bool
    error: str | None
    status_code: int | None
    response_payload: dict | None


class ManualCheckOut(BaseModel):
    ok: bool
    code: str
    error: str | None = None
    latency_ms: int | None = None
    today_availability: float


class AdminCreate(BaseModel):
    qq: str = Field(pattern=r"^\d{4,32}$")


class AdminOut(BaseModel):
    id: int
    qq: str
    created_at: datetime


class AppStatusOut(BaseModel):
    app_name: str
    app_timezone: str
    checker_enabled: bool
    onebot_http_configured: bool
    onebot_ws_configured: bool
    onebot_ws_connected: bool
    onebot_ws_last_error: str | None
