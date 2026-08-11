# 就绪检查与数据库事务规范实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use XWLPowers:executing-plans to implement this plan task-by-task.

**Goal:** 增加可供编排系统使用的存活/就绪探针，并建立请求级数据库事务的统一提交与回滚规范。

**Architecture:** `/health` 继续作为向后兼容的存活接口，新增 `/health/live` 和执行 `SELECT 1` 的 `/health/ready`。数据库依赖在每个请求中创建一个 Session，成功时提交，异常（包括取消）时回滚；统一的 `DbSession` 类型使用 FastAPI `scope="function"`，确保事务结束发生在响应发送前。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy AsyncSession、PostgreSQL、pytest

---

### Task 1: 建立请求级事务依赖

**Files:**
- Modify: `app/core/database.py`
- Create: `tests/core/test_database.py`

**Step 1: 写失败测试**

用可观察的异步 Session 替换 Session factory，分别验证成功退出调用一次 `commit()`，业务异常调用一次 `rollback()` 并原样抛出，同时 Session 上下文被关闭。

**Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_database.py -q`

Expected: FAIL，因为现有 `get_db()` 不提交也不回滚。

**Step 3: 实现最小事务边界**

在 `get_db()` 的 `yield` 外使用 `try/except BaseException`：正常退出 `commit()`，异常退出 `rollback()` 后重新抛出。增加：

```python
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]
```

仓储层只允许 `flush()`，不得自行 commit/rollback；事务由依赖统一结束。

**Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_database.py -q`

Expected: PASS。

### Task 2: 增加存活与数据库就绪探针

**Files:**
- Modify: `app/api/v1/endpoints/health.py`
- Modify: `tests/test_health.py`

**Step 1: 写失败测试**

验证 `/health/live` 不访问数据库并返回 200；用 dependency override 注入可用 Session，验证 `/health/ready` 执行 `SELECT 1` 并返回 200；注入抛错 Session，验证返回统一格式的 503 和 request ID。

**Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_health.py -q`

Expected: FAIL，因为两个探针路由尚不存在。

**Step 3: 实现探针**

就绪接口执行 `await session.execute(text("SELECT 1"))`。数据库异常写入 `readiness_check_failed` 异常日志，并抛出不暴露内部连接信息的 `AppError("Service Unavailable", 503)`。

**Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_health.py -q`

Expected: PASS。

### Task 3: 记录事务规范和运行方式

**Files:**
- Modify: `README.md`

**Step 1: 增加文档**

记录事务所有权、仓储层规则、后台任务禁止复用请求 Session，以及存活/就绪接口语义。

### Task 4: 全量验证

**Files:**
- Verify only

**Step 1: 运行全量测试**

Run: `uv run pytest -q`

Expected: 全部 PASS。

**Step 2: 运行编译和差异检查**

Run: `uv run python -m compileall -q app tests && git diff --check`

Expected: 退出码 0。

