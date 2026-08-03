import base64

import pytest
from wnba_apps.api.auth import decode_basic_authorization, hash_password, verify_password


def test_owner_password_hash_round_trip() -> None:
    encoded = hash_password("a-long-private-password", salt=b"0123456789abcdef")
    assert verify_password("a-long-private-password", encoded)
    assert not verify_password("wrong-private-password", encoded)


def test_short_owner_password_is_rejected() -> None:
    with pytest.raises(ValueError, match="16 characters"):
        hash_password("too-short")


def test_basic_authorization_parsing() -> None:
    token = base64.b64encode(b"owner:private-password").decode()
    assert decode_basic_authorization(f"Basic {token}") == ("owner", "private-password")
    assert decode_basic_authorization("Bearer nope") is None
