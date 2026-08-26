from collections.abc import Generator
from datetime import timedelta
from typing import ClassVar
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.object_storage import utcnow_naive
from app.models import Base
from app.models.document import DocumentBlock, DocumentVersion, ParseStatus
from app.services.parsing import (
    _payload_to_parsed_conversion,
    extract_blocks,
    find_stuck_parsing_versions,
    reconcile_parse,
    submit_parse,
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


SAMPLE_DOCLING_JSON = {
    "body": {
        "children": [
            {"$ref": "#/texts/0"},
            {"$ref": "#/groups/0"},
            {"$ref": "#/texts/2"},
        ]
    },
    "texts": [
        {
            "text": "第一章 招标公告",
            "label": "section_header",
            "prov": [],
            "children": [
                {"$ref": "#/texts/1"},
                {"$ref": "#/tables/0"},
            ],
        },
        {"text": "本项目为示例。", "label": "paragraph", "prov": []},
        {"text": "预算：100万", "label": "paragraph", "prov": []},
    ],
    "groups": [
        {
            "name": "g0",
            "self_ref": "#/groups/0",
            "children": [
                {"$ref": "#/texts/2"},
            ],
        }
    ],
    "tables": [
        {
            "label": "table",
            "prov": [{"page_no": 2}],
            "data": {
                "num_rows": 2,
                "num_cols": 2,
                "table_cells": [
                    {
                        "text": "序号",
                        "start_row_offset_idx": 0,
                        "end_row_offset_idx": 1,
                        "start_col_offset_idx": 0,
                        "end_col_offset_idx": 1,
                    },
                    {
                        "text": "项目",
                        "start_row_offset_idx": 0,
                        "end_row_offset_idx": 1,
                        "start_col_offset_idx": 1,
                        "end_col_offset_idx": 2,
                    },
                    {
                        "text": "1",
                        "start_row_offset_idx": 1,
                        "end_row_offset_idx": 2,
                        "start_col_offset_idx": 0,
                        "end_col_offset_idx": 1,
                    },
                    {
                        "text": "信息化平台",
                        "start_row_offset_idx": 1,
                        "end_row_offset_idx": 2,
                        "start_col_offset_idx": 1,
                        "end_col_offset_idx": 2,
                    },
                ],
            },
        }
    ],
}


def _make_version(
    session,
    *,
    parse_status,
    parse_job_id=None,
    parsing_started_at=None,
    version_number=1,
) -> int:
    version = DocumentVersion(
        document_id=1,
        version_number=version_number,
        object_key="raw/a.pdf",
        file_name="a.pdf",
        content_type="application/pdf",
        size_bytes=1,
        sha256="0" * 64,
        parse_status=parse_status,
        parse_job_id=parse_job_id,
        parsing_started_at=parsing_started_at,
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
    def __init__(self, *, task_id="task-1", fail_submit=False):
        self.task_id = task_id
        self.fail_submit = fail_submit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def submit(self, source, options=None, target=None):
        if self.fail_submit:
            raise RuntimeError("server down")
        return type("Job", (), {"task_id": self.task_id})()


class FakeStatus:
    def __init__(self, task_status: str):
        self.task_status = task_status


class FakePayload:
    class Document:
        md_content = "# 已恢复"
        json_content = SAMPLE_DOCLING_JSON

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


def test_extract_blocks_recurses_into_groups() -> None:
    blocks = extract_blocks(SAMPLE_DOCLING_JSON)
    assert len(blocks) == 4
    assert [b["block_type"] for b in blocks] == [
        "section_header",
        "paragraph",
        "table",
        "paragraph",
    ]
    assert "| 序号 | 项目 |" in blocks[2]["text"]
    assert "| 1 | 信息化平台 |" in blocks[2]["text"]
    assert blocks[3]["text"] == "预算：100万"


def test_extract_blocks_falls_back_without_body() -> None:
    no_body = {k: v for k, v in SAMPLE_DOCLING_JSON.items() if k != "body"}
    no_body["groups"] = []
    blocks = extract_blocks(no_body)
    assert [b["block_type"] for b in blocks] == [
        "section_header",
        "paragraph",
        "paragraph",
        "table",
    ]
    assert blocks[3]["block_type"] == "table"


def test_payload_to_parsed_conversion_supports_docling_document_model() -> None:
    """serve 响应的 json_content 是 DoclingDocument 模型(非 dict),必须能导出为 dict。"""

    class FakeModel:
        def export_to_dict(self) -> dict:
            return SAMPLE_DOCLING_JSON

    class FakeDocument:
        md_content = "# 第一章 招标公告"
        json_content = FakeModel()

    class FakePayload:
        document = FakeDocument()
        status = "success"
        errors = ()

    parsed = _payload_to_parsed_conversion(FakePayload())
    assert parsed.document_json["texts"][0]["text"] == "第一章 招标公告"


def test_payload_to_parsed_conversion_accepts_plain_dict() -> None:
    class FakeDocument:
        md_content = "# 标题"
        json_content: ClassVar[dict] = {"texts": [{"text": "x", "label": "paragraph"}]}

    class FakePayload:
        document = FakeDocument()
        status = "success"
        errors = ()

    parsed = _payload_to_parsed_conversion(FakePayload())
    assert parsed.document_json["texts"][0]["text"] == "x"


def test_reconcile_parse_persists_real_json_for_model_payload(
    session_factory: sessionmaker,
) -> None:
    """回归:json_content 为 DoclingDocument 模型时,parsed.json 产物必须非空且 blocks 可提取。"""
    import json as json_module

    with session_factory() as session:
        version_id = _make_version(
            session,
            parse_status=ParseStatus.PARSING,
            parse_job_id="t1",
            parsing_started_at=utcnow_naive(),
        )

    class FakeModel:
        def export_to_dict(self) -> dict:
            return SAMPLE_DOCLING_JSON

    class FakePayload:
        class Document:
            md_content = "# 第一章 招标公告"
            json_content = FakeModel()

        document = Document()
        status = "success"
        errors = ()

    class FakePollClientModel(FakePollClient):
        async def _fetch_convert_result_payload(
            self, task_id, last_status=None, async_client=None
        ):
            return FakePayload()

    class RecordingStorage(FakeStorage):
        def __init__(self) -> None:
            self.writes: list[tuple[str, object]] = []

        def put_object(self, bucket, object_key, data, length, *, content_type=None):
            self.writes.append((object_key, data))
            return object()

    storage = RecordingStorage()
    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
        patch("app.services.parsing.get_client", return_value=storage),
        patch("app.services.parsing.open_client", return_value=FakePollClientModel()),
    ):
        reconcile_parse()

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.PARSED
        blocks = session.scalars(select(DocumentBlock)).all()
        assert len(blocks) == 4

    json_write = next(w for w in storage.writes if w[0].endswith("parsed.json"))
    payload = json_module.loads(json_write[1].getvalue())
    assert len(payload["texts"]) == 3
    assert payload["body"]["children"] == [
        {"$ref": "#/texts/0"},
        {"$ref": "#/groups/0"},
        {"$ref": "#/texts/2"},
    ]


def test_find_stuck_parsing_versions_respects_limit(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        for n in range(1, 4):
            _make_version(
                session,
                parse_status=ParseStatus.PARSING,
                parse_job_id=f"t{n}",
                version_number=n,
            )
    with session_factory() as session:
        ids = find_stuck_parsing_versions(session, limit=2)
    assert len(ids) == 2


def test_submit_parse_stores_job_id(session_factory: sessionmaker) -> None:
    with session_factory() as session:
        version_id = _make_version(
            session, parse_status=ParseStatus.PARSING, parsing_started_at=utcnow_naive()
        )

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
        patch("app.services.parsing.get_client", return_value=FakeStorage()),
        patch("app.services.parsing.open_client", return_value=FakeDocling()),
    ):
        submit_parse(version_id)

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.PARSING
        assert version.parse_job_id == "task-1"


def test_submit_parse_failure_marks_failed(session_factory: sessionmaker) -> None:
    with session_factory() as session:
        version_id = _make_version(
            session, parse_status=ParseStatus.PARSING, parsing_started_at=utcnow_naive()
        )

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
        patch("app.services.parsing.get_client", return_value=FakeStorage()),
        patch(
            "app.services.parsing.open_client",
            return_value=FakeDocling(fail_submit=True),
        ),
    ):
        submit_parse(version_id)

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.FAILED
        assert "server down" in version.parse_error


