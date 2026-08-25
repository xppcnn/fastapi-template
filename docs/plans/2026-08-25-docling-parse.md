# Docling 文档解析实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use XWLPowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 FastAPI 通过独立的 docling-serve HTTP 服务解析上传的 PDF/DOCX 文档，产出 Markdown + DoclingDocument JSON 产物与结构化 blocks，供后续 LLM 审核使用。

**Architecture:** FastAPI 仅作为编排层——收到解析请求后原子认领版本状态(PARSING)，后台任务通过官方轻量客户端 `docling-slim[service-client]` 的 `AsyncDoclingServiceClient` 调用独立部署的 docling-serve(Local Engine，内置默认模型)，轮询任务结果后将产物写入 MinIO、blocks 写入 PG，最后更新 parse_status。解析库(含 PyTorch)完全不进入主服务依赖。

**Tech Stack:** fastapi、sqlalchemy[asyncio]、docling-slim[service-client](官方远程客户端，轻量)、minio、docling-serve(v1.21+，独立容器)、alembic、pytest+aiosqlite

---

## 已冻结的决策

| 决策点 | 结论 | 理由 |
|--------|------|------|
| 调用方式 | **官方 `AsyncDoclingServiceClient`**(docling-slim[service-client])，不手写 httpx | 官方将客户端拆为轻量包：仅 httpx/websockets/typer/rich/python-dotenv 等，无 torch/ML 依赖；自带重试、异常体系(404→TaskNotFoundError)、Job 封装与 WS/Polling watcher |
| 产物格式 | Markdown + DoclingDocument JSON 都存 | `client.submit()` 的 `ConversionResult.document` 是完整 DoclingDocument，`export_to_markdown()`/`export_to_dict()` 本地导出；产物各存一个 MinIO 对象 |
| 结果获取 | `target=InBodyTarget()` 显式指定 | 客户端默认 PresignedUrlTarget(失败才回退 InBody)；显式指定可确定性拿到内联 JSON 响应 |
| 状态监听 | `status_watcher=StatusWatcherKind.POLLING` | 默认是 WebSocket；Local 单节点轮询更简单可靠，与对账机制一致 |
| `partial_success` | 算 **parsed**，把 docling `errors` 摘要写入 `parse_error` | 内容已产出，只有局部缺图等；定为成功并保留警告 |
| 允许重解析 | 允许（uploaded/failed/parsed 均可触发；仅 parsing 拒绝 409） | 换模型/修 bug 后可重新跑，原子认领保证并发安全 |
| 部署形态 | docling-serve 独立容器(官方镜像，内置默认模型)，API key 保护 | 主服务零重型依赖；模型问题已由官方镜像解决 |
| OCR | `do_ocr=true`(ConvertDocumentsOptions 默认即 true；招投标扫描件多)，部署后需冒烟验证 OCR 模型是否内置 | models.md 明确额外模型需预下载，OCR 需实测 |
| `parse_job_id` | 类型 Integer → String(64)，语义改为 docling task_id | docling task_id 是 UUID 字符串 |

## 前置事实核对(已核实)

- `docling.service_client` 属于 **`docling-slim`** 包(pypi 元数据已核实)：`pip install "docling-slim[service-client]"` 只装 certifi/docling-core/filetype/pluggy/pydantic/pydantic-settings/requests/tqdm + httpx/websockets/typer/rich/python-dotenv，**无 torch、无 docling-ibm-models**(那些在 `models-local`/`standard`/`all` extras 里)。`docling-serve` 服务端 PyPI 包自身也依赖 `docling-slim[service-client]`——官方就是为"远程调用不装本地推理"设计的
- `AsyncDoclingServiceClient(url, api_key=..., status_watcher=..., job_timeout=..., poll_server_wait=..., http_retries=3, ...)` 为 async 上下文管理器，自动携带 `X-Api-Key` 与 `Accept-Docling-Document-Version` 协商头(服务端会按客户端 docling-core 版本降级输出，版本错开兼容)
- `client.submit(source, options=ConvertDocumentsOptions(...), target=InBodyTarget())` → `AsyncConversionJob`；`job.task_id` 提交后立即可得；`await job.poll(wait=...)` / `await job.result(timeout=...)`；queue 位置 `job.queue_position`
- 异常体系覆盖我方所有失败模式：`TaskNotFoundError`(serve 重启任务丢失 404)、`TaskTimeoutError`、`ServiceUnavailableError`、`ConversionError`
- `ConvertDocumentsOptions` 字段：`from_formats`、`to_formats`(默认 [markdown])、`do_ocr`(默认 true)、`table_mode`(默认 accurate)、`pdf_backend`(默认 DOCLING_PARSE)、`document_timeout`
- 官方镜像 `quay.io/docling-project/docling-serve` **内置默认模型**(layout + TableFormer)，标准管线生产无需下载模型；**不设** `DOCLING_SERVE_ARTIFACTS_PATH` 即可
- 一旦设置 `DOCLING_SERVE_ARTIFACTS_PATH`，连默认模型也改从该路径加载，卷里必须下全(layout/tableformer/OCR 模型)
- 启用增强能力(图片描述/公式等)的模型**不在镜像里且不会自动下载**，缺了直接报错
- docling-serve 配置用**环境变量**而非 CLI flag(uvicorn workers>1 时 CLI flag 失效)
- 任务状态是节点内存态(Local Engine)：serve 重启 → 任务丢失(poll 404)；API 重启 → poller 协程丢失
- 实测地址：`http://docling:5001`(docker 内网)或 `http://localhost:5001`(本机)

---

## Task 1: 配置项 + docling-slim 依赖

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/core/config.py`
- Test: `tests/test_config.py`

**Step 1: 写失败测试**

在 `tests/test_config.py` 追加：

```python
def test_docling_settings_defaults() -> None:
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.docling_serve_base_url == "http://localhost:5001"
    assert settings.docling_parse_timeout_minutes == 15
    assert settings.docling_do_ocr is True
    assert settings.docling_table_mode in ("fast", "accurate")
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL(AttributeError: docling_serve_base_url)

