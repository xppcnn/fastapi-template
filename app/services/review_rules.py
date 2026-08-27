from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.document import (
    DocType,
    Document,
    DocumentBlock,
    DocumentVersion,
    ParseStatus,
)
from app.models.review_rule import ReviewRule, RuleStatus, RuleType
from app.models.rule_extraction import (
    RuleExtractionRun,
    RuleExtractionRunStatus,
)
from app.repositories.projects import get_by_public_id
from app.schemas.llm import ExtractedRule
from app.schemas.review_rule import (
    ConfirmResponse,
    RuleConfirmRequest,
    RuleCreateRequest,
    RuleListQuery,
    RuleUpdateRequest,
)

_TERMINAL_RUN_STATUSES = (
    RuleExtractionRunStatus.FAILED,
    RuleExtractionRunStatus.PARTIAL,
)

_RUNNING_RUN_STATUSES = (
    RuleExtractionRunStatus.QUEUED,
    RuleExtractionRunStatus.RUNNING,
)


def normalize_rule_key(rule_type: RuleType, criterion: str) -> str:
    """规范化去重 key:类型 + 判定口径(仅压缩空白并小写,不改变语义)。

    跨段重复规则由程序端按此 key 去重,不交给 LLM 合并。
    """
    text = " ".join(str(criterion or "").strip().lower().split())
    return f"{rule_type.value}:{text}"


def rule_dedup_key(rule: Any) -> str:
    """规则判定口径:评分项优先取扣分判定口径(evaluation_criterion),其余取 description。

    接受 ExtractedRule / ReviewRule / 创建与更新请求等带 rule_type 的对象。
    """
    criterion = (
        getattr(rule, "evaluation_criterion", None)
        or getattr(rule, "description", None)
        or getattr(rule, "title", "")
    )
    return normalize_rule_key(rule.rule_type, criterion or "")


@dataclass(slots=True)
class MergedExtraction:
    rule: ExtractedRule
    source_uuids: set[UUID]
    needs_review: bool


def merge_extracted_rules(extracted: list[ExtractedRule]) -> list[MergedExtraction]:
    """合并铁律:

    1. 只合并已出现的信息,绝不新增分段结果中没有的信息;
    2. 跨段重复规则按规范化 key 去重,来源片段 UUID 取并集;
    3. 任一片段将其标为越界(explicitly_stated=False)则合并结果需人工复核。
    """
    merged: dict[str, MergedExtraction] = {}
    for spec in extracted:
        key = rule_dedup_key(spec)
        if key in merged:
            target = merged[key]
            target.source_uuids |= set(spec.source_segment_ids)
            target.needs_review = target.needs_review or not spec.explicitly_stated
        else:
            merged[key] = MergedExtraction(
                rule=spec,
                source_uuids=set(spec.source_segment_ids),
                needs_review=not spec.explicitly_stated,
            )
    return list(merged.values())


async def _require_project(
    session: AsyncSession, *, organization_id: int, project_public_id: UUID
):
    project = await get_by_public_id(
        session, organization_id=organization_id, public_id=project_public_id
    )
    if project is None:
        raise AppError("项目不存在", code=404)
    return project


async def _require_rule(
    session: AsyncSession,
    *,
    project_id: int,
    organization_id: int,
    rule_public_id: UUID,
) -> ReviewRule:
    rule = await session.scalar(
        select(ReviewRule).where(
            ReviewRule.project_id == project_id,
            ReviewRule.organization_id == organization_id,
            ReviewRule.public_id == rule_public_id,
        )
    )
    if rule is None:
        raise AppError("规则不存在", code=404)
    return rule


async def _tender_version(
    session: AsyncSession, *, project_id: int, version_public_id: UUID
) -> DocumentVersion:
    version = await session.scalar(
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.project_id == project_id,
            Document.doc_type == DocType.TENDER,
            Document.is_deleted.is_(False),
            DocumentVersion.public_id == version_public_id,
        )
    )
    if version is None:
        raise AppError("招标版本不存在", code=404)
    if version.parse_status != ParseStatus.PARSED:
        raise AppError("招标版本尚未解析完成", code=400)
    return version


