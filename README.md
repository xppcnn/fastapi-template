# fastapi-template

基于 FastAPI 的项目模板，集成 SQLAlchemy 2.0（异步）+ Alembic 数据库迁移。

## 技术栈

- FastAPI + pydantic-settings
- SQLAlchemy 2.0（async） + asyncpg + PostgreSQL
- Alembic（数据库迁移）
- pytest（测试）

## 快速开始

```bash
# 1. 启动本地 PostgreSQL
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
