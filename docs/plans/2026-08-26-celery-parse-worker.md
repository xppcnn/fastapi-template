# Celery 后台任务引入 + 解析流程 worker 化实施计划

> **状态：已完成**（2026-08-26 实施，76 测试全绿；真实环境冒烟见文末清单，需 docker 环境执行）

> **For Agent:** 按 Task 顺序逐任务执行；每个 Task 先写失败测试，再实现，再全量回归。

**Goal:** 引入 Redis + Celery，把文档解析（docling 编排）从 FastAPI 进程内 asyncio 后台任务迁到独立 worker：API 原子认领后入队，beat 定时对账轮询 docling 任务状态并落库。为后续 Review Run（LLM 批量审核）复用同一套"DB 为事实源 + 队列 + 对账"模式铺路。

**Architecture:** FastAPI(async) 只负责"认领 + 入队"；Celery worker(sync) 负责下载/提交/轮询/落库。worker 内 DB 用同步 psycopg2（无事件循环绑定问题），MinIO 直接用同步客户端，docling 官方 async 客户端以 `asyncio.run()` 薄桥包裹（客户端自包含，无跨循环共享状态）。DB 的 `parse_status`/`parse_job_id` 是 async 世界与 sync 世界的唯一契约。

```
FastAPI(async)                契约=DB                 Celery worker(sync)
请求→原子认领 PARSING ────────────────────────────► submit 任务: MinIO下载+提交docling+存task_id
     ↑                                                reconcile 任务(beat每5s):
     └── 202 返回 parse_status                       扫PARSING→每版本poll一次→终态落库
```

## 已冻结的决策

| 决策点 | 结论 | 理由 |
|--------|------|------|
| 任务框架 | Celery + Redis broker，**无 result backend** | 生态成熟；DB 状态列是唯一事实源，不引入第二事实源 |
| 任务形态 | **同步任务**（sync SQLAlchemy psycopg2 + 同步 MinIO） | 彻底消除 asyncpg 连接绑定事件循环的坑；sync 连接无循环绑定 |
| docling 访问 | async 官方客户端保留，任务内 `asyncio.run()` 薄桥包裹 | docling-slim 无同步客户端(2.121 已核实)；客户端每次 `open_client()` 自建，无跨循环状态 |
| 轮询模式 | **submit + beat reconcile 分离**，不用长轮询任务 | 任务全为短任务不占槽；reconcile 语义与现有 `_resume_parse` 一致；未来 Review Run 复用同模式 |
| reconcile 并发 | 单次 `asyncio.run()` 内 `asyncio.gather` 并发 poll，wait≤2s | 串行 50 版本最坏 250s/轮，会叠任务 |
| 超时 | 沿用 `parsing_started_at + docling_parse_timeout_minutes` 判死 | 超时判定放 reconcile 内，随对账自然执行 |
| 幂等 | 终态迁移用条件 UPDATE 守卫（`where parse_status=PARSING`） | 多 worker/beat 重复触发安全 |
| 队列预留 | `task_routes` 分 `parsing` 队列（submit/reconcile） | 未来 Review Run 长任务走独立 `review` 队列，避免抢占 reconcile 槽位 |
| beat 部署 | dev 用 `worker --beat` 单进程；文档写明 beat 须与 worker 同 supervisor | 少一个运维件；beat 挂了解析会卡 PARSING |
| 失败语义 | submit 失败即标 FAILED（同现状）；无 job_id 的 PARSING 由 reconcile 标 FAILED"重新触发" | 与现 `resume_parse` 行为对齐 |

## 不在本计划范围

- Review Run / LLM 审核任务（仅预留 `review` 队列路由口子）
- celery result backend、flower 等监控面板
- docling-serve 的 RQ/GPU 扩容（业务代码无感知）

---

## Task 1: 依赖 + 配置 + Redis 服务

**Files:**
- Modify: `pyproject.toml`(uv add)
- Modify: `app/core/config.py`
- Modify: `docker-compose.yml`
- Test: `tests/test_config.py`

