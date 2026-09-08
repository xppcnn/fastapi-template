import asyncio
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import app
from app.models import Base
from app.models.document import (
    DocType,
    Document,
    DocumentBlock,
    DocumentVersion,
    ParseStatus,
)
from app.models.identity import Organization
from app.models.project import Project
from app.models.review_rule import ReviewRule, RuleStatus, RuleType


@pytest.fixture
def review_env():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    asyncio.run(init())
    app.dependency_overrides[get_db] = db
    try:
        with (
            patch("app.main.parsing_reconcile.delay"),
            TestClient(app, base_url="https://testserver") as client,
        ):
            data = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "review@example.com",
                    "password": "correct horse battery staple",
                },
            ).json()["data"]
            headers = {"Authorization": "Bearer " + data["access_token"]}

            async def seed():
                async with factory() as session:
                    org = await session.scalar(
                        select(Organization).where(
                            Organization.public_id
                            == UUID(data["organization"]["public_id"])
                        )
                    )
                    project = Project(organization_id=org.id, name="审核项目")
                    session.add(project)
                    await session.flush()
                    versions = {}
                    for kind in (DocType.TENDER, DocType.BID):
                        doc = Document(
                            project_id=project.id,
                            organization_id=org.id,
                            name=kind.value,
                            doc_type=kind,
                        )
                        session.add(doc)
                        await session.flush()
                        version = DocumentVersion(
                            document_id=doc.id,
                            version_number=1,
                            object_key=f"raw/{kind}.pdf",
                            file_name=f"{kind}.pdf",
                            content_type="application/pdf",
                            size_bytes=10,
                            sha256="0" * 64,
                            parse_status=ParseStatus.PARSED,
                        )
                        session.add(version)
                        await session.flush()
                        doc.active_version_id = version.id
                        versions[kind] = version
                        session.add(
                            DocumentBlock(
                                version_id=version.id,
                                order_index=1,
                                block_type="paragraph",
                                text="投标人具有建筑工程施工总承包一级资质",
                                page_no=7,
                            )
                        )
                    rule = ReviewRule(
                        organization_id=org.id,
                        project_id=project.id,
                        tender_version_id=versions[DocType.TENDER].id,
                        rule_type=RuleType.QUALIFICATION,
                        title="建筑工程资质",
                        description="须具备建筑工程施工总承包一级资质",
                        status=RuleStatus.CONFIRMED,
                        source_segment_ids=[],
                        dedup_key="qualification:资质",
                    )
                    session.add(rule)
                    await session.commit()
                    return {
                        "project": project.public_id,
                        "tender": versions[DocType.TENDER].public_id,
                        "bid": versions[DocType.BID].public_id,
                        "rule": rule.public_id,
                    }

            yield client, factory, headers, asyncio.run(seed())
    finally:
        app.dependency_overrides.pop(get_db, None)
        asyncio.run(engine.dispose())


def _create(env, key="review-1"):
    client, _, headers, ids = env
    return client.post(
        f"/api/v1/projects/{ids['project']}/reviews",
        headers={**headers, "Idempotency-Key": key},
        json={
            "tender_version_id": str(ids["tender"]),
            "bid_version_id": str(ids["bid"]),
        },
    )


def test_create_snapshot_idempotent_and_retrieve_evidence(review_env):
    client, _, headers, ids = review_env
    response = _create(review_env)
    assert response.status_code == 201
    assert response.json()["code"] == 200
    run = response.json()["data"]
    assert run["status"] == "ready"
    assert run["tender_version_id"] == str(ids["tender"])
    assert run["rules"][0]["snapshot"]["title"] == "建筑工程资质"
    assert _create(review_env).json()["data"]["public_id"] == run["public_id"]
    base = f"/api/v1/projects/{ids['project']}/reviews"
    listed = client.get(base, headers=headers).json()["data"]
    assert listed["total"] == 1
    evidence = client.get(
        f"{base}/{run['public_id']}/rules/{run['rules'][0]['public_id']}/evidence",
        headers=headers,
    )
    assert evidence.status_code == 200
    result = evidence.json()["data"]
    assert result["retrieval_version"] == "keyword-v1"
    assert result["items"][0]["page_no"] == 7
    assert result["items"][0]["document_version_id"] == str(ids["bid"])
    assert "一级资质" in result["items"][0]["text"]


def test_snapshot_and_evidence_keep_original_input_after_replacement(review_env):
    client, factory, headers, ids = review_env
    response = _create(review_env)
    assert response.status_code == 201
    run = response.json()["data"]

    async def replace():
        async with factory() as session:
            rule = await session.scalar(
                select(ReviewRule).where(ReviewRule.public_id == ids["rule"])
            )
            rule.title = "未来版本的标题"
            old = await session.scalar(
                select(DocumentVersion).where(DocumentVersion.public_id == ids["bid"])
            )
            new = DocumentVersion(
                document_id=old.document_id,
                version_number=2,
                object_key="raw/new.pdf",
                file_name="new.pdf",
                content_type="application/pdf",
                size_bytes=10,
                sha256="1" * 64,
                parse_status=ParseStatus.PARSED,
            )
            session.add(new)
            await session.flush()
            doc = await session.get(Document, old.document_id)
            doc.active_version_id = new.id
            session.add(
                DocumentBlock(
                    version_id=new.id,
                    order_index=1,
                    block_type="paragraph",
                    text="建筑工程施工总承包一级资质 新版本不能混入",
                    page_no=99,
                )
            )
            await session.commit()
            return new.public_id

    new_id = asyncio.run(replace())
    base = f"/api/v1/projects/{ids['project']}/reviews/{run['public_id']}"
    detail = client.get(base, headers=headers).json()["data"]
    assert detail["rules"][0]["snapshot"]["title"] == "建筑工程资质"
    result = client.get(
        f"{base}/rules/{run['rules'][0]['public_id']}/evidence", headers=headers
    ).json()["data"]
    assert len(result["items"]) == 1
    assert result["items"][0]["page_no"] == 7
    # 既有请求即使活动版本变化仍能幂等读取；同键不同输入必须拒绝。
    assert _create(review_env).json()["data"]["public_id"] == run["public_id"]
    conflict = client.post(
        f"/api/v1/projects/{ids['project']}/reviews",
        headers={**headers, "Idempotency-Key": "review-1"},
        json={"tender_version_id": str(ids["tender"]), "bid_version_id": str(new_id)},
    )
    assert conflict.status_code == 409
    assert _create(review_env, "different-key").status_code == 409


