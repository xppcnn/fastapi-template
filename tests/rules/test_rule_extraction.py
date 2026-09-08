from collections.abc import Generator
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.integrations.llm.base import LLMError
from app.integrations.llm.fake import FakeLLMClient
from app.models import Base
from app.models.document import (
    DocumentBlock,
    DocumentVersion,
    ParseStatus,
)
from app.models.identity import Organization
from app.models.model_run import ModelRun, ModelRunStatus
from app.models.project import Project
from app.models.review_rule import ReviewRule, RuleStatus, RuleType
from app.models.rule_extraction import (
    RuleExtractBatch,
    RuleExtractBatchStatus,
    RuleExtractionRun,
    RuleExtractionRunStatus,
)
from app.prompts.common import PROMPT_VERSION, SYSTEM_MESSAGE
from app.prompts.rule_extraction import (
    RULE_PROMPTS,
    SegmentRef,
    build_extract_messages,
)
from app.schemas.llm import ExtractedRule
from app.services.review_rules import merge_extracted_rules
from app.workflows.extract_rules import group_into_batches, run_rule_extraction

_SEG_1 = "11111111-1111-1111-1111-111111111111"
_SEG_2 = "22222222-2222-2222-2222-222222222222"
_SEG_3 = "33333333-3333-3333-3333-333333333333"


def _rule(**overrides) -> ExtractedRule:
    base = {
        "rule_type": RuleType.QUALIFICATION,
        "title": "资质要求",
        "description": "投标人须具备建筑工程施工总承包一级资质",
        "evaluation_method": "semantic",
        "source_segment_ids": [UUID(_SEG_1)],
    }
    base.update(overrides)
    return ExtractedRule.model_validate(base)


# ---------- 合并铁律(原则四) ----------


def test_merge_keeps_rules_from_other_segments_when_one_says_not_mentioned() -> None:
    """某段写"没有提及"只代表该片段没有,以其他片段的有效信息为准。"""
    batch_a: list[ExtractedRule] = []  # batch_a mentioned=false → 无规则
    batch_b = [_rule()]

    merged_a = merge_extracted_rules(batch_a)
    merged_b = merge_extracted_rules(batch_b)

    assert merged_a == []
    assert len(merged_b) == 1
    assert merged_b[0].rule.title == "资质要求"


def test_merge_does_not_invent_information() -> None:
    """不新增分段结果中没有的信息:合并结果集合等于输入并集(去重后)。"""
    rules = [
        _rule(title="A", description="要求一"),
        _rule(title="B", description="要求二", source_segment_ids=[UUID(_SEG_2)]),
    ]

    merged = merge_extracted_rules(rules)

    assert len(merged) == 2
    titles = {m.rule.title for m in merged}
    assert titles == {"A", "B"}


def test_merge_dedups_cross_segment_duplicates() -> None:
    """跨段重复规则由程序端去重(规范化 key = 类型 + 判定口径),来源取并集。"""
    rules = [
        _rule(source_segment_ids=[UUID(_SEG_1)]),
        _rule(source_segment_ids=[UUID(_SEG_2)]),  # 同判定口径,不同片段
        _rule(
            title="不同", description="另一条要求", source_segment_ids=[UUID(_SEG_3)]
        ),
    ]

    merged = merge_extracted_rules(rules)

    assert len(merged) == 2
    dup = next(m for m in merged if m.rule.title == "资质要求")
    assert dup.source_uuids == {UUID(_SEG_1), UUID(_SEG_2)}
    assert dup.needs_review is False


def test_merge_marks_out_of_bound_rule_as_needs_review() -> None:
    """越界产物(模型自报 explicit_stated=false)合并后必须待确认。"""
    rules = [
        _rule(explicitly_stated=False, source_segment_ids=[UUID(_SEG_1)]),
        _rule(source_segment_ids=[UUID(_SEG_2)]),
    ]

    merged = merge_extracted_rules(rules)

    assert len(merged) == 1
    assert merged[0].needs_review is True
    assert merged[0].source_uuids == {UUID(_SEG_1), UUID(_SEG_2)}


# ---------- 稳定前缀(原则二) ----------