**Step 1: 写失败测试**

`tests/test_config.py` 追加：

```python
def test_celery_and_redis_settings_defaults() -> None:
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.celery_concurrency == 2
    assert settings.parsing_poll_interval_seconds == 5
    assert settings.parsing_reconcile_limit == 50


def test_sync_database_url_derived_from_async(monkeypatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://u:pw@db.example:5432/other",
    )
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.sync_database_url == (
            "postgresql+psycopg://u:pw@db.example:5432/other"
        )
    finally:
        get_settings.cache_clear()
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL(AttributeError: redis_url)

**Step 3: 实现**

```bash
uv add celery redis "psycopg[binary]"
```

`app/core/config.py` 的 `Settings` 追加：

```python
    redis_url: str = "redis://localhost:6379/0"
    celery_concurrency: int = 2
    parsing_poll_interval_seconds: int = 5
    parsing_reconcile_limit: int = 50
```

并加 property（同步 driver 的 URL 由 async URL 推导，不留第二条手填配置）：

```python
    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg://"
        )
```

（非 asyncpg scheme 时原样返回——测试用 sqlite 时直接 `SYNC_DATABASE_URL=sqlite:///...` 覆盖。）

**Step 4: 运行验证通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

Run: `uv run python -c "import celery, redis, psycopg; print(celery.__version__)"`
Expected: 正常打印（无 ImportError）

**Step 5: docker-compose 加 Redis**

`docker-compose.yml` 追加 service：

```yaml
  redis:
    image: redis:7-alpine
    container_name: fastapi-template-redis
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
```

Run: `docker compose config --quiet`
Expected: 校验通过

---

## Task 2: 同步 DB 会话工厂

**Files:**
- Modify: `app/core/database.py`
- Test: `tests/core/test_database.py`

**Step 1: 写失败测试**

`tests/core/test_database.py` 追加：

```python
def test_sync_session_factory_available() -> None:
    from app.core.database import sync_session_factory
    from sqlalchemy.orm import sessionmaker

    assert isinstance(sync_session_factory, sessionmaker)
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/core/test_database.py -v`
Expected: FAIL(ImportError)

**Step 3: 实现**

`app/core/database.py` 追加（模块级，懒连接，API 进程导入不建连）：

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

...

sync_engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
sync_session_factory = sessionmaker(bind=sync_engine, expire_on_commit=False)
```

**Step 4: 运行验证通过**

Run: `uv run pytest tests/core/test_database.py -v`
Expected: PASS

Run: `uv run ruff check app/core/database.py` && `uv run mypy app/core/database.py`
Expected: 无错误

---

## Task 3: parsing 服务同步化

**Files:**
- Rewrite: `app/services/parsing.py`(async → sync，保留纯函数)
- Rewrite: `tests/services/test_parsing.py`(sync sqlite)
- Delete: `tests/services/test_parsing_resume.py`(合并进主力文件)
- Test: `tests/services/test_parsing.py`(全量重写)

**核心 API（同步）:**

```python
def submit_parse(version_id: int) -> None:
    """入队后由 worker 执行:校验版本→MinIO 下载→提交 docling→存 task_id;失败标 FAILED。"""

def reconcile_parse() -> None:
    """beat 定时对账:扫 PARSING→并行 poll docling 一次→终态落库(成功/失败/超时/缺 job_id)。"""

def find_stuck_parsing_versions(session, *, limit: int | None = None) -> list[int]: ...

