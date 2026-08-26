from app.core.celery_app import celery_app
from app.services.parsing import reconcile_parse, submit_parse


@celery_app.task(name="parsing.submit", acks_late=True, max_retries=0)
def parsing_submit(version_id: int) -> None:
    """提交解析:校验版本 → 下载 → 提交 docling → 存 task_id;失败标 FAILED。"""
    submit_parse(version_id)


@celery_app.task(name="parsing.reconcile", acks_late=True, max_retries=0)
def parsing_reconcile() -> None:
    """定时对账:扫 PARSING 版本,逐版本 poll docling 一次并迁移终态。"""
    reconcile_parse()
