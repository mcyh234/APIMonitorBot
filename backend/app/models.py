from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend.app.time_utils import utc_now


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class APIConfig(TimestampMixin, Base):
    __tablename__ = "api_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    target_type: Mapped[str] = mapped_column(String(16), index=True)
    target_id: Mapped[str] = mapped_column(String(512), index=True)
    base_url: Mapped[str] = mapped_column(String(512))
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    last_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    outage_first_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outage_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outage_followup_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_checks: Mapped[int] = mapped_column(Integer, default=0)
    success_checks: Mapped[int] = mapped_column(Integer, default=0)

    records: Mapped[list["CheckRecord"]] = relationship(
        back_populates="api_config",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CheckRecord(Base):
    __tablename__ = "check_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_config_id: Mapped[int] = mapped_column(
        ForeignKey("api_configs.id", ondelete="CASCADE"),
        index=True,
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scheduled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    api_config: Mapped[APIConfig] = relationship(back_populates="records")


class Sub2Config(TimestampMixin, Base):
    __tablename__ = "sub2_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    target_type: Mapped[str] = mapped_column(String(16), index=True)
    target_id: Mapped[str] = mapped_column(String(512), index=True)
    base_url: Mapped[str] = mapped_column(String(512))
    # The table name is kept for compatibility with existing Sub2API installations.
    upstream_type: Mapped[str] = mapped_column(String(24), default="sub2api", index=True)
    credential_mode: Mapped[str] = mapped_column(String(24), default="password")
    email: Mapped[str] = mapped_column(String(255))
    password_encrypted: Mapped[str] = mapped_column(Text)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    newapi_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_cookie_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    login_extra_params_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    rates: Mapped[list["Sub2ChannelRate"]] = relationship(
        back_populates="sub2_config",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    rate_history: Mapped[list["Sub2RateHistory"]] = relationship(
        back_populates="sub2_config",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Sub2ChannelRate(Base):
    __tablename__ = "sub2_channel_rates"
    __table_args__ = (
        UniqueConstraint("sub2_config_id", "platform", "group_key", name="uq_sub2_rate_group"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sub2_config_id: Mapped[int] = mapped_column(
        ForeignKey("sub2_configs.id", ondelete="CASCADE"),
        index=True,
    )
    platform: Mapped[str] = mapped_column(String(64), index=True)
    group_key: Mapped[str] = mapped_column(String(128), index=True)
    group_name: Mapped[str] = mapped_column(String(255))
    rate_multiplier: Mapped[float] = mapped_column(Float)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    sub2_config: Mapped[Sub2Config] = relationship(back_populates="rates")


class Sub2RateHistory(Base):
    __tablename__ = "sub2_rate_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sub2_config_id: Mapped[int] = mapped_column(
        ForeignKey("sub2_configs.id", ondelete="CASCADE"),
        index=True,
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    platform: Mapped[str] = mapped_column(String(64), index=True)
    group_key: Mapped[str] = mapped_column(String(128), index=True)
    group_name: Mapped[str] = mapped_column(String(255))
    rate_multiplier: Mapped[float] = mapped_column(Float)

    sub2_config: Mapped[Sub2Config] = relationship(back_populates="rate_history")


class Sub2SentimentVote(TimestampMixin, Base):
    __tablename__ = "sub2_sentiment_votes"
    __table_args__ = (
        UniqueConstraint("user_id", "vote_date", name="uq_sub2_sentiment_user_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    vote_date: Mapped[date] = mapped_column(Date, index=True)
    direction: Mapped[str] = mapped_column(String(8), index=True)
    source_type: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(64))


class BotAdmin(TimestampMixin, Base):
    __tablename__ = "bot_admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    qq: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class BotCommandSetting(Base):
    __tablename__ = "bot_command_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    command: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ConversationState(Base):
    __tablename__ = "conversation_states"
    __table_args__ = (UniqueConstraint("user_id", name="uq_conversation_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    step: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommandRateLimit(Base):
    __tablename__ = "command_rate_limits"
    __table_args__ = (UniqueConstraint("user_id", "command", name="uq_rate_user_command"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    command: Mapped[str] = mapped_column(String(64), index=True)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReceivedMessage(Base):
    __tablename__ = "received_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    message_type: Mapped[str] = mapped_column(String(16), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    trigger_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reply_preview: Mapped[str | None] = mapped_column(Text, nullable=True)


class SendRecord(Base):
    __tablename__ = "send_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(16), index=True)
    target_id: Mapped[str] = mapped_column(String(512), index=True)
    message_preview: Mapped[str] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
