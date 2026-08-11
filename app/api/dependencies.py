from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.database import DbSession
from app.core.exceptions import AppError
from app.core.security import TokenError, decode_token
from app.models.identity import MembershipRole, OrganizationStatus
from app.repositories.users import get_identity_by_public_ids

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentPrincipal:
    user_id: int
    organization_id: int
    user_public_id: UUID
    organization_public_id: UUID
    role: MembershipRole
    email: str
    organization_name: str
    organization_status: OrganizationStatus


async def get_current_principal(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    session: DbSession,
) -> CurrentPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError("Invalid or expired access token", code=401)
    try:
        claims = decode_token(credentials.credentials, expected_type="access")
    except TokenError as exc:
        raise AppError("Invalid or expired access token", code=401) from exc

    organization_public_id = claims.organization_public_id
    if organization_public_id is None:
        raise AppError("Invalid or expired access token", code=401)
    identity = await get_identity_by_public_ids(
        session,
        user_public_id=claims.user_public_id,
        organization_public_id=organization_public_id,
    )
    if identity is None:
        raise AppError("Invalid or expired access token", code=401)
    return CurrentPrincipal(
        user_id=identity.user.id,
        organization_id=identity.organization.id,
        user_public_id=identity.user.public_id,
        organization_public_id=identity.organization.public_id,
        role=identity.membership.role,
        email=identity.user.email,
        organization_name=identity.organization.name,
        organization_status=identity.organization.status,
    )


CurrentPrincipalDep = Annotated[CurrentPrincipal, Depends(get_current_principal)]
