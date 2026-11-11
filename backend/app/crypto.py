"""Encryption at rest for secrets users give us (their own Gemini API keys)."""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

log = logging.getLogger("promptcode.crypto")


def _fernet() -> Fernet:
    secret = settings.key_encryption_secret or f"promptcode-user-keys:{settings.jwt_secret}"
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str | None:
    """None if the token can't be decrypted (e.g. the encryption secret changed)."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        log.warning("Could not decrypt a stored API key (was KEY_ENCRYPTION_SECRET/JWT_SECRET changed?)")
        return None