**Step 3: 实现**

添加依赖：

```bash
uv add "docling-slim[service-client]"
```

(httpx/websockets 由其传递引入；docling-core/pydantic 与项目现有 pydantic 2.x 兼容，docling-core `<3;>=2.91`。若 `uv add` 后 `uv run python -c "import docling, torch"` 能导入 torch 说明装错包了，检查 lock 文件。)

`app/core/config.py` 的 `Settings` 类追加：

```python
    docling_serve_base_url: str = "http://localhost:5001"
    docling_serve_api_key: str = ""
    docling_parse_timeout_minutes: int = 15
    docling_do_ocr: bool = True
    docling_table_mode: Literal["fast", "accurate"] = "fast"
```

**Step 4: 运行验证通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

Run: `uv run python -c "import docling; from docling.service_client import AsyncDoclingServiceClient; print(docling.__version__)"`
Expected: 打印 docling-slim 版本号

**Step 5: 提交**

```bash
git add pyproject.toml uv.lock app/core/config.py tests/test_config.py
git commit -m "feat(config): add docling-serve settings and slim client dep"
```

---

## Task 2: 修复 parse 端点路由缺 `/` 的 bug

**Files:**
- Modify: `app/api/v1/endpoints/projects.py:302`
- Test: `tests/test_document_parse_route.py`(新建)

**Step 1: 写失败测试**

创建 `tests/test_document_parse_route.py`：

```python
from app.main import app


def test_parse_route_path_is_correct() -> None:
    paths = [r.path for r in app.routes]
    assert "/api/v1/projects/{project_id}/documents/{document_id}/versions/{version_id}/parse" in paths
    assert not any("projects{project_id}" in p for p in paths)
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/test_document_parse_route.py -v`
Expected: FAIL(当前路由是 `/projects{project_id}/documents/.../parse`)

**Step 3: 实现**

`projects.py:302` 修改：

```python
@router.post(
    "/{project_id}/documents/{document_id}/versions/{version_id}/parse",
    response_model=ApiResponse[DocumentVersionResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def parse_project_document(
    project_id: UUID,
    document_id: UUID,
    version_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
):
    version = await parse_document(
        session,
        project_public_id=project_id,
        document_public_id=document_id,
        version_public_id=version_id,
        organization_id=principal.organization_id,
    )
    return ok(DocumentVersionResponse.model_validate(version))
```

**Step 4: 确认测试通过(路由正确)**

Run: `uv run pytest tests/test_document_parse_route.py -v`
Expected: PASS(style 上若 `parse_document` 未实现导致导入失败，本 Task 先只改路由字符串，body 留 `pass`，Task 6 再补服务实现)

**Step 5: 提交**

```bash
git add app/api/v1/endpoints/projects.py tests/test_document_parse_route.py
git commit -m "fix: correct parse route missing leading slash"
```

---

## Task 3: 模型变更 + Alembic 迁移

**Files:**
- Modify: `app/models/document.py`
- Create: `alembic/versions/xxxx_add_parse_columns_and_document_blocks.py`
- Test: `tests/test_models_document.py`(新建，参照 tests/test_base.py 风格)

**Step 1: 写失败测试**

创建 `tests/test_models_document.py`：

```python
from app.models.document import DocumentBlock, DocumentVersion


def test_document_version_parse_columns() -> None:
    cols = DocumentVersion.__table__.columns
    assert cols["parse_job_id"].type.python_type is str
    assert "parsed_object_key" in cols
    assert "parsed_markdown_object_key" in cols
    assert "parse_error" in cols
    assert "parsing_started_at" in cols


def test_document_block_columns() -> None:
    cols = DocumentBlock.__table__.columns
    assert "version_id" in cols
    assert "order_index" in cols
    assert "block_type" in cols
    assert "text" in cols
    assert "page_no" in cols
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/test_models_document.py -v`
Expected: FAIL(DocumentBlock 不存在 / parse_job_id 是 int)

**Step 3: 实现模型**

`app/models/document.py`：

- 顶部新增 import：`from sqlalchemy import Text`(在现有 import 里加)

- `DocumentVersion.parse_job_id` 改为：

```python
    parse_job_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="docling-serve 任务 id"
    )
```

- `DocumentVersion` 增加：

```python
    parsed_object_key: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="解析产物(DoclingDocument JSON)对象键"
    )
    parsed_markdown_object_key: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="解析产物(Markdown)对象键"
    )
    parse_error: Mapped[str | None] = mapped_column(
        String(2000), nullable=True, comment="解析失败原因或 partial_success 警告"
    )
    parsing_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

- 新增 `DocumentBlock` 模型(DocumentVersion 之后)：

```python
class DocumentBlock(Base):
    __tablename__ = "document_blocks"

    __table_args__ = (
        UniqueConstraint("version_id", "order_index", name="uq_document_blocks_version_order"),
    )

    version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(50), comment="text / table / title / ...")
    text: Mapped[str] = mapped_column(Text)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    version: Mapped[DocumentVersion] = relationship(back_populates="blocks")
```

- `DocumentVersion` 增加 relationship：

```python
    blocks: Mapped[list[DocumentBlock]] = relationship(
        back_populates="version", passive_deletes=True
    )
