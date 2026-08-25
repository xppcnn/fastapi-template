# fastapi-template

基于 FastAPI 的项目模板，集成 SQLAlchemy 2.0（异步）+ Alembic 数据库迁移。

## 技术栈

- FastAPI + pydantic-settings
- SQLAlchemy 2.0（async） + asyncpg + PostgreSQL
- Alembic（数据库迁移）
- pytest（测试）

## 快速开始

```bash
# 1. 启动本地 PostgreSQL 与 SILO 对象存储
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

请求处理使用 `app.core.database.DbSession` 注入 Session：

```python
from app.core.database import DbSession


async def create_item(session: DbSession) -> dict:
    try:
        async with session.begin():
            item = Item(...)
            await create_item_repo(session, item=item)
            return {"id": item.id}
    except IntegrityError as exc:
        raise AppError("Item already exists", code=409) from exc
```

统一规则：

- 一个请求共享一个 Session；请求依赖 `get_db` 只负责打开和关闭 Session，不再自动提交。
- Service 的写路径用 `async with session.begin():` 包裹整个工作单元（含业务读取），正常退出自动 `commit()`，异常自动 `rollback()`；只读服务无需 begin。
- `session.begin()` 必须是该 Session 的首次语句——Session 首次执行语句会自动开启事务（autobegin），之后再 `begin()` 会抛 `InvalidRequestError`。因此不要在依赖中先读库再在 Service 中 begin；写接口的身份校验应在 begin 块内完成（或依赖先显式结束只读事务）。
- Repository 可以执行查询、`add()` 和 `flush()`，不得调用 `commit()` 或 `rollback()`。
- Service 负责组织同一事务内的业务操作，但不持有全局 Session。
- 不得把请求 Session 传给后台任务；Worker 或后台任务必须创建自己的 Session 和事务。
- 需要独立事务时显式创建新 Session，不要在同一个请求 Session 中嵌套提交。

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
