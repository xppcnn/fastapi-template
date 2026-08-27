import asyncio
from collections.abc import AsyncGenerator, Generator
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
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
from app.models.rule_extraction import RuleExtractionRun, RuleExtractionRunStatus


@pytest.fixture
def env() -> Generator[tuple[TestClient, async_sessionmaker], None, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    asyncio.run(create_schema())
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app, base_url="https://testserver") as test_client:
            yield test_client, session_factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        asyncio.run(engine.dispose())


def _register(test_client: TestClient) -> tuple[str, UUID]:
    response = test_client.post(
        "/api/v1/auth/register",
        json={"email": "owner@example.com", "password": "correct horse battery staple"},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    return (
        data["access_token"],
        UUID(data["organization"]["public_id"]),
    )


def _register_second_org(test_client: TestClient) -> tuple[str, UUID]:
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner-b@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201
    data = response.json()["data"]
    return (
        data["access_token"],
        UUID(data["organization"]["public_id"]),
    )


def _seed_tender(
    session_factory, *, organization_public_id: UUID, parsed: bool = True
) -> dict[str, UUID]:
    async def seed() -> dict[str, UUID]:
        async with session_factory() as session:
            org = await session.scalar(
                select(Organization).where(
                    Organization.public_id == organization_public_id
                )
            )
            project = Project(organization_id=org.id, name="p")
            session.add(project)
            await session.flush()
            document = Document(
                organization_id=org.id,
                project_id=project.id,
                name="招标文件",
                doc_type=DocType.TENDER,
            )
            session.add(document)
            await session.flush()
            version = DocumentVersion(
                document_id=document.id,
                version_number=1,
                object_key="raw/t.pdf",
                file_name="t.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="0" * 64,
                parse_status=ParseStatus.PARSED if parsed else ParseStatus.UPLOADED,
            )
            session.add(version)
            await session.flush()
            document.active_version_id = version.id
            session.add_all(
                [
                    DocumentBlock(
                        version_id=version.id,
                        order_index=1,
                        block_type="section_header",
                        text="第一章",
                        page_no=1,
                    ),
                    DocumentBlock(
                        version_id=version.id,
                        order_index=2,
                        block_type="paragraph",
                        text="要求一",
                        page_no=1,
                    ),
                    DocumentBlock(
                        version_id=version.id,
                        order_index=3,
                        block_type="section_header",
                        text="第二章",
                        page_no=2,
                    ),
                    DocumentBlock(
                        version_id=version.id,
                        order_index=4,
                        block_type="paragraph",
                        text="要求二",
                        page_no=2,
                    ),
                ]
            )
            await session.commit()
            return {
                "project_id": project.public_id,
                "version_id": version.public_id,
            }

    return asyncio.run(seed())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_extract_returns_202_and_progress_polling(
    env: tuple[TestClient, async_sessionmaker],
) -> None:
    test_client, session_factory = env
    token, org_public_id = _register(test_client)
    seeded = _seed_tender(session_factory, organization_public_id=org_public_id)

    with patch(
        "app.tasks.rule_extraction.rule_extraction_submit",
    ) as submit:
        submit.delay.return_value = None
        response = test_client.post(
            f"/api/v1/projects/{seeded['project_id']}/rules/extract",
            json={"tender_version_id": str(seeded["version_id"])},
            headers=_auth(token),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["code"] == 200
        assert body["message"] == "ok"
        run_id = UUID(body["data"]["public_id"])
        assert body["data"]["status"] == "queued"
        assert body["data"]["total_batches"] == 2  # 两章 = 两批
        assert submit.delay.call_count == 1

        progress = test_client.get(
            f"/api/v1/projects/{seeded['project_id']}/rules/extract/{run_id}",
            headers=_auth(token),
        )
        assert progress.status_code == 200
        progress_body = progress.json()
        assert progress_body["data"]["public_id"] == str(run_id)
        assert progress_body["data"]["total_batches"] == 2
        assert progress_body["data"]["completed_batches"] == 0


def test_extract_rejects_unparsed_tender_version(
    env: tuple[TestClient, async_sessionmaker],
) -> None:
    test_client, session_factory = env
    token, org_public_id = _register(test_client)
    seeded = _seed_tender(
        session_factory, organization_public_id=org_public_id, parsed=False
    )

    with patch("app.tasks.rule_extraction.rule_extraction_submit") as submit:
        response = test_client.post(
            f"/api/v1/projects/{seeded['project_id']}/rules/extract",
            json={"tender_version_id": str(seeded["version_id"])},
            headers=_auth(token),
        )

        assert response.status_code == 400
        assert "尚未解析" in response.json()["message"]
        submit.delay.assert_not_called()


def test_extract_rejects_concurrent_running(
    env: tuple[TestClient, async_sessionmaker],
) -> None:
    test_client, session_factory = env
    token, org_public_id = _register(test_client)
    seeded = _seed_tender(session_factory, organization_public_id=org_public_id)

    with patch("app.tasks.rule_extraction.rule_extraction_submit") as submit:
        submit.delay.return_value = None
        first = test_client.post(
            f"/api/v1/projects/{seeded['project_id']}/rules/extract",
            json={"tender_version_id": str(seeded["version_id"])},
            headers=_auth(token),
        )
        assert first.status_code == 202

        second = test_client.post(
            f"/api/v1/projects/{seeded['project_id']}/rules/extract",
            json={"tender_version_id": str(seeded["version_id"])},
            headers=_auth(token),
        )

        assert second.status_code == 409
        assert "正在提取" in second.json()["message"]


def test_manual_rule_lifecycle(
    env: tuple[TestClient, async_sessionmaker],
) -> None:
    test_client, session_factory = env
    token, org_public_id = _register(test_client)
    seeded = _seed_tender(session_factory, organization_public_id=org_public_id)
    base = f"/api/v1/projects/{seeded['project_id']}"

    created = test_client.post(
        f"{base}/rules",
        json={
            "rule_type": "disqualification",
            "title": "保证金未到账",
            "description": "投标保证金未在截止时间前到账的,否决投标",
            "evaluation_method": "semantic",
        },
        headers=_auth(token),
    )
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["code"] == 200
    assert created_body["data"]["status"] == "draft"
    assert created_body["data"]["needs_review"] is False
    assert created_body["data"]["source_segment_ids"] == []
    rule_id = created_body["data"]["public_id"]

    listed = test_client.get(f"{base}/rules", headers=_auth(token))
    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["code"] == 200
    assert listed_body["data"]["total"] == 1
    assert listed_body["data"]["items"][0]["public_id"] == rule_id

    updated = test_client.patch(
        f"{base}/rules/{rule_id}",
        json={"title": "保证金未到账(修订)", "version": 1},
        headers=_auth(token),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["title"] == "保证金未到账(修订)"
    assert updated.json()["data"]["version"] == 2

    stale = test_client.patch(
        f"{base}/rules/{rule_id}",
        json={"title": "又改", "version": 1},
        headers=_auth(token),
    )
    assert stale.status_code == 409  # 乐观锁

    confirmed = test_client.post(
        f"{base}/rules/confirm",
        json={"rule_ids": [rule_id]},
        headers=_auth(token),
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"] == {"confirmed": 1, "pending": 0}

    after_confirm = test_client.patch(
        f"{base}/rules/{rule_id}",
        json={"title": "不能再改", "version": 2},
        headers=_auth(token),
    )
    assert after_confirm.status_code == 409  # confirmed 只读


def test_confirm_all_and_ignore(
    env: tuple[TestClient, async_sessionmaker],
) -> None:
    test_client, session_factory = env
    token, org_public_id = _register(test_client)
    seeded = _seed_tender(session_factory, organization_public_id=org_public_id)
    base = f"/api/v1/projects/{seeded['project_id']}"

    for i in range(2):
        assert (
            test_client.post(
                f"{base}/rules",
                json={
                    "rule_type": "response",
                    "title": f"要求{i}",
                    "description": f"须响应要求{i}",
                },
                headers=_auth(token),
            ).status_code
            == 201
        )
    ignore_me = test_client.post(
        f"{base}/rules",
        json={
            "rule_type": "response",
            "title": "忽略我",
            "description": "这条忽略",
        },
        headers=_auth(token),
    ).json()["data"]["public_id"]

    ignored = test_client.post(f"{base}/rules/{ignore_me}/ignore", headers=_auth(token))
    assert ignored.status_code == 200
    assert ignored.json()["data"]["status"] == "ignored"

    confirmed = test_client.post(f"{base}/rules/confirm", json={}, headers=_auth(token))
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["confirmed"] == 2  # ignored 不参与
    assert confirmed.json()["data"]["pending"] == 0


def test_extract_retry_requires_partial_or_failed(
    env: tuple[TestClient, async_sessionmaker],
) -> None:
    test_client, session_factory = env
    token, org_public_id = _register(test_client)
    seeded = _seed_tender(session_factory, organization_public_id=org_public_id)
    base = f"/api/v1/projects/{seeded['project_id']}"

    with patch("app.tasks.rule_extraction.rule_extraction_submit") as submit:
        submit.delay.return_value = None
        created = test_client.post(
            f"{base}/rules/extract",
            json={"tender_version_id": str(seeded["version_id"])},
            headers=_auth(token),
        ).json()["data"]
        run_id = created["public_id"]

        running_retry = test_client.post(
            f"{base}/rules/extract/{run_id}/retry", headers=_auth(token)
        )
        assert running_retry.status_code == 409  # queued 状态不可重试

        # 模拟 worker 把运行置为部分失败,再重试
        async def mark_partial() -> None:
            async with session_factory() as session:
                run = await session.scalar(
                    select(RuleExtractionRun).where(
                        RuleExtractionRun.public_id == UUID(run_id)
                    )
                )
                run.status = RuleExtractionRunStatus.PARTIAL
                run.failed_batches = 1
                await session.commit()

        asyncio.run(mark_partial())
        retried = test_client.post(
            f"{base}/rules/extract/{run_id}/retry", headers=_auth(token)
        )
        assert retried.status_code == 200
        retried_body = retried.json()
        assert retried_body["data"]["status"] == "queued"
        assert retried_body["data"]["public_id"] == run_id
        assert submit.delay.call_count == 2


def test_extract_requires_auth(env: tuple[TestClient, async_sessionmaker]) -> None:
    test_client, _ = env
    response = test_client.post("/api/v1/projects/x/rules/extract", json={})
    assert response.status_code == 401


def test_tenant_isolation_on_rules(
    env: tuple[TestClient, async_sessionmaker],
) -> None:
    """组织 A 无法读取/修改组织 B 的规则与提取任务,统一 404。"""
    test_client, session_factory = env
    token_a, _ = _register(test_client)

    token_b, org_b = _register_second_org(test_client)
    seeded_b = _seed_tender(session_factory, organization_public_id=org_b)
    base_b = f"/api/v1/projects/{seeded_b['project_id']}"
    created = test_client.post(
        f"{base_b}/rules",
        json={
            "rule_type": "response",
            "title": "B 的要求",
            "description": "仅 B 可见",
        },
        headers=_auth(token_b),
    ).json()["data"]
    rule_b = created["public_id"]

    listed = test_client.get(
        f"/api/v1/projects/{seeded_b['project_id']}/rules",
        headers=_auth(token_a),
    )
    assert listed.status_code == 404

    patched = test_client.patch(
        f"{base_b}/rules/{rule_b}",
        json={"title": "篡改", "version": 1},
        headers=_auth(token_a),
    )
    assert patched.status_code == 404

    run = test_client.post(
        f"{base_b}/rules/extract",
        json={"tender_version_id": str(seeded_b["version_id"])},
        headers=_auth(token_a),
    )
    assert run.status_code == 404


def test_openapi_rules_extract_uses_api_response(
    env: tuple[TestClient, async_sessionmaker],
) -> None:
    schema = app.openapi()
    path = "/api/v1/projects/{project_id}/rules/extract"
    post_schema = schema["paths"][path]["post"]
    assert "202" in post_schema["responses"]
    content = post_schema["responses"]["202"]["content"]["application/json"]
    ref = content["schema"]["$ref"]
    assert ref.endswith("ApiResponse_RuleExtractionRunResponse_")
    assert "extract/{run_id}/retry" in str(schema["paths"].keys())
