from __future__ import annotations

from app.core.database import sync_session_factory
from app.integrations.llm.base import LLMMessage, LLMResult
from app.models.model_run import ModelRun, ModelRunStatus


def _input_hash(messages: list[LLMMessage]) -> str:
    import hashlib
    import json

    canonical = json.dumps(
        [m.model_dump(mode="json") for m in messages],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def record_attempt_start(
    *,
    organization_id: int,
    project_id: int,
    operation_id: str,
    attempt: int | None = None,
    operation_name: str,
    model: str,
    prompt_version: str | None,
    messages: list[LLMMessage],
) -> ModelRun:
    """创建一次 RUNNING 尝试;相同 operation_id 不同 attempt 组成重试链。

    attempt 缺省时取该 operation 现有最大 attempt + 1:同一批失败后重跑继续递增,
    不重开 attempt 编号(保证 (operation_id, attempt) 唯一)。
    """
    from sqlalchemy import func, select

    with sync_session_factory() as session:
        if attempt is None:
            last = session.scalar(
                select(func.max(ModelRun.attempt)).where(
                    ModelRun.operation_id == operation_id
                )
            )
            attempt = (last or 0) + 1
        run = ModelRun(
            organization_id=organization_id,
            project_id=project_id,
            operation_id=operation_id,
            attempt=attempt,
            operation_name=operation_name,
            status=ModelRunStatus.RUNNING,
            model=model,
            prompt_version=prompt_version,
            model_input_hash=_input_hash(messages),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run


def record_attempt_success(run: ModelRun, *, result: LLMResult) -> None:
    with sync_session_factory() as session:
        row = session.get(ModelRun, run.id)
        if row is None:
            return
        row.status = ModelRunStatus.SUCCEEDED
        row.raw_output = result.raw
        row.prompt_tokens = result.prompt_tokens
        row.completion_tokens = result.completion_tokens
        row.latency_ms = result.latency_ms
        session.commit()


def record_attempt_failure(run: ModelRun, *, error: str) -> None:
    with sync_session_factory() as session:
        row = session.get(ModelRun, run.id)
        if row is None:
            return
        row.status = ModelRunStatus.FAILED
        row.error_message = error[:2000]
        session.commit()


def latest_successful_run(*, operation_id: str) -> ModelRun | None:
    """取该操作最后一条成功的尝试(用于关联业务对象,如规则)。"""
    from sqlalchemy import select

    with sync_session_factory() as session:
        stmt = (
            select(ModelRun)
            .where(
                ModelRun.operation_id == operation_id,
                ModelRun.status == ModelRunStatus.SUCCEEDED,
            )
            .order_by(ModelRun.attempt.desc())
            .limit(1)
        )
        return session.scalars(stmt).first()
