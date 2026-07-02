from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import SendRecord
from backend.app.onebot import OneBotClient, OneBotSendResult


@dataclass(slots=True)
class NotifyTarget:
    target_type: str
    target_id: str


class Notifier:
    async def send(self, target: NotifyTarget, message: str) -> None:
        raise NotImplementedError

    async def send_image(self, target: NotifyTarget, image_bytes: bytes, filename: str) -> None:
        raise NotImplementedError


class OneBotNotifier(Notifier):
    def __init__(self, client: OneBotClient, session_factory: sessionmaker | None = None) -> None:
        self.client = client
        self.session_factory = session_factory

    async def send(self, target: NotifyTarget, message: str) -> None:
        result = await self.client.send_message(target.target_type, target.target_id, message)
        if self.session_factory is not None:
            with self.session_factory() as session:
                record_send_result(session, result)

    async def send_image(self, target: NotifyTarget, image_bytes: bytes, filename: str) -> None:
        result = await self.client.send_image_message(
            target.target_type,
            target.target_id,
            image_bytes,
            filename,
        )
        if self.session_factory is not None:
            with self.session_factory() as session:
                record_send_result(session, result)


def record_send_result(session: Session, result: OneBotSendResult) -> None:
    row = SendRecord(
        action=result.action or "",
        target_type=result.target_type or "",
        target_id=result.target_id or "",
        message_preview=(result.message or "")[:500],
        ok=result.ok,
        error=result.error,
        status_code=result.status_code,
        response_payload=result.payload,
    )
    session.add(row)
    session.commit()
