from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Membership, Organization, OrganizationStatus, User


@dataclass(frozen=True)
class IdentityContext:
    user: User
    organization: Organization
    membership: Membership


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_user_by_email(session: AsyncSession, *, email: str) -> User | None:
    result = await session.execute(
        select(User).where(User.email == normalize_email(email))
    )
    return result.scalar_one_or_none()


async def create_user_identity(
    session: AsyncSession,
    *,
    user: User,
    organization: Organization,
    membership: Membership,
) -> None:
    session.add_all([user, organization, membership])
    await session.flush()


async def get_identity_by_email(
    session: AsyncSession, *, email: str
) -> IdentityContext | None:
    result = await session.execute(
        select(User, Organization, Membership)
        .join(Membership, Membership.user_id == User.id)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(
            User.email == normalize_email(email),
            User.is_active.is_(True),
            User.is_deleted == false(),
            Organization.status == OrganizationStatus.ACTIVE,
            Organization.is_deleted == false(),
            Membership.is_deleted == false(),
        )
        .order_by(Membership.id)
        .limit(1)
    )
    row = result.one_or_none()
    if row is None:
        return None
    user, organization, membership = row
    return IdentityContext(
        user=user,
        organization=organization,
        membership=membership,
    )


async def get_identity_by_user_public_id(
    session: AsyncSession, *, user_public_id: UUID
) -> IdentityContext | None:
    result = await session.execute(
        select(User, Organization, Membership)
        .join(Membership, Membership.user_id == User.id)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(
            User.public_id == user_public_id,
            User.is_active.is_(True),
            User.is_deleted == false(),
            Organization.status == OrganizationStatus.ACTIVE,
            Organization.is_deleted == false(),
            Membership.is_deleted == false(),
        )
        .order_by(Membership.id)
        .limit(1)
    )
    row = result.one_or_none()
    if row is None:
        return None
    user, organization, membership = row
    return IdentityContext(
        user=user,
        organization=organization,
        membership=membership,
    )


async def get_identity_by_public_ids(
    session: AsyncSession,
    *,
    user_public_id: UUID,
    organization_public_id: UUID,
) -> IdentityContext | None:
    result = await session.execute(
        select(User, Organization, Membership)
        .join(Membership, Membership.user_id == User.id)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(
            User.public_id == user_public_id,
            Organization.public_id == organization_public_id,
            User.is_active.is_(True),
            User.is_deleted == false(),
            Organization.status == OrganizationStatus.ACTIVE,
            Organization.is_deleted == false(),
            Membership.is_deleted == false(),
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    user, organization, membership = row
    return IdentityContext(
        user=user,
        organization=organization,
        membership=membership,
    )