```

**Step 4: 生成迁移(autogenerate，不手写)**

先确保本地数据库已升级到最新迁移，再让 alembic 根据模型元数据对比生成：

```bash
uv run alembic upgrade head          # 确保 schema 与代码模型一致
uv run alembic revision --autogenerate -m "add parse columns and document_blocks"
```

Expected: 生成一个新迁移文件(如 `alembic/versions/xxxx_add_parse_columns_and_document_blocks.py`)，内容含 document_blocks 建表与 5 个新列。

(env.py 已配置 `target_metadata = Base.metadata`，autogenerate 可用。)

**Step 5: 人工审查生成的迁移文件(不手写主体，只补漏项)**

⚠️ **重要**：`alembic/env.py` 未开启 `compare_type=True`，autogenerate **不会检测** `parse_job_id` 的 Integer→String(64) 类型变更，必须手动补这一条。其余按审查清单核对：

- [ ] `parse_job_id`:手动补 `op.alter_column("document_versions", "parse_job_id", existing_type=sa.Integer(), type_=sa.String(64), existing_nullable=True, postgresql_using="parse_job_id::text")`(类型变更 autogenerate 检测不到)
- [ ] 5 个新列已生成：`parsed_object_key` / `parsed_markdown_object_key` / `parse_error` / `parsing_started_at`(加 `parsed_at` 若模型已含)
- [ ] `document_blocks` 表已生成，且含：`version_id` FK(ondelete=CASCADE)+ 索引、`order_index`、`block_type`、`text`、`page_no`、`public_id` 唯一索引、`UniqueConstraint("version_id", "order_index")`
- [ ] 若 autogenerate 把 `parse_job_id` 只当成 drop/add 列处理(而非 alter),把该段改为上方 `op.alter_column` 写法(避免丢旧列注释)
- [ ] downgrade() 与之对称

> 如项目里传了 `--autogenerate` 生成的迁移风格，以生成物为准；仅当命名/约束不符合项目既有迁移风格(a34eb0615892 的 `sa.BigInteger().with_variant(sa.Integer(), "sqlite")` 等)时轻微调整。

(参考上文的手写版迁移内容即为"预期生成物"的对照样例——若 autogenerate 输出与该样例差异明显，以样例为准微调。)

**Step 6: 应用迁移 + 校验**

Run: `uv run alembic upgrade head`
Expected: 无报错

Run: `uv run alembic check`
Expected: "No new upgrade operations detected"——确认模型与 DB 已同步

**Step 6: 跑测试 + 提交**

Run: `uv run pytest tests/test_models_document.py -v`
Expected: PASS

```bash
git add app/models/document.py tests/test_models_document.py alembic/versions/
git commit -m "feat: add parse columns and document_blocks table"
```

---

## Task 4: docling-slim 客户端适配层

不手写 httpx——直接用官方 `AsyncDoclingServiceClient`，只包一个薄适配层把「settings + ConversionResult」翻译成项目内类型，方便测试 mock。

**Files:**
- Create: `app/core/docling_service.py`
- Test: `tests/core/test_docling_service.py`(新建)

**Step 1: 写失败测试**

创建 `tests/core/test_docling_service.py`(用 fake job/client，不依赖真实服务)：

```python
import asyncio

from app.core.config import get_settings
from app.core.docling_service import (
    build_convert_options,
    ParsedConversion,
    to_parsed_conversion,
)


class FakeJob:
    task_id = "task-1"

    async def result(self, timeout=None):
        return _fake_result()


def _fake_result():
    class FakeDoc:
        def export_to_markdown(self) -> str:
            return "# Title"
        def export_to_dict(self) -> dict:
            return {"texts": [{"text": "x", "label": "paragraph", "prov": [{"page_no": 1}]}]}
    class FakeResult:
        status = "success"
        errors = []
        document = FakeDoc()
    return FakeResult()


async def test_build_convert_options_from_settings() -> None:
    options = build_convert_options(get_settings())
    assert options.do_ocr is True
    assert options.table_mode == "fast"
    assert len(options.to_formats) == 2  # markdown + json


async def test_to_parsed_conversion_maps_result() -> None:
    result = to_parsed_conversion(await FakeJob().result())
    assert result.markdown == "# Title"
    assert result.document_json["texts"][0]["text"] == "x"
    assert result.status == "success"
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/core/test_docling_service.py -v`
Expected: FAIL(ImportError: app.core.docling_service 不存在)

**Step 3: 实现**

创建 `app/core/docling_service.py`：

```python
from dataclasses import dataclass

from docling.datamodel.service.options import ConvertDocumentsOptions
from docling.datamodel.service.targets import InBodyTarget
from docling.service_client import (
    AsyncDoclingServiceClient,
    StatusWatcherKind,
)
from docling.service_client.job import AsyncConversionJob

from app.core.config import Settings


@dataclass(slots=True)
class ParsedConversion:
    status: str  # success / partial_success / ...
    markdown: str
    document_json: dict
    errors: list[str]


def build_convert_options(settings: Settings) -> ConvertDocumentsOptions:
    return ConvertDocumentsOptions(
        from_formats=[],  # 不预筛,交给服务端按文件类型
        to_formats=["md", "json"],
        do_ocr=settings.docling_do_ocr,
        table_mode=settings.docling_table_mode,
    )


def open_client(settings: Settings) -> AsyncDoclingServiceClient:
    """返回可 async with 的官方客户端;job 超时 = 配置的解析超时。"""
    return AsyncDoclingServiceClient(
        url=settings.docling_serve_base_url,
        api_key=settings.docling_serve_api_key,
        status_watcher=StatusWatcherKind.POLLING,
        job_timeout=settings.docling_parse_timeout_minutes * 60.0,
        http_connect_timeout=10.0,
        http_read_timeout=600.0,
    )


def submit_document(
    client: AsyncDoclingServiceClient,
    *,
    file_path: str,
    file_name: str,
    settings: Settings,
) -> "AsyncConversionJob":
    """提交文件转换任务(上传 multipart),返回 Job(task_id 立即可用)。"""
    from pathlib import Path

    return client.submit(
        source=Path(file_path),
        options=build_convert_options(settings),
        target=InBodyTarget(),
    )


