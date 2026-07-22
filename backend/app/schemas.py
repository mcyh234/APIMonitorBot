from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class APIConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target: str = Field(pattern=r"^[GPgp]\d+([&＆][GPgp]\d+)*$", max_length=512)
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = Field(min_length=1)
    model_name: str = Field(min_length=1, max_length=160)
    enabled: bool = True


class APIConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    target: str | None = Field(default=None, pattern=r"^[GPgp]\d+([&＆][GPgp]\d+)*$", max_length=512)
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


class WebUIAuthStatusOut(BaseModel):
    configured: bool
    authenticated: bool


class WebUISecretIn(BaseModel):
    secret: str = Field(min_length=8, max_length=256)


class WebUILoginIn(BaseModel):
    secret: str = Field(min_length=1, max_length=256)


class WebUITokenOut(BaseModel):
    token: str


class OneBotSettingsOut(BaseModel):
    ws_url: str
    access_token_configured: bool
    access_token_preview: str | None
    ws_token_in_query: bool
    connected: bool
    last_error: str | None


class OneBotSettingsUpdate(BaseModel):
    ws_url: str = Field(default="", max_length=512)
    access_token: str | None = Field(default=None, max_length=512)
    ws_token_in_query: bool = True


class MonitoringSettingsOut(BaseModel):
    night_saver_enabled: bool
    night_saver_start_time: str
    night_saver_end_time: str
    night_saver_interval_minutes: int
    command_cooldown_minutes: int


class MonitoringSettingsUpdate(BaseModel):
    night_saver_enabled: bool
    night_saver_start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    night_saver_end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    night_saver_interval_minutes: int = Field(ge=1, le=1440)
    command_cooldown_minutes: int = Field(ge=0, le=1440)


class CommandSettingOut(BaseModel):
    command: str
    label: str
    description: str
    enabled: bool
    aliases: list[str] = Field(default_factory=list)


class CommandSettingUpdate(BaseModel):
    enabled: bool | None = None
    aliases: list[str] | None = Field(default=None, max_length=16)


class Sub2RateHistoryPointOut(BaseModel):
    recorded_at: datetime
    rate_multiplier: float


class Sub2DailyCandleOut(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float


class Sub2RateOut(BaseModel):
    platform: str
    group_key: str
    group_name: str
    rate_multiplier: float
    previous_rate: float | None
    change_percent: float | None
    last_seen_at: datetime
    history: list[Sub2RateHistoryPointOut]
    candles: list[Sub2DailyCandleOut]


class Sub2SentimentOut(BaseModel):
    date: str
    up_count: int
    down_count: int
    total_count: int
    up_percent: float
    down_percent: float


class Sub2PriceBoardOut(BaseModel):
    config_id: int
    name: str
    target_type: str
    target_id: str
    target: str
    base_url: str
    upstream_type: str
    credential_configured: bool
    enabled: bool
    last_checked_at: datetime | None
    last_error: str | None
    rates: list[Sub2RateOut]
    best_groups: list["BestGroupOut"] = Field(default_factory=list)


class BestGroupOut(BaseModel):
    category: str
    label: str
    group_name: str
    platform: str
    rate_multiplier: float


class UpstreamImportIn(BaseModel):
    urls: str = Field(min_length=1, max_length=20000)
    target: str = Field(pattern=r"^[GPgp]\d+([&\uff06][GPgp]\d+)*$", max_length=512)
    upstream_type: str = Field(default="auto", pattern=r"^(auto|sub2api|newapi)$")


class UpstreamImportOut(BaseModel):
    created: list[str]
    skipped: list[str]


class UpstreamLoginIn(BaseModel):
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=1024)
    access_token: str | None = Field(default=None, max_length=4096)
    user_id: str | None = Field(default=None, max_length=64)
    upstream_type: str = Field(default="auto", pattern=r"^(auto|sub2api|newapi)$")


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


class UpgradeStatusOut(BaseModel):
    current_version: str
    process_id: int
    last_installed_version: str | None = None
    last_installed_at: str | None = None
    last_backup_path: str | None = None


class UpgradePackageInfoOut(BaseModel):
    version: str
    created_at: str
    file_count: int
    total_size: int


class UpgradeInstallOut(BaseModel):
    version: str
    previous_version: str
    installed_at: str
    updated_files: int
    backup_path: str
    dependencies_installed: bool
    restarting: bool
