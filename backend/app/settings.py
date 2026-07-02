from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "APIMonitorBot"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_timezone: str = "Asia/Shanghai"
    database_url: str = "sqlite:///./data/apimonitor.sqlite3"

    secret_master_key: str = Field(default="", repr=False)

    onebot_api_base_url: str = ""
    onebot_ws_url: str = ""
    onebot_access_token: str = Field(default="", repr=False)
    onebot_ws_token_in_query: bool = False
    onebot_inbound_access_token: str = Field(default="", repr=False)

    default_admin_qq: str = "2087900785"
    check_interval_seconds: int = 60
    night_saver_enabled: bool = True
    night_saver_start_hour: int = Field(default=0, ge=0, le=23)
    night_saver_end_hour: int = Field(default=8, ge=0, le=23)
    night_saver_interval_seconds: int = Field(default=600, ge=60)
    check_retry_delay_seconds: int = 5
    request_timeout_seconds: float = 20.0
    internet_check_url: str = "https://www.google.com/generate_204"
    internet_check_timeout_seconds: float = 8.0
    internet_disconnect_notify_cooldown_seconds: int = 600
    api_ssl_ca_bundle: str = ""
    outage_repeat_checks: int = 10
    recovery_confirm_checks: int = 2
    command_check_cooldown_seconds: int = 300
    checker_enabled: bool = True

    status_snapshot_url: str = "https://status.gptstore.club/"
    status_snapshot_browser_path: str = ""
    status_snapshot_timeout_seconds: float = 45.0
    status_snapshot_viewport_width: int = Field(default=1920, ge=800, le=3840)


@lru_cache
def get_settings() -> Settings:
    return Settings()
