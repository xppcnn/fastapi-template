from app.core.celery_app import celery_app
from app.workflows.extract_rules import run_rule_extraction


@celery_app.task(
    name="rule_extraction.submit",
    acks_late=True,
    max_retries=0,
    queue="review",
)
def rule_extraction_submit(run_id: int) -> None:
    """规则提取:按批执行 LLM 调用与 draft 落库(LLM 密集,独立 review 队列)。"""
    run_rule_extraction(run_id)