def to_parsed_conversion(
    conversion_result,
) -> ParsedConversion:
    """把官方 ConversionResult 映射为项目内类型。"""
    document = conversion_result.document
    return ParsedConversion(
        status=getattr(conversion_result.status, "value", str(conversion_result.status)),
        markdown=document.export_to_markdown(),
        document_json=document.export_to_dict(),
        errors=[
            getattr(e, "error_message", None) or str(e)
            for e in (conversion_result.errors or [])
        ],
    )
```

**Step 4: 运行验证通过**

Run: `uv run pytest tests/core/test_docling_service.py -v`
Expected: PASS

**Step 5: 提交**

```bash
git add app/core/docling_service.py tests/core/test_docling_service.py
git commit -m "feat: add docling-slim client adapter"
```

---

## Task 5: 原子认领解析状态

**Files:**
- Modify: `app/repositories/documents.py`
- Test: `tests/repositories/test_documents_claim.py`(新建)

**Step 1: 写失败测试**

创建 `tests/repositories/test_documents_claim.py`(参照 tests/auth/test_services.py 的 sqlite session_factory 模式)：

```python
import asyncio
from collections.abc import Generator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.document import DocumentVersion, ParseStatus
from app.repositories.documents import claim_version_for_parsing


@pytest.fixture
def session_factory() -> Generator[async_sessionmaker, None, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def create_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    asyncio.run(engine.dispose())


def test_claim_sets_parsing_and_guards_reentry(session_factory: async_sessionmaker) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1,
                version_number=1,
                object_key="k",
                file_name="a.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="0" * 64,
            )
            session.add(version)
            await session.flush()

            claimed = await claim_version_for_parsing(session, version_id=version.id)
            assert claimed is True
            assert version.parse_status == ParseStatus.PARSING
            assert version.parsing_started_at is not None

            again = await claim_version_for_parsing(session, version_id=version.id)
            assert again is False

    asyncio.run(run())
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/repositories/test_documents_claim.py -v`
Expected: FAIL(ImportError: claim_version_for_parsing)

**Step 3: 实现**

`app/repositories/documents.py` 顶部 import 增加 `update`，并导入 `utcnow_naive`、`ParseStatus`：

```python
from sqlalchemy import false, func, select, update

from app.core.object_storage import utcnow_naive
from app.models.document import ParseStatus
```

追加函数：

```python
async def claim_version_for_parsing(
    session: AsyncSession, *, version_id: int
) -> bool:
    """原子认领版本进入 PARSING 状态；已在解析(或已删除)返回 False。"""
    stmt = (
        update(DocumentVersion)
        .where(
            DocumentVersion.id == version_id,
            DocumentVersion.parse_status != ParseStatus.PARSING,
            DocumentVersion.is_deleted == false(),
        )
        .values(
            parse_status=ParseStatus.PARSING,
            parsing_started_at=utcnow_naive(),
            parse_error=None,
        )
    )
    result = await session.execute(stmt)
    return result.rowcount == 1
```

**Step 4: 运行验证通过**

Run: `uv run pytest tests/repositories/test_documents_claim.py -v`
Expected: PASS

**Step 5: 提交**

```bash
git add app/repositories/documents.py tests/repositories/test_documents_claim.py
git commit -m "feat: atomic claim version for parsing"
```

---

## Task 6: 解析编排服务(parse_document + 后台执行)

**Files:**
- Modify: `app/services/documents.py`(parse_document 补全)
- Create: `app/services/parsing.py`(后台执行 + blocks 提取)
- Test: `tests/services/test_parsing.py`(新建)

**Step 1: 写失败测试**

创建 `tests/services/test_parsing.py`，mock 客户端与 MinIO，验证状态流转：

```python
import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.document import DocumentBlock, DocumentVersion, ParseStatus
from app.services.parsing import extract_blocks, run_parse


@pytest.fixture
def session_factory() -> Generator[async_sessionmaker, None, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def create_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    asyncio.run(engine.dispose())


SAMPLE_DOCLING_JSON = {
    "texts": [
        {"text": "第一章 招标公告", "label": "title", "prov": [{"page_no": 1}]},
        {"text": "本项目为示例。", "label": "paragraph", "prov": [{"page_no": 1}]},
        {"text": "预算：100万", "label": "paragraph", "prov": [{"page_no": 2}]},
    ],
    "tables": [
        {
            "label": "table",
            "prov": [{"page_no": 2}],
            "data": {"html": "<table><tr><td>a</td></tr></table>"},
        }
    ],
}


def test_extract_blocks_from_docling_json() -> None:
    blocks = extract_blocks(SAMPLE_DOCLING_JSON)
    assert len(blocks) == 4
    assert blocks[0]["block_type"] == "title"
    assert blocks[0]["page_no"] == 1
    assert blocks[3]["block_type"] == "table"
    assert "<table>" in blocks[3]["text"]


def test_run_parse_success_persists_blocks_and_status(session_factory: async_sessionmaker) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1,
                version_number=1,
                object_key="raw/x/a.pdf",
                file_name="a.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="0" * 64,
                parse_status=ParseStatus.PARSING,
            )
            session.add(version)
            await session.flush()
            version_id = version.id

        class FakeDoc:
            def export_to_markdown(self) -> str:
                return "# 第一章 招标公告"
            def export_to_dict(self) -> dict:
                return SAMPLE_DOCLING_JSON

        class FakeJob:
            task_id = "task-1"
            async def result(self, timeout=None):
                return type("R", (), {"status": "success", "errors": [], "document": FakeDoc()})()

        class FakeClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return None
            def submit(self, source, options=None, target=None):
                return FakeJob()

        with (
            patch("app.services.parsing.fget_object", new=AsyncMock(return_value=None)),
            patch("app.services.parsing.put_object", new=AsyncMock(return_value=object())),
            patch("app.services.parsing.open_client", return_value=FakeClient()),
        ):
            await run_parse(version_id)

        async with session_factory() as session:
            version = await session.get(DocumentVersion, version_id)
            assert version.parse_status == ParseStatus.PARSED
            assert version.parsed_at is not None
            assert version.parsed_object_key is not None
            assert version.parsed_markdown_object_key is not None

            blocks = (await session.execute(
                __import__("sqlalchemy").select(DocumentBlock)
            )).scalars().all()
            assert len(blocks) == 4

    asyncio.run(run())


