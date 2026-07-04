from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base
from backend.app.webui_auth import (
    create_webui_token,
    set_webui_secret,
    verify_webui_secret,
    verify_webui_token,
    webui_secret_configured,
)


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_webui_secret_setup_login_and_token_verification():
    session = make_session()

    assert webui_secret_configured(session) is False
    set_webui_secret(session, "webui-secret-123")

    assert webui_secret_configured(session) is True
    assert verify_webui_secret(session, "webui-secret-123") is True
    assert verify_webui_secret(session, "wrong-secret") is False

    token = create_webui_token(session)

    assert verify_webui_token(session, token) is True
    assert verify_webui_token(session, token + "bad") is False