def test_build_extract_messages_keeps_stable_prefix() -> None:
    """回归测试守护:四类规则 messages 前两条(system + 招标全文)必须逐字节相同。"""
    tender_text = "招标文件全文内容" * 20
    batch_a = [
        SegmentRef(uuid=UUID(_SEG_1), page_no=1, order_index=1),
        SegmentRef(uuid=UUID(_SEG_2), page_no=1, order_index=2),
    ]
    batch_b = [SegmentRef(uuid=UUID(_SEG_3), page_no=2, order_index=3)]

    msg_a = build_extract_messages(
        rule_type=RuleType.DISQUALIFICATION,
        tender_text=tender_text,
        batch_refs=batch_a,
        chapter="第一章",
    )
    msg_b = build_extract_messages(
        rule_type=RuleType.RESPONSE,
        tender_text=tender_text,
        batch_refs=batch_b,
        chapter="第二章",
    )

    assert len(msg_a) == 4
    assert msg_a[0].content == SYSTEM_MESSAGE
    assert msg_a[0].content == msg_b[0].content
    assert msg_a[1].content == msg_b[1].content
    assert msg_a[2].content != msg_b[2].content  # 片段目录随批次变化,放全文之后
    assert msg_a[3].content != msg_b[3].content


def test_build_extract_messages_channels_are_distinct() -> None:
    tender_text = "全文"
    refs = [SegmentRef(uuid=UUID(_SEG_1), page_no=1)]

    drafts = [
        build_extract_messages(
            rule_type=rt, tender_text=tender_text, batch_refs=refs, chapter="第一章"
        )
        for rt in RuleType
    ]

    for rt, msgs in zip(RuleType, drafts, strict=True):
        assert msgs[0].content == SYSTEM_MESSAGE  # 稳定常量 system
        assert RULE_PROMPTS[rt].concept_boundary.split()[0] in msgs[3].content
    assert len({m[3].content for m in drafts}) == 4  # 四类任务 prompt 互不相同


def test_group_into_batches_splits_by_section_header() -> None:
    blocks = [
        DocumentBlock(
            id=1,
            version_id=1,
            order_index=1,
            block_type="section_header",
            text="第一章",
            page_no=1,
        ),
        DocumentBlock(
            id=2,
            version_id=1,
            order_index=2,
            block_type="paragraph",
            text="一",
            page_no=1,
        ),
        DocumentBlock(
            id=3,
            version_id=1,
            order_index=3,
            block_type="section_header",
            text="第二章",
            page_no=2,
        ),
        DocumentBlock(
            id=4,
            version_id=1,
            order_index=4,
            block_type="paragraph",
            text="二",
            page_no=2,
        ),
    ]

    batches = group_into_batches(blocks)

    assert len(batches) == 2
    assert [b.order_index for b in batches[0]] == [1, 2]
    assert [b.order_index for b in batches[1]] == [3, 4]


# ---------- 提取工作流(端到端,无网络) ----------


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


def _seed_tender(session_factory) -> tuple[int, dict[str, UUID], int]:
    with session_factory() as session:
        org = Organization(name="org")
        session.add(org)
        session.flush()
        project = Project(organization_id=org.id, name="p")
        session.add(project)
        session.flush()
        version = DocumentVersion(
            document_id=1,
            version_number=1,
            object_key="raw/t.pdf",
            file_name="t.pdf",
            content_type="application/pdf",
            size_bytes=1,
            sha256="0" * 64,
            parse_status=ParseStatus.PARSED,
        )
        session.add(version)
        session.flush()
        blocks = [
            DocumentBlock(
                version_id=version.id,
                order_index=1,
                block_type="section_header",
                text="第一章 投标人须知",
                page_no=1,
            ),
            DocumentBlock(
                version_id=version.id,
                order_index=2,
                block_type="paragraph",
                text="投标人须具备建筑工程施工总承包一级资质",
                page_no=1,
            ),
            DocumentBlock(
                version_id=version.id,
                order_index=3,
                block_type="section_header",
                text="第二章 评标办法",
                page_no=2,
            ),
            DocumentBlock(
                version_id=version.id,
                order_index=4,
                block_type="paragraph",
                text="技术标满分40分",
                page_no=2,
            ),
        ]
        session.add_all(blocks)
        session.flush()
        run = RuleExtractionRun(
            organization_id=org.id,
            project_id=project.id,
            tender_version_id=version.id,
            status=RuleExtractionRunStatus.QUEUED,
            total_batches=0,
        )
        session.add(run)
        session.commit()
        ids = {f"block{i}": b.public_id for i, b in enumerate(blocks, 1)}
        return run.id, ids, version.id


