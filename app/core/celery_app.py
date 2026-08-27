import importlib
import pkgutil

from celery import Celery  # type: ignore[import-untyped]  # celery 无官方 stubs

from app.core.config import get_settings

settings = get_settings()

# beat 调度表独立维护,便于按环境增删条目(参考:环境条件定时任务管理风格)。
# 示例:if settings.environment == "production": _beat_schedule["xxx"] = {...}
_beat_schedule = {
    "parsing-reconcile": {
        "task": "parsing.reconcile",
        "schedule": settings.parsing_poll_interval_seconds,
    },
}

celery_app = Celery(
    "fastapi_template",
    broker=settings.redis_url,
)

celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # 与团队运维惯例对齐:业务/报告时间均按北京时间书写,未来 cron 直接写本地时刻
    enable_utc=False,
    timezone="Asia/Shanghai",
    task_default_queue="parsing",
    task_routes={
        "parsing.submit": {"queue": "parsing"},
        "parsing.reconcile": {"queue": "parsing"},
        # 规则提取/逐项审核/评分共用:LLM 密集队列,与 CPU 密集的 parsing 分离
        "rule_extraction.submit": {"queue": "review"},
    },
    beat_schedule=_beat_schedule,
    worker_concurrency=settings.celery_concurrency,
    # 防 worker 长跑内存泄漏:重启前最多处理 N 个任务
    worker_max_tasks_per_child=settings.worker_max_tasks_per_child,
)


def _collect_task_modules() -> None:
    """扫描 app/tasks/ 下所有模块并导入,自动注册其中的 @celery_app.task。

    新增任务只需在 app/tasks/ 下建文件,无需修改任何注册配置。
    """
    import app.tasks as tasks_package

    for module_info in pkgutil.walk_packages(
        tasks_package.__path__, tasks_package.__name__ + "."
    ):
        importlib.import_module(module_info.name)


_collect_task_modules()