def _persist_result(version_id: int, parsed) -> None: ...   # MinIO put + blocks + PARSED
def _mark_failed(version_id: int, message: str) -> None: ...  # 条件 UPDATE 守卫
```

**关键实现要点**

- 保留 `ParseError` / `_table_to_markdown` / `extract_blocks` / `_payload_to_parsed_conversion`，逐字复用
- MinIO：同步直接调 `get_client().fget_object/put_object`（object_storage 的 async 层不碰）
- docling：`asyncio.run()` 包裹 `open_client()` + `submit_document`；reconcile 用单次 `asyncio.run(gather(...))` 并发 poll（`_poll_task_status(task_id, wait=2)` → 终态 success 再 `_fetch_convert_result_payload`）
- 终态迁移统一走条件 UPDATE：`update(DocumentVersion).where(id==, parse_status==PARSING).values(...)` rowcount==0 跳过（幂等）
- 超时判定：`parsing_started_at + docling_parse_timeout_minutes` 已过 → FAILED("解析超时")；无 job_id 且超过 120s 宽限期 → FAILED("解析任务 id 缺失，请重新触发解析")（宽限期内视为 submit 在途，跳过不误杀）
- 删除孤儿：`run_parse` / `_await_and_persist` / `_resume_parse` / `spawn_parse` / `resume_parse`

**测试（同步 sqlite fixture）**

```python
@pytest.fixture
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    engine.dispose()
```

用例覆盖（patch `app.services.parsing.sync_session_factory` + `get_client` + `open_client`）：
- `submit_parse` 成功：job_id 落库
- `submit_parse` docling down：FAILED + parse_error
- `reconcile_parse` 成功终态：PARSED + blocks 入库（fake poll success + fetch payload）
- `reconcile_parse` failure 终态：FAILED
- `reconcile_parse` 进行中：保持 PARSING
- `reconcile_parse` 无 job_id(超宽限期)：FAILED("重新触发")
- `reconcile_parse` 超时：FAILED("超时")
- `find_stuck_parsing_versions` limit 生效
- `extract_blocks` 组序/回退（保留原用例）

---

## Task 4: Celery 应用 + 任务

**Files:**
- Create: `app/core/celery_app.py`
- Create: `app/tasks/__init__.py`
- Create: `app/tasks/parsing.py`
- Test: `tests/services/test_parsing_tasks.py`(新建)

**Step 1: 写失败测试**

```python
def test_parsing_submit_task_eager_runs(monkeypatch, session_factory) -> None:
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    # 构造 PARSING 版本 + patch sync_session_factory / get_client / open_client
    parsing_submit.delay(version_id)          # 同步执行
    # 断言 parse_job_id 已落库
```

**Step 2: 运行验证失败**

Run: `uv run pytest tests/services/test_parsing_tasks.py -v`
Expected: FAIL(ImportError: app.core.celery_app)

**Step 3: 实现**

`app/core/celery_app.py`：

- 创建 `celery_app` 后调用 `_collect_task_modules()`：用 `pkgutil.walk_packages` 扫描 `app/tasks/` 下所有模块并导入，`@celery_app.task` 装饰器自动注册（**无需手动 include**，新增任务只建文件）
- 验证：`uv run python -c "from app.core.celery_app import celery_app; print(sorted(k for k in celery_app.tasks if k.startswith('parsing')))"` 输出两个任务

```python
from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "fastapi_template",
    broker=settings.redis_url,
    include=["app.tasks.parsing"],
)

celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    timezone="UTC",
    task_default_queue="parsing",
    task_routes={
        # 预留:未来 Review Run 长任务走独立 review 队列,避免抢占 reconcile
        "parsing.submit": {"queue": "parsing"},
        "parsing.reconcile": {"queue": "parsing"},
    },
    beat_schedule={
        "parsing-reconcile": {
            "task": "parsing.reconcile",
            "schedule": settings.parsing_poll_interval_seconds,
        },
    },
)
```

`app/tasks/parsing.py`：

```python
from app.core.celery_app import celery_app
from app.services.parsing import reconcile_parse, submit_parse


@celery_app.task(name="parsing.submit", acks_late=True, max_retries=0)
def parsing_submit(version_id: int) -> None:
    submit_parse(version_id)


@celery_app.task(name="parsing.reconcile", acks_late=True, max_retries=0)
def parsing_reconcile() -> None:
    reconcile_parse()