def _run_with_fake(session_factory, run_id: int, fake: FakeLLMClient) -> None:
    with (
        patch("app.workflows.extract_rules.get_llm_client", return_value=fake),
        patch("app.workflows.extract_rules.sync_session_factory", session_factory),
        patch("app.services.model_runs.sync_session_factory", session_factory),
    ):
        run_rule_extraction(run_id)


def _batch0_outcomes(ids: dict[str, UUID]) -> list[dict]:
    return [
        {"batch_order": 0, "mentioned": False, "rules": []},  # 否决:本段未提及
        {
            "batch_order": 0,
            "mentioned": True,
            "rules": [
                {
                    "rule_type": "qualification",
                    "title": "施工总承包一级资质",
                    "description": "投标人须具备建筑工程施工总承包一级资质",
                    "evaluation_method": "deterministic",
                    "condition": {"operator": "exists", "field": "资质证书"},
                    "source_segment_ids": [str(ids["block2"])],
                }
            ],
        },
        {"batch_order": 0, "mentioned": False, "rules": []},  # 响应
        {"batch_order": 0, "mentioned": False, "rules": []},  # 评分
    ]


def _batch1_outcomes(ids: dict[str, UUID]) -> list[dict]:
    return [
        {"batch_order": 1, "mentioned": False, "rules": []},  # 否决
        {
            "batch_order": 1,
            "mentioned": True,
            "rules": [
                {
                    "rule_type": "qualification",
                    "title": "施工总承包一级资质",
                    "description": "投标人须具备建筑工程施工总承包一级资质",  # 与 batch0 重复
                    "source_segment_ids": [str(ids["block4"])],
                }
            ],
        },
        {"batch_order": 1, "mentioned": False, "rules": []},  # 响应
        {
            "batch_order": 1,
            "mentioned": True,
            "rules": [
                {
                    "rule_type": "scoring",
                    "title": "技术标评分",
                    "description": "技术标满分40分",
                    "evaluation_criterion": "满足技术参数得满分,缺失酌情扣分",
                    "scoring_method": "subjective",
                    "max_score": 40.0,
                    "source_segment_ids": [str(ids["block4"])],
                    "explicitly_stated": False,  # 越界产物:模型经验推断
                }
            ],
        },
    ]


def test_workflow_extracts_dedups_and_marks_needs_review(session_factory) -> None:
    run_id, ids, _ = _seed_tender(session_factory)
    fake = FakeLLMClient(outcomes=_batch0_outcomes(ids) + _batch1_outcomes(ids))

    _run_with_fake(session_factory, run_id, fake)

    with session_factory() as session:
        run = session.get(RuleExtractionRun, run_id)
        assert run.status == RuleExtractionRunStatus.SUCCEEDED
        assert run.total_batches == 2
        assert run.completed_batches == 2
        assert run.failed_batches == 0

        batches = list(session.scalars(select(RuleExtractBatch)))
        assert len(batches) == 2
        assert all(b.status == RuleExtractBatchStatus.SUCCEEDED for b in batches)

        rules = list(session.scalars(select(ReviewRule)))
        assert len(rules) == 2  # 跨段去重:batch0/batch1 的资质规则合并为一条

        qual = next(r for r in rules if r.rule_type == RuleType.QUALIFICATION)
        assert qual.status == RuleStatus.DRAFT
        assert qual.needs_review is False
        assert set(qual.source_segment_ids) == {str(ids["block2"]), str(ids["block4"])}
        assert qual.model_run_id is not None
        assert qual.prompt_version == PROMPT_VERSION
        assert qual.extraction_run_id == run_id

        scoring = next(r for r in rules if r.rule_type == RuleType.SCORING)
        assert scoring.needs_review is True  # 越界产物待确认
        assert scoring.max_score == 40.0
        assert scoring.evaluation_criterion == "满足技术参数得满分,缺失酌情扣分"

        model_runs = list(session.scalars(select(ModelRun)))
        assert len(model_runs) == 8  # 2 批 × 4 类
        assert all(m.status == ModelRunStatus.SUCCEEDED for m in model_runs)
        assert all(m.prompt_version == PROMPT_VERSION for m in model_runs)
        assert len({m.operation_id for m in model_runs}) == 8


