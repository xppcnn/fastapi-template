# fastapi-template

基于 FastAPI 的项目模板，集成 SQLAlchemy 2.0（异步）+ Alembic 数据库迁移 + Celery 后台任务（Redis）。

## 技术栈

- FastAPI + pydantic-settings
- SQLAlchemy 2.0（async）+ asyncpg + PostgreSQL（Worker 内为同步 psycopg2，见"后台任务"）
- Alembic（数据库迁移）
- Celery + Redis（后台任务与定时对账）
- pytest（测试）

## 快速开始

```bash
# 1. 启动本地 PostgreSQL、SILO 与 Redis
docker compose up -d

# 2. 安装依赖
uv sync

# 3. 启动开发服务器
uv run fastapi dev --port 8000
```

数据库连接配置在 `.env`（参考 `.env.example`）：

```bash
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fastapi_template
```

### SILO 对象存储（本地）

`docker compose up -d` 会同时启动 [SILO](https://silo.pgsty.com/)（S3 兼容对象存储，MinIO 社区维护分支）：

- S3 API：`http://127.0.0.1:9000`
- 管理控制台：`http://127.0.0.1:9001`，凭据 `silo-admin` / `silo-admin`
- 镜像固定在 `pgsty/silo:RELEASE.2026-08-06T00-00-00Z`，升级时改 `docker-compose.yml` 中的 tag 即可（勿跨版本滚动迁移数据）

仅限本地开发。投入生产前请改用强凭据、启用 TLS，并阅读 [SILO 部署文档](https://silo.pgsty.com/zh/operations/deployments/)。

### Python 调用 SILO

存储客户端集中在 `app.core.object_storage`（对标 `core.database` 持有 engine 的职责），配置从 `.env` 的 `SILO_*` 读取：

```python
from datetime import timedelta
import io

from app.core.object_storage import (
    ensure_bucket,
    put_object,
    get_object,
    stat_object,
    remove_object,
    presigned_put_url,
    presigned_get_url,
)

ensure_bucket()  # 幂等建桶

# 上传（异步包装，不阻塞事件循环）
await put_object("doc/xxx.pdf", io.BytesIO(data), len(data), content_type="application/pdf")

# 下载
resp = await get_object("doc/xxx.pdf")
data = resp.read()
resp.release_conn()

# 元数据 / 删除
stat_object("doc/xxx.pdf")  # size / content_type / etag
await remove_object("doc/xxx.pdf")

# 预签名 URL（浏览器直传/直取，不经后端）
presigned_put_url("doc/yyy.pdf", expires=timedelta(minutes=15))
presigned_get_url("doc/yyy.pdf", expires=timedelta(hours=1))
```

- presign 与 `stat_object` 为同步短调用，可直接在 endpoint 调用；网络传输类（`put_object`/`get_object`/`remove_object`）已内部 `asyncio.to_thread` 包装。
- 应用容器化时把 `SILO_ENDPOINT` 改成 `silo:9000`。

### 上传功能测试页

访问 `http://127.0.0.1:8000/static/upload_test.html` 可图形化测试文档上传链路（登录 → 选项目/文档 → 发起预签名 → 浏览器直传 SILO → 完成上传建版本），步骤与日志一一对应 §6.5/§6.6。

前提：SILO 容器运行中（`docker compose up -d`），且桶 CORS 已放行 `localhost:8000`（docker-compose 里 `MINIO_API_CORS_ALLOW_ORIGIN` 已配置）。

## Alembic 使用说明

数据库变更通过 **Alembic 自动管理**：迁移文件记录变更历史，`alembic upgrade head` 自动应用，数据库侧的 `alembic_version` 表追踪当前版本（不会重复执行、不会漏）。

### 核心机制

- **迁移文件**（`alembic/versions/<revision>_<名称>.py`）：记录 schema 变更，靠 `down_revision` 串成链
- **版本追踪**：数据库 `alembic_version` 表记录当前版本，自动管理的前提
- **SQL 导出**（`sql/`）：仅无数据库访问权限的环境备用

### 日常流程

```bash
# 1. 生成迁移模板（不连接数据库）
uv run alembic revision -m "add users table"

uv run alembic revision --autogenerate -m "add age to users" 
# 2. 手写 upgrade()/downgrade() 中的 DDL 操作
#    参考 alembic/versions/ 中已有文件的写法

# 3. 自动应用到数据库
uv run alembic upgrade head

# 4. 查看数据库当前版本
uv run alembic current
```

### 迁移文件写法

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("users")
```

### 常用命令速查

| 命令 | 作用 |
|---|---|
| `uv run alembic revision -m "msg"` | 生成迁移文件（手写 DDL） |
| `uv run alembic upgrade head` | 升级到最新 |
| `uv run alembic upgrade <rev>` | 升级到指定版本 |
| `uv run alembic downgrade -1` | 回退一个版本 |
| `uv run alembic current` | 查看当前版本 |
| `uv run alembic history` | 查看迁移历史链 |
| `uv run alembic upgrade head --sql > up.sql` | 导出全链 SQL（无库权限环境手动导入用） |

### 常见字段变更

| 变更 | upgrade() 写法 |
|---|---|
| 加字段 | `op.add_column("users", sa.Column("email", sa.String(255), nullable=True))` |
| 删字段 | `op.drop_column("users", "email")` |
| 改字段名 | `op.alter_column("users", "name", new_column_name="username")` |
| 改类型 | `op.alter_column("users", "age", type_=sa.Integer())` |
| 加唯一约束 | `op.create_unique_constraint("uq_users_email", "users", ["email"])` |
| 加索引 | `op.create_index("ix_users_email", "users", ["email"])` |

### 注意事项

- **已应用的迁移文件永远不要删除**：会破坏 `down_revision` 链，后续迁移对不上
- **反悔已应用的变更**：写一个新迁移做反向操作（如 `op.drop_table`），再 `alembic upgrade head`
- **不要手改已应用的迁移文件**：历史不可变，只允许追加新迁移
- **手动导入 SQL 的环境**：`alembic upgrade head --sql` 导出的文件含版本记录，导入后 alembic 状态保持一致

## 健康检查

| 接口 | 用途 | 是否访问数据库 |
|---|---|---|
| `GET /api/v1/health` | 向后兼容的基础存活检查 | 否 |
| `GET /api/v1/health/live` | 容器 liveness probe，判断进程是否存活 | 否 |
| `GET /api/v1/health/ready` | readiness probe，执行 `SELECT 1` 判断数据库是否可用 | 是 |

数据库不可用时，readiness 返回 HTTP 503。负载均衡器或编排系统应停止向该实例发送新请求，但不应仅因为 readiness 失败立即重启进程；进程重启由 liveness 决定。

## 数据库事务规范

请求处理使用 `app.core.database.DbSession` 注入 Session。当前实现由 `get_db`
在请求成功结束时统一提交，异常时回滚；认证依赖与 Service 共享同一个 Session。

- Service / Repository 的普通写路径使用 `add()`、`flush()`，不另起 `session.begin()`；认证查询已触发 autobegin。
- 必须先提交再入队的后台任务接口显式提交任务记录。入队失败时另行持久化失败状态，再返回服务不可用；客户端可查询并重试。
- Worker 创建独立的同步 Session，不接收请求 Session。
- 文档与规则变更、审核快照通过行锁串行化相关输入。规则修改还校验客户端版本号。
- Repository 不负责提交或回滚；若未来改用 Service 显式事务，需要一起调整认证依赖和数据库依赖，不能混用两套约定。

## 后台任务（Celery + Redis）

耗时操作（文档解析等）不在请求进程内执行：API 原子认领状态后入队，独立 worker 执行，DB 状态列（`parse_status` 等）是 async 与 sync 世界的唯一契约。

### 执行模型

```
FastAPI(async)                   契约=DB                 Celery worker(sync)
请求→原子认领 PARSING ────────────────────────────► submit 任务: MinIO下载+提交docling+存task_id
  ↑                                                    reconcile 任务(beat 每5s):
  └── 返回 202 / parse_status                          扫PARSING→并行poll各版本→终态落库
```

- API 用 asyncpg；worker 内 DB 访问用**同步 psycopg2**（连接无事件循环绑定，无需任何跨循环处理）
- docling 官方只有 async 客户端，在任务内以 `asyncio.run()` 薄桥包裹（客户端自包含，无跨循环共享状态）
- 失败即标 `FAILED` 落 `parse_error`；卡住版本由 beat 对账兜底（超时/任务丢失 → FAILED，提示重新触发）

### 启动

```bash
# Redis（docker compose up -d 已含）
docker compose up -d redis

# dev:worker + beat 单进程(脚本封装,可用 CELERY_CONCURRENCY/CELERY_LOGLEVEL 覆盖)
./scripts/celery_dev.sh

# 等价的手工命令(生产建议 beat 与 worker 同 supervisor 托管)
uv run celery -A app.core.celery_app worker -Q parsing --concurrency=2 --loglevel=INFO
uv run celery -A app.core.celery_app worker -Q review --concurrency=2 --loglevel=INFO
uv run celery -A app.core.celery_app beat --loglevel=INFO
```

> ⚠️ beat 挂了会导致解析永久停在 `parsing` 状态（无人判超时），必须与 worker 同生命周期托管。
> 队列名 `parsing` 在 `app/core/celery_app.py` 的 `task_routes` 配置；规则提取已使用独立 `review` 队列。开发脚本同时消费 `parsing,review`，生产可按上面的命令分别启动 worker。

调度与运维配置（`app/core/celery_app.py`）：时区 `Asia/Shanghai`（cron 直接写北京时间）、`worker_max_tasks_per_child=100`（防内存泄漏）、并发与对账间隔由 settings 控制。beat 表独立维护，可仿照"按 `settings.environment` 条件增删条目"的管理方式。

### 新增任务

1. `app/tasks/` 下新建模块（如 `app/tasks/review.py`），写 `@celery_app.task(name="xxx.yyy", acks_late=True, max_retries=0)`
2. 任务体保持薄包装，业务逻辑放 `app/services/`（worker 内用 `sync_session_factory`）
3. 任务模块会被**自动收集**（`app/core/celery_app.py` 启动时扫描 `app/tasks/` 下所有模块导入注册），无需改任何注册配置
4. 测试用 eager 模式（`celery_app.conf.task_always_eager = True`）直接调 `.delay()`，见 `tests/services/test_parsing_tasks.py`

## 规则版本与提取恢复

- `GET /api/v1/projects/{project_id}/rules` 默认只返回活动招标版本规则；用 `tender_version_id=<UUID>` 只读查询历史版本。
- 换版后旧规则保留，但不能编辑、忽略、确认或重新提取；历史版本的失败提取也不能重试。
- `POST .../rules/confirm` 的 `{}` 表示确认当前版本全部草稿；显式 `rule_ids: []` 返回 422。
- 提取产生的草稿必须等所属提取运行成功后才能编辑或确认，部分失败时先重试失败批次。
- 进度仍使用实际接口 `GET .../rules/extract/{run_id}`，重试使用 `POST .../rules/extract/{run_id}/retry`，尚未引入通用 `/jobs`。
- 每批完成即保存规则和进度。数据库原子认领运行；重复投递不重复执行，重试更换执行令牌，旧 worker 的迟到结果不会覆盖当前运行。
- 入队失败返回 503，并保存可查询的失败记录。beat 在 `parsing` 队列执行规则提取对账；默认排队超过 5 分钟、执行超过 30 分钟标记失败，用户重试时保留成功批次。
- 时限是运行的总时限，不是无进展时间。大文档可调整 `.env.example` 中三个 `RULE_EXTRACTION_*` 配置；beat 和 parsing worker 必须持续运行。

## 审核运行输入与证据基线

本阶段创建状态为 `ready` 的审核运行：表示输入已冻结，尚未启动逐项审核。

| 接口 | 功能 |
|---|---|
| `POST /api/v1/projects/{project_id}/reviews` | 固定招投标版本和已确认规则快照 |
| `GET /api/v1/projects/{project_id}/reviews` | 分页查询运行列表 |
| `GET /api/v1/projects/{project_id}/reviews/{run_id}` | 查询版本、快照哈希和运行规则 |
| `GET /api/v1/projects/{project_id}/reviews/{run_id}/rules/{rule_id}/evidence` | 检索本次投标版本的候选原文，`limit` 为 1–50 |

创建请求示例（UUID 替换为实际版本公开 ID）：

```json
{
  "tender_version_id": "11111111-1111-1111-1111-111111111111",
  "bid_version_id": "22222222-2222-2222-2222-222222222222"
}
```

创建时两个版本必须属于当前项目、类型正确、处于活动状态且解析完成并有文本；
规则提取不能仍在排队或执行，全部草稿须先确认或忽略，至少保留一条确认规则。
可传 `Idempotency-Key`（1–64 字符）：同键同输入返回原运行，同键不同输入返回 409。
省略幂等键会创建新运行。规则快照和版本在后续换版后保持不变。

证据接口的 `rule_id` 是运行详情 `rules[].public_id`，不是项目原始规则 ID。
当前检索为中文二元词与英文词的 BM25 基线 `keyword-v1`；`score` 仅用于候选排序，
不表示符合性或置信度。没有命中返回空列表；未知页码保留 `null`，不生成页码。
删除输入文档后禁止访问运行详情和证据。尚未实现向量召回、逐项审核、评分、复核和报告。

### 本次升级

新增迁移到 `b8e091080002`。先停止旧版本 worker 和 beat，再运行 `uv run alembic upgrade head`，
随后一起启动新版 API、worker、beat。旧队列中的提取消息只有一个参数，与新增执行令牌不兼容；
停止旧 worker 后应让过期运行经对账进入失败，再通过重试接口发布新格式消息。
不要在有旧 worker 执行时滚动切换。迁移不会修改已有文件或规则内容。

## 测试

```bash
uv run pytest
```

## 项目结构

```
app/
  api/             # 路由层
  core/            # 配置、数据库、异常
  models/          # SQLAlchemy 模型
  repositories/    # 数据访问层
  schemas/         # Pydantic 模型
  services/        # 业务逻辑层
alembic/           # 迁移文件
sql/               # 导出的 SQL（备用）
tests/
```
