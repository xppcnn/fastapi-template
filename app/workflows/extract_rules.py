from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import sync_session_factory
from app.core.object_storage import utcnow_naive
from app.integrations.llm.base import (
    LLMClient,
    LLMError,
    LLMResult,
    OutputValidationError,
)
from app.integrations.llm.openai_compatible import OpenAICompatibleClient
from app.models.document import DocumentBlock
from app.models.review_rule import ReviewRule, RuleStatus, RuleType
from app.models.rule_extraction import (
    RuleExtractBatch,
    RuleExtractBatchStatus,
    RuleExtractionRun,
    RuleExtractionRunStatus,
)
from app.prompts.common import PROMPT_VERSION
from app.prompts.rule_extraction import SegmentRef, build_extract_messages
from app.schemas.llm import BatchExtractionResult, ExtractedRule
from app.services.model_runs import (
    latest_successful_run,
    record_attempt_failure,
    record_attempt_start,
    record_attempt_success,
)
from app.services.review_rules import (
    MergedExtraction,
    merge_extracted_rules,
    rule_dedup_key,
)

ALL_RULE_TYPES = [
    RuleType.DISQUALIFICATION,
    RuleType.QUALIFICATION,
    RuleType.RESPONSE,
    RuleType.SCORING,
]

_LLM_MAX_ATTEMPTS = 3
_DEFAULT_CONCURRENCY = 4


def group_into_batches(blocks: list[DocumentBlock]) -> list[list[DocumentBlock]]:
    """按章节分批:section_header 块开始新一批;无章节标题时整份为一批。"""
    batches: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    for block in blocks:
        if block.block_type == "section_header" and current:
            batches.append(current)
            current = [block]
        else:
            current.append(block)
    if current:
        batches.append(current)
    return batches


def _chapter_name(blocks: list[DocumentBlock], index: int) -> str:
    if blocks and blocks[0].block_type == "section_header":
        return blocks[0].text.strip()[:80] or f"第{index + 1}章"
    return f"第{index + 1}段"


def _join_blocks(blocks: list[DocumentBlock]) -> str:
    return "\n".join(b.text for b in blocks)


def get_llm_client() -> LLMClient:
    settings = get_settings()
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.llm_api_key.get_secret_value() or "missing",
        base_url=settings.llm_base_url,
        timeout=120.0,
    )
    return OpenAICompatibleClient(
        client=client,
        model=settings.llm_model,
        context_length_limit=settings.llm_context_length_limit,
    )


@dataclass(slots=True)
class _RunCtx:
    run_id: int
    run_public_id: UUID
    organization_id: int
    project_id: int
    tender_version_id: int
    prompt_version: str


async def _call_with_retry(
    llm: LLMClient,
    ctx: _RunCtx,
    *,
    operation_id: str,
    operation_name: str,
    messages,
    output_schema,
) -> LLMResult:
    """外部重试链:同一 operation_id 按 attempt 递增记录 ModelRun,不收敛则批次失败。"""
    last_error = ""
    for _ in range(1, _LLM_MAX_ATTEMPTS + 1):
        run = record_attempt_start(
            organization_id=ctx.organization_id,
            project_id=ctx.project_id,
            operation_id=operation_id,
            attempt=None,  # 自动接续该 operation 的重试链编号
            operation_name=operation_name,
            model=llm.model,
            prompt_version=ctx.prompt_version,
            messages=messages,
        )
        try:
            result = await llm.structured(
                operation=operation_id, messages=messages, output_schema=output_schema
            )
        except (OutputValidationError, LLMError) as exc:
            last_error = str(exc)
            record_attempt_failure(run, error=last_error)
            continue
        record_attempt_success(run, result=result)
        return result
    raise LLMError(f"retries exhausted for {operation_id}: {last_error}")


async def _extract_batch(
    llm: LLMClient,
    ctx: _RunCtx,
    *,
    batch_index: int,
    blocks: list[DocumentBlock],
    tender_text: str,
) -> list[tuple[ExtractedRule, str]]:
    """单批 = 单章,内部按四类规则各调用一次(独立 prompt 与 ModelRun attempt 链)。"""
    chapter = _chapter_name(blocks, batch_index)
    refs = [
        SegmentRef(uuid=b.public_id, page_no=b.page_no, order_index=b.order_index)
        for b in blocks
    ]
    extracted: list[tuple[ExtractedRule, str]] = []
    for rule_type in ALL_RULE_TYPES:
        messages = build_extract_messages(
            rule_type=rule_type,
            tender_text=tender_text,
            batch_refs=refs,
            chapter=chapter,
        )
        operation_id = f"rule-extract:{ctx.run_id}:{batch_index}:{rule_type.value}"
        result = await _call_with_retry(
            llm,
            ctx,
            operation_id=operation_id,
            operation_name=f"rule-extract:{rule_type.value}",
            messages=messages,
            output_schema=BatchExtractionResult,
        )
        if result.data.mentioned:
            extracted.extend((rule, operation_id) for rule in result.data.rules)
    return extracted


