"""Single-owner HTTP authentication for the private analyst console."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

ITERATIONS = 600_000


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 16:
        raise ValueError("owner password must contain at least 16 characters")
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), actual_salt, ITERATIONS)
    encoded_salt = base64.urlsafe_b64encode(actual_salt).decode()
    encoded_digest = base64.urlsafe_b64encode(digest).decode()
    return f"pbkdf2_sha256${ITERATIONS}${encoded_salt}${encoded_digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            base64.urlsafe_b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(base64.urlsafe_b64encode(actual).decode(), expected)
    except (ValueError, TypeError):
        return False


def configured_owner() -> tuple[str, str] | None:
    username = os.getenv("WNBA_OWNER_USERNAME", "").strip()
    password_hash = os.getenv("WNBA_OWNER_PASSWORD_HASH", "").strip()
    return (username, password_hash) if username and password_hash else None


def decode_basic_authorization(value: str | None) -> tuple[str, str] | None:
    if not value or not value.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(value[6:], validate=True).decode()
        username, password = decoded.split(":", 1)
        return username, password
    except (ValueError, UnicodeDecodeError):
        return None
