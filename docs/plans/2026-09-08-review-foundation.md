# 审核基础收口与输入快照 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use XWLPowers:executing-plans to implement this plan task-by-task.

**Goal:** 在已有代码上完成 A 阶段，并交付 B 阶段的审核运行快照和关键词证据检索基线。

**Architecture:** 保留模块化单体和现有请求提交事务约定。规则写入限定活动招标版本；提取任务用数据库条件更新认领、执行令牌隔离重试、逐批持久化和超时对账。审核运行保存明确文档版本与确认规则快照，证据只能来自本次投标版本。

**Tech Stack:** FastAPI、SQLAlchemy、PostgreSQL、Celery、pytest；测试使用 SQLite 与 Fake LLM。

---

用户已授权继续执行；在当前功能分支实施，不另开会话。暂不部署、不连接真实模型、不修改现有业务数据库。

### Task 1：规则版本边界

- 修改 `app/services/review_rules.py`、`app/schemas/review_rule.py`。
- 在 `tests/rules/test_rule_api.py` 增加换版后的列表、确认、编辑、忽略和提取测试；先运行并确认失败。
- 默认列表只展示活动招标版本；显式 `tender_version_id` 可只读查询历史；旧版本规则不可修改、确认或重新提取。
- 空规则 ID 列表不得退化为全部确认；条件字段更新正确支持结构化值与清空。
- 运行 `.venv/bin/python -m pytest tests/rules/test_rule_api.py -q`。

### Task 2：规则提取可靠性

- 修改 `app/workflows/extract_rules.py`、`app/services/review_rules.py`、`app/models/rule_extraction.py`、`app/tasks/rule_extraction.py`、`app/core/config.py`、`app/core/celery_app.py`。
- 新增迁移 `alembic/versions/20260908_0008_extraction_recovery.py`。
- 先测试入队异常、重复消息、旧消息、超时、失败重试和逐批落库。
- 执行令牌随重新排队改变；数据库原子认领；过期 worker 不得写入新一轮结果。提取运行在配置的总时限内结束，超时由 beat 标记失败供人工重试。
- 成功批次即时落库，重试跳过成功批次；确认/编辑提取规则前必须等待提取成功。
- 修正 `scripts/celery_dev.sh` 同时消费 parsing/review，更新 README 和 `.env.example`。
- 运行 `.venv/bin/python -m pytest tests/rules tests/services/test_parsing_tasks.py -q`。

### Task 3：审核运行快照与证据基线

- 新建 `app/models/review.py`、`app/schemas/review.py`、`app/services/reviews.py`、`app/services/retrieval.py`、`app/api/v1/endpoints/reviews.py` 和对应迁移。
- 在 `tests/reviews/` 先测试创建、幂等、版本约束、快照不变、跨组织拒绝和证据版本隔离。
- POST `/projects/{project_id}/reviews` 创建 ready 运行；GET 列表/详情；GET `/{run_id}/rules/{rule_id}/evidence` 返回关键词候选片段与原始页码。
- 创建时锁定项目及文档，要求解析完成、规则全部确认或忽略、无提取正在进行，并复制规则内容。相同幂等键的不同输入返回 409。
- 基线仅返回候选证据，不生成合规结论、不把未召回解释为缺失；向量检索留待用标注样本比较。
- 运行 `.venv/bin/python -m pytest tests/reviews -q`。

### Task 4：验证与交付记录

- 运行全量 pytest、变更文件 Ruff、迁移 SQL 生成和迁移往返验证（隔离环境可用时）。
- 更新 README 的实际接口、启动方法、事务约定和本次交付边界。
- 记录已完成项及后续 C–F 阶段，不宣称整个 MVP 已完成。