@pytest.mark.parametrize("invalid", ["draft", "unparsed", "no_rules", "extracting"])
def test_rejects_unready_inputs(review_env, invalid):
    from app.models.rule_extraction import RuleExtractionRun, RuleExtractionRunStatus

    _, factory, _, ids = review_env

    async def invalidate():
        async with factory() as session:
            rule = await session.scalar(
                select(ReviewRule).where(ReviewRule.public_id == ids["rule"])
            )
            if invalid in {"draft", "no_rules"}:
                rule.status = (
                    RuleStatus.DRAFT if invalid == "draft" else RuleStatus.IGNORED
                )
            elif invalid == "unparsed":
                version = await session.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.public_id == ids["bid"]
                    )
                )
                version.parse_status = ParseStatus.UPLOADED
            else:
                session.add(
                    RuleExtractionRun(
                        organization_id=rule.organization_id,
                        project_id=rule.project_id,
                        tender_version_id=rule.tender_version_id,
                        status=RuleExtractionRunStatus.RUNNING,
                        total_batches=1,
                    )
                )
            await session.commit()

    asyncio.run(invalidate())
    assert _create(review_env).status_code == 409


def test_review_resources_reject_other_organization(review_env):
    client, _, _headers, ids = review_env
    created = _create(review_env)
    assert created.status_code == 201
    run = created.json()["data"]
    other = client.post(
        "/api/v1/auth/register",
        json={"email": "other@example.com", "password": "correct horse battery staple"},
    ).json()["data"]
    bad = {"Authorization": "Bearer " + other["access_token"]}
    base = f"/api/v1/projects/{ids['project']}/reviews"
    for url in (
        base,
        base + f"/{run['public_id']}",
        base + f"/{run['public_id']}/rules/{run['rules'][0]['public_id']}/evidence",
    ):
        assert client.get(url, headers=bad).status_code == 404
    assert (
        client.post(
            base,
            headers=bad,
            json={
                "tender_version_id": str(ids["tender"]),
                "bid_version_id": str(ids["bid"]),
            },
        ).status_code
        == 404
    )


def test_evidence_no_match_and_unknown_page_are_not_fabricated(review_env):
    client, factory, headers, ids = review_env
    response = _create(review_env)
    assert response.status_code == 201
    run = response.json()["data"]
    url = f"/api/v1/projects/{ids['project']}/reviews/{run['public_id']}/rules/{run['rules'][0]['public_id']}/evidence"

    async def change(text):
        async with factory() as session:
            version = await session.scalar(
                select(DocumentVersion).where(DocumentVersion.public_id == ids["bid"])
            )
            block = await session.scalar(
                select(DocumentBlock).where(DocumentBlock.version_id == version.id)
            )
            block.text = text
            block.page_no = None
            await session.commit()

    asyncio.run(change("一级资质"))
    result = client.get(url, headers=headers).json()["data"]
    assert result["items"][0]["page_no"] is None
    asyncio.run(change("abcdefg"))
    assert client.get(url, headers=headers).json()["data"]["items"] == []
    assert client.get(url, headers=headers, params={"limit": 0}).status_code == 422


def test_other_run_rule_cannot_be_used_for_evidence(review_env):
    client, _, headers, ids = review_env
    first = _create(review_env)
    second = _create(review_env, "second")
    assert first.status_code == second.status_code == 201
    a, b = first.json()["data"], second.json()["data"]
    url = f"/api/v1/projects/{ids['project']}/reviews/{a['public_id']}/rules/{b['rules'][0]['public_id']}/evidence"
    assert client.get(url, headers=headers).status_code == 404


def test_swapped_document_types_are_rejected(review_env):
    client, _, headers, ids = review_env
    response = client.post(
        f"/api/v1/projects/{ids['project']}/reviews",
        headers=headers,
        json={
            "tender_version_id": str(ids["bid"]),
            "bid_version_id": str(ids["tender"]),
        },
    )
    assert response.status_code == 404


def test_deleted_bid_document_revokes_evidence_access(review_env):
    client, factory, headers, ids = review_env
    response = _create(review_env)
    assert response.status_code == 201
    run = response.json()["data"]

    async def delete():
        async with factory() as session:
            version = await session.scalar(
                select(DocumentVersion).where(DocumentVersion.public_id == ids["bid"])
            )
            document = await session.get(Document, version.document_id)
            document.is_deleted = True
            await session.commit()

    asyncio.run(delete())
    url = f"/api/v1/projects/{ids['project']}/reviews/{run['public_id']}/rules/{run['rules'][0]['public_id']}/evidence"
    assert client.get(url, headers=headers).status_code == 404
