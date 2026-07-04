import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.command_settings import is_command_enabled, set_command_enabled
from backend.app.commands import CommandRouter, IncomingMessage
from backend.app.crypto import SecretBox
from backend.app.models import Base, BotAdmin
from backend.app.settings import Settings
from tests.test_commands import FakeOneBot, FakeProbe


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    session.add(BotAdmin(qq="2087900785"))
    session.commit()
    return session


@pytest.mark.asyncio
async def test_disabled_command_is_rejected_by_router():
    session = make_session()
    set_command_enabled(session, "/list", False)
    router = CommandRouter(Settings(), FakeOneBot(), SecretBox("test-key"), probe=FakeProbe())

    reply = await router.handle_message(
        session,
        IncomingMessage(user_id="2087900785", message="/list", message_type="private"),
    )

    assert is_command_enabled(session, "/list") is False
    assert reply == "该命令已关闭。"
