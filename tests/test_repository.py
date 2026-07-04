from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base
from backend.app.repository import consume_rate_limit, format_target, parse_target, parse_targets, storage_target, target_contains


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_parse_target_group_and_private():
    assert parse_target("G123") == ("group", "123")
    assert parse_target("P456") == ("private", "456")




def test_parse_targets_supports_multiple_targets_and_deduplicates():
    assert parse_targets("G123&P456＆g123") == [("group", "123"), ("private", "456")]
    assert storage_target("G123&P456") == ("multi", "G123&P456")
    assert format_target("multi", "G123&P456") == "G123&P456"
    assert target_contains("multi", "G123&P456", "group", "123") is True
    assert target_contains("multi", "G123&P456", "private", "456") is True
    assert target_contains("multi", "G123&P456", "group", "999") is False


def test_parse_target_rejects_multiple_targets_when_single_required():
    try:
        parse_target("G123&P456")
    except ValueError as exc:
        assert "单个" in str(exc)
    else:
        raise AssertionError("parse_target should reject multiple targets")

def test_rate_limit_consumes_then_blocks():

    session = make_session()
    assert consume_rate_limit(session, "10001", "check", 300) == (True, 0)
    allowed, remaining = consume_rate_limit(session, "10001", "check", 300)
    assert allowed is False
    assert remaining > 0

