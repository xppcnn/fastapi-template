from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Query, status

from app.api.dependencies import CurrentPrincipalDep
from app.core.database import DbSession
from app.core.response import ApiResponse, ok
from app.schemas.review_rule import (
    ConfirmResponse,
    ReviewRuleResponse,
    RuleConfirmRequest,
    RuleCreateRequest,
    RuleExtractionRunResponse,
    RuleExtractRequest,
    RuleListQuery,
    RuleListResponse,
    RuleUpdateRequest,
)
from app.services.review_rules import (
    confirm_rules,
    create_manual_rule,
    get_extraction_run,
    ignore_rule,
    list_rules,
    retry_extraction_run,
    start_rule_extraction,
    update_rule,
)

router = APIRouter(prefix="/projects")
logger = structlog.get_logger(__name__)


@router.post(
    "/{project_id}/rules/extract",
    response_model=ApiResponse[RuleExtractionRunResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def extract_rules(
    project_id: UUID,
    payload: RuleExtractRequest,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    run = await start_rule_extraction(
        session,
        organization_id=principal.organization_id,
        project_public_id=project_id,
        tender_version_public_id=payload.tender_version_id,
    )
    return ok(RuleExtractionRunResponse.model_validate(run))


@router.get(
    "/{project_id}/rules/extract/{run_id}",
    response_model=ApiResponse[RuleExtractionRunResponse],
)
async def extraction_run_progress(
    project_id: UUID,
    run_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    run = await get_extraction_run(
        session,
        organization_id=principal.organization_id,
        project_public_id=project_id,
        run_public_id=run_id,
    )
    return ok(RuleExtractionRunResponse.model_validate(run))


@router.post(
    "/{project_id}/rules/extract/{run_id}/retry",
    response_model=ApiResponse[RuleExtractionRunResponse],
)
async def retry_extraction(
    project_id: UUID,
    run_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    run = await retry_extraction_run(
        session,
        organization_id=principal.organization_id,
        project_public_id=project_id,
        run_public_id=run_id,
    )
    return ok(RuleExtractionRunResponse.model_validate(run))


@router.get("/{project_id}/rules", response_model=ApiResponse[RuleListResponse])
async def rule_list(
    project_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
    query: Annotated[RuleListQuery, Query()],
) -> dict:
    result = await list_rules(
        session,
        organization_id=principal.organization_id,
        project_public_id=project_id,
        query=query,
    )
    return ok(
        RuleListResponse(
            items=[ReviewRuleResponse.model_validate(item) for item in result["items"]],
            total=result["total"],
            page=result["page"],
            page_size=result["page_size"],
        )
    )


@router.post(
    "/{project_id}/rules",
    response_model=ApiResponse[ReviewRuleResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_rule(
    project_id: UUID,
    payload: RuleCreateRequest,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    rule = await create_manual_rule(
        session,
        organization_id=principal.organization_id,
        project_public_id=project_id,
        payload=payload,
        created_by_id=principal.user_id,
    )
    return ok(ReviewRuleResponse.model_validate(rule))


@router.patch(
    "/{project_id}/rules/{rule_id}",
    response_model=ApiResponse[ReviewRuleResponse],
)
async def patch_rule(
    project_id: UUID,
    rule_id: UUID,
    payload: RuleUpdateRequest,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    rule = await update_rule(
        session,
        organization_id=principal.organization_id,
        project_public_id=project_id,
        rule_public_id=rule_id,
        payload=payload,
    )
    return ok(ReviewRuleResponse.model_validate(rule))


@router.post(
    "/{project_id}/rules/confirm",
    response_model=ApiResponse[ConfirmResponse],
)
async def confirm_rules_endpoint(
    project_id: UUID,
    payload: RuleConfirmRequest,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    result = await confirm_rules(
        session,
        organization_id=principal.organization_id,
        project_public_id=project_id,
        payload=payload,
    )
    return ok(result)


@router.post(
    "/{project_id}/rules/{rule_id}/ignore",
    response_model=ApiResponse[ReviewRuleResponse],
)
async def ignore_rule_endpoint(
    project_id: UUID,
    rule_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
) -> dict:
    rule = await ignore_rule(
        session,
        organization_id=principal.organization_id,
        project_public_id=project_id,
        rule_public_id=rule_id,
    )
    return ok(ReviewRuleResponse.model_validate(rule))
