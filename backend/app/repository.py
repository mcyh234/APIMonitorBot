from __future__ import annotations

from datetime import timedelta
from html import unescape as html_unescape
from unicodedata import normalize as unicode_normalize

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from backend.app.crypto import SecretBox
from backend.app.models import APIConfig, BotAdmin, CheckRecord, CommandRateLimit, ConversationState
from backend.app.schemas import APIConfigCreate, APIConfigOut
from backend.app.time_utils import api_datetime, coerce_aware_utc, local_day_start_utc, utc_now


TargetTuple = tuple[str, str]


def parse_target(value: str) -> TargetTuple:
    targets = parse_targets(value)
    if len(targets) != 1:
        raise ValueError("目标格式必须是单个 G群号 或 PQQ号。")
    return targets[0]


def parse_targets(value: str) -> list[TargetTuple]:
    clean = value.strip()
    for _ in range(2):
        unescaped = html_unescape(clean)
        if unescaped == clean:
            break
        clean = unescaped
    clean = unicode_normalize("NFKC", clean).upper()
    if not clean:
        raise ValueError("目标格式必须是 G群号 或 PQQ号，多个目标用 & 连接。")
    targets: list[TargetTuple] = []
    seen: set[TargetTuple] = set()
    for raw_part in clean.split("&"):
        part = raw_part.strip()
        if len(part) < 2 or part[0] not in {"G", "P"} or not part[1:].isdigit():
            raise ValueError("目标格式必须是 G群号 或 PQQ号，多个目标用 & 连接。")
        target = ("group" if part[0] == "G" else "private", part[1:])
        if target not in seen:
            targets.append(target)
            seen.add(target)
    if not targets:
        raise ValueError("目标格式必须是 G群号 或 PQQ号，多个目标用 & 连接。")
    return targets


def storage_target(value: str) -> TargetTuple:
    targets = parse_targets(value)
    if len(targets) == 1:
        return targets[0]
    return ("multi", format_targets(targets))


def format_target(target_type: str, target_id: str) -> str:
    if target_type == "multi":
        return format_targets(target_entries(target_type, target_id))
    return ("G" if target_type == "group" else "P") + target_id


def format_targets(targets: list[TargetTuple] | tuple[TargetTuple, ...]) -> str:
    return "&".join(("G" if target_type == "group" else "P") + target_id for target_type, target_id in targets)


def target_entries(target_type: str, target_id: str) -> list[TargetTuple]:
    if target_type == "multi":
        return parse_targets(target_id)
    return [(target_type, target_id)]


def target_contains(target_type: str, target_id: str, expected_type: str, expected_id: str) -> bool:
    return any(
        item_type == expected_type and item_id == str(expected_id)
        for item_type, item_id in target_entries(target_type, target_id)
    )


def is_admin(session: Session, qq: str) -> bool:
    return session.scalar(select(BotAdmin).where(BotAdmin.qq == str(qq))) is not None


def today_availability(session: Session, config_id: int, timezone_name: str) -> float:
    start = local_day_start_utc(timezone_name)
    rows = session.execute(
        select(
            func.count(CheckRecord.id),
            func.sum(case((CheckRecord.status == "ok", 1), else_=0)),
        )
        .where(CheckRecord.api_config_id == config_id)
        .where(CheckRecord.scheduled.is_(True))
        .where(CheckRecord.checked_at >= start)
    ).one()
    total = int(rows[0] or 0)
    ok = int(rows[1] or 0)
    if total == 0:
        return 100.0
    return round(ok * 100 / total, 1)


def config_to_out(session: Session, config: APIConfig, timezone_name: str) -> APIConfigOut:
    return APIConfigOut(
        id=config.id,
        name=config.name,
        target_type=config.target_type,
        target_id=config.target_id,
        target=format_target(config.target_type, config.target_id),
        base_url=config.base_url,
        model_name=config.model_name,
        enabled=config.enabled,
        status=config.status,
        last_code=config.last_code,
        last_error=config.last_error,
        last_checked_at=api_datetime(config.last_checked_at),
        last_latency_ms=config.last_latency_ms,
        today_availability=today_availability(session, config.id, timezone_name),
        created_at=api_datetime(config.created_at),
        updated_at=api_datetime(config.updated_at),
    )


def create_api_config(session: Session, secret_box: SecretBox, data: APIConfigCreate) -> APIConfig:
    target_type, target_id = storage_target(data.target)
    config = APIConfig(
        name=data.name.strip(),
        target_type=target_type,
        target_id=target_id,
        base_url=data.base_url.strip(),
        api_key_encrypted=secret_box.encrypt(data.api_key.strip()),
        model_name=data.model_name.strip(),
        enabled=data.enabled,
    )
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def clear_conversation(session: Session, user_id: str) -> None:
    session.execute(delete(ConversationState).where(ConversationState.user_id == str(user_id)))
    session.commit()


def get_conversation(session: Session, user_id: str) -> ConversationState | None:
    state = session.scalar(select(ConversationState).where(ConversationState.user_id == str(user_id)))
    if state and state.expires_at and coerce_aware_utc(state.expires_at) < utc_now():
        clear_conversation(session, user_id)
        return None
    return state


def upsert_conversation(
    session: Session,
    user_id: str,
    step: str,
    payload: dict,
    ttl_minutes: int = 15,
) -> ConversationState:
    state = session.scalar(select(ConversationState).where(ConversationState.user_id == str(user_id)))
    expires_at = utc_now() + timedelta(minutes=ttl_minutes)
    if state is None:
        state = ConversationState(user_id=str(user_id), step=step, payload=payload, expires_at=expires_at)
        session.add(state)
    else:
        state.step = step
        state.payload = payload
        state.expires_at = expires_at
    session.commit()
    session.refresh(state)
    return state


def consume_rate_limit(session: Session, user_id: str, command: str, cooldown_seconds: int) -> tuple[bool, int]:
    now = utc_now()
    row = session.scalar(
        select(CommandRateLimit)
        .where(CommandRateLimit.user_id == str(user_id))
        .where(CommandRateLimit.command == command)
    )
    if row is not None:
        elapsed = (now - coerce_aware_utc(row.last_used_at)).total_seconds()
        if elapsed < cooldown_seconds:
            return False, int(cooldown_seconds - elapsed)
        row.last_used_at = now
    else:
        row = CommandRateLimit(user_id=str(user_id), command=command, last_used_at=now)
        session.add(row)
    session.commit()
    return True, 0