```

`app/tasks/__init__.py`：空文件。

**Step 4: 运行验证通过**

Run: `uv run pytest tests/services/test_parsing_tasks.py -v`
Expected: PASS

Run: `uv run celery -A app.core.celery_app inspect registered`
Expected: 列出 parsing.submit / parsing.reconcile（无 redis 时该命令报连接错属预期，改跑 `uv run python -c "from app.core.celery_app import celery_app; print(celery_app.tasks.keys())"`）

---

## Task 5: 接线（API 入队 + 启动对账）

**Files:**
- Modify: `app/services/documents.py`
- Modify: `app/main.py`

**Step 1: 实现**

`documents.py`：`spawn_parse(version.id)` → `parsing_submit.delay(version.id)`；import 改 `from app.tasks.parsing import parsing_submit`。

`main.py` lifespan 替换启动对账：

```python
from app.tasks.parsing import parsing_reconcile

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        parsing_reconcile.delay()
    except Exception:
        logger.exception("reconcile_enqueue_failed")
    yield
    await engine.dispose()
```

（移除 `find_stuck_parsing_versions`/`resume_parse` import；redis 不可达不阻塞启动——只记日志，卡住的版本等 beat 起来后由对账兜底。）

**Step 2: 运行验证**

Run: `uv run pytest tests/test_health.py tests/services/test_parsed_result.py -v`
Expected: PASS(确认接线不破坏应用启动与周边)

Run: `uv run ruff check app/` && `uv run mypy app/`
Expected: 无错误

---

## Task 6: 收尾（全量回归 + 文档 + 冒烟）

**Step 1: 全量测试**

Run: `uv run pytest -v`
Expected: 全部 PASS

**Step 2: lint + 类型**

Run: `uv run ruff check app/ tests/` && `uv run ruff format --check app/ tests/` && `uv run mypy app/`
Expected: 无错误

**Step 3: 文档**

- `README.md`：后台任务章节——Redis 启动、worker 命令（`celery -A app.core.celery_app worker -Q parsing --beat --concurrency=2`）、执行模型图、双驱动说明（API=asyncpg / worker=psycopg2）
- 本计划更新为完成状态

**Step 4: 真实环境冒烟清单（需 docker）**

1. `docker compose up -d redis postgres silo`
2. `uv run alembic upgrade head`
3. `uv run celery -A app.core.celery_app worker -Q parsing --beat --concurrency=2 --loglevel=INFO`
4. 起 `uv run uvicorn app.main:app` + docling 容器
5. 上传 PDF → `POST .../parse`(202) → 观察 worker 日志 submit → 5s 内 reconcile → 版本状态 parsed
6. 停 docling 容器后再触发：submit 失败 → FAILED + parse_error 可读
7. 停掉 worker+beat → 触发解析（卡 PARSING）→ 重启 worker → reconcile 兜底续跑或标失败

---

## 实施顺序速查

| Task | 内容 | 依赖 |
|------|------|------|
| 1 | 依赖+配置+Redis 服务 | — |
| 2 | 同步 DB 会话工厂 | 1 |
| 3 | parsing 同步化 + 测试重写 | 2 |
| 4 | Celery 应用 + 任务 | 1,3 |
| 5 | 接线 | 3,4 |
| 6 | 回归+文档+冒烟 | 全部 |

## 已知风险与对策

| 风险 | 对策 |
|------|------|
| docling 私有 API `_poll_task_status`/`_fetch_convert_result_payload` 版本漂移 | 既有风险(现状 resume 路径已在用)，Task 6 冒烟第 7 步覆盖 |
| beat 挂了解析永久卡 PARSING | dev 用 `--beat` 单进程；文档要求 beat 与 worker 同 supervisor 托管；重启后 reconcile 兜底 |
| reconcile 任务叠加 | beat 每 5s 入队 + 单轮 gather 并发 ≤2s 完成，正常不叠；异常叠任务时幂等守卫兜底 |
| 双驱动配置漂移 | `sync_database_url` 由 async URL 推导，无手填第二份 |