def test_run_parse_failure_marks_failed(session_factory: async_sessionmaker) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1,
                version_number=1,
                object_key="raw/x/b.pdf",
                file_name="b.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="0" * 64,
                parse_status=ParseStatus.PARSING,
            )
            session.add(version)
            await session.flush()
            version_id = version.id

        class Boom:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return None
            def submit(self, source, options=None, target=None):
                raise RuntimeError("server down")

        with (
            patch("app.services.parsing.fget_object", new=AsyncMock(return_value=None)),
            patch("app.services.parsing.open_client", return_value=Boom()),
        ):
            await run_parse(version_id)

        async with session_factory() as session:
            version = await session.get(DocumentVersion, version_id)
            assert version.parse_status == ParseStatus.FAILED
            assert "server down" in version.parse_error

    asyncio.run(run())
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/services/test_parsing.py -v`
Expected: FAIL(ImportError: app.services.parsing 不存在)

**Step 3: 实现**

创建 `app/services/parsing.py`：

```python
import asyncio
import json
import tempfile
from datetime import timedelta
from pathlib import Path

import structlog
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.core.docling_service import (
    open_client,
    ParsedConversion,
    submit_document,
    to_parsed_conversion,
)
from app.core.object_storage import (
    fget_object,
    generate_object_key,
    put_object,
    utcnow_naive,
)
from app.models.document import DocumentBlock, DocumentVersion, ParseStatus
from app.repositories.documents import get_version_by_id

logger = structlog.get_logger(__name__)

_background_tasks: set[asyncio.Task] = set()


class ParseError(Exception):
    """解析失败(可转 parse_error 落库)。"""


def extract_blocks(document_json: dict) -> list[dict]:
    """把 DoclingDocument JSON 抽成 blocks(顺序:先文本后表格,各按文档内顺序)。"""
    blocks: list[dict] = []
    index = 0
    for item in document_json.get("texts") or []:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        index += 1
        page_no = ((item.get("prov") or [{}])[0] or {}).get("page_no")
        blocks.append(
            {
                "order_index": index,
                "block_type": item.get("label") or "text",
                "text": text,
                "page_no": page_no,
            }
        )
    for item in document_json.get("tables") or []:
        data = item.get("data") or {}
        index += 1
        page_no = ((item.get("prov") or [{}])[0] or {}).get("page_no")
        blocks.append(
            {
                "order_index": index,
                "block_type": "table",
                "text": data.get("html") or "",
                "page_no": page_no,
            }
        )
    return blocks


