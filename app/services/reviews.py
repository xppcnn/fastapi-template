import hashlib
import json
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppError
from app.models.document import (
    DocType,
    Document,
    DocumentBlock,
    DocumentVersion,
    ParseStatus,
)
from app.models.project import Project
from app.models.review import ReviewRun, ReviewRunRule
from app.models.review_rule import ReviewRule, RuleStatus
from app.models.rule_extraction import RuleExtractionRun, RuleExtractionRunStatus
from app.schemas.review import ReviewCreateRequest
from app.services.retrieval import RETRIEVAL_VERSION, rank_evidence


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


async def _project(
    session: AsyncSession, organization_id: int, project_id: UUID, *, lock=False
) -> Project:
    stmt = select(Project).where(
        Project.public_id == project_id,
        Project.organization_id == organization_id,
        Project.is_deleted.is_(False),
    )
    if lock:
        stmt = stmt.with_for_update()
    project = await session.scalar(stmt)
    if project is None:
        raise AppError("项目不存在", code=404)
    return project


def _run_query():
    return select(ReviewRun).options(
        selectinload(ReviewRun.tender_version), selectinload(ReviewRun.bid_version)
    )


def _summary(run: ReviewRun) -> dict:
    return {
        "public_id": run.public_id,
        "status": run.status,
        "tender_version_id": run.tender_version.public_id,
        "bid_version_id": run.bid_version.public_id,
        "snapshot_hash": run.snapshot_hash,
        "rule_count": run.rule_count,
        "created_at": run.created_at,
    }


async def _version(
    session: AsyncSession, project: Project, public_id: UUID, kind: DocType
) -> DocumentVersion:
    pair = (
        await session.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(
                Document.project_id == project.id,
                Document.organization_id == project.organization_id,
                Document.doc_type == kind,
                Document.is_deleted.is_(False),
                DocumentVersion.public_id == public_id,
            )
            .with_for_update(of=Document)
        )
    ).first()
    if pair is None:
        raise AppError("文档版本不存在", code=404)
    document, version = pair
    if document.active_version_id != version.id:
        raise AppError("请选择当前活动文档版本", code=409)
    if version.parse_status != ParseStatus.PARSED:
        raise AppError("文档版本尚未解析完成", code=409)
    has_text = await session.scalar(
        select(DocumentBlock.id)
        .where(
            DocumentBlock.version_id == version.id,
            func.length(func.trim(DocumentBlock.text)) > 0,
        )
        .limit(1)
    )
    if has_text is None:
        raise AppError("文档没有可审核的文本", code=409)
    return version


