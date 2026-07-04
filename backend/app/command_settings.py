from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import BotCommandSetting


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    command: str
    label: str
    description: str


COMMAND_DEFINITIONS: tuple[CommandDefinition, ...] = (
    CommandDefinition("/addapi", "添加 API", "管理员多轮添加 OpenAI 兼容 API。"),
    CommandDefinition("/addsub2", "添加 Sub2API", "管理员多轮添加 Sub2API 渠道倍率监控。"),
    CommandDefinition("/list", "配置列表", "列出所有运行中的 API 配置。"),
    CommandDefinition("/remove", "删除配置", "删除指定 API 配置。"),
    CommandDefinition("/check", "立即检查", "手动检查某个 API 配置。"),
    CommandDefinition("/status", "状态图", "发送 API 状态条图片。"),
    CommandDefinition("/stat", "网页快照", "抓取 GPTStore 状态页快照。"),
    CommandDefinition("/price", "价格图", "发送 Sub2API 渠道倍率图片。"),
)


def known_command(command: str) -> bool:
    return any(item.command == command for item in COMMAND_DEFINITIONS)


def is_command_enabled(session: Session, command: str) -> bool:
    row = session.scalar(select(BotCommandSetting).where(BotCommandSetting.command == command))
    return True if row is None else bool(row.enabled)


def set_command_enabled(session: Session, command: str, enabled: bool) -> BotCommandSetting:
    if not known_command(command):
        raise ValueError("未知命令。")
    row = session.scalar(select(BotCommandSetting).where(BotCommandSetting.command == command))
    if row is None:
        row = BotCommandSetting(command=command, enabled=enabled)
        session.add(row)
    else:
        row.enabled = enabled
    session.commit()
    session.refresh(row)
    return row


def list_command_settings(session: Session) -> list[tuple[CommandDefinition, bool]]:
    rows = {
        item.command: item.enabled
        for item in session.scalars(select(BotCommandSetting)).all()
    }
    return [(item, bool(rows.get(item.command, True))) for item in COMMAND_DEFINITIONS]
