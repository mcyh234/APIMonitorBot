from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.availability import ApiProbe
from backend.app.crypto import SecretBox
from backend.app.models import APIConfig, ReceivedMessage
from backend.app.onebot import OneBotClient
from backend.app.notifier import record_send_result
from backend.app.repository import (
    clear_conversation,
    consume_rate_limit,
    create_api_config,
    format_target,
    get_conversation,
    is_admin,
    parse_target,
    today_availability,
    upsert_conversation,
)
from backend.app.schemas import APIConfigCreate
from backend.app.settings import Settings
from backend.app.status_bars import build_status_bars
from backend.app.status_image import render_status_image
from backend.app.web_snapshot import StatusPageSnapshotter, StatusSnapshotError


@dataclass(slots=True)
class IncomingMessage:
    user_id: str
    message: str
    message_type: str
    group_id: str | None = None


class CommandRouter:
    def __init__(
        self,
        settings: Settings,
        onebot: OneBotClient,
        secret_box: SecretBox,
        probe: ApiProbe | None = None,
        snapshotter: StatusPageSnapshotter | None = None,
    ) -> None:
        self.settings = settings
        self.onebot = onebot
        self.secret_box = secret_box
        self.probe = probe or ApiProbe(timeout_seconds=settings.request_timeout_seconds)
        self.snapshotter = snapshotter or StatusPageSnapshotter(settings)

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
            return await self._continue_addapi(session, incoming, state.step, dict(state.payload or {}), text)

        if not text.startswith("/"):
            return None
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if command == "/cancel":
            clear_conversation(session, incoming.user_id)
            return "已取消当前对话。"
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
        if command == "/check":
            if not arg:
                return "用法：/check <apiname>"
            return await self._manual_check(session, incoming, arg)
        if command == "/status":
            return await self._status(session, incoming, arg)
        if command == "/stat":
            return await self._stat(session, incoming)
        return None

    async def _manual_check(self, session: Session, incoming: IncomingMessage, name: str) -> str:
        config = session.scalar(select(APIConfig).where(APIConfig.name == name))
        if config is None:
            return f"没有找到配置：{name}"
        allowed = False
        if incoming.message_type == "group" and incoming.group_id:
            allowed = config.target_type == "group" and config.target_id == incoming.group_id
        else:
            allowed = is_admin(session, incoming.user_id)
        if not allowed:
            return "权限不足。"
        ok, remaining = self._consume_command_cooldown(session, incoming, "check")
        if not ok:
            return f"操作太频繁，请 {remaining} 秒后再试。"
        api_key = self.secret_box.decrypt(config.api_key_encrypted)
        result = await self.probe.probe(config.base_url, api_key, config.model_name)
        availability = today_availability(session, config.id, self.settings.app_timezone)
        state = "服务可用" if result.ok else "服务不可用"
        return f"【{config.name}】\n当前{state}: {result.code}\n最近请求成功率: {availability:.1f}%"

    async def _status(self, session: Session, incoming: IncomingMessage, name: str) -> str:
        configs = self._status_configs_for_context(session, incoming, name)
        if configs is None:
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
        if result.ok:
            return ""
        return f"状态图发送失败：{result.error or result.status_code or '未知错误'}"

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
                if config.target_type == "group" and config.target_id == incoming.group_id:
                    return [config]
                return None
            if is_admin(session, incoming.user_id):
                return [config]
            return None

        if incoming.message_type == "group" and incoming.group_id:
            return list(
                session.scalars(
                    select(APIConfig)
                    .where(APIConfig.target_type == "group")
                    .where(APIConfig.target_id == incoming.group_id)
                    .order_by(APIConfig.name)
                ).all()
            )
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
        if result.ok:
            return ""
        return f"网页快照发送失败：{result.error or result.status_code or '未知错误'}"

    def _notification_target_for_context(
        self,
        session: Session,
        incoming: IncomingMessage,
    ) -> tuple[str, str] | None:
        if incoming.message_type == "group" and incoming.group_id:
            bound_group = session.scalar(
                select(APIConfig.id)
                .where(APIConfig.target_type == "group")
                .where(APIConfig.target_id == incoming.group_id)
                .limit(1)
            )
            if bound_group is not None or is_admin(session, incoming.user_id):
                return ("group", incoming.group_id)
            return None
        if is_admin(session, incoming.user_id):
            return ("private", incoming.user_id)
        return None

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
            return "请输入报告群号/私聊QQ号 (G+群号 或者 P+QQ号)"

        if step == "target":
            try:
                target_type, target_id = parse_target(text)
            except ValueError as exc:
                return str(exc)
            payload["target"] = ("G" if target_type == "group" else "P") + target_id
            upsert_conversation(session, incoming.user_id, "base_url", payload)
            if target_type == "group":
                in_group = await self.onebot.is_in_group(target_id)
                state_text = "在" if in_group else "不在" if in_group is False else "无法确认是否在"
                return f"你添加了一个群聊，我{state_text}这个群里面。请输入BaseURL"
            return "你添加了一个私聊通知目标。请输入BaseURL"

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


def trigger_type_for_message(message: IncomingMessage, reply: str | None) -> str | None:
    if reply is None:
        return None
    text = message.message.strip()
    if text.startswith("/"):
        command = text.split(maxsplit=1)[0].lower()
        return f"command:{command}"
    return "conversation"


def log_incoming_message(session: Session, message: IncomingMessage, reply: str | None) -> None:
    row = ReceivedMessage(
        message_type=message.message_type,
        user_id=message.user_id,
        group_id=message.group_id,
        message=message.message[:2000],
        triggered=reply is not None,
        trigger_type=trigger_type_for_message(message, reply),
        reply_preview=reply[:500] if reply else None,
    )
    session.add(row)
    session.commit()
