# 标书合规审核 SaaS Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use XWLPowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 8 周内基于现有 FastAPI 模板交付一个可部署、可追溯、可人工复核的标书合规审核 SaaS MVP。

**Architecture:** 使用模块化单体承载 HTTP API 和业务逻辑，Celery Worker 承担文件解析、规则提取、逐项审核、评分和报告生成。PostgreSQL 保存业务数据及 pgvector 向量，Redis 保存任务队列，对象存储保存原文件和报告；所有 AI 输出通过适配器、结构化 Schema 和不可变运行记录进入领域模型。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、SQLAlchemy Async、Alembic、PostgreSQL/pgvector、Redis、Celery、S3/MinIO、PyMuPDF、python-docx、HTTPX 云端模型适配器、pytest、Docker Compose。

---

## 实施约定

- 在仓库根目录执行所有命令。
- 严格使用测试驱动：先写失败测试，再写最小实现。
- 普通测试禁止访问真实 LLM、OCR 或对象存储；统一使用 Fake Adapter。
- 每完成一个任务提交一次，不把多个领域能力堆进同一个提交。
- API 只使用 `public_id: UUID`，数据库内部关联继续使用现有 `BigInteger id`。
- 所有业务查询都必须显式接收 `organization_id`。
- 迁移文件手写并执行 upgrade/downgrade 测试，不修改已提交迁移。
- 每个任务结束运行目标测试；每个里程碑结束运行全量测试和静态检查。
- 所有普通 JSON 成功接口使用 `ApiResponse[T]` 作为 `response_model` 并通过 `ok(...)` 返回 `{"data": ..., "code": 200, "message": "ok"}`；业务 `code` 固定为 `200`，即使创建接口的 HTTP 状态码为 `201`。
- 普通 JSON 错误响应的 `code` 与 HTTP 错误状态码一致并包含 `request_id`；删除成功返回 HTTP `200` 和统一成功响应体，不使用 HTTP `204`。
- SSE 接口使用 `text/event-stream`，不套用 `ApiResponse`；其事件负载使用对应的稳定事件 Schema。
- 每个 API Task 的集成测试必须断言成功响应包含 `data`、`code == 200` 和 `message == "ok"`，并验证 OpenAPI 成功响应引用对应的 `ApiResponse[T]`。

## 里程碑

| 周次 | 任务 | 可验收结果 |
|---|---|---|
| 第 1 周 | 1-3 | 工程基线、数据库测试、注册登录和组织隔离 |
| 第 2 周 | 4-5 | 项目 CRUD、文档版本和对象存储上传 |
| 第 3 周 | 6-7 | 后台任务、PDF/DOCX/OCR 解析和片段落库 |
| 第 4 周 | 8 | 审核规则提取、模型运行追踪和人工确认 |
| 第 5 周 | 9-10 | 审核运行快照、混合检索和逐项结论 |
| 第 6 周 | 11-12 | 人工复核、评分冻结、报告生成 |
| 第 7 周 | 13-14 | 安全加固、审计、回归数据集和 Agent Evals |
| 第 8 周 | 15 | 部署、监控、备份恢复和端到端验收 |

### Task 1: 建立工程质量与统一错误基线

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/core/config.py`
- Modify: `app/core/exceptions.py`
- Modify: `app/main.py`
- Create: `app/core/middleware.py`
- Create: `tests/core/test_errors.py`
- Modify: `tests/test_config.py`

**Step 1: 添加开发与运行依赖**

Run:

```bash
uv add "pyjwt>=2.10" "pwdlib[argon2]>=0.3" "email-validator>=2.2" "celery[redis]>=5.5" "redis>=6.0" "boto3>=1.40" "pymupdf>=1.26" "python-docx>=1.2" "pgvector>=0.4" "httpx>=0.28" "reportlab>=4.4" "puremagic>=1.30"
uv add --dev "pytest-asyncio>=1.1" "ruff>=0.12" "mypy>=1.17" "testcontainers[postgres]>=4.12" "faker>=37.5"
```

Expected: `pyproject.toml` 和 `uv.lock` 更新成功。

**Step 2: 写失败测试**

在 `tests/core/test_errors.py` 验证：

```python
def test_app_error_contains_request_id(client):
    response = client.get("/api/v1/test-error")
    assert response.status_code == 409
    assert response.json()["code"] == 409
    assert response.json()["data"] is None
    assert response.json()["request_id"] == response.headers["x-request-id"]
