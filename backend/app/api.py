from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.availability import ApiProbe
from backend.app.command_settings import list_command_settings, set_command_enabled
from backend.app.crypto import SecretBox, get_secret_box
from backend.app.db import get_session
from backend.app.models import APIConfig, BotAdmin, CheckRecord, ReceivedMessage, SendRecord, Sub2Config
from backend.app.monitor import MonitorService
from backend.app.repository import (
    config_to_out,
    create_api_config,
    format_target,
    storage_target,
    today_availability,
)
from backend.app.runtime_settings import current_onebot_runtime_settings, save_onebot_runtime_settings
from backend.app.schemas import (
    APIConfigCreate,
    APIConfigOut,
    APIConfigUpdate,
    AdminCreate,
    AdminOut,
    AppStatusOut,
    CheckRecordOut,
    CommandSettingOut,
    CommandSettingUpdate,
    ConfigStatusBarsOut,
    ManualCheckOut,
    OneBotSettingsOut,
    OneBotSettingsUpdate,
    ReceivedMessageOut,
    SendRecordOut,
    StatusBucketOut,
    StatusWindowOut,
    Sub2PriceBoardOut,
    Sub2RateHistoryPointOut,
    Sub2RateOut,
    WebUIAuthStatusOut,
    WebUILoginIn,
    WebUISecretIn,
    WebUITokenOut,
)
from backend.app.settings import Settings, get_settings
from backend.app.status_bars import ConfigStatusBarsData, build_status_bars
from backend.app.sub2_rates import Sub2StoredRate, stored_sub2_rate_views
from backend.app.time_utils import api_datetime
from backend.app.webui_auth import (
    bearer_token,
    create_webui_token,
    set_webui_secret,
    verify_webui_secret,
    verify_webui_token,
    webui_secret_configured,
)


api_router = APIRouter(prefix="/api")
onebot_router = APIRouter(prefix="/onebot")


def get_app_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def get_monitor(request: Request) -> MonitorService:
    monitor = getattr(request.app.state, "monitor", None)
    if monitor is None:
        raise HTTPException(status_code=503, detail="Monitor service is not ready.")
    return monitor


