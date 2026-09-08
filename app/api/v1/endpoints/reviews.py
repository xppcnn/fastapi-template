from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query

from app.api.dependencies import CurrentPrincipalDep
from app.core.database import DbSession
from app.core.response import ApiResponse, ok
from app.schemas.review import (
    EvidenceCandidatesResponse,
    ReviewCreateRequest,
    ReviewDetail,
    ReviewListResponse,
)
from app.services import reviews

router = APIRouter(prefix="/projects/{project_id}/reviews")


@router.post("", response_model=ApiResponse[ReviewDetail], status_code=201)
async def create(
    project_id: UUID,
    payload: ReviewCreateRequest,
    principal: CurrentPrincipalDep,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=64)] = None,
) -> dict:
    return ok(
        await reviews.create_review(
            session,
            organization_id=principal.organization_id,
            user_id=principal.user_id,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    )


@router.get("", response_model=ApiResponse[ReviewListResponse])
async def list_runs(
    project_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    return ok(
        await reviews.list_reviews(
            session,
            organization_id=principal.organization_id,
            project_id=project_id,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/{run_id}", response_model=ApiResponse[ReviewDetail])
async def detail(
    project_id: UUID, run_id: UUID, principal: CurrentPrincipalDep, session: DbSession
) -> dict:
    return ok(
        await reviews.get_review(
            session,
            organization_id=principal.organization_id,
            project_id=project_id,
            run_id=run_id,
        )
    )


@router.get(
    "/{run_id}/rules/{rule_id}/evidence",
    response_model=ApiResponse[EvidenceCandidatesResponse],
)
async def evidence(
    project_id: UUID,
    run_id: UUID,
    rule_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    return ok(
        await reviews.find_evidence(
            session,
            organization_id=principal.organization_id,
            project_id=project_id,
            run_id=run_id,
            rule_id=rule_id,
            limit=limit,
        )
    )