```

在 `tests/test_config.py` 增加生产环境密钥不能为空、文件大小和任务重试配置可被环境变量覆盖的测试。

**Step 3: 运行测试确认失败**

Run: `uv run pytest tests/core/test_errors.py tests/test_config.py -q`

Expected: FAIL，缺少请求 ID 中间件和新配置字段。

**Step 4: 实现统一配置与错误协议**

`Settings` 至少增加：

```python
secret_key: str = "development-only-change-me"
access_token_minutes: int = 15
refresh_token_days: int = 7
redis_url: str = "redis://localhost:6379/0"
s3_endpoint_url: str = "http://localhost:9000"
s3_bucket: str = "bid-review"
s3_access_key: str = "minio"
s3_secret_key: str = "minio123"
max_file_size_mb: int = 100
max_model_retries: int = 2
llm_base_url: str = "https://api.example.com/v1"
llm_api_key: SecretStr = SecretStr("")
llm_model: str = "configure-me"
embedding_model: str = "configure-me"
```

`request_id_middleware` 接受客户端合法 UUID 或生成新 UUID，并写入 `request.state.request_id` 和响应头。成功响应为 `{"data": ..., "code": 200, "message": "ok"}`；错误响应固定为：

```json
{
  "data": null,
  "code": 409,
  "message": "用户可读信息",
  "request_id": "uuid"
}
```

成功响应的 `code` 固定为 `200`，不随 HTTP `200` 或 `201` 等成功状态变化；错误响应的 `code` 与 HTTP 错误状态码一致。`AppError`、422 校验错误、HTTPException 与未捕获异常均输出该统一错误格式。

**Step 5: 运行目标测试与静态检查**

Run:

```bash
uv run pytest tests/core/test_errors.py tests/test_config.py tests/test_health.py -q
uv run ruff check app tests
```

Expected: PASS，Ruff 无错误。

**Step 6: 提交**

```bash
git add pyproject.toml uv.lock app/core app/main.py tests/core tests/test_config.py
git commit -m "chore: establish application quality baseline"
```

### Task 2: 建立异步数据库测试夹具和领域基础类型

**Files:**
- Modify: `app/models/base.py`
- Create: `app/models/enums.py`
- Modify: `app/models/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/models/test_base_model.py`
- Create: `tests/integration/test_database.py`

**Step 1: 写 Base 公共字段失败测试**

验证 `public_id` 是 UUID、唯一且非空，时间字段有时区，软删除字段默认 false。

```python
def test_base_exposes_public_uuid():
    column = Demo.__table__.columns["public_id"]
    assert column.unique
    assert not column.nullable
```

**Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_base.py tests/models/test_base_model.py -q`

Expected: FAIL，`public_id` 不存在。

**Step 3: 扩展 Base 并创建枚举**

`Base` 增加：

```python
public_id: Mapped[uuid.UUID] = mapped_column(
    Uuid, default=uuid.uuid4, unique=True, nullable=False, index=True
)
```

在 `app/models/enums.py` 定义文档类型、规则类别、评估方式、评分方式、任务状态、审核运行状态、结论 verdict 和复核状态。枚举值必须与设计文档一致。

**Step 4: 建立 PostgreSQL 集成测试夹具**

`tests/conftest.py` 提供：

- session 级 PostgreSQL testcontainer；
- function 级事务回滚；
- 覆盖 `get_db` 的 `AsyncSession`；
- `TestClient` 与已认证客户端工厂；
- 不依赖 Docker 的纯单元测试仍可单独运行。

**Step 5: 验证数据库夹具**

Run: `uv run pytest tests/integration/test_database.py -q`

Expected: PASS，可建表、写入并在测试结束后回滚。

**Step 6: 提交**

```bash
git add app/models tests/conftest.py tests/models tests/integration
git commit -m "test: add async database harness and domain enums"
```

### Task 3: 实现用户、组织与认证闭环

**Files:**
- Create: `app/models/identity.py`
- Create: `app/schemas/auth.py`
- Create: `app/repositories/users.py`
- Create: `app/services/auth.py`
- Create: `app/core/security.py`
- Create: `app/api/dependencies.py`
- Create: `app/api/v1/endpoints/auth.py`
- Modify: `app/api/v1/router.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/20260810_0001_identity.py`
- Create: `tests/auth/test_auth_api.py`
- Create: `tests/auth/test_security.py`

**Step 1: 写认证失败测试**

覆盖注册、重复邮箱、登录、错误密码、刷新令牌、`GET /me` 和过期令牌。

```python
def test_register_creates_default_organization(client):
    response = client.post("/api/v1/auth/register", json={
        "email": "owner@example.com", "password": "correct horse battery staple"
    })
    assert response.status_code == 201
    assert response.json()["code"] == 200
    assert response.json()["message"] == "ok"
    assert response.json()["data"]["organization"]["role"] == "owner"
```

**Step 2: 运行测试确认失败**

Run: `uv run pytest tests/auth -q`

Expected: FAIL，认证路由不存在。

**Step 3: 实现身份模型与迁移**

模型契约：

- `User(email, password_hash, is_active)`，邮箱使用标准化小写唯一索引；
- `Organization(name, status)`；
- `Membership(user_id, organization_id, role)`，二者联合唯一；
- 注册在一个事务内创建三条记录。