def spawn_parse(version_id: int) -> None:
    task = asyncio.create_task(run_parse(version_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def run_parse(version_id: int) -> None:
    """后台任务:下载原文件 → 提交 docling → 等待结果 → 写产物与状态。"""
    settings = get_settings()
    try:
        await _run_parse(version_id, settings)
    except Exception as exc:  # noqa: BLE001 - 后台任务,任何异常都落库为 failed
        logger.exception("parse_failed", version_id=version_id, error=str(exc))
        await _mark_failed(version_id, str(exc)[:2000])


async def _run_parse(version_id: int, settings) -> None:
    async with async_session_factory() as session:
        version = await get_version_by_id(session, version_id=version_id)
        if version is None or version.parse_status != ParseStatus.PARSING:
            return
        file_name = version.file_name
        object_key = version.object_key
        public_id = version.public_id

    async with open_client(settings) as client:
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir) / file_name
            await fget_object(object_key, str(local_path))
            job = await submit_document(
                client,
                file_path=str(local_path),
                file_name=file_name,
                settings=settings,
            )

        async with async_session_factory() as session:
            version = await get_version_by_id(session, version_id=version_id)
            version.parse_job_id = job.task_id
            await session.commit()

        await _await_and_persist(version_id, public_id, job, settings)


async def _await_and_persist(
    version_id: int,
    version_public_id,
    job,
    settings,
) -> None:
    """等待 Job 终态,把产物写 MinIO + blocks 入库 + 状态回写。"""
    timeout_seconds = settings.docling_parse_timeout_minutes * 60.0
    conversion = await job.result(timeout=timeout_seconds)
    parsed = to_parsed_conversion(conversion)

    if parsed.status not in ("success", "partial_success"):
        raise ParseError(f"docling 转换失败: status={parsed.status}")

    md_key = generate_object_key(f"parsed/{version_public_id}", "parsed.md")
    json_key = generate_object_key(f"parsed/{version_public_id}", "parsed.json")

    md_bytes = parsed.markdown.encode("utf-8")
    await put_object(md_key, md_bytes, len(md_bytes), content_type="text/markdown")
    json_bytes = json.dumps(parsed.document_json, ensure_ascii=False).encode("utf-8")
    await put_object(json_key, json_bytes, len(json_bytes), content_type="application/json")

    blocks = extract_blocks(parsed.document_json)
    partial_note = "; ".join(parsed.errors)[:2000] if parsed.errors else None

    async with async_session_factory() as session:
        version = await get_version_by_id(session, version_id=version_id)
        if version is None:
            return
        version.parse_status = ParseStatus.PARSED
        version.parsed_object_key = json_key
        version.parsed_markdown_object_key = md_key
        version.parsed_at = utcnow_naive()
        version.parse_error = (
            partial_note if parsed.status == "partial_success" else None
        )
        await session.execute(delete(DocumentBlock).where(
            DocumentBlock.version_id == version_id
        ))
        for block in blocks:
            session.add(DocumentBlock(version_id=version_id, **block))
        await session.commit()


async def _mark_failed(version_id: int, message: str) -> None:
    async with async_session_factory() as session:
        version = await get_version_by_id(session, version_id=version_id)
        if version is None:
            return
        version.parse_status = ParseStatus.FAILED
        version.parse_error = message
        await session.commit()
```

修正 `app/services/documents.py` 的 `parse_document`(同时完成 Task 2 的悬空调用)：

```python
async def parse_document(
    session: AsyncSession,
    *,
    project_public_id: UUID,
    document_public_id: UUID,
    version_public_id: UUID,
    organization_id: int,
):
    document = await _require_document(
        session,
        organization_id=organization_id,
        project_public_id=project_public_id,
        document_public_id=document_public_id,
    )
    version = await get_document_version(
        session, document=document, version_id=version_public_id
    )
    if version is None:
        raise AppError("当前版本不存在", code=404)
    claimed = await claim_version_for_parsing(session, version_id=version.id)
    if not claimed:
        raise AppError("当前版本正在解析", code=409)
    await session.commit()
    spawn_parse(version.id)
    return version
```

新增 import：

```python
from app.repositories.documents import claim_version_for_parsing
from app.services.parsing import spawn_parse
```

**Step 4: 运行验证通过**

Run: `uv run pytest tests/services/test_parsing.py -v`
Expected: PASS

Run: `uv run pytest tests/repositories/test_documents_claim.py tests/core/test_docling_service.py -v`
Expected: PASS(回归)

**Step 5: 提交**

```bash
git add app/services/parsing.py app/services/documents.py tests/services/test_parsing.py
git commit -m "feat: implement parse orchestration with docling-serve"
```

---

## Task 7: 启动对账 + 端点接线

**Files:**
- Modify: `app/main.py`(lifespan 对账)
- Modify: `app/services/parsing.py`(resume 函数)
- Test: `tests/services/test_parsing_resume.py`(新建)

**Step 1: 写失败测试**

创建 `tests/services/test_parsing_resume.py`(复用 session_factory fixture 模式)：

```python
def test_resume_awaits_inflight_job(session_factory) -> None:
    """status=parsing 且有 task_id → 对账捞出来,续跑轮询。"""
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1, version_number=1, object_key="raw/a.pdf",
                file_name="a.pdf", content_type="application/pdf",
                size_bytes=1, sha256="0" * 64,
                parse_status=ParseStatus.PARSING,
                parse_job_id="t1",
            )
            session.add(version)
            await session.flush()
            version_id = version.id

        from app.services.parsing import find_stuck_parsing_versions
        async with session_factory() as session:
            ids = await find_stuck_parsing_versions(session)
            assert version_id in ids

    asyncio.run(run())
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/services/test_parsing_resume.py -v`
Expected: FAIL(ImportError)

**Step 3: 实现**

`app/services/parsing.py` 追加：

```python
async def find_stuck_parsing_versions(session) -> list[int]:
    """启动对账:捞所有 parse_status=PARSING 的版本 id。"""
    rows = await session.scalars(
        select(DocumentVersion.id).where(
            DocumentVersion.parse_status == ParseStatus.PARSING
        )
    )
    return list(rows)
```

`app/main.py` lifespan 增加对账(用于 resume 的后台任务封装,复用 `_background_tasks`):

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import async_session_factory, engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware
from app.services.parsing import find_stuck_parsing_versions, resume_parse

settings = get_settings()
configure_logging(settings.log_level, json_output=settings.json_logs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with async_session_factory() as session:
        stuck = await find_stuck_parsing_versions(session)
    for version_id in stuck:
        resume_parse(version_id)
    yield
    await engine.dispose()
```

`find_stuck_parsing_versions` 捞出的版本，无论 task_id 有无都走 `resume_parse`(有 task_id → 继续轮询该任务；无 → 直接标 failed 提示重新触发)：

```python
def resume_parse(version_id: int) -> None:
    """对账续跑:任务在 serve 侧还活着就等它完成。"""
    task = asyncio.create_task(_resume_parse(version_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _resume_parse(version_id: int) -> None:
    settings = get_settings()
    try:
        async with async_session_factory() as session:
            version = await get_version_by_id(session, version_id=version_id)
            if version is None or version.parse_status != ParseStatus.PARSING:
                return
            task_id = version.parse_job_id
            public_id = version.public_id
        if task_id is None:
            raise ParseError("解析任务 id 缺失，请重新触发解析")

        # 通过 client 重新构造一个只做等待+取结果的 job 引用:
        # AsyncConversionJob 需要 handlers,这里改用原生 client 的轮询接口。
        async with open_client(settings) as client:
            from docling.service_client import AsyncConversionJob  # noqa: F401
            # 更简单可靠:直接用 submit 幂等性?不——docling 无重传接口,
            # 采用"用 task_id 重建 Job"不可行时,降级为直接轮询 task:
            # 见下方 _resume_then_persist 实现(走 client._poll_task_status + fetch_result)
            await _resume_then_persist(version_id, public_id, task_id, settings)
    except Exception as exc:  # noqa: BLE001
        logger.exception("resume_parse_failed", version_id=version_id, error=str(exc))
        await _mark_failed(version_id, str(exc)[:2000])
```

(注：docling-slim 的 `AsyncConversionJob` 只能在 submit 时构造，对已提交任务续跑，官方策略是直接调用 `client._poll_task_status` 与 `client._fetch_convert_result` 私有方法——计划中 `_resume_then_persist` 封装这一逻辑；若版本升级后 API 变化，以回归测试为准。替代方案：对账时改为「直接透传 task_id 轮询 + GET /v1/result」两条原生请求，此路径在 Task 4 适配层已预留 poll/fetch 能力。)