async def _run_batches(
    llm: LLMClient,
    ctx: _RunCtx,
    *,
    batches: list[list[DocumentBlock]],
    pending_indices: list[int],
    tender_text: str,
) -> dict[int, list[tuple[ExtractedRule, str]] | Exception]:
    """批次并发的 LLM 相位:只做模型调用,持久化回到同步侧逐批落库。"""
    sem = asyncio.Semaphore(_DEFAULT_CONCURRENCY)

    async def one(idx: int) -> tuple[int, list[tuple[ExtractedRule, str]] | Exception]:
        async with sem:
            try:
                return idx, await _extract_batch(
                    llm,
                    ctx,
                    batch_index=idx,
                    blocks=batches[idx],
                    tender_text=tender_text,
                )
            except Exception as exc:  # noqa: BLE001
                return idx, exc

    outcomes = await asyncio.gather(*(one(i) for i in pending_indices))
    return dict(outcomes)


def _valid_segment_uuids(session, *, version_id: int, uuids: set[UUID]) -> set[UUID]:
    if not uuids:
        return set()
    rows = session.scalars(
        select(DocumentBlock.public_id).where(
            DocumentBlock.version_id == version_id,
            DocumentBlock.public_id.in_(uuids),
        )
    )
    return set(rows)


def _persist_batch(
    *,
    ctx: _RunCtx,
    batch_index: int,
    extracted: list[tuple[ExtractedRule, str]] | Exception | None,
) -> None:
    """单批事务:插入 draft 规则 + 标记批次成功(或失败),同事务提交保证 checkpoint 原子。"""
    with sync_session_factory() as session:
        batch_row = session.scalar(
            select(RuleExtractBatch).where(
                RuleExtractBatch.run_id == ctx.run_id,
                RuleExtractBatch.order_index == batch_index,
            )
        )
        if batch_row is None:
            return
        if batch_row.status == RuleExtractBatchStatus.SUCCEEDED:
            return  # 幂等:该批已成功

        if isinstance(extracted, BaseException) or extracted is None:
            batch_row.status = RuleExtractBatchStatus.FAILED
            batch_row.error_message = (str(extracted) if extracted else "未知错误")[
                :2000
            ]
            session.commit()
            return

        # 按 operation(类型调用)分别合并:跨段重复由程序端去重(铁律三)
        by_op: dict[str, list[ExtractedRule]] = {}
        for rule, operation_id in extracted:
            by_op.setdefault(operation_id, []).append(rule)

        draft_specs: list[tuple[MergedExtraction, int | None]] = []
        for operation_id, rules in by_op.items():
            model_run = latest_successful_run(operation_id=operation_id)
            model_run_id = model_run.id if model_run else None
            for merged in merge_extracted_rules(rules):
                draft_specs.append((merged, model_run_id))

        all_uuids = {u for rule, _op in extracted for u in rule.source_segment_ids}
        valid = _valid_segment_uuids(
            session, version_id=ctx.tender_version_id, uuids=all_uuids
        )

        for merged, model_run_id in draft_specs:
            valid_sources = valid & merged.source_uuids
            needs_review = merged.needs_review or not valid_sources
            session.add(
                ReviewRule(
                    organization_id=ctx.organization_id,
                    project_id=ctx.project_id,
                    tender_version_id=ctx.tender_version_id,
                    rule_type=merged.rule.rule_type,
                    title=merged.rule.title,
                    description=merged.rule.description,
                    evaluation_method=merged.rule.evaluation_method,
                    condition=(
                        merged.rule.condition.model_dump(mode="json")
                        if merged.rule.condition is not None
                        else None
                    ),
                    scoring_method=merged.rule.scoring_method,
                    max_score=merged.rule.max_score,
                    evaluation_criterion=merged.rule.evaluation_criterion,
                    status=RuleStatus.DRAFT,
                    needs_review=needs_review,
                    source_segment_ids=[str(u) for u in sorted(valid_sources, key=str)],
                    dedup_key=rule_dedup_key(merged.rule),
                    model_run_id=model_run_id,
                    extraction_run_id=ctx.run_id,
                    prompt_version=ctx.prompt_version,
                )
            )

        batch_row.status = RuleExtractBatchStatus.SUCCEEDED
        session.commit()