**Step 4: 实现密码和令牌服务**

`app/core/security.py` 暴露：

```python
def hash_password(password: str) -> str: ...
def verify_password(password: str, password_hash: str) -> bool: ...
def create_access_token(*, user_public_id: UUID, organization_public_id: UUID) -> str: ...
def create_refresh_token(*, user_public_id: UUID, token_id: UUID) -> str: ...
def decode_token(token: str, expected_type: Literal["access", "refresh"]) -> TokenClaims: ...
```

刷新令牌放入 Secure、HttpOnly、SameSite=Lax Cookie；访问令牌通过响应体返回并由前端保存在内存。

**Step 5: 实现 API 与当前主体依赖**

`CurrentPrincipal` 包含内部 `user_id`、`organization_id` 与公开 UUID。所有后续业务 Service 只接受该对象或内部组织 ID。

**Step 6: 运行测试和迁移往返**

Run:

```bash
uv run pytest tests/auth -q
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: 全部 PASS，迁移可升级、回退、再次升级。

**Step 7: 提交**

```bash
git add app alembic/versions/20260810_0001_identity.py tests/auth
git commit -m "feat: add organization-scoped authentication"
```

### Task 4: 实现组织隔离的审核项目 CRUD

**Files:**
- Create: `app/models/project.py`
- Create: `app/schemas/project.py`
- Create: `app/repositories/projects.py`
- Create: `app/services/projects.py`
- Create: `app/api/v1/endpoints/projects.py`
- Modify: `app/api/v1/router.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/20260810_0002_projects.py`
- Create: `tests/projects/test_project_api.py`
- Create: `tests/projects/test_tenant_isolation.py`

**Step 1: 写项目 CRUD 和越权失败测试**

必须证明组织 A 无法通过列表、详情、修改和删除访问组织 B 的项目，响应统一为 404，避免泄露资源存在性。
创建、列表、详情、修改和删除成功响应必须断言统一 `ApiResponse` 结构；创建可以返回 HTTP `201`，但响应体 `code` 固定为 `200`，删除成功返回 HTTP `200`。

**Step 2: 运行测试确认失败**

Run: `uv run pytest tests/projects -q`

Expected: FAIL，项目资源不存在。

**Step 3: 实现 Project 模型和 Repository**

`Project` 字段：`organization_id`、`name`、`description`、`status=active`、`created_by_id`、公共 UUID 和乐观锁版本号。

Repository 方法必须具有以下形态：

```python
async def get_by_public_id(
    session: AsyncSession, *, organization_id: int, public_id: UUID
) -> Project | None: ...
```

禁止提供不带组织范围的业务查询方法。

**Step 4: 实现 Service、Schema 和路由**

支持分页列表、创建、详情、修改与软删除。更新请求携带 `version`，冲突返回 `409 RESOURCE_VERSION_CONFLICT`。

**Step 5: 验证**

Run: `uv run pytest tests/projects -q`

Expected: PASS，包括全部越权场景。

**Step 6: 提交**

```bash
git add app alembic/versions/20260810_0002_projects.py tests/projects
git commit -m "feat: add tenant-isolated review projects"
```

### Task 5: 实现文档、不可变版本与预签名上传

**Files:**
- Create: `app/models/document.py`
- Create: `app/schemas/document.py`
- Create: `app/repositories/documents.py`
- Create: `app/services/documents.py`
- Create: `app/integrations/storage/base.py`
- Create: `app/integrations/storage/s3.py`
- Create: `app/integrations/storage/fake.py`
- Create: `app/api/v1/endpoints/documents.py`
- Modify: `app/api/v1/router.py`
- Create: `alembic/versions/20260810_0003_documents.py`
- Create: `tests/documents/test_document_api.py`
- Create: `tests/documents/test_document_versions.py`
- Create: `tests/documents/test_storage_contract.py`

**Step 1: 写文档版本失败测试**

覆盖：创建 `tender/bid` 逻辑文档、申请上传、完成上传、SHA-256 去重、列出版本、解析前激活失败、解析后激活成功和跨租户拒绝。
所有普通 JSON 成功响应必须断言 `data`、`code == 200` 和 `message == "ok"`，并验证对应的 `ApiResponse[T]` OpenAPI 模型。

**Step 2: 定义存储端口契约**

```python
class ObjectStorage(Protocol):
    async def create_upload(self, *, key: str, content_type: str, expires_in: int) -> PresignedUpload: ...
    async def head(self, *, key: str) -> ObjectMetadata: ...
    async def download(self, *, key: str) -> AsyncIterator[bytes]: ...
    async def create_download(self, *, key: str, expires_in: int) -> str: ...
    async def delete(self, *, key: str) -> None: ...
