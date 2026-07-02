from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.availability import ApiProbe, CheckResult, InternetConnectivityProbe
from backend.app.crypto import SecretBox
from backend.app.models import APIConfig, CheckRecord, Sub2Config
from backend.app.notifier import Notifier, NotifyTarget
from backend.app.repository import today_availability
from backend.app.settings import Settings
from backend.app.sub2_price_image import Sub2PriceBoard, render_sub2_price_image
from backend.app.sub2_rates import stored_sub2_rate_views, sync_sub2_rates
from backend.app.sub2api import Sub2ApiClient
from backend.app.time_utils import coerce_aware_utc, utc_now

logger = logging.getLogger(__name__)
IGNORED_SCHEDULED_CODES = {"TIMEOUT", "NETWORK_ERROR"}


def status_message(config: APIConfig, body: str, availability: float, code: str | None = None) -> str:
    code_text = f": {code}" if code else ""
    return f"【{config.name}】\n{body}{code_text}\n最近请求成功率: {availability:.1f}%"


@dataclass(slots=True)
class NotificationEvent:
    target: NotifyTarget
    message: str


def night_saver_active(settings: Settings, at: datetime | None = None) -> bool:
    if not settings.night_saver_enabled:
        return False
    start_hour = settings.night_saver_start_hour
    end_hour = settings.night_saver_end_hour
    if start_hour == end_hour:
        return False
    local = coerce_aware_utc(at or utc_now()).astimezone(ZoneInfo(settings.app_timezone))
    if start_hour < end_hour:
        return start_hour <= local.hour < end_hour
    return local.hour >= start_hour or local.hour < end_hour


def scheduled_interval_seconds(settings: Settings, at: datetime | None = None) -> int:
    if night_saver_active(settings, at):
        return settings.night_saver_interval_seconds
    return settings.check_interval_seconds


