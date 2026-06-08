import pytest
from web.backend.auth.service import hash_password, verify_password, create_access_token, decode_access_token


def test_password_hash():
    hashed = hash_password("testpassword")
    assert verify_password("testpassword", hashed)
    assert not verify_password("wrongpassword", hashed)


def test_jwt_token():
    token = create_access_token("user-123")
    user_id = decode_access_token(token)
    assert user_id == "user-123"


def test_invalid_token():
    assert decode_access_token("invalid.token.here") is None