`_resume_then_persist` 实现同 `_await_and_persist` 但基于原生轮询接口(轮询至 success/failure/超时后,复用 `_await_and_persist` 前段逻辑抽取的 `_persist`)。实施时把 `_await_and_persist` 拆为「轮询至终态 + 持久化」两段,resume 只复用持久化段。

**Step 4: 运行验证通过**

Run: `uv run pytest tests/services/test_parsing_resume.py -v`
Expected: PASS

Run: `uv run pytest tests/test_health.py -v`
Expected: PASS(确认 lifespan 改动不破坏应用启动)

**Step 5: 提交**

```bash
git add app/services/parsing.py app/main.py tests/services/test_parsing_resume.py
git commit -m "feat: reconcile stuck parsing versions on startup"
```

---

## Task 8: GET parsed 产物端点

**Files:**
- Modify: `app/services/documents.py`(parsed_result 服务)
- Modify: `app/api/v1/endpoints/projects.py`
- Modify: `app/schemas/document.py`
- Test: `tests/services/test_parsed_result.py`(新建)

**Step 1: 写失败测试**

创建 `tests/services/test_parsed_result.py`：

```python
import asyncio
from collections.abc import Generator
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.exceptions import AppError
from app.models import Base
from app.models.document import DocumentVersion, ParseStatus


@pytest.fixture
def session_factory() -> Generator[async_sessionmaker, None, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def create_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    asyncio.run(engine.dispose())


def test_parsed_result_returns_presigned_urls(session_factory: async_sessionmaker) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1, version_number=1, object_key="raw/a.pdf",
                file_name="a.pdf", content_type="application/pdf",
                size_bytes=1, sha256="0" * 64,
                parse_status=ParseStatus.PARSED,
                parsed_markdown_object_key="parsed/x/parsed.md",
                parsed_object_key="parsed/x/parsed.json",
            )
            session.add(version)
            await session.flush()

            from app.services.documents import parsed_result_urls
            with patch(
                "app.services.documents.presigned_get_url",
                side_effect=lambda key: f"http://minio/{key}?sig=abc",
            ):
                md_url, json_url = await parsed_result_urls(
                    session, version_id=version.id
                )

        assert md_url == "http://minio/parsed/x/parsed.md?sig=abc"
        assert json_url == "http://minio/parsed/x/parsed.json?sig=abc"
    asyncio.run(run())


def test_parsed_result_unparsed_raises(session_factory: async_sessionmaker) -> None:
    async def run() -> None:
        async with session_factory() as session:
            version = DocumentVersion(
                document_id=1, version_number=1, object_key="raw/a.pdf",
                file_name="a.pdf", content_type="application/pdf",
                size_bytes=1, sha256="0" * 64,
                parse_status=ParseStatus.UPLOADED,
            )
            session.add(version)
            await session.flush()
            from app.services.documents import parsed_result_urls
            try:
                await parsed_result_urls(session, version_id=version.id)
            except AppError as exc:
                assert exc.code == 400
                return
            raise AssertionError("expected AppError 400")
    asyncio.run(run())
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/services/test_parsed_result.py -v`
Expected: FAIL(ImportError)

**Step 3: 实现**

`app/services/documents.py` 追加：

```python
async def parsed_result_urls(session, *, version_id: int) -> tuple[str, str]:
    version = await get_version_by_id(session, version_id=version_id)
    if version is None:
        raise AppError("版本不存在", code=404)
    if version.parse_status != ParseStatus.PARSED:
        raise AppError("版本尚未解析完成", code=400)
    if not version.parsed_markdown_object_key or not version.parsed_object_key:
        raise AppError("解析产物缺失", code=409)
    md_url = presigned_get_url(version.parsed_markdown_object_key)
    json_url = presigned_get_url(version.parsed_object_key)
    return md_url, json_url
```

(顶部 import 补充 `presigned_get_url`、`get_version_by_id`。)

`app/schemas/document.py` 追加：

```python
class ParsedResultResponse(BaseModel):
    markdown_url: str
    json_url: str
    parse_error: str | None = None
```

`app/api/v1/endpoints/projects.py` 追加端点(复用 `_require_document` 的归属校验，先经服务层取 version 实体)：

```python
@router.get(
    "/{project_id}/documents/{document_id}/versions/{version_id}/parsed",
    response_model=ApiResponse[ParsedResultResponse],
)
async def parsed_project_document(
    project_id: UUID,
    document_id: UUID,
    version_id: UUID,
    principal: CurrentPrincipalDep,
    session: DbSession,
):
    document = await document_detail(
        session,
        project_public_id=project_id,
        document_public_id=document_id,
        organization_id=principal.organization_id,
    )
    version = await get_document_version_for_parsed(session, document=document, version_id=version_id)
    md_url, json_url = await parsed_result_urls(session, version_id=version.id)
    return ok(
        ParsedResultResponse(
            markdown_url=md_url,
            json_url=json_url,
            parse_error=version.parse_error,
        )
    )
```

(注:实施时在服务层补一个 `get_document_version_for_parsed(session, document, version_id)` 或直接复用 `get_document_version` + 404 处理,确保版本归属文档。)

**Step 4: 运行验证通过**

Run: `uv run pytest tests/services/test_parsed_result.py -v`
Expected: PASS

Run: `uv run ruff check app/`
Expected: 无错误

**Step 5: 提交**

```bash
git add app/services/documents.py app/schemas/document.py app/api/v1/endpoints/projects.py tests/services/test_parsed_result.py
git commit -m "feat: add parsed result endpoint with presigned urls"
```

---

## Task 9: 部署编排 docker-compose

**Files:**
- Create: `docker-compose.docling.yml`
- Create: `docs/deployment/docling-serve.md`

**Step 1: 写部署冒烟清单**

