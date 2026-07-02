from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base
from backend.app.repository import consume_rate_limit, parse_target


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_parse_target_group_and_private():
    assert parse_target("G123") == ("group", "123")
    assert parse_target("P456") == ("private", "456")


def test_rate_limit_consumes_then_blocks():
    session = make_session()
    assert consume_rate_limit(session, "10001", "check", 300) == (True, 0)
    allowed, remaining = consume_rate_limit(session, "10001", "check", 300)
    assert allowed is False
    assert remaining > 0