def test_workflow_marks_fake_citation_as_needs_review(session_factory) -> None:
    """模型引用虚假片段 UUID:程序回查失败 → 越界产物待确认,不伪造规则。"""
    run_id, ids, _ = _seed_tender(session_factory)
    outcomes = _batch0_outcomes(ids) + _batch1_outcomes(ids)
    outcomes[1]["rules"][0]["source_segment_ids"] = [
        "99999999-9999-9999-9999-999999999999"
    ]
    fake = FakeLLMClient(outcomes=outcomes)

    _run_with_fake(session_factory, run_id, fake)

    with session_factory() as session:
        rules = list(session.scalars(select(ReviewRule)))
        qual = next(r for r in rules if r.rule_type == RuleType.QUALIFICATION)
        assert qual.needs_review is True  # 至少一片段引用造假 → 越界产物待确认
        assert set(qual.source_segment_ids) == {
            str(ids["block4"])
        }  # 假引用被程序剔除,保留真实来源


def test_workflow_failed_batch_retry_reruns_only_failed(session_factory) -> None:
    run_id, ids, _ = _seed_tender(session_factory)
    outcomes = _batch0_outcomes(ids) + [
        LLMError("transient 500"),  # batch1 否决:第 1 次
        LLMError("transient 500"),  # 第 2 次
        LLMError("transient 500"),  # 第 3 次 → 重试耗尽,批次失败
    ]
    fake = FakeLLMClient(outcomes=outcomes)

    _run_with_fake(session_factory, run_id, fake)

    with session_factory() as session:
        run = session.get(RuleExtractionRun, run_id)
        assert run.status == RuleExtractionRunStatus.PARTIAL
        assert run.completed_batches == 1
        assert run.failed_batches == 1

    # 重跑仅处理失败批次;成功批次不再重复提取
    retry_outcomes = _batch1_outcomes(ids)  # 4 类齐全
    fake2 = FakeLLMClient(outcomes=retry_outcomes)
    with session_factory() as session:
        session.get(RuleExtractionRun, run_id).status = RuleExtractionRunStatus.QUEUED
        session.commit()
    _run_with_fake(session_factory, run_id, fake2)

    with session_factory() as session:
        run = session.get(RuleExtractionRun, run_id)
        assert run.status == RuleExtractionRunStatus.SUCCEEDED
        assert run.completed_batches == 2
        rules = list(session.scalars(select(ReviewRule)))
        assert len(rules) == 2  # 成功批次没有产生重复 draft
        model_runs = list(session.scalars(select(ModelRun)))
        # 首轮:批次0 四类成功(4) + 批次1 否决 3 次失败尝试;重跑:批次1 四类成功(4)
        assert len(model_runs) == 4 + 3 + 4
        failed = [m for m in model_runs if m.status == ModelRunStatus.FAILED]
        assert len(failed) == 3
        assert all(f.attempt == a for f, a in zip(failed, [1, 2, 3], strict=True))
        # 重跑批次1 的否决项接续 attempt 编号(4),不重开编号
        retried = [
            m
            for m in model_runs
            if m.operation_id.endswith(":1:disqualification")
            and m.status == ModelRunStatus.SUCCEEDED
        ]
        assert len(retried) == 1
        assert retried[0].attempt == 4


def test_duplicate_running_delivery_does_not_call_model(session_factory):
    run_id, _, _ = _seed_tender(session_factory)
    with session_factory() as session:
        run = session.get(RuleExtractionRun, run_id)
        run.status = RuleExtractionRunStatus.RUNNING
        session.commit()
    fake = FakeLLMClient()
    _run_with_fake(session_factory, run_id, fake)
    assert fake.calls == []


