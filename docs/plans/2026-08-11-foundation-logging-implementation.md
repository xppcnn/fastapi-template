# 基础日志模块实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use XWLPowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 FastAPI 应用建立可配置、可关联请求且默认不泄露敏感请求数据的基础日志设施。

**Architecture:** 使用 Python 标准库 `logging`，由 `app/core/logging.py` 统一提供文本与 JSON 格式、日志初始化以及基于 `ContextVar` 的 `request_id` 上下文。现有请求 ID 中间件负责绑定/清理上下文并记录请求完成事件，应用启动时根据环境和日志配置初始化根日志器。

**Tech Stack:** Python 3.12、标准库 logging/contextvars、FastAPI/Starlette、pytest

---

### Task 1: 定义日志格式和请求上下文

**Files:**
- Create: `app/core/logging.py`
- Create: `tests/core/test_logging.py`

**Step 1: 写失败测试**

测试 JSON 日志必须包含 `timestamp`、`level`、`logger`、`event`、`message` 和绑定后的 `request_id`；异常日志包含异常信息。

**Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_logging.py -q`

Expected: FAIL，原因是 `app.core.logging` 尚不存在。

**Step 3: 实现最小日志模块**

实现 `bind_request_id()`、`reset_request_id()`、上下文过滤器、JSON formatter 和 `configure_logging()`。只使用标准库，避免为基础功能增加运行时依赖。

**Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_logging.py -q`

Expected: PASS。

### Task 2: 记录 HTTP 请求完成事件

**Files:**
- Modify: `app/core/middleware.py`
- Modify: `tests/core/test_errors.py`

**Step 1: 写失败测试**

对健康检查发起带固定 `X-Request-ID` 的请求，断言日志记录包含事件名、请求 ID、method、path、status code 和非负耗时。附带敏感 query 参数，断言日志不包含 query 值。

**Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_errors.py -q`

Expected: FAIL，原因是尚未产生日志事件。

**Step 3: 实现请求日志**

在 `RequestIDMiddleware` 中绑定上下文，使用 `time.perf_counter()` 计算耗时，在响应完成后写一条 `http_request_completed` 日志并清理上下文。只记录 `request.url.path`，不记录 query、body 和 headers。

**Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_errors.py -q`

Expected: PASS。

### Task 3: 接入配置和应用启动

**Files:**
- Modify: `app/core/config.py`
- Modify: `app/main.py`
- Modify: `tests/test_config.py`

**Step 1: 写失败测试**

断言默认日志级别为 `INFO`、开发环境默认文本格式、生产环境的 `auto` 格式解析为 JSON，并支持 `LOG_LEVEL`/`LOG_FORMAT` 环境变量覆盖。

**Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_config.py -q`

Expected: FAIL，原因是日志配置项尚不存在。

**Step 3: 实现并接入**

增加 `log_level` 和 `log_format` 配置，提供格式解析方法；在创建 FastAPI 应用前调用 `configure_logging()`。

**Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_config.py -q`

Expected: PASS。

### Task 4: 全量验证

**Files:**
- Verify only

**Step 1: 运行全量测试**

Run: `uv run pytest -q`

Expected: 全部 PASS。

**Step 2: 执行静态编译检查**

Run: `uv run python -m compileall -q app tests`

Expected: 退出码 0。

