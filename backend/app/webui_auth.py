from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from sqlalchemy.orm import Session

from backend.app.app_settings import get_app_setting, set_app_setting

WEBUI_SECRET_HASH_KEY = "webui.secret_hash"
WEBUI_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
_HASH_ITERATIONS = 260_000


def webui_secret_configured(session: Session) -> bool:
    return bool(get_app_setting(session, WEBUI_SECRET_HASH_KEY))


def set_webui_secret(session: Session, secret: str) -> None:
    clean = secret.strip()
    if len(clean) < 8:
        raise ValueError("WebUI 进入密钥至少需要 8 个字符。")
    set_app_setting(session, WEBUI_SECRET_HASH_KEY, hash_webui_secret(clean))


def verify_webui_secret(session: Session, secret: str) -> bool:
    stored = get_app_setting(session, WEBUI_SECRET_HASH_KEY)
    if not stored:
        return False
    return verify_secret_hash(secret.strip(), stored)


def create_webui_token(session: Session) -> str:
    stored = get_app_setting(session, WEBUI_SECRET_HASH_KEY)
    if not stored:
        raise ValueError("WebUI 进入密钥尚未设置。")
    issued_at = str(int(time.time()))
    nonce = secrets.token_urlsafe(18)
    payload = f"{issued_at}.{nonce}"
    signature = _sign_token_payload(stored, payload)
    return f"{payload}.{signature}"


def verify_webui_token(session: Session, token: str | None) -> bool:
    if not token:
        return False
    stored = get_app_setting(session, WEBUI_SECRET_HASH_KEY)
    if not stored:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    issued_at, nonce, signature = parts
    if not issued_at.isdigit() or not nonce:
        return False
    age = time.time() - int(issued_at)
    if age < 0 or age > WEBUI_TOKEN_TTL_SECONDS:
        return False
    expected = _sign_token_payload(stored, f"{issued_at}.{nonce}")
    return hmac.compare_digest(signature, expected)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def hash_webui_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, _HASH_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _HASH_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_secret_hash(secret: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, int(iterations))
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)


def _sign_token_payload(secret_hash: str, payload: str) -> str:
    return hmac.new(secret_hash.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