```

Fake 与 S3 实现必须通过同一组契约测试。

**Step 3: 实现模型和上传事务**

- `Document(project_id, type, active_version_id)`；同一项目同一类型只能有一个逻辑文档；
- `DocumentVersion(document_id, version_number, object_key, sha256, size, media_type, parse_status)`；
- 完成上传时通过对象存储 `head` 校验大小、MIME 与哈希；
- `object_key` 使用组织和项目 UUID 前缀，禁止使用原始文件名作为路径。

**Step 4: 实现 API**

严格按照设计文档第 8.1 节实现文档与版本端点。`active-version` 只接受 `parsed` 版本。

**Step 5: 验证**

Run: `uv run pytest tests/documents -q`

Expected: PASS，重复完成上传保持幂等。

**Step 6: 提交**

```bash
git add app alembic/versions/20260810_0003_documents.py tests/documents
git commit -m "feat: add immutable document uploads"
```

### Task 6: 建立可恢复后台任务与进度事件

**Files:**
- Create: `app/models/job.py`
- Create: `app/schemas/job.py`
- Create: `app/repositories/jobs.py`
- Create: `app/services/jobs.py`
- Create: `app/workers/celery_app.py`
- Create: `app/workers/tasks.py`
- Create: `app/api/v1/endpoints/jobs.py`
- Modify: `docker-compose.yml`
- Create: `alembic/versions/20260810_0004_jobs.py`
- Create: `tests/jobs/test_job_state_machine.py`
- Create: `tests/jobs/test_job_api.py`
- Create: `tests/jobs/test_retry.py`

**Step 1: 写任务状态机失败测试**

允许的迁移：

```text
queued -> running -> succeeded
                  -> failed(retryable=true) -> queued
                  -> failed(retryable=false)
```

非法迁移必须抛出稳定业务错误；重复的幂等键返回原任务。

**Step 2: 实现 Job 模型与事务服务**

字段包括：组织、项目、可选审核运行、类型、状态、进度、当前步骤、检查点 JSON、重试次数、最大重试、幂等键、错误码和是否可重试。

**Step 3: 实现 Celery 配置**

任务消息只携带 `job_public_id`。Worker 启动后重新从数据库读取任务与业务输入；启用 `acks_late`，并保证任务实现幂等。

**Step 4: 实现任务 API 和 SSE**

- `GET /jobs/{id}` 返回持久化状态；
- `GET /jobs/{id}/events` 每次状态变化发送 SSE，心跳不写数据库；
- `POST /jobs/{id}/retry` 只重试可重试失败任务。
- 任务查询与重试使用统一 `ApiResponse[T]`；SSE 保持 `text/event-stream`，不套用 `ApiResponse`。

**Step 5: 验证**

Run: `uv run pytest tests/jobs -q`

Expected: PASS，包括重复执行和恢复检查点。

**Step 6: 提交**

```bash
git add app docker-compose.yml alembic/versions/20260810_0004_jobs.py tests/jobs
git commit -m "feat: add durable background jobs"
```

### Task 7: 实现 PDF、DOCX、OCR 与文档片段化

**Files:**
- Create: `app/models/document_segment.py`
- Create: `app/integrations/parsers/base.py`
- Create: `app/integrations/parsers/pdf.py`
- Create: `app/integrations/parsers/docx.py`
- Create: `app/integrations/ocr/base.py`
- Create: `app/integrations/ocr/fake.py`
- Create: `app/services/document_parsing.py`
- Create: `app/workflows/parse_document.py`
- Modify: `app/workers/tasks.py`
- Create: `alembic/versions/20260810_0005_document_segments.py`
- Create: `tests/fixtures/documents/simple.pdf`
- Create: `tests/fixtures/documents/simple.docx`
- Create: `tests/parsing/test_pdf_parser.py`
- Create: `tests/parsing/test_docx_parser.py`
- Create: `tests/parsing/test_parse_workflow.py`

**Step 1: 写解析端口和样本测试**

统一输出：

```python
class ParsedBlock(BaseModel):
    page_number: int
    sequence: int
    kind: Literal["paragraph", "table", "ocr"]
    text: str
    bbox: tuple[float, float, float, float] | None
