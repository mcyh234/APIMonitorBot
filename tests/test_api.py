import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.api import update_config
from backend.app.crypto import SecretBox
from backend.app.models import APIConfig, Base
from backend.app.schemas import APIConfigUpdate
from backend.app.settings import Settings


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def add_config(session, secret_box, name: str):
    config = APIConfig(
        name=name,
        target_type="group",
        target_id="123",
        base_url="https://example.com/v1",
        api_key_encrypted=secret_box.encrypt("sk-test"),
        model_name="gpt-test",
        enabled=True,
    )
    session.add(config)
    session.commit()
    return config


def test_update_config_renames_api_config():
    session = make_session()
    secret_box = SecretBox("test-key")
    add_config(session, secret_box, "old-name")

    result = update_config(
        "old-name",
        APIConfigUpdate(name="new-name", model_name="gpt-new"),
        session,
        Settings(),
        secret_box,
    )

    assert result.name == "new-name"
    assert result.model_name == "gpt-new"
    assert session.scalar(select(APIConfig).where(APIConfig.name == "old-name")) is None
    assert session.scalar(select(APIConfig).where(APIConfig.name == "new-name")) is not None


def test_update_config_rejects_duplicate_name():
    session = make_session()
    secret_box = SecretBox("test-key")
    add_config(session, secret_box, "first")
    add_config(session, secret_box, "second")

    with pytest.raises(HTTPException) as exc_info:
        update_config(
            "first",
            APIConfigUpdate(name="second"),
            session,
            Settings(),
            secret_box,
        )

    assert exc_info.value.status_code == 409
