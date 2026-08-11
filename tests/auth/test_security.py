from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_is_hashed_with_argon2_and_can_be_verified() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert password_hash.startswith("$argon2")
    assert password_hash != "correct horse battery staple"
    assert verify_password("correct horse battery staple", password_hash) is True
    assert verify_password("wrong password", password_hash) is False


def test_access_token_carries_user_and_organization_public_ids() -> None:
    user_public_id = UUID("0ea5d883-b1f8-4208-a5a6-d2809cc1d846")
    organization_public_id = UUID("62b39868-6171-45a3-b51e-b13df6398269")

    token = create_access_token(
        user_public_id=user_public_id,
        organization_public_id=organization_public_id,
    )
    claims = decode_token(token, expected_type="access")

    assert claims.token_type == "access"
    assert claims.user_public_id == user_public_id
    assert claims.organization_public_id == organization_public_id
    assert claims.token_id is None


def test_refresh_token_carries_user_and_unique_token_ids() -> None:
    user_public_id = UUID("0ea5d883-b1f8-4208-a5a6-d2809cc1d846")
    token_id = UUID("98355452-b75a-4f67-979f-d62750485eba")

    token = create_refresh_token(
        user_public_id=user_public_id,
        token_id=token_id,
    )
    claims = decode_token(token, expected_type="refresh")

    assert claims.token_type == "refresh"
    assert claims.user_public_id == user_public_id
    assert claims.organization_public_id is None
    assert claims.token_id == token_id


def test_refresh_token_without_token_id_is_rejected() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    malformed_token = jwt.encode(
        {
            "sub": "0ea5d883-b1f8-4208-a5a6-d2809cc1d846",
            "type": "refresh",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError, match="token ID"):
        decode_token(malformed_token, expected_type="refresh")


def test_expired_access_token_is_rejected() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    expired_token = jwt.encode(
        {
            "sub": "0ea5d883-b1f8-4208-a5a6-d2809cc1d846",
            "org": "62b39868-6171-45a3-b51e-b13df6398269",
            "type": "access",
            "iat": now - timedelta(minutes=10),
            "exp": now - timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError, match="Invalid token"):
        decode_token(expired_token, expected_type="access")


def test_token_type_cannot_be_used_at_another_authentication_seam() -> None:
    access_token = create_access_token(
        user_public_id=UUID("0ea5d883-b1f8-4208-a5a6-d2809cc1d846"),
        organization_public_id=UUID("62b39868-6171-45a3-b51e-b13df6398269"),
    )

    with pytest.raises(TokenError, match="token type"):
        decode_token(access_token, expected_type="refresh")
