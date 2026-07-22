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
    default_aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedCommand:
    command: str
    arg: str
    alias: str | None = None


COMMAND_DEFINITIONS: tuple[CommandDefinition, ...] = (
    CommandDefinition("/addapi", "添加 API", "管理员多轮添加 OpenAI 兼容 API。"),
    CommandDefinition("/addsub2", "添加 Sub2API", "管理员多轮添加 Sub2API 渠道倍率监控。"),
    CommandDefinition("/list", "配置列表", "列出所有运行中的 API 配置。"),
    CommandDefinition("/remove", "删除配置", "删除指定 API 配置。"),
    CommandDefinition("/check", "立即检查", "手动检查某个 API 配置；不带参数时检查当前通知对象的全部配置。"),
    CommandDefinition("/status", "状态图", "发送 API 状态条图片。"),
    CommandDefinition("/stat", "网页快照", "抓取 GPTStore 状态页快照。"),
    CommandDefinition("/price", "价格图", "发送 Sub2API 渠道倍率图片。"),
    CommandDefinition("/up", "看涨投票", "看涨整体 Token 倍率。", ("up",)),
    CommandDefinition("/down", "看跌投票", "看跌整体 Token 倍率。", ("down",)),
    CommandDefinition("/radar", "降智雷达", "读取 Codex Radar 公开摘要并发送模型 IQ 趋势图。"),
    CommandDefinition("/tibo", "Tibo 雷达", "读取 Tibo 最新公开帖子与 presence 摘要并发送 X 风格图片。"),
)


KNOWN_COMMANDS = {item.command for item in COMMAND_DEFINITIONS}


def normalize_command(command: str) -> str:
    return "/" + command.strip().lstrip("/").casefold()


def known_command(command: str) -> bool:
    return normalize_command(command) in KNOWN_COMMANDS


def command_key_for_rate_limit(command: str) -> str:
    return normalize_command(command).lstrip("/")


def is_command_enabled(session: Session, command: str) -> bool:
    normalized = normalize_command(command)
    row = session.scalar(select(BotCommandSetting).where(BotCommandSetting.command == normalized))
    return True if row is None else bool(row.enabled)


def set_command_enabled(session: Session, command: str, enabled: bool) -> BotCommandSetting:
    return set_command_setting(session, command, enabled=enabled)


def set_command_aliases(session: Session, command: str, aliases: list[str]) -> BotCommandSetting:
    return set_command_setting(session, command, aliases=aliases)


def set_command_setting(
    session: Session,
    command: str,
    *,
    enabled: bool | None = None,
    aliases: list[str] | None = None,
) -> BotCommandSetting:
    normalized = normalize_command(command)
    if normalized not in KNOWN_COMMANDS:
        raise ValueError("未知命令。")
    row = session.scalar(select(BotCommandSetting).where(BotCommandSetting.command == normalized))
    if row is None:
        row = BotCommandSetting(command=normalized, enabled=True, aliases=[])
        session.add(row)
    if enabled is not None:
        row.enabled = enabled
    if aliases is not None:
        row.aliases = _validate_aliases(session, normalized, aliases)
    session.commit()
    session.refresh(row)
    return row


def list_command_settings(session: Session) -> list[tuple[CommandDefinition, bool, list[str]]]:
    rows = {
        item.command: item
        for item in session.scalars(select(BotCommandSetting)).all()
    }
    result: list[tuple[CommandDefinition, bool, list[str]]] = []
    for definition in COMMAND_DEFINITIONS:
        row = rows.get(definition.command)
        enabled = True if row is None else bool(row.enabled)
        aliases = _merge_aliases(definition.default_aliases, [] if row is None else row.aliases or [])
        result.append((definition, enabled, aliases))
    return result


def resolve_command_text(session: Session, text: str) -> ResolvedCommand | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("/"):
        parts = stripped.split(maxsplit=1)
        command = normalize_command(parts[0])
        arg = parts[1].strip() if len(parts) > 1 else ""
        return ResolvedCommand(command=command, arg=arg)

    parts = stripped.split(maxsplit=1)
    head = parts[0].casefold()
    arg = parts[1].strip() if len(parts) > 1 else ""
    for definition in COMMAND_DEFINITIONS:
        for alias in definition.default_aliases:
            if alias.casefold() == head:
                return ResolvedCommand(command=definition.command, arg=arg, alias=alias)
    for definition, _enabled, aliases in list_command_settings(session):
        for alias in aliases:
            if alias.casefold() == head:
                return ResolvedCommand(command=definition.command, arg=arg, alias=alias)
    return None


def _validate_aliases(session: Session, command: str, aliases: list[str]) -> list[str]:
    cleaned = _clean_alias_list(aliases)
    if len(cleaned) > 16:
        raise ValueError("每个命令最多设置 16 个别名。")
    for alias in cleaned:
        if len(alias) > 32:
            raise ValueError("别名长度不能超过 32 个字符。")
        if alias.startswith("/"):
            raise ValueError("别名不需要以 / 开头，请直接填写触发词。")
        if any(ch.isspace() for ch in alias):
            raise ValueError("别名不能包含空白字符。")

    current_keys = {alias.casefold() for alias in cleaned}
    for definition in COMMAND_DEFINITIONS:
        if definition.command == command:
            continue
        for alias in definition.default_aliases:
            if alias.casefold() in current_keys:
                raise ValueError(f"别名“{alias}”已经被 {definition.command} 使用。")
    rows = session.scalars(select(BotCommandSetting)).all()
    for row in rows:
        if row.command == command:
            continue
        for alias in _clean_alias_list(row.aliases or []):
            if alias.casefold() in current_keys:
                raise ValueError(f"别名“{alias}”已经被 {row.command} 使用。")
    return cleaned


def _merge_aliases(defaults: tuple[str, ...], configured: list[str]) -> list[str]:
    return _clean_alias_list([*defaults, *configured])


def _clean_alias_list(aliases: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in aliases:
        alias = str(item or "").strip()
        if not alias:
            continue
        key = alias.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(alias)
    return result
