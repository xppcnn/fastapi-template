from collections.abc import Generator
from typing import ClassVar
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.celery_app import celery_app
from app.core.object_storage import utcnow_naive
from app.models import Base
from app.models.document import DocumentVersion, ParseStatus
from app.services.parsing import reconcile_parse, submit_parse
from app.tasks.parsing import (
    parsing_reconcile,
    parsing_submit,
)


@pytest.fixture
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def eager_celery():
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False


def _make_version(session, *, parse_status, parse_job_id=None) -> int:
    version = DocumentVersion(
        document_id=1,
        version_number=1,
        object_key="raw/a.pdf",
        file_name="a.pdf",
        content_type="application/pdf",
        size_bytes=1,
        sha256="0" * 64,
        parse_status=parse_status,
        parse_job_id=parse_job_id,
        parsing_started_at=utcnow_naive(),
    )
    session.add(version)
    session.commit()
    return version.id


class FakeStorage:
    def fget_object(self, bucket, object_key, file_path) -> None:
        with open(file_path, "wb") as f:
            f.write(b"%PDF")

    def put_object(self, bucket, object_key, data, length, *, content_type=None):
        return object()


class FakeDocling:
    def __init__(self, *, task_id="task-1"):
        self.task_id = task_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def submit(self, source, options=None, target=None):
        return type("Job", (), {"task_id": self.task_id})()


class FakeStatus:
    def __init__(self, task_status: str):
        self.task_status = task_status


class FakePayload:
    class Document:
        md_content = "# 已恢复"
        json_content: ClassVar[dict] = {
            "texts": [
                {
                    "text": "已恢复",
                    "label": "paragraph",
                    "prov": [{"page_no": 1}],
                }
            ]
        }

    document = Document()
    status = "success"
    errors = ()


class FakePollClient:
    def __init__(self, task_status: str = "success"):
        self._async_client = None
        self.task_status = task_status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def _poll_task_status(self, task_id, wait):
        return FakeStatus(self.task_status)

    async def _fetch_convert_result_payload(
        self, task_id, last_status=None, async_client=None
    ):
        return FakePayload()


def test_task_names_registered() -> None:
    assert "parsing.submit" in celery_app.tasks
    assert "parsing.reconcile" in celery_app.tasks


def test_celery_conf_matches_settings() -> None:
    from app.core.config import get_settings

    settings = get_settings()
    assert celery_app.conf.timezone == "Asia/Shanghai"
    assert celery_app.conf.enable_utc is False
    assert celery_app.conf.worker_concurrency == settings.celery_concurrency
    assert (
        celery_app.conf.worker_max_tasks_per_child
        == settings.worker_max_tasks_per_child
    )
    assert "parsing-reconcile" in celery_app.conf.beat_schedule
    entry = celery_app.conf.beat_schedule["parsing-reconcile"]
    assert entry["task"] == "parsing.reconcile"
    assert entry["schedule"] == settings.parsing_poll_interval_seconds


def test_parsing_submit_task_eager_runs(
    session_factory: sessionmaker, eager_celery
) -> None:
    with session_factory() as session:
        version_id = _make_version(session, parse_status=ParseStatus.PARSING)

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
        patch("app.services.parsing.get_client", return_value=FakeStorage()),
        patch("app.services.parsing.open_client", return_value=FakeDocling()),
    ):
        parsing_submit.delay(version_id)

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.PARSING
        assert version.parse_job_id == "task-1"


def test_parsing_reconcile_task_eager_runs(
    session_factory: sessionmaker, eager_celery
) -> None:
    with session_factory() as session:
        version_id = _make_version(
            session, parse_status=ParseStatus.PARSING, parse_job_id="t1"
        )

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
        patch("app.services.parsing.get_client", return_value=FakeStorage()),
        patch("app.services.parsing.open_client", return_value=FakePollClient()),
    ):
        parsing_reconcile.delay()

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.PARSED


def test_delay_routes_through_celery_api(
    session_factory: sessionmaker, eager_celery
) -> None:
    """确认任务对象是可调度的 celery 任务(非普通函数)。"""
    assert hasattr(parsing_submit, "delay")
    assert hasattr(parsing_reconcile, "delay")
    assert callable(parsing_submit.delay)
    assert callable(parsing_reconcile.delay)


def test_tasks_wrap_parsing_services() -> None:
    """任务体应直接委托服务层(薄包装),保证行为单源。"""
    assert parsing_submit.run is not None
    assert parsing_reconcile.run is not None
    # 任务体与服务的同名函数存在(避免包装漂移)
    import inspect

    assert inspect.getsource(parsing_reconcile).find("reconcile_parse") != -1
    assert inspect.getsource(parsing_submit).find("submit_parse") != -1
    assert reconcile_parse is not None
    assert submit_parse is not None


def test_task_modules_auto_collected_in_fresh_process() -> None:
    """app/tasks/ 下的任务在全新进程中被自动收集,无需手动 include。"""
    import subprocess
    import sys

    script = (
        "from app.core.celery_app import celery_app; "
        "assert 'parsing.submit' in celery_app.tasks, celery_app.tasks.keys(); "
        "assert 'parsing.reconcile' in celery_app.tasks"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=__import__("pathlib").Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, (
        f"auto-collection failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
