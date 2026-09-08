from app.core.celery_app import celery_app
from app.workflows.extract_rules import reconcile_rule_extractions, run_rule_extraction


@celery_app.task(
    name="rule_extraction.submit",
    acks_late=True,
    max_retries=0,
    queue="review",
)
def rule_extraction_submit(run_id: int, execution_token: str) -> None:
    """规则提取:按批执行 LLM 调用与 draft 落库(LLM 密集,独立 review 队列)。"""
    run_rule_extraction(run_id, execution_token)


@celery_app.task(name="rule_extraction.reconcile", queue="parsing")
def rule_extraction_reconcile() -> int:
    return reconcile_rule_extractions()