```

测试页码从 1 开始、顺序稳定、空文本被忽略、表格单元格按行保留。

**Step 2: 实现 PDF 与 DOCX Parser**

- PDF 使用 PyMuPDF 提取文本块和页码；
- DOCX 使用 python-docx，按段落与表格顺序生成逻辑页；
- OCR Adapter 只接受渲染后的页面字节，不接受对象存储凭据；
- 文本字符数低于阈值的 PDF 页进入 OCR。

**Step 3: 实现片段化和质量指标**

`DocumentSegment` 保存版本、页码、顺序、种类、文本、坐标、文本哈希和解析器版本。版本同时保存总页数、OCR 页数、空白页数和质量状态。

**Step 4: 实现解析工作流**

步骤固定为 `download -> inspect -> parse -> ocr -> segment -> persist -> activate-ready`，每步写 Job 检查点。相同文件哈希与解析器版本可以复用结果。

**Step 5: 验证**

Run: `uv run pytest tests/parsing tests/documents -q`

Expected: PASS，重复解析不会产生重复片段。

**Step 6: 提交**

```bash
git add app alembic/versions/20260810_0005_document_segments.py tests/parsing tests/fixtures/documents
git commit -m "feat: parse bid documents into traceable segments"
```

### Task 8: 实现模型适配器、运行记录与审核规则提取

**Files:**
- Create: `app/models/model_run.py`
- Create: `app/models/review_rule.py`
- Create: `app/schemas/llm.py`
- Create: `app/schemas/review_rule.py`
- Create: `app/integrations/llm/base.py`
- Create: `app/integrations/llm/openai_compatible.py`
- Create: `app/integrations/llm/fake.py`
- Create: `app/prompts/rule_extraction.py`
- Create: `app/services/model_runs.py`
- Create: `app/services/review_rules.py`
- Create: `app/workflows/extract_rules.py`
- Create: `app/api/v1/endpoints/review_rules.py`
- Create: `alembic/versions/20260810_0006_review_rules.py`
- Create: `tests/llm/test_llm_contract.py`
- Create: `tests/rules/test_rule_extraction.py`
- Create: `tests/rules/test_rule_api.py`

**Step 1: 写 LLM 适配器契约测试**

```python
class LLMClient(Protocol):
    async def structured(
        self, *, operation: str, input_text: str, output_schema: type[T]
    ) -> LLMResult[T]: ...
```

Fake Adapter 必须能模拟成功、超时、限流、无效 JSON 和重试后成功。

**Step 2: 写规则提取失败测试**

验证四类规则、来源片段、`evaluation_method`、受控 `condition`、`scoring_method`、重复规则合并和人工补充规则。

**Step 3: 实现 ModelRun 与 ReviewRule**

- `ModelRun` 一行代表一次调用尝试，相同 `operation_id` 组成重试链；
- AI 规则关联最终成功的 `model_run_id`；
- 人工补充规则无模型关联，但必须归属当前招标版本；
- `condition` 使用 Pydantic 判别联合验证受控运算符，禁止任意表达式或代码。

**Step 4: 实现提取工作流**

按章节批次处理招标片段，模型只返回片段 UUID；程序回查片段、校验页码、去重并保存 draft。超过 2 次仍无效时记录失败批次，不伪造规则。

**Step 5: 实现规则确认 API**

支持列表、修改 draft、忽略、确认和批量确认。只有当前招标版本的 confirmed 规则可以创建审核运行。
所有普通 JSON 成功响应使用 `ApiResponse[T]` 和 `ok(...)`，API 测试断言 `code == 200`、`message == "ok"` 和业务数据位于 `data`。

**Step 6: 验证**

Run: `uv run pytest tests/llm tests/rules -q`

Expected: PASS，普通测试无网络访问。

**Step 7: 提交**

```bash
git add app alembic/versions/20260810_0006_review_rules.py tests/llm tests/rules
git commit -m "feat: extract traceable tender review rules"
```

### Task 9: 实现审核运行输入快照与 pgvector 检索

**Files:**
- Create: `app/models/review_run.py`
- Create: `app/models/review_run_rule.py`
- Create: `app/integrations/embeddings/base.py`
- Create: `app/integrations/embeddings/openai_compatible.py`
- Create: `app/integrations/embeddings/fake.py`
- Create: `app/services/review_runs.py`
- Create: `app/services/retrieval.py`
- Create: `app/api/v1/endpoints/review_runs.py`
- Create: `alembic/versions/20260810_0007_review_runs_vector.py`
- Create: `tests/reviews/test_review_run_snapshot.py`
- Create: `tests/reviews/test_retrieval.py`
- Create: `tests/reviews/test_review_run_api.py`

**Step 1: 写运行快照失败测试**

验证创建运行时固定：活动招标版本、活动投标版本、confirmed 规则完整副本和提示词/执行器版本。创建后修改当前规则或更换文档版本不得影响旧运行。
审核运行 API 的创建、列表与详情成功响应必须断言统一 `ApiResponse` 结构；创建可以返回 HTTP `201`，但响应体 `code` 固定为 `200`。

**Step 2: 创建 pgvector 迁移**

迁移先执行 `CREATE EXTENSION IF NOT EXISTS vector`，为投标片段增加向量列和适合当前数据量的索引。downgrade 删除索引和列，不删除共享扩展。

**Step 3: 实现 ReviewRun 创建事务**

前置条件：两个活动版本均 parsed，规则至少一条且全部属于活动招标版本。事务创建运行和 `ReviewRunRule` 快照；幂等键相同则返回原运行。

**Step 4: 实现混合检索**

召回结果合并：

```text
候选 = keyword_top_20 ∪ vector_top_20
排序 = 0.4 * normalized_keyword + 0.6 * normalized_vector
最终返回 top_12，并保留各分数与检索版本
```

检索必须限定当前运行的投标文档版本。

**Step 5: 验证**

Run: `uv run pytest tests/reviews/test_review_run_snapshot.py tests/reviews/test_retrieval.py tests/reviews/test_review_run_api.py -q`

Expected: PASS，旧运行不受当前版本变化影响。

**Step 6: 提交**

```bash
git add app alembic/versions/20260810_0007_review_runs_vector.py tests/reviews
git commit -m "feat: snapshot review inputs and retrieve evidence"
```

### Task 10: 实现确定性与语义审核合并器

**Files:**
- Create: `app/models/finding.py`
- Create: `app/models/evidence.py`
- Create: `app/schemas/finding.py`
- Create: `app/services/evaluators/deterministic.py`
- Create: `app/services/evaluators/semantic.py`
- Create: `app/services/evaluators/merge.py`
- Create: `app/prompts/semantic_review.py`
- Create: `app/workflows/review_bid.py`
- Modify: `app/workers/tasks.py`
- Create: `alembic/versions/20260810_0008_findings.py`
- Create: `tests/evaluators/test_deterministic.py`
- Create: `tests/evaluators/test_merge_matrix.py`
- Create: `tests/reviews/test_review_workflow.py`

**Step 1: 用参数化测试写完整合并矩阵**

至少覆盖：解析质量差、无证据、确定性通过/失败/运算错误、语义高低置信度、无效引用、否决风险和输出重试耗尽。

```python
@pytest.mark.parametrize(("case", "expected"), [
    (Case(parse_ok=False), Verdict.UNCERTAIN),
    (Case(parse_ok=True, evidence=[]), Verdict.MISSING),
    (Case(method="semantic", confidence=0.79), Verdict.UNCERTAIN),
])
def test_merge_matrix(case, expected):
    assert merge(case).verdict is expected