async def list_rules(
    session: AsyncSession,
    *,
    organization_id: int,
    project_public_id: UUID,
    query: RuleListQuery,
) -> dict:
    project = await _require_project(
        session, organization_id=organization_id, project_public_id=project_public_id
    )
    stmt = select(ReviewRule).where(
        ReviewRule.project_id == project.id,
        ReviewRule.organization_id == organization_id,
    )
    if query.rule_type is not None:
        stmt = stmt.where(ReviewRule.rule_type == query.rule_type)
    if query.status is not None:
        stmt = stmt.where(ReviewRule.status == query.status)
    stmt = stmt.order_by(ReviewRule.id.desc())

    total = await session.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    rows = list(
        (
            await session.execute(
                stmt.offset((query.page - 1) * query.page_size).limit(query.page_size)
            )
        ).scalars()
    )
    return {
        "items": rows,
        "total": total or 0,
        "page": query.page,
        "page_size": query.page_size,
    }


async def create_manual_rule(
    session: AsyncSession,
    *,
    organization_id: int,
    project_public_id: UUID,
    payload: RuleCreateRequest,
    created_by_id: int,
) -> ReviewRule:
    """人工补充规则:无 ModelRun 关联,归属当前活动招标版本。"""
    project = await _require_project(
        session, organization_id=organization_id, project_public_id=project_public_id
    )
    tender_doc = await session.scalar(
        select(Document).where(
            Document.project_id == project.id,
            Document.doc_type == DocType.TENDER,
            Document.is_deleted.is_(False),
        )
    )
    version_id = tender_doc.active_version_id if tender_doc else None
    if version_id is None:
        raise AppError("项目尚未激活招标版本", code=400)
    version = await session.get(DocumentVersion, version_id)
    if version is None or version.parse_status != ParseStatus.PARSED:
        raise AppError("招标版本尚未解析完成", code=400)

    rule = ReviewRule(
        organization_id=organization_id,
        project_id=project.id,
        tender_version_id=version.id,
        rule_type=payload.rule_type,
        title=payload.title,
        description=payload.description,
        evaluation_method=payload.evaluation_method,
        condition=payload.condition.model_dump(mode="json")
        if payload.condition
        else None,
        scoring_method=payload.scoring_method,
        max_score=payload.max_score,
        evaluation_criterion=payload.evaluation_criterion,
        status=RuleStatus.DRAFT,
        needs_review=False,
        source_segment_ids=[],
        dedup_key=rule_dedup_key(payload),
        extraction_run_id=None,
        created_by_id=created_by_id,
    )
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    return rule


async def update_rule(
    session: AsyncSession,
    *,
    organization_id: int,
    project_public_id: UUID,
    rule_public_id: UUID,
    payload: RuleUpdateRequest,
) -> ReviewRule:
    project = await _require_project(
        session, organization_id=organization_id, project_public_id=project_public_id
    )
    rule = await _require_rule(
        session,
        project_id=project.id,
        organization_id=organization_id,
        rule_public_id=rule_public_id,
    )
    if rule.status != RuleStatus.DRAFT:
        raise AppError("只有草稿规则可以修改", code=409)
    if rule.version != payload.version:
        raise AppError("版本冲突,请刷新后重试", code=409)
    for field, value in payload.model_dump(exclude={"version"}).items():
        if value is None:
            continue
        if field == "condition":
            rule.condition = value.model_dump(mode="json")
        else:
            setattr(rule, field, value)
    rule.dedup_key = rule_dedup_key(rule)
    rule.version += 1
    await session.flush()
    await session.refresh(rule)
    return rule


async def ignore_rule(
    session: AsyncSession,
    *,
    organization_id: int,
    project_public_id: UUID,
    rule_public_id: UUID,
) -> ReviewRule:
    project = await _require_project(
        session, organization_id=organization_id, project_public_id=project_public_id
    )
    rule = await _require_rule(
        session,
        project_id=project.id,
        organization_id=organization_id,
        rule_public_id=rule_public_id,
    )
    if rule.status != RuleStatus.DRAFT:
        raise AppError("只有草稿规则可以忽略", code=409)
    rule.status = RuleStatus.IGNORED
    rule.version += 1
    await session.flush()
    await session.refresh(rule)
    return rule