def test_submit_parse_skips_non_parsing_version(session_factory: sessionmaker) -> None:
    with session_factory() as session:
        version_id = _make_version(session, parse_status=ParseStatus.PARSED)

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
        patch("app.services.parsing.get_client", return_value=FakeStorage()),
        patch("app.services.parsing.open_client", return_value=FakeDocling()),
    ):
        submit_parse(version_id)

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.PARSED
        assert version.parse_job_id is None


def test_reconcile_parse_success_persists_blocks_and_status(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        version_id = _make_version(
            session,
            parse_status=ParseStatus.PARSING,
            parse_job_id="t1",
            parsing_started_at=utcnow_naive(),
        )

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
        patch("app.services.parsing.get_client", return_value=FakeStorage()),
        patch("app.services.parsing.open_client", return_value=FakePollClient()),
    ):
        reconcile_parse()

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.PARSED
        assert version.parsed_at is not None
        assert version.parsed_object_key is not None
        assert version.parsed_markdown_object_key is not None

        blocks = session.scalars(select(DocumentBlock)).all()
        assert len(blocks) == 4


def test_reconcile_parse_keeps_pending(session_factory: sessionmaker) -> None:
    with session_factory() as session:
        version_id = _make_version(
            session,
            parse_status=ParseStatus.PARSING,
            parse_job_id="t1",
            parsing_started_at=utcnow_naive(),
        )

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
        patch(
            "app.services.parsing.open_client", return_value=FakePollClient("started")
        ),
    ):
        reconcile_parse()

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.PARSING


