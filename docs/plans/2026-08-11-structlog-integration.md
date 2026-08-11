# structlog 集成实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use XWLPowers:executing-plans to implement this plan task-by-task.

**Goal:** 用 structlog 替换自定义 JSON formatter，并保留现有请求关联、环境格式切换以及标准 logging 兼容能力。

**Architecture:** `app/core/logging.py` 使用 structlog 的 `ProcessorFormatter` 作为统一出口；structlog 业务事件与 Uvicorn、SQLAlchemy 等标准库日志共享时间、级别、logger 名称和异常处理器。请求中间件使用 `structlog.contextvars` 绑定并恢复 `request_id`，业务日志直接传递结构化字段。

**Tech Stack:** Python 3.12、structlog、标准库 logging、FastAPI/Starlette、pytest

---

### Task 1: 锁定 structlog 输出契约并加入依赖

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/core/test_logging.py`

**Step 1: 写失败测试**

将日志测试改为期望根 handler 使用 `structlog.stdlib.ProcessorFormatter`，并通过真实 structlog logger 验证 JSON 输出包含 `timestamp`、`level`、`logger`、`event`、`message`、`request_id` 及业务字段。

**Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_logging.py -q`

Expected: FAIL，原因是 structlog 尚未安装或现有 formatter 类型不匹配。

**Step 3: 加入依赖**

Run: `uv add structlog`

Expected: `pyproject.toml` 和 `uv.lock` 包含受支持的 structlog 版本。

### Task 2: 使用 ProcessorFormatter 实现统一配置

**Files:**
- Modify: `app/core/logging.py`
- Test: `tests/core/test_logging.py`

**Step 1: 实现最小配置**

配置共享 processors：`merge_contextvars`、logger name、level、ISO UTC timestamp、异常格式化；structlog 链以 `wrap_for_formatter` 结束，标准 logging 通过 `foreign_pre_chain` 进入相同链路。JSON 使用 `JSONRenderer`，本地文本使用 `ConsoleRenderer(colors=False)`。

**Step 2: 运行测试确认通过**

Run: `uv run pytest tests/core/test_logging.py -q`

Expected: PASS。

### Task 3: 迁移请求和异常业务日志

**Files:**
- Modify: `app/core/middleware.py`
- Modify: `app/core/exceptions.py`
- Modify: `tests/core/test_errors.py`

**Step 1: 写失败测试**

测试请求日志来自 structlog BoundLogger，事件字段仍包含安全 path、状态码、耗时和 `request_id`；异常日志包含事件名、请求 ID 和异常信息。

**Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_errors.py -q`

Expected: FAIL，原因是业务模块仍使用标准 logger/旧上下文 API。

**Step 3: 迁移业务调用**

使用 `structlog.get_logger()`；请求开始先清理 structlog context，再绑定 `request_id`，最终用 tokens 恢复。日志调用使用 `logger.info("http_request_completed", ...)` 和 `logger.exception("unhandled_error", ...)`。

**Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_errors.py -q`

Expected: PASS。

### Task 4: 全量验证

**Files:**
- Verify only

**Step 1: 运行全量测试**

Run: `uv run pytest -q`

Expected: 全部 PASS。

**Step 2: 执行编译检查和差异检查**

Run: `uv run python -m compileall -q app tests && git diff --check`

Expected: 退出码 0。

