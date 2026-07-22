from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.availability import ApiProbe
from backend.app.check_image import CheckResultImageRow, render_check_result_image
from backend.app.command_settings import is_command_enabled, resolve_command_text
from backend.app.codex_radar import CodexRadarClient, CodexRadarError, render_codex_radar_image
from backend.app.crypto import SecretBox
from backend.app.models import APIConfig, ReceivedMessage, Sub2Config
from backend.app.onebot import OneBotClient
from backend.app.notifier import record_send_result
from backend.app.repository import (
    clear_conversation,
    consume_rate_limit,
    create_api_config,
    format_target,
    get_conversation,
    is_admin,
    storage_target,
    target_contains,
    target_entries,
    today_availability,
    upsert_conversation,
)
from backend.app.schemas import APIConfigCreate
from backend.app.settings import Settings
from backend.app.status_bars import build_status_bars
from backend.app.status_image import render_status_image
from backend.app.sub2_price_image import Sub2PriceBoard, render_sub2_price_image
from backend.app.sub2_rates import stored_sub2_rate_views, sync_sub2_rates
from backend.app.sub2_sentiment import record_sentiment_vote, sentiment_summary
from backend.app.sub2api import Sub2ApiClient, Sub2ApiError
from backend.app.tibo_radar import TiboRadarClient, TiboRadarError, render_tibo_radar_image
from backend.app.time_utils import utc_now
from backend.app.web_snapshot import StatusPageSnapshotter, StatusSnapshotError


@dataclass(slots=True)
class IncomingMessage:
    user_id: str
    message: str
    message_type: str
    group_id: str | None = None


@dataclass(slots=True)
class ManualCheckResult:
    text: str
    image_row: CheckResultImageRow


