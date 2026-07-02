from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from backend.app.settings import get_settings


def normalize_master_key(raw: str) -> bytes:
    value = (raw or "").strip()
    if value:
        candidate = value.encode("utf-8")
        try:
            Fernet(candidate)
            return candidate
        except Exception:
            digest = hashlib.sha256(candidate).digest()
            return base64.urlsafe_b64encode(digest)

    digest = hashlib.sha256(b"APIMonitorBot development fallback key").digest()
    return base64.urlsafe_b64encode(digest)


class SecretBox:
    def __init__(self, master_key: str) -> None:
        self._fernet = Fernet(normalize_master_key(master_key))

    def encrypt(self, value: str) -> str:
        if value == "":
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        if token == "":
            return ""
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Encrypted secret cannot be decrypted with the current master key.") from exc


@lru_cache
def get_secret_box() -> SecretBox:
    return SecretBox(get_settings().secret_master_key)