def test_batch_checkpoint_is_visible_before_other_batches_finish(session_factory):
    run_id, ids, _ = _seed_tender(session_factory)

    class ObservingFake(FakeLLMClient):
        async def structured(self, **kwargs):
            if kwargs["operation"].endswith(":1:disqualification"):
                with session_factory() as session:
                    run = session.get(RuleExtractionRun, run_id)
                    assert run.completed_batches == 1, "成功批次应立即持久化"
            return await super().structured(**kwargs)

    fake = ObservingFake(outcomes=_batch0_outcomes(ids) + _batch1_outcomes(ids))
    _run_with_fake(session_factory, run_id, fake)
    with session_factory() as session:
        assert (
            session.get(RuleExtractionRun, run_id).status
            == RuleExtractionRunStatus.SUCCEEDED
        )


def test_worker_setup_failure_marks_run_failed(session_factory):
    run_id, _, _ = _seed_tender(session_factory)
    with (
        patch("app.workflows.extract_rules.sync_session_factory", session_factory),
        patch(
            "app.workflows.extract_rules.get_llm_client",
            side_effect=RuntimeError("private connection"),
        ),
    ):
        run_rule_extraction(run_id)
    with session_factory() as session:
        run = session.get(RuleExtractionRun, run_id)
        assert run.status == RuleExtractionRunStatus.FAILED
        assert "private" not in run.error_message


def test_timeout_and_old_delivery_cannot_overwrite_retry(session_factory):
    from datetime import timedelta
    from uuid import uuid4

    from app.core.object_storage import utcnow_naive
    from app.workflows.extract_rules import reconcile_rule_extractions

    run_id, _, _ = _seed_tender(session_factory)
    with session_factory() as session:
        run = session.get(RuleExtractionRun, run_id)
        old_token = run.execution_token
        run.status = RuleExtractionRunStatus.RUNNING
        run.started_at = utcnow_naive() - timedelta(days=1)
        session.commit()
    with patch("app.workflows.extract_rules.sync_session_factory", session_factory):
        assert reconcile_rule_extractions() == 1
    with session_factory() as session:
        run = session.get(RuleExtractionRun, run_id)
        assert run.status == RuleExtractionRunStatus.FAILED
        assert run.finished_at is not None
        run.status = RuleExtractionRunStatus.QUEUED
        run.execution_token = str(uuid4())
        session.commit()
    with (
        patch("app.workflows.extract_rules.sync_session_factory", session_factory),
        patch("app.workflows.extract_rules.get_llm_client") as llm,
    ):
        run_rule_extraction(run_id, old_token)
        llm.assert_not_called()
    with session_factory() as session:
        assert (
            session.get(RuleExtractionRun, run_id).status
            == RuleExtractionRunStatus.QUEUED
        )


def test_late_batch_is_discarded_after_timeout(session_factory):
    from datetime import timedelta

    from app.core.object_storage import utcnow_naive
    from app.workflows.extract_rules import reconcile_rule_extractions

    run_id, ids, _ = _seed_tender(session_factory)

    class TimedOutFake(FakeLLMClient):
        async def structured(self, **kwargs):
            result = await super().structured(**kwargs)
            with session_factory() as session:
                run = session.get(RuleExtractionRun, run_id)
                run.started_at = utcnow_naive() - timedelta(days=1)
                session.commit()
            reconcile_rule_extractions()
            return result

    fake = TimedOutFake(outcomes=_batch0_outcomes(ids) + _batch1_outcomes(ids))
    _run_with_fake(session_factory, run_id, fake)
    with session_factory() as session:
        assert (
            session.get(RuleExtractionRun, run_id).status
            == RuleExtractionRunStatus.FAILED
        )
        assert list(session.scalars(select(ReviewRule))) == []


def test_queued_timeout_does_not_expire_recent_run(session_factory):
    from datetime import timedelta

    from app.core.object_storage import utcnow_naive
    from app.workflows.extract_rules import reconcile_rule_extractions

    run_id, _, _ = _seed_tender(session_factory)
    with patch("app.workflows.extract_rules.sync_session_factory", session_factory):
        assert reconcile_rule_extractions() == 0
        with session_factory() as session:
            session.get(RuleExtractionRun, run_id).updated_at = (
                utcnow_naive() - timedelta(days=1)
            )
            session.commit()
        assert reconcile_rule_extractions() == 1