class CommandRouter:
    def __init__(
        self,
        settings: Settings,
        onebot: OneBotClient,
        secret_box: SecretBox,
        probe: ApiProbe | None = None,
        snapshotter: StatusPageSnapshotter | None = None,
        sub2_client: Sub2ApiClient | None = None,
        radar_client: CodexRadarClient | None = None,
        tibo_client: TiboRadarClient | None = None,
    ) -> None:
        self.settings = settings
        self.onebot = onebot
        self.secret_box = secret_box
        self.probe = probe or ApiProbe(timeout_seconds=settings.request_timeout_seconds)
        self.snapshotter = snapshotter or StatusPageSnapshotter(settings)
        self.sub2_client = sub2_client or Sub2ApiClient(timeout_seconds=settings.request_timeout_seconds)
        self.radar_client = radar_client or CodexRadarClient(
            settings.codex_radar_source_url,
            timeout_seconds=settings.codex_radar_timeout_seconds,
        )
        self.tibo_client = tibo_client or TiboRadarClient(
            settings.tibo_radar_source_url,
            timeout_seconds=settings.tibo_radar_timeout_seconds,
        )

    async def handle_event(self, session: Session, event: dict) -> None:
        message = parse_onebot_message(event)
        if message is None:
            return
        reply = await self.handle_message(session, message)
        log_incoming_message(session, message, reply)
        if reply:
            await self.reply(session, message, reply)

    async def reply(self, session: Session, incoming: IncomingMessage, text: str) -> None:
        if incoming.message_type == "group" and incoming.group_id:
            result = await self.onebot.send_group_msg(incoming.group_id, text)
        else:
            result = await self.onebot.send_private_msg(incoming.user_id, text)
        record_send_result(session, result)

    async def handle_message(self, session: Session, incoming: IncomingMessage) -> str | None:
        text = incoming.message.strip()
        state = get_conversation(session, incoming.user_id)
        if state is not None and not text.startswith("/cancel"):
            payload = dict(state.payload or {})
            if state.step.startswith("sub2_"):
                return await self._continue_addsub2(session, incoming, state.step, payload, text)
            return await self._continue_addapi(session, incoming, state.step, payload, text)

        resolved = resolve_command_text(session, text)
        if resolved is None:
            return None
        command = resolved.command
        arg = resolved.arg

        if command == "/cancel":
            clear_conversation(session, incoming.user_id)
            return "已取消当前对话。"
        if not is_command_enabled(session, command):
            return "该命令已关闭。"
        if command == "/list":
            if not is_admin(session, incoming.user_id):
                return "权限不足。"
            configs = list(session.scalars(select(APIConfig).order_by(APIConfig.name)).all())
            if not configs:
                return "当前没有正在运行的 API 配置。"
            lines = ["正在运行的配置："]
            lines.extend(
                f"- {item.name} -> {format_target(item.target_type, item.target_id)} ({'启用' if item.enabled else '停用'})"
                for item in configs
            )
            return "\n".join(lines)
        if command == "/remove":
            if not is_admin(session, incoming.user_id):
                return "权限不足。"
            if not arg:
                return "用法：/remove <apiname>"
            deleted = session.execute(delete(APIConfig).where(APIConfig.name == arg)).rowcount
            session.commit()
            return f"已删除配置：{arg}" if deleted else f"没有找到配置：{arg}"
        if command == "/addapi":
            if not is_admin(session, incoming.user_id):
                return "权限不足。"
            upsert_conversation(session, incoming.user_id, "name", {})
            return "请输入api配置名称"
        if command == "/addsub2":
            if not is_admin(session, incoming.user_id):
                return "权限不足。"
            upsert_conversation(session, incoming.user_id, "sub2_name", {})
            return "请输入API名称"
        if command == "/check":
            return await self._manual_check(session, incoming, arg)
        if command == "/status":
            return await self._status(session, incoming, arg)
        if command == "/stat":
            return await self._stat(session, incoming)
        if command == "/price":
            return await self._price(session, incoming)
        if command == "/up":
            return self._sentiment_vote(session, incoming, "up", arg)
        if command == "/down":
            return self._sentiment_vote(session, incoming, "down", arg)
        if command == "/radar":
            return await self._radar(session, incoming)
        if command == "/tibo":
            return await self._tibo(session, incoming)
        return None

    async def _manual_check(self, session: Session, incoming: IncomingMessage, name: str) -> str:
        configs = self._check_configs_for_context(session, incoming, name)
        if configs is None:
            return "\u6743\u9650\u4e0d\u8db3\u3002"
        if not configs:
            if name:
                return f"\u6ca1\u6709\u627e\u5230\u914d\u7f6e\uff1a{name}"
            return "\u5f53\u524d\u901a\u77e5\u5bf9\u8c61\u6ca1\u6709\u7ed1\u5b9a API \u68c0\u6d4b\u4efb\u52a1\u3002"
        ok, remaining = self._consume_command_cooldown(session, incoming, "check")
        if not ok:
            return f"\u64cd\u4f5c\u592a\u9891\u7e41\uff0c\u8bf7 {remaining} \u79d2\u540e\u518d\u8bd5\u3002"

        results = [await self._manual_check_config(session, config) for config in configs]
        if name:
            return results[0].text

        image = render_check_result_image(
            [result.image_row for result in results],
            timezone_name=self.settings.app_timezone,
        )
        if incoming.message_type == "group" and incoming.group_id:
            target_type = "group"
            target_id = incoming.group_id
        else:
            target_type = "private"
            target_id = incoming.user_id
        send_result = await self.onebot.send_image_message(target_type, target_id, image, "api-check.png")
        record_send_result(session, send_result)
        return ""

    def _check_configs_for_context(
        self,
        session: Session,
        incoming: IncomingMessage,
        name: str,
    ) -> list[APIConfig] | None:
        if name:
            config = session.scalar(select(APIConfig).where(APIConfig.name == name))
            if config is None:
                return []
            if incoming.message_type == "group" and incoming.group_id:
                if target_contains(config.target_type, config.target_id, "group", incoming.group_id):
                    return [config]
                return None
            if is_admin(session, incoming.user_id):
                return [config]
            return None

        if incoming.message_type == "group" and incoming.group_id:
            return [
                config
                for config in session.scalars(select(APIConfig).order_by(APIConfig.name)).all()
                if target_contains(config.target_type, config.target_id, "group", incoming.group_id)
            ]
        if not is_admin(session, incoming.user_id):
            return None
        return [
            config
            for config in session.scalars(select(APIConfig).order_by(APIConfig.name)).all()
            if target_contains(config.target_type, config.target_id, "private", incoming.user_id)
        ]

    async def _manual_check_config(self, session: Session, config: APIConfig) -> ManualCheckResult:
        api_key = self.secret_box.decrypt(config.api_key_encrypted)
        result = await self.probe.probe(config.base_url, api_key, config.model_name)
        availability = today_availability(session, config.id, self.settings.app_timezone)
        state = "\u670d\u52a1\u53ef\u7528" if result.ok else "\u670d\u52a1\u4e0d\u53ef\u7528"
        text = f"\u3010{config.name}\u3011\n\u5f53\u524d{state}: {result.code}\n\u6700\u8fd1\u8bf7\u6c42\u6210\u529f\u7387: {availability:.1f}%"
        return ManualCheckResult(
            text=text,
            image_row=CheckResultImageRow(
                name=config.name,
                ok=result.ok,
                code=result.code,
                success_rate=availability,
                latency_ms=result.latency_ms,
            ),
        )

    async def _status(self, session: Session, incoming: IncomingMessage, name: str) -> str:
        configs = self._status_configs_for_context(session, incoming, name)
        if configs is None:
            if not name:
                return ""
            return "权限不足。"
        if not configs:
            return "没有可显示的 API 配置。"
        ok, remaining = self._consume_command_cooldown(session, incoming, "status")
        if not ok:
            return f"操作太频繁，请 {remaining} 秒后再试。"

        bars = build_status_bars(session, configs, self.settings.app_timezone)
        image = render_status_image(bars, timezone_name=self.settings.app_timezone)
        if incoming.message_type == "group" and incoming.group_id:
            target_type = "group"
            target_id = incoming.group_id
        else:
            target_type = "private"
            target_id = incoming.user_id
        result = await self.onebot.send_image_message(target_type, target_id, image, "status.png")
        record_send_result(session, result)
        return ""

    def _status_configs_for_context(
        self,
        session: Session,
        incoming: IncomingMessage,
        name: str,
    ) -> list[APIConfig] | None:
        if name:
            config = session.scalar(select(APIConfig).where(APIConfig.name == name))
            if config is None:
                return []
            if incoming.message_type == "group" and incoming.group_id:
                if target_contains(config.target_type, config.target_id, "group", incoming.group_id):
                    return [config]
                return None
            if is_admin(session, incoming.user_id):
                return [config]
            return None

        if incoming.message_type == "group" and incoming.group_id:
            return [
                config
                for config in session.scalars(select(APIConfig).order_by(APIConfig.name)).all()
                if target_contains(config.target_type, config.target_id, "group", incoming.group_id)
            ]
        if not is_admin(session, incoming.user_id):
            return None
        return list(session.scalars(select(APIConfig).order_by(APIConfig.name)).all())

    async def _stat(self, session: Session, incoming: IncomingMessage) -> str:
        target = self._notification_target_for_context(session, incoming)
        if target is None:
            return "权限不足，当前会话不是通知对象。"
        ok, remaining = self._consume_command_cooldown(session, incoming, "stat")
        if not ok:
            return f"操作太频繁，请 {remaining} 秒后再试。"

        try:
            image = await self.snapshotter.capture()
        except StatusSnapshotError as exc:
            return f"网页快照失败：{exc}"
        except Exception as exc:
            return f"网页快照失败：{exc}"

        target_type, target_id = target
        result = await self.onebot.send_image_message(target_type, target_id, image, "gptstore-status.png")
        record_send_result(session, result)
        return ""

    async def _price(self, session: Session, incoming: IncomingMessage) -> str:
        configs = self._sub2_configs_for_notification_context(session, incoming)
        if configs is None:
            return "权限不足，当前会话不是 Sub2API 通知对象。"
        if not configs:
            return "没有可显示的 Sub2API 价格表。"
        ok, remaining = self._consume_command_cooldown(session, incoming, "price")
        if not ok:
            return f"操作太频繁，请 {remaining} 秒后再试。"

        boards: list[Sub2PriceBoard] = []
        for config in configs:
            rate_views = stored_sub2_rate_views(session, config)
            model_prices = ()
            if config.enabled and config.upstream_type != "newapi":
                try:
                    catalog = await self.sub2_client.fetch_available_catalog_with_cached_token(
                        session,
                        config,
                        self.secret_box,
                    )
                    if catalog.rates:
                        sync_sub2_rates(session, config, list(catalog.rates))
                    rate_views = stored_sub2_rate_views(session, config)
                    model_prices = catalog.model_prices
                    config.last_error = None
                    session.commit()
                except Sub2ApiError as exc:
                    config.last_error = str(exc)
                    session.commit()
            elif not rate_views and config.enabled:
                try:
                    rates = await self.sub2_client.fetch_rates_with_cached_token(session, config, self.secret_box)
                    sync_sub2_rates(session, config, rates)
                    rate_views = stored_sub2_rate_views(session, config)
                except Sub2ApiError as exc:
                    config.last_error = str(exc)
                    session.commit()
            boards.append(
                Sub2PriceBoard(
                    config.name,
                    rate_views,
                    model_prices=model_prices,
                )
            )

        image = render_sub2_price_image(
            boards,
            title="Sub2API 渠道倍率",
            timezone_name=self.settings.app_timezone,
            sentiment=sentiment_summary(session),
        )
        if incoming.message_type == "group" and incoming.group_id:
            target_type = "group"
            target_id = incoming.group_id
        else:
            target_type = "private"
            target_id = incoming.user_id
        result = await self.onebot.send_image_message(target_type, target_id, image, "sub2-price.png")
        record_send_result(session, result)
        return ""

    def _sentiment_vote(
        self,
        session: Session,
        incoming: IncomingMessage,
        direction: str,
        arg: str,
    ) -> str:
        if arg:
            return f"用法：/{direction}（无需参数）"
        if incoming.message_type == "group" and incoming.group_id:
            bound = any(
                target_contains(config.target_type, config.target_id, "group", incoming.group_id)
                for config in session.scalars(select(Sub2Config)).all()
            )
            if not bound and not is_admin(session, incoming.user_id):
                return "权限不足，当前群不是 Sub2API 通知对象。"
            source_type = "group"
            source_id = incoming.group_id
        elif is_admin(session, incoming.user_id):
            source_type = "private"
            source_id = incoming.user_id
        else:
            return "权限不足，私聊投票仅限管理员。"

        result = record_sentiment_vote(
            session,
            incoming.user_id,
            direction,
            source_type,
            source_id,
        )
        direction_label = "看涨" if direction == "up" else "看跌"
        action_labels = {
            "created": f"已记录今日{direction_label}投票。",
            "changed": f"已将今日投票改为{direction_label}。",
            "unchanged": f"你今天已经投过{direction_label}。",
        }
        summary = result.summary
        return (
            f"{action_labels[result.action]}\n"
            f"当前：看涨 {summary.up_percent:.1f}% · 看跌 {summary.down_percent:.1f}% · 共 {summary.total_count} 票"
        )

    async def _radar(self, session: Session, incoming: IncomingMessage) -> str:
        target = self._notification_target_for_context(session, incoming)
        if target is None:
            return "权限不足，当前会话不是通知对象。"
        ok, remaining = self._consume_command_cooldown(session, incoming, "radar")
        if not ok:
            return f"操作太频繁，请 {remaining} 秒后再试。"
        try:
            report = await self.radar_client.fetch()
            image = render_codex_radar_image(report)
        except CodexRadarError as exc:
            return f"降智雷达数据读取失败：{exc}"
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            return f"降智雷达图片生成失败：{detail}"
        target_type, target_id = target
        result = await self.onebot.send_image_message(target_type, target_id, image, "codex-radar.png")
        record_send_result(session, result)
        return ""

    async def _tibo(self, session: Session, incoming: IncomingMessage) -> str:
        target = self._notification_target_for_context(session, incoming)
        if target is None:
            return "权限不足，当前会话不是通知对象。"
        ok, remaining = self._consume_command_cooldown(session, incoming, "tibo")
        if not ok:
            return f"操作太频繁，请 {remaining} 秒后再试。"
        try:
            report = await self.tibo_client.fetch()
            image = render_tibo_radar_image(report)
        except TiboRadarError as exc:
            return f"Tibo 雷达数据读取失败：{exc}"
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            return f"Tibo 雷达图片生成失败：{detail}"
        target_type, target_id = target
        result = await self.onebot.send_image_message(target_type, target_id, image, "tibo-radar.png")
        record_send_result(session, result)
        return ""

    def _notification_target_for_context(
        self,
        session: Session,
        incoming: IncomingMessage,
    ) -> tuple[str, str] | None:
        if incoming.message_type == "group" and incoming.group_id:
            bound_group = any(
                target_contains(config.target_type, config.target_id, "group", incoming.group_id)
                for config in session.scalars(select(APIConfig)).all()
            )
            if bound_group or is_admin(session, incoming.user_id):
                return ("group", incoming.group_id)
            return None
        if is_admin(session, incoming.user_id):
            return ("private", incoming.user_id)
        return None

    def _sub2_configs_for_notification_context(
        self,
        session: Session,
        incoming: IncomingMessage,
    ) -> list[Sub2Config] | None:
        if incoming.message_type == "group" and incoming.group_id:
            configs = [
                config
                for config in session.scalars(select(Sub2Config).order_by(Sub2Config.name)).all()
                if target_contains(config.target_type, config.target_id, "group", incoming.group_id)
            ]
            if configs or is_admin(session, incoming.user_id):
                return configs
            return None
        if not is_admin(session, incoming.user_id):
            return None
        return [
            config
            for config in session.scalars(select(Sub2Config).order_by(Sub2Config.name)).all()
            if target_contains(config.target_type, config.target_id, "private", incoming.user_id)
        ]

    def _consume_command_cooldown(
        self,
        session: Session,
        incoming: IncomingMessage,
        command: str,
    ) -> tuple[bool, int]:
        if is_admin(session, incoming.user_id):
            return True, 0
        return consume_rate_limit(
            session,
            incoming.user_id,
            command,
            self.settings.command_check_cooldown_seconds,
        )


    async def _target_confirmation_text(self, target_type: str, target_id: str) -> str:
        entries = target_entries(target_type, target_id)
        if len(entries) == 1:
            entry_type, entry_id = entries[0]
            if entry_type == "group":
                in_group = await self.onebot.is_in_group(entry_id)
                state_text = "在" if in_group else "不在" if in_group is False else "无法确认是否在"
                return f"你添加了一个群聊，我{state_text}这个群里面。"
            return "你添加了一个私聊通知目标。"

        lines = ["你添加了多个通知对象："]
        for entry_type, entry_id in entries:
            if entry_type == "group":
                in_group = await self.onebot.is_in_group(entry_id)
                state_text = "在" if in_group else "不在" if in_group is False else "无法确认是否在"
                lines.append(f"- G{entry_id}：群聊，我{state_text}这个群里面")
            else:
                lines.append(f"- P{entry_id}：私聊通知目标")
        return "\n".join(lines)

    async def _continue_addsub2(
        self,
        session: Session,
        incoming: IncomingMessage,
        step: str,
        payload: dict,
        text: str,
    ) -> str:
        if step == "sub2_name":
            name = text.strip()
            if not name:
                return "API名称不能为空，请重新输入。"
            exists = session.scalar(select(Sub2Config).where(Sub2Config.name == name))
            if exists is not None:
                return "这个 Sub2API 名称已经存在，请换一个名称。"
            payload["name"] = name
            upsert_conversation(session, incoming.user_id, "sub2_base_url", payload)
            return "请输入Sub2API的BaseURL"

        if step == "sub2_base_url":
            base_url = text.strip()
            if not base_url.startswith(("http://", "https://")):
                return "Sub2API BaseURL 必须以 http:// 或 https:// 开头，请重新输入。"
            payload["base_url"] = base_url
            upsert_conversation(session, incoming.user_id, "sub2_email", payload)
            return "请输入email"

        if step == "sub2_email":
            email = text.strip()
            if not email:
                return "email 不能为空，请重新输入。"
            payload["email"] = email
            upsert_conversation(session, incoming.user_id, "sub2_password", payload)
            return "请输入密码"

        if step == "sub2_password":
            password = text.strip()
            if not password:
                return "密码不能为空，请重新输入。"
            try:
                tokens = await self.sub2_client.login(payload["base_url"], payload["email"], password)
                rates = await self.sub2_client.fetch_rates(payload["base_url"], tokens.access_token)
            except Sub2ApiError as exc:
                return f"登录或读取渠道失败：{exc}\n请重新输入密码，或发送 /cancel 取消。"
            payload["password"] = password
            payload["access_token"] = tokens.access_token
            payload["refresh_token"] = tokens.refresh_token
            payload["token_expires_at"] = tokens.expires_at.isoformat() if tokens.expires_at else None
            payload["rate_count"] = len(rates)
            upsert_conversation(session, incoming.user_id, "sub2_target", payload)
            return f"我测试，登录成功，已读取 {len(rates)} 个渠道分组。现在输入报告群号/私聊QQ号 (G+群号 或者 P+QQ号，多个用 & 连接)"

        if step == "sub2_target":
            try:
                target_type, target_id = storage_target(text)
            except ValueError as exc:
                return str(exc)
            token_expires_at = _parse_datetime(payload.get("token_expires_at"))
            config = Sub2Config(
                name=payload["name"],
                target_type=target_type,
                target_id=target_id,
                base_url=payload["base_url"],
                email=payload["email"],
                password_encrypted=self.secret_box.encrypt(payload["password"]),
                access_token_encrypted=self.secret_box.encrypt(payload["access_token"]),
                refresh_token_encrypted=(
                    self.secret_box.encrypt(payload["refresh_token"]) if payload.get("refresh_token") else None
                ),
                token_expires_at=token_expires_at,
                enabled=True,
            )
            session.add(config)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return "这个 Sub2API 名称刚刚被占用了，请发送 /addsub2 重新开始。"
            session.refresh(config)

            try:
                rates = await self.sub2_client.fetch_rates_with_cached_token(
                    session,
                    config,
                    self.secret_box,
                )
                sync_sub2_rates(session, config, rates)
                config.last_checked_at = utc_now()
                config.last_error = None
                session.commit()
            except Sub2ApiError as exc:
                config.last_error = str(exc)
                session.commit()

            clear_conversation(session, incoming.user_id)
            target_text = await self._target_confirmation_text(target_type, target_id)
            return f"{target_text}\n添加成功。"

        clear_conversation(session, incoming.user_id)
        return "Sub2API 对话状态异常，已重置。请重新发送 /addsub2。"

    async def _continue_addapi(
        self,
        session: Session,
        incoming: IncomingMessage,
        step: str,
        payload: dict,
        text: str,
    ) -> str:
        if step == "name":
            name = text.strip()
            if not name:
                return "配置名称不能为空，请重新输入。"
            exists = session.scalar(select(APIConfig).where(APIConfig.name == name))
            if exists is not None:
                return "这个配置名已经存在，请换一个名称。"
            payload["name"] = name
            upsert_conversation(session, incoming.user_id, "target", payload)
            return "请输入报告群号/私聊QQ号 (G+群号 或者 P+QQ号，多个用 & 连接)"

        if step == "target":
            try:
                target_type, target_id = storage_target(text)
            except ValueError as exc:
                return str(exc)
            payload["target"] = format_target(target_type, target_id)
            upsert_conversation(session, incoming.user_id, "base_url", payload)
            target_text = await self._target_confirmation_text(target_type, target_id)
            return f"{target_text}\n请输入BaseURL"

        if step == "base_url":
            base_url = text.strip()
            if not base_url.startswith(("http://", "https://")):
                return "BaseURL 必须以 http:// 或 https:// 开头，请重新输入。"
            payload["base_url"] = base_url
            upsert_conversation(session, incoming.user_id, "api_key", payload)
            if incoming.message_type != "private":
                result = await self.onebot.send_private_msg(incoming.user_id, "请在私聊里继续输入APIKey")
                record_send_result(session, result)
                return "为了避免泄露 APIKey，后续步骤请在私聊中完成。"
            return "请输入APIKey"

        if step == "api_key":
            if incoming.message_type != "private":
                return "APIKey 只能在私聊中输入，请切换到私聊。"
            api_key = text.strip()
            if not api_key:
                return "APIKey 不能为空，请重新输入。"
            payload["api_key"] = api_key
            upsert_conversation(session, incoming.user_id, "model_name", payload)
            return "请输入监听模型名称"

        if step == "model_name":
            model_name = text.strip()
            if not model_name:
                return "模型名称不能为空，请重新输入。"
            payload["model_name"] = model_name
            check = await self.probe.probe(payload["base_url"], payload["api_key"], model_name)
            if not check.ok:
                return f"验证失败：{check.code} {check.error or ''}\n请重新输入监听模型名称，或发送 /cancel 取消。"
            try:
                config = create_api_config(
                    session,
                    self.secret_box,
                    APIConfigCreate(
                        name=payload["name"],
                        target=payload["target"],
                        base_url=payload["base_url"],
                        api_key=payload["api_key"],
                        model_name=model_name,
                    ),
                )
            except IntegrityError:
                session.rollback()
                return "这个配置名刚刚被占用了，请发送 /addapi 重新开始。"
            clear_conversation(session, incoming.user_id)
            return f"添加成功：{config.name}\n首次验证返回：{check.code}"

        clear_conversation(session, incoming.user_id)
        return "对话状态异常，已重置。请重新发送 /addapi。"


