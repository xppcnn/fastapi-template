from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

_password_hash = PasswordHash.recommended()


class TokenError(ValueError):
    pass


class TokenClaims(BaseModel):
    token_type: Literal["access", "refresh"]
    user_public_id: UUID
    organization_public_id: UUID | None = None
    token_id: UUID | None = None
    issued_at: datetime
    expires_at: datetime


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def create_access_token(
    *, user_public_id: UUID, organization_public_id: UUID
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_public_id),
            "org": str(organization_public_id),
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def create_refresh_token(*, user_public_id: UUID, token_id: UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_public_id),
            "jti": str(token_id),
            "type": "refresh",
            "iat": now,
            "exp": now + timedelta(days=settings.refresh_token_expire_days),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_token(
    token: str, expected_type: Literal["access", "refresh"]
) -> TokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "type", "iat", "exp"]},
        )
        claims = TokenClaims(
            token_type=payload["type"],
            user_public_id=payload["sub"],
            organization_public_id=payload.get("org"),
            token_id=payload.get("jti"),
            issued_at=payload["iat"],
            expires_at=payload["exp"],
        )
    except (InvalidTokenError, KeyError, TypeError, ValidationError, ValueError) as exc:
        raise TokenError("Invalid token") from exc

    if claims.token_type != expected_type:
        raise TokenError("Invalid token type")
    if expected_type == "access" and claims.organization_public_id is None:
        raise TokenError("Missing organization claim")
    if expected_type == "refresh" and claims.token_id is None:
        raise TokenError("Missing refresh token ID")
    return claims
