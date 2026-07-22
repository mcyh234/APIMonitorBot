import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.command_settings import is_command_enabled, list_command_settings, resolve_command_text, set_command_aliases, set_command_enabled
from backend.app.commands import CommandRouter, IncomingMessage
from backend.app.crypto import SecretBox
from backend.app.models import Base, BotAdmin, BotCommandSetting
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


@pytest.mark.asyncio
async def test_disabled_up_command_rejects_bare_default_alias():
    session = make_session()
    set_command_enabled(session, "/up", False)
    router = CommandRouter(Settings(), FakeOneBot(), SecretBox("test-key"), probe=FakeProbe())

    reply = await router.handle_message(
        session,
        IncomingMessage(user_id="2087900785", message="up", message_type="private"),
    )

    assert reply == "该命令已关闭。"


def test_command_alias_resolves_to_canonical_command():
    session = make_session()
    set_command_aliases(session, "/status", ["状态", "STATUS"])

    resolved = resolve_command_text(session, "状态 cfg-one")

    assert resolved is not None
    assert resolved.command == "/status"
    assert resolved.arg == "cfg-one"
    assert resolved.alias == "状态"


def test_up_down_bare_aliases_are_permanent_defaults():
    session = make_session()

    assert resolve_command_text(session, "up").command == "/up"
    assert resolve_command_text(session, "down").command == "/down"
    set_command_aliases(session, "/up", [])

    assert resolve_command_text(session, "up").command == "/up"
    settings = {definition.command: aliases for definition, _enabled, aliases in list_command_settings(session)}
    assert settings["/up"] == ["up"]


def test_default_vote_alias_wins_over_legacy_custom_alias_collision():
    session = make_session()
    set_command_aliases(session, "/status", ["状态"])
    row = session.query(BotCommandSetting).filter_by(command="/status").one()
    row.aliases = ["up"]
    session.commit()

    assert resolve_command_text(session, "up").command == "/up"