def parse_onebot_message(event: dict) -> IncomingMessage | None:
    if event.get("post_type") != "message":
        return None
    message_type = str(event.get("message_type") or "")
    if message_type not in {"private", "group"}:
        return None
    raw = event.get("raw_message")
    if raw is None:
        raw = event.get("message")
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "text":
                data = item.get("data")
                if isinstance(data, dict):
                    parts.append(str(data.get("text") or ""))
        raw = "".join(parts)
    user_id = event.get("user_id")
    if user_id is None:
        return None
    group_id = event.get("group_id")
    return IncomingMessage(
        user_id=str(user_id),
        message=str(raw or ""),
        message_type=message_type,
        group_id=str(group_id) if group_id is not None else None,
    )


def trigger_type_for_message(session: Session, message: IncomingMessage, reply: str | None) -> str | None:
    if reply is None:
        return None
    resolved = resolve_command_text(session, message.message)
    if resolved is not None:
        return f"command:{resolved.command}"
    return "conversation"


def log_incoming_message(session: Session, message: IncomingMessage, reply: str | None) -> None:
    row = ReceivedMessage(
        message_type=message.message_type,
        user_id=message.user_id,
        group_id=message.group_id,
        message=message.message[:2000],
        triggered=reply is not None,
        trigger_type=trigger_type_for_message(session, message, reply),
        reply_preview=reply[:500] if reply else None,
    )
    session.add(row)
    session.commit()


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