async def create_review(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    project_id: UUID,
    payload: ReviewCreateRequest,
    idempotency_key: str | None,
) -> dict:
    project = await _project(session, organization_id, project_id, lock=True)
    input_hash = _hash(payload.model_dump(mode="json"))
    if idempotency_key is not None:
        existing = await session.scalar(
            _run_query()
            .where(
                ReviewRun.project_id == project.id,
                ReviewRun.organization_id == organization_id,
                ReviewRun.idempotency_key == idempotency_key,
            )
            .options(selectinload(ReviewRun.rules))
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise AppError("幂等键已用于其他审核输入", code=409)
            return await get_review(
                session,
                organization_id=organization_id,
                project_id=project_id,
                run_id=existing.public_id,
            )
    tender = await _version(session, project, payload.tender_version_id, DocType.TENDER)
    bid = await _version(session, project, payload.bid_version_id, DocType.BID)
    extracting = await session.scalar(
        select(RuleExtractionRun.id)
        .where(
            RuleExtractionRun.project_id == project.id,
            RuleExtractionRun.tender_version_id == tender.id,
            RuleExtractionRun.status.in_(
                [RuleExtractionRunStatus.QUEUED, RuleExtractionRunStatus.RUNNING]
            ),
        )
        .limit(1)
    )
    if extracting is not None:
        raise AppError("请等待规则提取完成", code=409)
    rules = list(
        await session.scalars(
            select(ReviewRule)
            .where(
                ReviewRule.organization_id == organization_id,
                ReviewRule.project_id == project.id,
                ReviewRule.tender_version_id == tender.id,
                ReviewRule.is_deleted.is_(False),
            )
            .order_by(ReviewRule.id)
            .with_for_update()
        )
    )
    if any(rule.status == RuleStatus.DRAFT for rule in rules):
        raise AppError("请先确认或忽略全部草稿规则", code=409)
    confirmed = [rule for rule in rules if rule.status == RuleStatus.CONFIRMED]
    if not confirmed:
        raise AppError("至少需要一条已确认规则", code=409)
    fields = (
        "rule_type",
        "title",
        "description",
        "evaluation_method",
        "condition",
        "scoring_method",
        "max_score",
        "evaluation_criterion",
        "source_segment_ids",
        "model_run_id",
        "prompt_version",
    )
    snapshots = [
        {
            **{field: getattr(rule, field) for field in fields},
            "source_rule_id": str(rule.public_id),
            "source_rule_version": rule.version,
        }
        for rule in confirmed
    ]
    run = ReviewRun(
        organization_id=organization_id,
        project_id=project.id,
        created_by_id=user_id,
        tender_version_id=tender.id,
        bid_version_id=bid.id,
        idempotency_key=idempotency_key,
        input_hash=input_hash,
        snapshot_hash=_hash(
            {"versions": payload.model_dump(mode="json"), "rules": snapshots}
        ),
        rule_count=len(snapshots),
    )
    session.add(run)
    await session.flush()
    for source, snapshot in zip(confirmed, snapshots, strict=True):
        session.add(
            ReviewRunRule(
                run_id=run.id,
                source_rule_id=source.id,
                source_rule_version=source.version,
                snapshot=snapshot,
            )
        )
    await session.flush()
    return await get_review(
        session,
        organization_id=organization_id,
        project_id=project_id,
        run_id=run.public_id,
    )


async def _get_run(
    session: AsyncSession, organization_id: int, project_id: UUID, run_id: UUID
) -> ReviewRun:
    project = await _project(session, organization_id, project_id)
    run = await session.scalar(
        _run_query()
        .where(
            ReviewRun.organization_id == organization_id,
            ReviewRun.project_id == project.id,
            ReviewRun.public_id == run_id,
        )
        .options(selectinload(ReviewRun.rules))
    )
    if run is None:
        raise AppError("审核运行不存在", code=404)
    available = await session.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.id.in_(
                [run.tender_version.document_id, run.bid_version.document_id]
            ),
            Document.organization_id == organization_id,
            Document.project_id == project.id,
            Document.is_deleted.is_(False),
        )
    )
    if available != 2:
        raise AppError("审核运行文档已删除", code=404)
    return run


async def get_review(
    session: AsyncSession, *, organization_id: int, project_id: UUID, run_id: UUID
) -> dict:
    run = await _get_run(session, organization_id, project_id, run_id)
    return {**_summary(run), "rules": run.rules}


async def list_reviews(
    session: AsyncSession,
    *,
    organization_id: int,
    project_id: UUID,
    page: int,
    page_size: int,
) -> dict:
    project = await _project(session, organization_id, project_id)
    stmt = _run_query().where(
        ReviewRun.organization_id == organization_id, ReviewRun.project_id == project.id
    )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    runs = list(
        await session.scalars(
            stmt.order_by(ReviewRun.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "items": [_summary(run) for run in runs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def find_evidence(
    session: AsyncSession,
    *,
    organization_id: int,
    project_id: UUID,
    run_id: UUID,
    rule_id: UUID,
    limit: int,
) -> dict:
    run = await _get_run(session, organization_id, project_id, run_id)
    rule = next((rule for rule in run.rules if rule.public_id == rule_id), None)
    if rule is None:
        raise AppError("审核运行规则不存在", code=404)
    # 固定本次投标版本，不读取活动版本，也不在组织范围以外检索。
    blocks = list(
        await session.scalars(
            select(DocumentBlock)
            .where(DocumentBlock.version_id == run.bid_version_id)
            .order_by(DocumentBlock.order_index)
        )
    )
    query = rule.snapshot["title"] + " " + rule.snapshot["description"]
    candidates = rank_evidence(query, blocks, limit=limit)
    return {
        "retrieval_version": RETRIEVAL_VERSION,
        "items": [
            {
                "segment_id": block.public_id,
                "document_version_id": run.bid_version.public_id,
                "page_no": block.page_no,
                "text": block.text,
                "score": score,
            }
            for block, score in candidates
        ],
    }