```

**Step 2: 实现 Finding、FindingRevision 与 Evidence**

- `Finding` 稳定关联一条运行规则并指向当前修订；
- 初始修订不可变；AI 修订记录模型运行，程序修订记录执行器版本和输入哈希；
- Evidence 只能引用当前运行的招标或投标片段；
- 置信度不能替代证据校验。

**Step 3: 实现两个 evaluator**

确定性 evaluator 只支持白名单运算符。语义 evaluator 只返回 verdict、理由、confidence 和证据片段 UUID。两者都返回统一 `EvaluationResult`。

**Step 4: 实现审核工作流**

逐规则检查点保存成功规则 ID，重试只运行失败规则。完成后把运行切换为 `human_review`；失败且可恢复时进入 `review_failed`。

**Step 5: 验证**

Run: `uv run pytest tests/evaluators tests/reviews/test_review_workflow.py -q`

Expected: PASS，合并矩阵没有未定义分支。

**Step 6: 提交**

```bash
git add app alembic/versions/20260810_0008_findings.py tests/evaluators tests/reviews
git commit -m "feat: evaluate bid compliance with evidence"
```

### Task 11: 实现人工修订、冻结事务与模拟评分

**Files:**
- Create: `app/models/score_result.py`
- Create: `app/schemas/score_result.py`
- Create: `app/services/findings.py`
- Create: `app/services/scoring.py`
- Create: `app/prompts/subjective_scoring.py`
- Create: `app/workflows/score_review.py`
- Modify: `app/api/v1/endpoints/review_runs.py`
- Modify: `app/workers/tasks.py`
- Create: `alembic/versions/20260810_0009_scoring.py`
- Create: `tests/reviews/test_finding_revisions.py`
- Create: `tests/reviews/test_review_freeze.py`
- Create: `tests/scoring/test_scoring_workflow.py`
- Create: `tests/scoring/test_scoring_retry.py`

**Step 1: 写追加修订失败测试**

验证 PATCH 不覆盖 AI 修订，而是创建新修订并更新 `current_revision_id`；运行不在 `human_review` 时返回 409。
结论修改与查询的成功响应必须断言 `data`、`code == 200` 和 `message == "ok"`，并验证 OpenAPI 使用对应的 `ApiResponse[T]`。

**Step 2: 写冻结与评分竞态测试**

使用两个并发事务证明：`POST complete` 成功后，任何修订请求失败；同一个 complete 幂等键只创建一个评分任务。

**Step 3: 实现提交复核事务**

事务顺序固定：锁定 ReviewRun 行、确认状态、检查所有 pending 结论、冻结当前修订 ID、切换 `scoring`、创建评分 Job、提交事务。

**Step 4: 实现评分工作流**

- `objective` 使用程序计算并保存 `calculated`；
- `subjective` 使用 LLM 保存 `suggested` 与模型运行；
- 证据不足保存 `unavailable`，系统错误进入 `scoring_failed`；
- 全部评分写入后，同一事务将运行更新为 `completed`。

**Step 5: 实现评分失败恢复**

重试原 Job 时复用冻结输入，状态 `scoring_failed -> scoring`，不会创建第二套评分结果；按运行规则设置唯一约束。

**Step 6: 验证**

Run: `uv run pytest tests/reviews/test_finding_revisions.py tests/reviews/test_review_freeze.py tests/scoring -q`

Expected: PASS，无冻结竞态和重复评分。

**Step 7: 提交**

```bash
git add app alembic/versions/20260810_0009_scoring.py tests/reviews tests/scoring
git commit -m "feat: review findings and calculate simulated scores"
```

### Task 12: 实现不可变报告快照与 PDF 导出

**Files:**
- Create: `app/models/report.py`
- Create: `app/schemas/report.py`
- Create: `app/services/reports.py`
- Create: `app/integrations/reports/pdf.py`
- Create: `app/api/v1/endpoints/reports.py`
- Create: `alembic/versions/20260810_0010_reports.py`
- Create: `tests/reports/test_report_snapshot.py`
- Create: `tests/reports/test_report_pdf.py`
- Create: `tests/reports/test_report_api.py`

**Step 1: 写报告前置条件和快照测试**

未 completed 的运行不能生成报告。报告创建时必须写入所有当前 `finding_revision_id` 和 `score_result_id`；后续重新导出创建新版本。
报告创建、列表与下载成功响应必须断言统一 `ApiResponse` 结构；下载接口把短期预签名 URL 放在 `data` 中。

**Step 2: 实现报告模型**

- `Report(review_run_id, version, status, object_key, sha256)`；
- `ReportFindingItem(report_id, finding_revision_id)`；
- `ReportScoreItem(report_id, score_result_id)`；
- 报告与版本联合唯一，快照行不允许更新。

**Step 3: 实现 PDF 生成器**

报告章节固定为项目摘要、风险摘要、否决项、资格项、响应项、模拟评分、证据附录和免责声明。中文字体通过配置注入；测试只验证文本、页数和文件签名，不依赖肉眼截图。

**Step 4: 实现下载 API**

生成任务成功后上传对象存储并保存哈希。下载端点返回 5 分钟预签名 URL 并写审计事件。

**Step 5: 验证**

Run: `uv run pytest tests/reports -q`

Expected: PASS，旧报告内容不受当前数据变化影响。

**Step 6: 提交**

```bash
git add app alembic/versions/20260810_0010_reports.py tests/reports
git commit -m "feat: generate immutable review reports"
```

### Task 13: 补齐审计、文件安全与数据删除

**Files:**
- Create: `app/models/audit_log.py`
- Create: `app/services/audit.py`
- Create: `app/integrations/security/file_scanner.py`
- Create: `app/services/data_retention.py`
- Modify: `app/services/documents.py`
- Modify: `app/services/projects.py`
- Modify: `app/services/reports.py`
- Create: `alembic/versions/20260810_0011_audit.py`
- Create: `tests/security/test_file_validation.py`
- Create: `tests/security/test_prompt_injection.py`
- Create: `tests/security/test_audit_log.py`
- Create: `tests/security/test_data_deletion.py`

**Step 1: 写安全失败测试**

覆盖伪装扩展名、超大文件、路径穿越文件名、提示注入文本、跨组织下载、过期 URL、软删除后访问和清理任务幂等。
涉及既有普通 JSON API 的安全失败与成功路径必须继续验证统一响应结构；错误响应断言 `code` 与 HTTP 错误状态码一致且包含 `request_id`。

**Step 2: 实现文件检查链**

顺序为大小、文件头、MIME、允许类型、恶意文件扫描。任何失败都不进入解析队列，并删除未认领上传对象。

**Step 3: 实现审计日志**

记录上传、删除、规则确认、结论修订、复核提交、报告生成和下载。审计元数据禁止包含正文、令牌、密码和预签名 URL。

**Step 4: 实现保留与删除任务**

软删除立即阻止访问；清理任务按对象清单删除存储文件、片段向量和派生结果，并可安全重跑。

**Step 5: 验证**

Run: `uv run pytest tests/security tests/projects/test_tenant_isolation.py -q`

Expected: PASS，日志中无样本文本和敏感配置。

**Step 6: 提交**

```bash
git add app alembic/versions/20260810_0011_audit.py tests/security
git commit -m "feat: harden file handling and audit access"
```

### Task 14: 建立冻结回归集和可重复 Agent Evals

**Files:**
- Create: `evals/README.md`
- Create: `evals/schema.py`
- Create: `evals/run.py`
- Create: `evals/metrics.py`
- Create: `evals/datasets/.gitkeep`
- Create: `tests/evals/test_metrics.py`
- Create: `tests/evals/test_dataset_validation.py`
- Modify: `pyproject.toml`

**Step 1: 写指标公式测试**

构造小型数据验证：一对一规则匹配、否决规则召回率、否决风险召回率、证据页码准确率、结构化输出有效率和三次完全一致率。

**Step 2: 定义评测数据 Schema**

每条 gold rule 包含稳定 ID、类别、要求、招标页码、预期 verdict、`evidence_required`、证据页码集合、参考理由和参考分数。数据集 manifest 包含版本、文件 SHA-256、标注者和冻结时间。

**Step 3: 实现评测 CLI**

Run:

```bash
uv run python -m evals.run --dataset evals/datasets/v1/manifest.json --repeat 3
```

输出机器可读 JSON 和终端摘要；失败时进程码非 0。真实文档不提交 Git，只提交脱敏后的 manifest 示例和说明。

**Step 4: 加入 CI 可运行的离线指标测试**

普通 CI 使用保存的预测 fixture，不调用真实模型。真实评测由 staging 手动触发并保存模型、参数和提示词版本。

**Step 5: 验证**

Run: `uv run pytest tests/evals -q`

Expected: PASS，所有公式与设计文档定义一致。

**Step 6: 提交**

```bash
git add evals tests/evals pyproject.toml
git commit -m "test: add reproducible bid review evaluations"
```

### Task 15: 完成容器部署、可观测性和端到端验收

**Files:**
- Modify: `docker-compose.yml`
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `.env.example`
- Create: `app/core/logging.py`
- Create: `app/core/metrics.py`
- Create: `scripts/backup.sh`
- Create: `scripts/restore.sh`
- Create: `docs/runbooks/deployment.md`
- Create: `docs/runbooks/backup-restore.md`
- Create: `docs/runbooks/job-recovery.md`
- Create: `.github/workflows/ci.yml`
- Create: `tests/e2e/test_bid_review_flow.py`

**Step 1: 写端到端失败测试**

使用 Fake LLM、Fake OCR 和测试对象存储走通：注册、项目、两份文档、解析、规则确认、审核运行、人工复核、评分和报告。
端到端测试同时抽查认证、项目、文档、任务查询、规则、审核运行和报告接口的统一成功响应；断言业务数据位于 `data`、`code == 200`、`message == "ok"`。SSE 只验证 `text/event-stream` 与事件 Schema。

**Step 2: 扩展 Docker Compose**

服务包括 `api`、`worker`、`postgres`、`redis` 和 `minio`。API 与 Worker 使用同一镜像；healthcheck 就绪后再启动依赖服务。容器不以 root 运行。

**Step 3: 实现结构化日志与指标**

日志字段至少包含时间、级别、事件名、`request_id`、`job_id`、`review_run_id` 和 `model_run_id`。指标包括 API 延迟、队列长度、任务耗时、失败率、Token、费用、OCR 页数和人工修订率。

**Step 4: 实现 CI**

CI 顺序：

```bash
uv sync --frozen
uv run ruff check app tests evals
uv run mypy app
uv run pytest -q
```

构建 Docker 镜像，但不在普通 PR 中调用真实模型。

**Step 5: 验证备份恢复和任务中断**

在 staging 副本执行数据库备份、恢复到新数据库、校验报告与审核运行数量；终止 Worker 后重新启动，确认任务从检查点继续。

**Step 6: 执行最终验收**

Run:

```bash
uv run pytest -q
uv run ruff check app tests evals
uv run mypy app
docker compose config
docker compose up -d --build
curl --fail http://localhost:8000/api/v1/health
```

Expected: 全部命令成功；随后按照设计文档第 11.3 和第 16 节执行冻结样本、20 次端到端运行和一次恢复演练。

**Step 7: 提交**

```bash
git add Dockerfile .dockerignore .env.example docker-compose.yml app/core scripts docs/runbooks .github tests/e2e
git commit -m "ops: deploy and observe bid review SaaS"
```

## 最终交付检查

完成 Task 1-15 后，逐项确认：

- `uv run pytest -q` 全部通过；
- `uv run ruff check app tests evals` 无错误；
- `uv run mypy app` 无错误；
- Alembic 可以从空库升级到 head；
- Docker Compose 可以启动全部服务；
- 不联网的普通测试可稳定执行；
- 组织隔离测试覆盖所有业务资源；
- AI 产出可追踪到成功 ModelRun；
- 确定性结论可追踪执行器版本和输入哈希；
- 审核运行完成后不可修改，报告保留输入快照；
- 冻结回归集达到设计文档中的量化门槛；
- 部署、备份恢复和任务恢复文档已经实际演练。