在 `docs/deployment/docling-serve.md`(新建)中写部署清单(无自动测试，人工冒烟)：

```
1. 启动:docker compose -f docker-compose.docling.yml up -d
2. 健康检查:curl -s http://localhost:5001/health 预期 200
3. 示例 PDF 冒烟:先 `uv run python -c "from docling... "` 或用 curl:
   curl -X POST http://localhost:5001/v1/convert/file/async \
     -H "X-Api-Key: <key>" -F "files=@sample.pdf" \
     -F "to_formats=md" -F "to_formats=json" -F "do_ocr=true"
   记录 task_id → 轮询 → 取结果
4. 扫描件冒烟:确认 OCR 模型可用(若报缺模型,按 models.md 预下载挂 DOCLING_SERVE_ARTIFACTS_PATH)
5. 用真实客户端冒烟:uv run python scripts/smoke_docling.py(见 Task 10)
```

**Step 2: 实现**

创建 `docker-compose.docling.yml`：

```yaml
services:
  docling:
    image: quay.io/docling-project/docling-serve
    container_name: docling-serve
    restart: unless-stopped
    ports:
      - "5001:5001"
    environment:
      DOCLING_SERVE_API_KEY: ${DOCLING_API_KEY:?set DOCLING_API_KEY in .env}
      DOCLING_DEVICE: cpu
      DOCLING_NUM_THREADS: 4
      DOCLING_SERVE_ENG_LOC_NUM_WORKERS: 2
      DOCLING_SERVE_MAX_FILE_SIZE: 52428800   # 50MB,与上传限制对齐
      DOCLING_SERVE_MAX_NUM_PAGES: 500
```

**Step 3: 冒烟验证**

Run: `docker compose -f docker-compose.docling.yml config`
Expected: 校验通过

Run(如有 docker):`docker compose -f docker-compose.docling.yml up -d` + 按 Step 1 清单冒烟
Expected: /docs 可访问；示例转换任务从 pending → success

**Step 4: 提交**

```bash
git add docker-compose.docling.yml docs/deployment/docling-serve.md
git commit -m "docs: add docling-serve deployment compose"
```

---

## Task 10: 端到端测试 + 质量门禁 + 手动冒烟

**Files:**
- Create: `scripts/smoke_docling.py`(可选)
- Modify: `.env.example`(若有)增加 docling 配置

**Step 1: 全量测试**

Run: `uv run pytest -v`
Expected: 全部 PASS

**Step 2: lint + 类型检查**

Run: `uv run ruff check app/ tests/`
Expected: 无错误

Run: `uv run ruff format --check app/ tests/`
Expected: 无 diff

Run: `uv run mypy app/`
Expected: 无错误(若项目 mypy 配置覆盖 tests 则一并跑)

**Step 3: 真实环境冒烟(需要 docker + docling 容器)**

1. `uv run alembic upgrade head`(跑迁移)
2. 起 docling 容器:`docker compose -f docker-compose.docling.yml up -d`
3. 启动应用,上传一个 PDF → complete → `POST .../parse`(预期 202 + parse_status=parsing)
4. 轮询版本列表,等待 parse_status=parsed(约数秒~数十秒)
5. `GET .../versions/{version_id}/parsed` → 打开 markdown_url 验证内容;查 `document_blocks` 表确认 blocks 已入库
6. 触发重解析,确认原子认领 409 生效(连续两次调用第二次 409)
7. 停掉 docling 容器,再触发解析,确认 parse_status 最终变成 failed、parse_error 可读

**Step 4: 修余问题**

若冒烟发现 `to_formats`/`table_mode` 等服务端版本与客户端 schema 不兼容(报 ValidationError)，比对 `docling-slim` 与 `docling-serve` 镜像 tag 版本，对齐到同一发布版本后重试。

**Step 5: 提交**

```bash
git add -A
git commit -m "test: end-to-end verification of docling parse flow"
```

---

## 实施顺序速查

| Task | 内容 | 依赖 |
|------|------|------|
| 1 | 配置 + docling-slim 依赖 | — |
| 2 | 修路由 bug | — |
| 3 | 模型 + 迁移 | — |
| 4 | docling 客户端适配层 | 1 |
| 5 | 原子认领 | 3 |
| 6 | 解析编排 | 3,4,5 |
| 7 | 启动对账 | 6 |
| 8 | parsed 端点 | 3,6 |
| 9 | docker-compose | — |
| 10 | 质量门禁 + 冒烟 | 全部 |

## 不在本计划范围

- `document_blocks` 的 LLM 审核消费(下游独立功能)
- docling-serve 的 RQ/Redis 与 GPU 扩容(阶段 2/3，已预留 `DOCLING_SERVE_ENG_KIND` 配置项，不动业务代码)
- blocks 分页查询 API(做 LLM 审核时按需加)

## 已知风险与对策

| 风险 | 对策 |
|------|------|
| 官方镜像 OCR 模型可能缺失 | Task 9 冒烟清单第 4 步显式验证;缺则按 models.md 预下载挂 ARTIFACTS_PATH |
| serve 重启丢任务(内存态) | `job.result()` 抛 `TaskNotFoundError` → failed + parse_error 提示重触发;对账只恢复 API 侧 poller |
| API 重启丢 poller | Task 7 启动对账重启轮询 |
| 解析超时 | `job.result(timeout=...)` 抛 `TaskTimeoutError` → failed;parsing_started_at 落库供对账复用 |
| 客户端与服务端版本错开 | 客户端自动带 `Accept-Docling-Document-Version`,服务端降级输出;升级时同步对齐 docling-slim 与镜像 tag |
| 官方客户端内含私有轮询接口(续跑用) | 对账续跑路径封装在 Task 7 单一函数内,版本升级后以回归测试为准;必要时降级为该路径的两条原生 HTTP 请求 |