def test_reconcile_parse_failure_marks_failed(session_factory: sessionmaker) -> None:
    with session_factory() as session:
        version_id = _make_version(
            session,
            parse_status=ParseStatus.PARSING,
            parse_job_id="t1",
            parsing_started_at=utcnow_naive(),
        )

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
        patch(
            "app.services.parsing.open_client", return_value=FakePollClient("failure")
        ),
    ):
        reconcile_parse()

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.FAILED
        assert "转换失败" in version.parse_error


def test_reconcile_parse_marks_failed_when_job_id_missing(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        version_id = _make_version(
            session,
            parse_status=ParseStatus.PARSING,
            parse_job_id=None,
            parsing_started_at=utcnow_naive() - timedelta(minutes=10),
        )

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
    ):
        reconcile_parse()

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.FAILED
        assert "重新触发" in version.parse_error


def test_reconcile_parse_skips_fresh_missing_job_id(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        version_id = _make_version(
            session,
            parse_status=ParseStatus.PARSING,
            parse_job_id=None,
            parsing_started_at=utcnow_naive(),
        )

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
    ):
        reconcile_parse()

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.PARSING


def test_reconcile_parse_marks_failed_when_timed_out(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        version_id = _make_version(
            session,
            parse_status=ParseStatus.PARSING,
            parse_job_id="t1",
            parsing_started_at=utcnow_naive()
            - timedelta(minutes=16),  # 默认超时 15 分钟
        )

    with (
        patch("app.services.parsing.sync_session_factory", session_factory),
    ):
        reconcile_parse()

    with session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version.parse_status == ParseStatus.FAILED
        assert "超时" in version.parse_error
