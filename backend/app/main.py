from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Request
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api import api_router, mount_spa_routes, onebot_router
from backend.app.availability import ApiProbe
from backend.app.commands import CommandRouter
from backend.app.crypto import get_secret_box
from backend.app.db import SessionLocal, init_db
from backend.app.monitor import MonitorService
from backend.app.notifier import OneBotNotifier
from backend.app.onebot import OneBotClient, OneBotWebSocketReceiver
from backend.app.runtime_settings import apply_runtime_settings
from backend.app.settings import Settings, get_settings
from backend.app.webui_auth import bearer_token, verify_webui_token, webui_secret_configured

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app_settings)
        secret_box = get_secret_box()
        with SessionLocal() as session:
            apply_runtime_settings(session, app_settings, secret_box)
        onebot_client = OneBotClient(app_settings)
        notifier = OneBotNotifier(onebot_client, SessionLocal)
        monitor = MonitorService(
            settings=app_settings,
            secret_box=secret_box,
            notifier=notifier,
            probe=ApiProbe(timeout_seconds=app_settings.request_timeout_seconds),
        )
        command_router = CommandRouter(app_settings, onebot_client, secret_box)
        ws_receiver = OneBotWebSocketReceiver(
            app_settings,
            lambda event: _handle_ws_event(command_router, event),
        )
        onebot_client.set_websocket_receiver(ws_receiver)
        scheduler = AsyncIOScheduler(timezone=app_settings.app_timezone)

        app.state.settings = app_settings
        app.state.onebot = onebot_client
        app.state.monitor = monitor
        app.state.command_router = command_router
        app.state.onebot_ws_receiver = ws_receiver
        app.state.scheduler = scheduler

        if app_settings.checker_enabled:
            scheduler.add_job(
                monitor.run_all_scheduled,
                "interval",
                args=[SessionLocal],
                seconds=app_settings.check_interval_seconds,
                id="api-availability-check",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.start()
        ws_receiver.start()
        try:
            yield
        finally:
            await ws_receiver.stop()
            if scheduler.running:
                scheduler.shutdown(wait=False)

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    add_webui_auth_middleware(app)
    app.include_router(api_router)
    app.include_router(onebot_router)

    root = Path(__file__).resolve().parents[2]
    frontend_dist = root / "frontend" / "dist"
    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    mount_spa_routes(app, frontend_dist)
    return app


def add_webui_auth_middleware(app: FastAPI) -> None:
    public_api_paths = {
        "/api/webui/auth-status",
        "/api/webui/setup",
        "/api/webui/login",
    }

    @app.middleware("http")
    async def webui_auth_middleware(request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api") or path in public_api_paths:
            return await call_next(request)
        with SessionLocal() as session:
            configured = webui_secret_configured(session)
            token = bearer_token(request.headers.get("Authorization"))
            if not configured:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "WebUI 进入密钥尚未设置。"},
                )
            if not verify_webui_token(session, token):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "请先登录 WebUI。"},
                )
        return await call_next(request)


async def _handle_ws_event(command_router: CommandRouter, event: dict) -> None:
    with SessionLocal() as session:
        await command_router.handle_event(session, event)


app = create_app()