async def confirm_rules(
    session: AsyncSession,
    *,
    organization_id: int,
    project_public_id: UUID,
    payload: RuleConfirmRequest,
) -> ConfirmResponse:
    """最后的批量确认接口:只把选定(或全部)draft 落为 confirmed。"""
    project = await _require_project(
        session, organization_id=organization_id, project_public_id=project_public_id
    )
    stmt = select(ReviewRule).where(
        ReviewRule.project_id == project.id,
        ReviewRule.organization_id == organization_id,
        ReviewRule.status == RuleStatus.DRAFT,
    )
    if payload.rule_ids:
        stmt = stmt.where(ReviewRule.public_id.in_(payload.rule_ids))
    rows = list((await session.execute(stmt)).scalars())
    if payload.rule_ids and len(rows) != len({str(i) for i in payload.rule_ids}):
        raise AppError("部分规则不存在或已确认", code=409)
    for row in rows:
        row.status = RuleStatus.CONFIRMED
        row.version += 1
    await session.flush()
    for row in rows:
        await session.refresh(row)
    pending = await session.scalar(
        select(func.count())
        .select_from(ReviewRule)
        .where(
            ReviewRule.project_id == project.id,
            ReviewRule.organization_id == organization_id,
            ReviewRule.status == RuleStatus.DRAFT,
        )
    )
    return ConfirmResponse(confirmed=len(rows), pending=pending or 0)


async def get_extraction_run(
    session: AsyncSession,
    *,
    organization_id: int,
    project_public_id: UUID,
    run_public_id: UUID,
) -> RuleExtractionRun:
    project = await _require_project(
        session, organization_id=organization_id, project_public_id=project_public_id
    )
    run = await session.scalar(
        select(RuleExtractionRun).where(
            RuleExtractionRun.project_id == project.id,
            RuleExtractionRun.organization_id == organization_id,
            RuleExtractionRun.public_id == run_public_id,
        )
    )
    if run is None:
        raise AppError("提取任务不存在", code=404)
    return run


async def start_rule_extraction(
    session: AsyncSession,
    *,
    organization_id: int,
    project_public_id: UUID,
    tender_version_public_id: UUID,
) -> RuleExtractionRun:
    """原子认领:创建 QUEUED 运行(同一版本在跑的运行存在则 409),提交后入队。"""
    project = await _require_project(
        session, organization_id=organization_id, project_public_id=project_public_id
    )
    version = await _tender_version(
        session, project_id=project.id, version_public_id=tender_version_public_id
    )
    running = await session.scalar(
        select(RuleExtractionRun).where(
            RuleExtractionRun.project_id == project.id,
            RuleExtractionRun.tender_version_id == version.id,
            RuleExtractionRun.status.in_(_RUNNING_RUN_STATUSES),
        )
    )
    if running is not None:
        raise AppError("该招标版本正在提取规则", code=409)

    from app.workflows.extract_rules import group_into_batches

    blocks = list(
        (
            await session.execute(
                select(DocumentBlock)
                .where(DocumentBlock.version_id == version.id)
                .order_by(DocumentBlock.order_index)
            )
        ).scalars()
    )
    total_batches = max(1, len(group_into_batches(blocks)))

    run = RuleExtractionRun(
        organization_id=organization_id,
        project_id=project.id,
        tender_version_id=version.id,
        status=RuleExtractionRunStatus.QUEUED,
        total_batches=total_batches,
        completed_batches=0,
        failed_batches=0,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    from app.tasks.rule_extraction import rule_extraction_submit

    rule_extraction_submit.delay(run.id)
    return run


async def retry_extraction_run(
    session: AsyncSession,
    *,
    organization_id: int,
    project_public_id: UUID,
    run_public_id: UUID,
) -> RuleExtractionRun:
    """失败批次单独重跑:仅重入失败的运行,成功批次在 worker 侧被跳过。"""
    run = await get_extraction_run(
        session,
        organization_id=organization_id,
        project_public_id=project_public_id,
        run_public_id=run_public_id,
    )
    if run.status not in _TERMINAL_RUN_STATUSES:
        raise AppError("当前状态不允许重试提取", code=409)
    run.status = RuleExtractionRunStatus.QUEUED
    run.finished_at = None
    await session.flush()
    await session.commit()

    from app.tasks.rule_extraction import rule_extraction_submit

    rule_extraction_submit.delay(run.id)
    return run
