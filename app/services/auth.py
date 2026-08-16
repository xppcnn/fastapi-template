from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.identity import (
    Membership,
    MembershipRole,
    Organization,
    User,
)
from app.repositories.users import (
    IdentityContext,
    create_user_identity,
    get_identity_by_email,
    get_identity_by_user_public_id,
    get_user_by_email,
    normalize_email,
)
from app.schemas.auth import LoginRequest, RegisterRequest


@dataclass(frozen=True)
class AuthResult:
    user: User
    organization: Organization
    membership: Membership
    access_token: str
    refresh_token: str


def _issue_tokens(identity: IdentityContext) -> AuthResult:
    return AuthResult(
        user=identity.user,
        organization=identity.organization,
        membership=identity.membership,
        access_token=create_access_token(
            user_public_id=identity.user.public_id,
            organization_public_id=identity.organization.public_id,
        ),
        refresh_token=create_refresh_token(
            user_public_id=identity.user.public_id,
            token_id=uuid4(),
        ),
    )


async def register_user(
    session: AsyncSession, *, payload: RegisterRequest
) -> AuthResult:
    try:
        async with session.begin():
            email = normalize_email(str(payload.email))
            if await get_user_by_email(session, email=email) is not None:
                raise AppError("Email already registered", code=409)

            user = User(
                email=email,
                password_hash=hash_password(payload.password),
                is_active=True,
            )
            organization = Organization(
                name=payload.organization_name or f"{email} Organization"
            )
            membership = Membership(
                user=user,
                organization=organization,
                role=MembershipRole.OWNER,
            )
            await create_user_identity(
                session,
                user=user,
                organization=organization,
                membership=membership,
            )

            return AuthResult(
                user=user,
                organization=organization,
                membership=membership,
                access_token=create_access_token(
                    user_public_id=user.public_id,
                    organization_public_id=organization.public_id,
                ),
                refresh_token=create_refresh_token(
                    user_public_id=user.public_id,
                    token_id=uuid4(),
                ),
            )
    except IntegrityError as exc:
        raise AppError("Email already registered", code=409) from exc


async def login_user(session: AsyncSession, *, payload: LoginRequest) -> AuthResult:
    identity = await get_identity_by_email(session, email=str(payload.email))
    if identity is None or not verify_password(
        payload.password, identity.user.password_hash
    ):
        raise AppError("Invalid email or password", code=401)
    return _issue_tokens(identity)


async def refresh_session(session: AsyncSession, *, refresh_token: str) -> AuthResult:
    try:
        claims = decode_token(refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise AppError("Invalid or expired refresh token", code=401) from exc

    identity = await get_identity_by_user_public_id(
        session, user_public_id=claims.user_public_id
    )
    if identity is None:
        raise AppError("Invalid or expired refresh token", code=401)
    return _issue_tokens(identity)