class MonitorService:
    def __init__(
        self,
        settings: Settings,
        secret_box: SecretBox,
        notifier: Notifier,
        probe: ApiProbe | None = None,
        internet_probe: InternetConnectivityProbe | None = None,
        sub2_client: Sub2ApiClient | None = None,
    ) -> None:
        self.settings = settings
        self.secret_box = secret_box
        self.notifier = notifier
        self.probe = probe or ApiProbe(timeout_seconds=settings.request_timeout_seconds)
        self.internet_probe = internet_probe or InternetConnectivityProbe(
            url=settings.internet_check_url,
            timeout_seconds=settings.internet_check_timeout_seconds,
        )
        self.sub2_client = sub2_client or Sub2ApiClient(timeout_seconds=settings.request_timeout_seconds)
        self._lock = asyncio.Lock()
        self._last_scheduled_run_at: datetime | None = None
        self._last_internet_disconnect_notified_at: datetime | None = None

    def should_run_scheduled(self, at: datetime | None = None) -> bool:
        now = at or utc_now()
        if self._last_scheduled_run_at is None:
            return True
        elapsed_seconds = (
            coerce_aware_utc(now) - coerce_aware_utc(self._last_scheduled_run_at)
        ).total_seconds()
        return elapsed_seconds >= scheduled_interval_seconds(self.settings, now)

    def outage_repeat_checks_for_current_interval(self, at: datetime | None = None) -> int:
        interval = scheduled_interval_seconds(self.settings, at)
        base_window = self.settings.outage_repeat_checks * self.settings.check_interval_seconds
        return max(2, math.ceil(base_window / interval))

    async def check_config(
        self,
        session: Session,
        config: APIConfig,
        scheduled: bool,
        notify: bool,
        *,
        notify_timeouts: bool | None = None,
    ) -> CheckResult:
        if notify_timeouts is None:
            notify_timeouts = notify
        api_key = self.secret_box.decrypt(config.api_key_encrypted)
        result = await self.probe.probe(config.base_url, api_key, config.model_name)
        if scheduled and not result.ok:
            await asyncio.sleep(self.settings.check_retry_delay_seconds)
            retry = await self.probe.probe(config.base_url, api_key, config.model_name)
            if retry.ok:
                result = retry
            else:
                result = retry

        if scheduled and result.code in IGNORED_SCHEDULED_CODES:
            await self._handle_ignored_scheduled_result(config, result, notify_timeouts)
            return result

        self._record_result(session, config, result, scheduled)
        if scheduled and notify:
            await self._handle_notifications(session, config, result)
        return result

    async def run_all_scheduled(self, session_factory) -> None:
        now = utc_now()
        if self._lock.locked():
            return
        async with self._lock:
            with session_factory() as session:
                await self._run_sub2_scheduled(session)

            now = utc_now()
            if not self.should_run_scheduled(now):
                return
            self._last_scheduled_run_at = now
            with session_factory() as session:
                configs = list(session.scalars(select(APIConfig).where(APIConfig.enabled.is_(True))).all())
                events: list[NotificationEvent] = []
                for config in configs:
                    try:
                        result = await self.check_config(
                            session,
                            config,
                            scheduled=True,
                            notify=False,
                            notify_timeouts=True,
                        )
                        if result.code not in IGNORED_SCHEDULED_CODES:
                            event = self._notification_event(session, config, result)
                            if event is not None:
                                events.append(event)
                    except Exception:
                        logger.exception("Scheduled check failed for %s", config.name)
                await self._send_grouped_notifications(events)

    async def _run_sub2_scheduled(self, session: Session) -> None:
        configs = list(session.scalars(select(Sub2Config).where(Sub2Config.enabled.is_(True))).all())
        for config in configs:
            try:
                rates = await self.sub2_client.fetch_rates_with_cached_token(
                    session,
                    config,
                    self.secret_box,
                )
                changes = sync_sub2_rates(session, config, rates)
                config.last_checked_at = utc_now()
                config.last_error = None
                session.commit()
                if changes:
                    rate_views = stored_sub2_rate_views(session, config)
                    image = render_sub2_price_image(
                        [Sub2PriceBoard(config.name, rate_views, changes)],
                        title="Sub2API 价格变动",
                        timezone_name=self.settings.app_timezone,
                    )
                    await self.notifier.send_image(
                        NotifyTarget(config.target_type, config.target_id),
                        image,
                        "sub2-price-change.png",
                    )
            except Exception as exc:
                logger.exception("Scheduled Sub2API check failed for %s", config.name)
                config.last_checked_at = utc_now()
                config.last_error = str(exc)
                session.commit()

    def _record_result(self, session: Session, config: APIConfig, result: CheckResult, scheduled: bool) -> None:
        now = utc_now()
        status = "ok" if result.ok else "down"
        record = CheckRecord(
            api_config_id=config.id,
            checked_at=now,
            status=status,
            code=result.code,
            error=result.error,
            latency_ms=result.latency_ms,
            scheduled=scheduled,
        )
        session.add(record)
        config.status = status
        config.last_code = result.code
        config.last_error = result.error
        config.last_checked_at = now
        config.last_latency_ms = result.latency_ms
        if scheduled:
            if result.ok:
                config.success_checks += 1
                config.failure_checks = 0
            else:
                config.failure_checks += 1
                config.success_checks = 0
        session.commit()
        session.refresh(config)

    async def _handle_ignored_scheduled_result(
        self,
        config: APIConfig,
        result: CheckResult,
        notify: bool,
    ) -> None:
        logger.info(
            "Ignored scheduled %s for %s; it will not affect API availability.",
            result.code,
            config.name,
        )
        if result.code != "TIMEOUT":
            return
        connectivity = await self.internet_probe.check()
        if connectivity.ok:
            return
        if not notify or not self._should_notify_internet_disconnect():
            return
        self._last_internet_disconnect_notified_at = utc_now()
        error_text = f" {connectivity.error}" if connectivity.error else ""
        await self.notifier.send(
            NotifyTarget("private", self.settings.default_admin_qq),
            "【APIMonitorBot】\n当前国际互联网连接断开\n"
            f"Google 连通性检测失败：{connectivity.code}{error_text}\n"
            "API Timeout 已忽略，不会计入可用性，也不会向业务群发送中断通报。",
        )

    def _should_notify_internet_disconnect(self) -> bool:
        if self._last_internet_disconnect_notified_at is None:
            return True
        elapsed = (
            utc_now() - coerce_aware_utc(self._last_internet_disconnect_notified_at)
        ).total_seconds()
        return elapsed >= self.settings.internet_disconnect_notify_cooldown_seconds

    def _notification_event(
        self,
        session: Session,
        config: APIConfig,
        result: CheckResult,
    ) -> NotificationEvent | None:
        if result.code in IGNORED_SCHEDULED_CODES:
            return None
        availability = today_availability(session, config.id, self.settings.app_timezone)
        target = NotifyTarget(config.target_type, config.target_id)
        if not result.ok:
            if config.outage_first_at is None:
                config.outage_first_at = utc_now()
                config.outage_notified_at = utc_now()
                config.outage_followup_sent = False
                session.commit()
                return NotificationEvent(
                    target,
                    status_message(config, "当前出现业务中断", availability, result.code),
                )

            if (
                not config.outage_followup_sent
                and config.failure_checks >= self.outage_repeat_checks_for_current_interval()
            ):
                config.outage_followup_sent = True
                session.commit()
                return NotificationEvent(
                    target,
                    status_message(
                        config,
                        "10分钟仍未恢复业务",
                        availability,
                        result.code,
                    )
                    + "\n待恢复后会发布恢复通报",
                )
            return None

        if config.outage_first_at is not None and config.success_checks >= self.settings.recovery_confirm_checks:
            config.outage_first_at = None
            config.outage_notified_at = None
            config.outage_followup_sent = False
            session.commit()
            return NotificationEvent(
                target,
                status_message(config, "当前服务恢复可用", availability),
            )
        return None

    async def _handle_notifications(self, session: Session, config: APIConfig, result: CheckResult) -> None:
        event = self._notification_event(session, config, result)
        if event is not None:
            await self.notifier.send(event.target, event.message)

    async def _send_grouped_notifications(self, events: list[NotificationEvent]) -> None:
        grouped: dict[tuple[str, str], list[NotificationEvent]] = {}
        for event in events:
            key = (event.target.target_type, event.target.target_id)
            grouped.setdefault(key, []).append(event)

        for group in grouped.values():
            first = group[0]
            if len(group) == 1:
                await self.notifier.send(first.target, first.message)
                continue
            message = "【APIMonitorBot】\n本轮检测发现多项 API 状态变化：\n\n" + "\n\n".join(
                event.message for event in group
            )
            await self.notifier.send(first.target, message)