def _finalize_run(*, run_id: int, total_batches: int) -> None:
    """终态落库:批次计数以批次行状态为准(幂等),成功批次最后做跨批去重。"""
    with sync_session_factory() as session:
        run = session.get(RuleExtractionRun, run_id)
        if run is None:
            return
        batch_rows = list(
            session.scalars(
                select(RuleExtractBatch).where(RuleExtractBatch.run_id == run_id)
            )
        )
        run.total_batches = total_batches
        run.completed_batches = sum(
            1 for row in batch_rows if row.status == RuleExtractBatchStatus.SUCCEEDED
        )
        run.failed_batches = sum(
            1 for row in batch_rows if row.status == RuleExtractBatchStatus.FAILED
        )
        run.finished_at = utcnow_naive()
        if run.failed_batches == 0 and run.completed_batches == total_batches:
            run.status = RuleExtractionRunStatus.SUCCEEDED
        elif run.completed_batches == 0:
            run.status = RuleExtractionRunStatus.FAILED
        else:
            run.status = RuleExtractionRunStatus.PARTIAL
        _dedupe_drafts(session, run_id=run_id)
        session.commit()


def _dedupe_drafts(session, *, run_id: int) -> None:
    """本次运行产生的 draft 按 dedup_key 去重:保留最小 id,来源与越界标记取并集。"""
    rows = list(
        session.scalars(
            select(ReviewRule)
            .where(
                ReviewRule.extraction_run_id == run_id,
                ReviewRule.status == RuleStatus.DRAFT,
            )
            .order_by(ReviewRule.id)
        )
    )
    for key, group in _group_by_dedup_key(rows):
        if len(group) <= 1:
            continue
        keeper, *dupes = group
        sources: set[str] = set(keeper.source_segment_ids or [])
        needs = keeper.needs_review
        for dup in dupes:
            sources.update(dup.source_segment_ids or [])
            needs = needs or dup.needs_review
            session.delete(dup)
        keeper.source_segment_ids = sorted(sources, key=str)
        keeper.needs_review = needs


def _group_by_dedup_key(rows):
    from collections import OrderedDict

    groups: OrderedDict[str, list] = OrderedDict()
    for row in rows:
        groups.setdefault(row.dedup_key, []).append(row)
    return groups.items()


def run_rule_extraction(run_id: int) -> None:
    """Celery worker 入口(同步):按批执行规则提取,批内失败单独重跑,最后跨批去重。"""
    llm = get_llm_client()
    with sync_session_factory() as session:
        run = session.get(RuleExtractionRun, run_id)
        if run is None:
            return
        if run.status == RuleExtractionRunStatus.SUCCEEDED:
            return
        blocks = list(
            session.scalars(
                select(DocumentBlock)
                .where(DocumentBlock.version_id == run.tender_version_id)
                .order_by(DocumentBlock.order_index)
            )
        )
        batches = group_into_batches(blocks)
        existing = {
            row.order_index: row
            for row in session.scalars(
                select(RuleExtractBatch).where(RuleExtractBatch.run_id == run.id)
            )
        }
        for idx, batch in enumerate(batches):
            if idx not in existing:
                row = RuleExtractBatch(
                    run_id=run.id,
                    order_index=idx,
                    status=RuleExtractBatchStatus.PENDING,
                    first_order_index=batch[0].order_index,
                    last_order_index=batch[-1].order_index,
                )
                session.add(row)
                existing[idx] = row
        run.status = RuleExtractionRunStatus.RUNNING
        run.started_at = utcnow_naive()
        run.error_message = None
        session.commit()

        ctx = _RunCtx(
            run_id=run.id,
            run_public_id=run.public_id,
            organization_id=run.organization_id,
            project_id=run.project_id,
            tender_version_id=run.tender_version_id,
            prompt_version=PROMPT_VERSION,
        )

    pending_indices = [
        i
        for i, batch in enumerate(batches)
        if existing.get(i) is not None
        and existing[i].status != RuleExtractBatchStatus.SUCCEEDED
    ]

    if pending_indices:
        tender_text = _join_blocks(blocks)
        outcomes = asyncio.run(
            _run_batches(
                llm,
                ctx,
                batches=batches,
                pending_indices=pending_indices,
                tender_text=tender_text,
            )
        )
        for idx in pending_indices:
            _persist_batch(ctx=ctx, batch_index=idx, extracted=outcomes[idx])

    _finalize_run(run_id=run_id, total_batches=len(batches))