@api_router.get("/webui/auth-status", response_model=WebUIAuthStatusOut)
def webui_auth_status(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> WebUIAuthStatusOut:
    configured = webui_secret_configured(session)
    authenticated = configured and verify_webui_token(session, bearer_token(authorization))
    return WebUIAuthStatusOut(configured=configured, authenticated=authenticated)


@api_router.post("/webui/setup", response_model=WebUITokenOut, status_code=status.HTTP_201_CREATED)
def setup_webui_secret(data: WebUISecretIn, session: Session = Depends(get_session)) -> WebUITokenOut:
    if webui_secret_configured(session):
        raise HTTPException(status_code=409, detail="WebUI 进入密钥已设置。")
    try:
        set_webui_secret(session, data.secret)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return WebUITokenOut(token=create_webui_token(session))


@api_router.post("/webui/login", response_model=WebUITokenOut)
def login_webui(data: WebUILoginIn, session: Session = Depends(get_session)) -> WebUITokenOut:
    if not webui_secret_configured(session):
        raise HTTPException(status_code=409, detail="WebUI 进入密钥尚未设置。")
    if not verify_webui_secret(session, data.secret):
        raise HTTPException(status_code=401, detail="WebUI 进入密钥不正确。")
    return WebUITokenOut(token=create_webui_token(session))


@api_router.get("/settings/onebot", response_model=OneBotSettingsOut)
def get_onebot_settings(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    secret_box: SecretBox = Depends(get_secret_box),
) -> OneBotSettingsOut:
    receiver = getattr(request.app.state, "onebot_ws_receiver", None)
    current = current_onebot_runtime_settings(session, settings, secret_box)
    return OneBotSettingsOut(
        ws_url=current.ws_url,
        access_token_configured=current.access_token_configured,
        access_token_preview=current.access_token_preview,
        ws_token_in_query=current.ws_token_in_query,
        connected=bool(getattr(receiver, "connected", False)),
        last_error=getattr(receiver, "last_error", None),
    )


@api_router.put("/settings/onebot", response_model=OneBotSettingsOut)
async def update_onebot_settings(
    data: OneBotSettingsUpdate,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    secret_box: SecretBox = Depends(get_secret_box),
) -> OneBotSettingsOut:
    token = data.access_token if data.access_token is not None and data.access_token.strip() else None
    try:
        current = save_onebot_runtime_settings(
            session,
            settings,
            secret_box,
            ws_url=data.ws_url,
            access_token=token,
            ws_token_in_query=data.ws_token_in_query,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    receiver = getattr(request.app.state, "onebot_ws_receiver", None)
    if receiver is not None:
        await receiver.restart()
    return OneBotSettingsOut(
        ws_url=current.ws_url,
        access_token_configured=current.access_token_configured,
        access_token_preview=current.access_token_preview,
        ws_token_in_query=current.ws_token_in_query,
        connected=bool(getattr(receiver, "connected", False)),
        last_error=getattr(receiver, "last_error", None),
    )


@api_router.get("/settings/commands", response_model=list[CommandSettingOut])
def get_command_settings(session: Session = Depends(get_session)) -> list[CommandSettingOut]:
    return [
        CommandSettingOut(
            command=definition.command,
            label=definition.label,
            description=definition.description,
            enabled=enabled,
        )
        for definition, enabled in list_command_settings(session)
    ]


@api_router.patch("/settings/commands/{command}", response_model=CommandSettingOut)
def update_command_setting(
    command: str,
    data: CommandSettingUpdate,
    session: Session = Depends(get_session),
) -> CommandSettingOut:
    normalized = "/" + command.lstrip("/").lower()
    try:
        row = set_command_enabled(session, normalized, data.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for definition, _enabled in list_command_settings(session):
        if definition.command == row.command:
            return CommandSettingOut(
                command=definition.command,
                label=definition.label,
                description=definition.description,
                enabled=row.enabled,
            )
    raise HTTPException(status_code=404, detail="未知命令。")


@api_router.get("/status", response_model=AppStatusOut)
def get_status(request: Request, settings: Settings = Depends(get_app_settings)) -> AppStatusOut:
    receiver = getattr(request.app.state, "onebot_ws_receiver", None)
    return AppStatusOut(
        app_name=settings.app_name,
        app_timezone=settings.app_timezone,
        checker_enabled=settings.checker_enabled,
        onebot_http_configured=bool(settings.onebot_api_base_url),
        onebot_ws_configured=bool(settings.onebot_ws_url),
        onebot_ws_connected=bool(getattr(receiver, "connected", False)),
        onebot_ws_last_error=getattr(receiver, "last_error", None),
    )


@api_router.get("/configs", response_model=list[APIConfigOut])
def list_configs(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> list[APIConfigOut]:
    configs = session.scalars(select(APIConfig).order_by(APIConfig.name)).all()
    return [config_to_out(session, item, settings.app_timezone) for item in configs]


@api_router.post("/configs", response_model=APIConfigOut, status_code=status.HTTP_201_CREATED)
async def add_config(
    data: APIConfigCreate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    secret_box: SecretBox = Depends(get_secret_box),
) -> APIConfigOut:
    probe = ApiProbe(timeout_seconds=settings.request_timeout_seconds)
    result = await probe.probe(data.base_url, data.api_key, data.model_name)
    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"验证失败：{result.code} {result.error or ''}".strip(),
        )
    try:
        config = create_api_config(session, secret_box, data)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="配置名已存在。") from exc
    return config_to_out(session, config, settings.app_timezone)


@api_router.patch("/configs/{name}", response_model=APIConfigOut)
def update_config(
    name: str,
    data: APIConfigUpdate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    secret_box: SecretBox = Depends(get_secret_box),
) -> APIConfigOut:
    config = session.scalar(select(APIConfig).where(APIConfig.name == name))
    if config is None:
        raise HTTPException(status_code=404, detail="配置不存在。")
    if data.name is not None:
        next_name = data.name.strip()
        if not next_name:
            raise HTTPException(status_code=422, detail="配置名不能为空。")
        config.name = next_name
    if data.target is not None:
        target_type, target_id = storage_target(data.target)
        config.target_type = target_type
        config.target_id = target_id
    if data.base_url is not None:
        config.base_url = data.base_url.strip()
    if data.api_key is not None:
        config.api_key_encrypted = secret_box.encrypt(data.api_key.strip())
    if data.model_name is not None:
        config.model_name = data.model_name.strip()
    if data.enabled is not None:
        config.enabled = data.enabled
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="配置名已存在。") from exc
    session.refresh(config)
    return config_to_out(session, config, settings.app_timezone)


@api_router.delete("/configs/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_config(name: str, session: Session = Depends(get_session)) -> Response:
    deleted = session.execute(delete(APIConfig).where(APIConfig.name == name)).rowcount
    session.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="配置不存在。")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.post("/configs/{name}/check", response_model=ManualCheckOut)
async def manual_check_config(
    name: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    monitor: MonitorService = Depends(get_monitor),
) -> ManualCheckOut:
    config = session.scalar(select(APIConfig).where(APIConfig.name == name))
    if config is None:
        raise HTTPException(status_code=404, detail="配置不存在。")
    result = await monitor.check_config(session, config, scheduled=False, notify=False)
    return ManualCheckOut(
        ok=result.ok,
        code=result.code,
        error=result.error,
        latency_ms=result.latency_ms,
        today_availability=today_availability(session, config.id, settings.app_timezone),
    )


@api_router.get("/configs/{name}/history", response_model=list[CheckRecordOut])
def config_history(name: str, session: Session = Depends(get_session)) -> list[CheckRecordOut]:
    config = session.scalar(select(APIConfig).where(APIConfig.name == name))
    if config is None:
        raise HTTPException(status_code=404, detail="配置不存在。")
    rows = session.scalars(
        select(CheckRecord)
        .where(CheckRecord.api_config_id == config.id)
        .order_by(CheckRecord.checked_at.desc())
        .limit(60)
    ).all()
    return [
        CheckRecordOut(
            id=row.id,
            checked_at=api_datetime(row.checked_at),
            status=row.status,
            code=row.code,
            error=row.error,
            latency_ms=row.latency_ms,
            scheduled=row.scheduled,
        )
        for row in rows
    ]


@api_router.get("/status-bars", response_model=list[ConfigStatusBarsOut])
def list_status_bars(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> list[ConfigStatusBarsOut]:
    configs = list(session.scalars(select(APIConfig).order_by(APIConfig.name)).all())
    return [
        status_bars_to_out(item)
        for item in build_status_bars(session, configs, settings.app_timezone)
    ]


def status_bars_to_out(item: ConfigStatusBarsData) -> ConfigStatusBarsOut:
    return ConfigStatusBarsOut(
        config_id=item.config_id,
        config_name=item.config_name,
        target=item.target,
        model_name=item.model_name,
        status=item.status,
        last_code=item.last_code,
        success_rate=item.success_rate,
        windows=[
            StatusWindowOut(
                key=window.key,
                label=window.label,
                bucket_minutes=window.bucket_minutes,
                buckets=[
                    StatusBucketOut(
                        start_at=api_datetime(bucket.start_at),
                        end_at=api_datetime(bucket.end_at),
                        state=bucket.state,
                        ok_count=bucket.ok_count,
                        down_count=bucket.down_count,
                        total_count=bucket.total_count,
                    )
                    for bucket in window.buckets
                ],
            )
            for window in item.windows
        ],
    )


@api_router.get("/sub2/prices", response_model=list[Sub2PriceBoardOut])
def list_sub2_prices(session: Session = Depends(get_session)) -> list[Sub2PriceBoardOut]:
    configs = session.scalars(select(Sub2Config).order_by(Sub2Config.name)).all()
    return [
        Sub2PriceBoardOut(
            config_id=config.id,
            name=config.name,
            target_type=config.target_type,
            target_id=config.target_id,
            target=format_target(config.target_type, config.target_id),
            base_url=config.base_url,
            enabled=config.enabled,
            last_checked_at=api_datetime(config.last_checked_at),
            last_error=config.last_error,
            rates=[sub2_rate_to_out(rate) for rate in stored_sub2_rate_views(session, config)],
        )
        for config in configs
    ]


def sub2_rate_to_out(rate: Sub2StoredRate) -> Sub2RateOut:
    return Sub2RateOut(
        platform=rate.platform,
        group_key=rate.group_key,
        group_name=rate.group_name,
        rate_multiplier=rate.rate_multiplier,
        previous_rate=rate.previous_rate,
        change_percent=rate.change_percent,
        last_seen_at=api_datetime(rate.last_seen_at),
        history=[
            Sub2RateHistoryPointOut(
                recorded_at=api_datetime(point.recorded_at),
                rate_multiplier=point.rate_multiplier,
            )
            for point in rate.history
        ],
    )


@api_router.get("/messages/recent", response_model=list[ReceivedMessageOut])
def recent_messages(session: Session = Depends(get_session)) -> list[ReceivedMessageOut]:
    rows = session.scalars(
        select(ReceivedMessage).order_by(ReceivedMessage.received_at.desc()).limit(10)
    ).all()
    return [
        ReceivedMessageOut(
            id=row.id,
            received_at=api_datetime(row.received_at),
            message_type=row.message_type,
            user_id=row.user_id,
            group_id=row.group_id,
            message=row.message,
            triggered=row.triggered,
            trigger_type=row.trigger_type,
            reply_preview=row.reply_preview,
        )
        for row in rows
    ]


@api_router.get("/sends/recent-failures", response_model=list[SendRecordOut])
def recent_send_failures(session: Session = Depends(get_session)) -> list[SendRecordOut]:
    rows = session.scalars(
        select(SendRecord)
        .where(SendRecord.ok.is_(False))
        .order_by(SendRecord.sent_at.desc())
        .limit(10)
    ).all()
    return [
        SendRecordOut(
            id=row.id,
            sent_at=api_datetime(row.sent_at),
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            message_preview=row.message_preview,
            ok=row.ok,
            error=row.error,
            status_code=row.status_code,
            response_payload=row.response_payload,
        )
        for row in rows
    ]


@api_router.get("/admins", response_model=list[AdminOut])
def list_admins(session: Session = Depends(get_session)) -> list[AdminOut]:
    admins = session.scalars(select(BotAdmin).order_by(BotAdmin.qq)).all()
    return [AdminOut(id=item.id, qq=item.qq, created_at=api_datetime(item.created_at)) for item in admins]


@api_router.post("/admins", response_model=AdminOut, status_code=status.HTTP_201_CREATED)
def add_admin(data: AdminCreate, session: Session = Depends(get_session)) -> AdminOut:
    admin = BotAdmin(qq=data.qq)
    session.add(admin)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="管理员已存在。") from exc
    session.refresh(admin)
    return AdminOut(id=admin.id, qq=admin.qq, created_at=api_datetime(admin.created_at))


@api_router.delete("/admins/{qq}", status_code=status.HTTP_204_NO_CONTENT)
def delete_admin(qq: str, session: Session = Depends(get_session)) -> Response:
    count = session.scalar(select(func.count()).select_from(BotAdmin))
    if count == 1:
        raise HTTPException(status_code=400, detail="至少保留一个管理员。")
    deleted = session.execute(delete(BotAdmin).where(BotAdmin.qq == qq)).rowcount
    session.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="管理员不存在。")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@onebot_router.post("/webhook")
async def onebot_webhook(
    request: Request,
) -> dict[str, str]:
    await request.body()
    return {"status": "ignored", "reason": "OneBot HTTP webhook is deprecated. Use WebSocket only."}


def mount_spa_routes(app, frontend_dist: Path) -> None:
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        index = frontend_dist / "index.html"
        requested = frontend_dist / full_path
        if full_path and requested.is_file():
            return FileResponse(requested)
        if index.exists():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="Frontend has not been built yet.")
