# 规则提取实现要点（吸收 OpenBidKit 工程经验）

日期：2026-08-27
范围：Task 8（模型适配器、运行记录与审核规则提取）的细化增量
基础：《design §7.2 规则提取》、Task 8 Step 1-6

> **状态：已完成**（2026-08-27 实施，tests/llm + tests/rules 全绿、无网络访问；含跨段去重、越界待确认、失败批次重跑、稳定前缀回归测试；迁移 upgrade/downgrade 与 alembic check 均通过）

## 背景

FastAPI 后端的规则提取应在保持「结构化 Schema + 片段 UUID 引用 + 人工确认门槛 +
ModelRun 追溯」的基础上，吸收 OpenBidKit（易标投标工具箱）的四个工程经验：
任务化 prompt、稳定前缀缓存、分段结果合并规则、失败批次恢复。

## 原则一：任务化 prompt，按规则类型拆分

不写"提取所有规则"性质的单一长提示词；按规则类型（否决/资格/响应/评分）各建独立
prompt 模块，存放在 `app/prompts/rule_extraction.py`，每类包含：

- `concept_boundary`：该类规则的概念边界与同义表达清单（如否决项：否决投标、
  不予受理、重大偏差、实质性偏离 均须归类），仿 `bidAnalysisTask.cjs` 的
  `buildInvalidBidAndRejectionItemsPrompt`。
- `must_vs_may`：响应项必须区分「必须提供」与「如适用/可选」，不得自行判断，
  仿 `responseFileRequirements`。
- `scoring_breakdown`：评分类拆分两字段——「评价对象」（需要响应/撰写的内容）与
  「扣分判定口径」（通用评审规则、扣分规则、适用范围），供 Task 10 确定性 evaluation
  使用，仿 `techRequirements` 的「评分项 vs 评分要求」。

输出统一为 Pydantic Schema 强约束 JSON（沿用现有计划），仅最后的批量确认接口负责
落 confirmed。

## 原则二：稳定前缀吃 prompt cache

提取调用均按固定顺序组消息，保证每个批次的公共前缀一致（全文位次永远不变）：

```text
messages = [
  system(通用提取约束: 不编造 / 无提及写"没有提及" / 只输出结果 / 简体中文),
  user(招标全文),                      # ← 稳定前缀，永远相同
  user(片段目录: UUID→章节/页码),        # ← 随批次变化，放全文之后
  user(类型任务 prompt),
]
```

同一招标版本的所有批次共享稳定前缀 → 服务商 Prefix Cache 命中 → 显著降低多租户
长文档场景的输入成本（OpenBidKit 实测输入成本下降约一个量级）。提示词版本号写入
ModelRun（沿用现有 plan 的 `prompt_version` 字段），缓存结构变更时提醒更新版本。

实现上以 `app/prompts/rule_extraction.py` 暴露 `build_extract_messages(...)`，
由 `app/workflows/extract_rules.py` 调用，便于对批次顺序进行单测。

## 原则三：仅提取文件明确提到；经验项走人工补充渠道

- 提取 prompt 只允许「招标文件明确提到或同义表达」的内容；禁止模型经验外推。
- 模型越界产物一律落为 `draft + 待确认`，不作为 confirmed，防止错误规则向后传播
  （对应 §7.3「错误规则不向后传播」）。
- 用户「经验补充规则」继续走 ReviewRule 的人工补充通道（无 ModelRun 关联、归属当前
  招标版本），与 OpenBidKit 让 LLM 自动补 3-5 条的做法刻意不同。

## 原则四：分段结果合并与去重的程序铁律

按章节批处理时，跨章节需要合并，直接采用 OpenBidKit `segmentedAiResultMerger` 的
判定语义，写入合并逻辑与测试：

1. 某段写"没有提及"只代表该片段没有，以其他片段的有效信息为准。
2. 不新增分段结果中没有的信息。
3. 跨段重复规则由**程序端去重**（规范化 key = 类型 + 判定口径），不交给 LLM 合并。

去重后、确认前允许用户看到合并来源（哪些片段产生该 draft），便于复核。

## 明确不抄的（差异化保持）

1. **不返回原文文本**：模型只返回片段 UUID，程序回查片段、校验页码、保存 draft
   （沿用 Task 8 Step 4）。OpenBidKit 全量文本 + 无页码是它最弱处，我们用 docling
   segment + page 保持优势。
2. **不引入 LLM 经验补充**：见原则三。
3. **不引入标段限定**（`bidSectionContext`）：MVP 单标段，`section_hint` 预留字段但不实现。

## 落地改动（相对 Task 8 的增量）

| 文件 | 改动 |
|---|---|
| `app/prompts/rule_extraction.py` | 新增：四类规则概念边界 + 拆分评测（原则一） |
| `app/workflows/extract_rules.py` | 新增：稳定前缀消息构建 + 批次并发（原则二、四） |
| `app/services/review_rules.py` | 新增：程序端去重规范化 key + 越界产物标注待确认（原则三、四） |
| `app/schemas/review_rule.py` | 评分项新增 `evaluation_criterion`（扣分判定口径）字段 |
| `app/integrations/llm/openai_compatible.py` | 确认支持 `context_length_limit` 供切分/缓存策略 |
| `tests/rules/test_rule_extraction.py` | 新增：merge 铁律、跨段去重、越界产物待确认、稳定前缀测试 |
| `app/tasks/rule_extraction.py` | 新增：Celery 薄包装（`review` 队列），逻辑在 `workflows/extract_rules.py` |
| `app/core/celery_app.py` | `task_routes` 增加 `review` 队列（LLM 密集，与 `parsing` 分离） |
| `app/core/config.py` + `.env.example` | 新增 LLM 配置：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_CONTEXT_LENGTH_LIMIT` |
| `app/api/v1/endpoints/review_rules.py` | `POST /rules/extract` 原子认领后入队返回 **202 + job_id**，进度走 `GET /jobs/{job_id}` 轮询（**不做 SSE**） |
| `alembic/versions/..._rule_extract_batches.py` | 新增：规则提取批次/进度追踪（按批 checkpoint，失败批次单独重跑；`model_run` 的 attempt 链承接重试语义） |

## 决策记录：Strands Agents 使用边界与试点（2026-08-27）

### 背景

评估 AWS 开源的 Strands Agents（模型驱动 agent SDK：agent loop、结构化输出、
上下文管理、hooks、OTel、swarm/graph/A2A 多 agent 模式）是否可以用于本项目。

### 决策 1：MVP 主链路不使用 agent 框架

规则提取、逐项审核、评分、查重的每个步骤必须是"固定输入的一次可追溯调用"
（ModelRun 语义），agent loop 会造成以下破坏：

- 输入随每步变化，ModelRun 的"一次调用 = 固定输入 + 尝试链"不可追溯；
- 合并器/枚举的"程序不允许模型扩展状态"约束与模型自主规划冲突；
- 框架 context manager 会破坏"稳定前缀吃 prompt cache"的消息顺序（原则二）。

保持 `app/integrations/llm/` 薄协议（Protocol + Fake 适配器 + 无网络测试）不变。

### 决策 2：Strands 的三个候选落点（按价值排序）

| 落点 | 场景 | 时机 |
|---|---|---|
| 人工复核助手 | 复核工作台对话式质询：检索工具取证据、只"提议修订"、永不自动落库，修改仍走 FindingRevision + 用户确认（Interrupt 模式） | 试点通过后 |
| 审核报告草稿 | 风险摘要/结论表述/免责声明的 LLM 起草（报告主体仍是程序汇总） | Task 12 试点 |
| 用户咨询助手 | 对自有文档/规则/结论的 RAG 问答，会话管理 + guardrails | MVP 后 |

### 决策 3：试点方案

- Task 12 报告阶段把"报告草稿用 Strands 起草"列为**可选实验**，一周内验证：
  工具化 findings/evidence/scores 查询 + 片段检索；验证 loop/OTel 在 Celery
  `review` 队列 worker 内的表现；验证是否破坏 prompt cache。
- 试点通过后再决策人工复核助手（需确认追溯方案：AgentRun 会话 → 多条
  ModelRun，或按"提议的修订"记录一次 run）。
- 从现在起**不**新建 `app/integrations/agent/` 边界，避免与非确定性需求互相诱惑；
  试点时再评估独立边界和候选框架替代（LangGraph / OpenAI Agents SDK 等在此
  文档记录即可，不展开）。

## 决策记录：LLM 适配层选型（2026-08-27）

- **不引入通用 LLM 框架库**（LangChain / LlamaIndex / pydantic-ai / LiteLLM / Strands
  均不用于 MVP 主链路）；"接入多种模型"由 OpenAI-compatible 协议本身覆盖
  （DeepSeek/通义/智谱/Kimi/Ollama/LM Studio/vLLM 均暴露 `/v1/chat/completions`，
  换 `base_url + api_key + model` 即可）。
- `app/integrations/llm/openai_compatible.py` 基于**官方 `openai` SDK（async）**实现，
  SDK 是任务内可引用的运行时依赖，不属于框架级抽象。
- 适配器内两层职责自持：`response_format` 按 provider 能力回退（`json_object` 普适 /
  `json_schema` 仅部分支持）+ 输出校验失败触发**外部重试链**（对应 ModelRun attempt），
  校验与追溯逻辑留在 `app/services/model_runs.py`，不交给库。
- 未来出现统一路由/成本聚合/跨云 failover 需求时，在 Protocol 后新增 LiteLLM 实现
  （几十行），业务代码不动——维持薄协议边界即为此留位。

## 决策记录：提示词管理规范（2026-08-27）

- **代码内函数式管理**，不用 DB/模板文件：prompt 与 Schema/程序校验强耦合
  （改 prompt 常需同步改 schema、合并器、测试），git 历史 = prompt 变更史，
  diff/review/回滚免费获得；DB 覆盖留到未来（代码默认 + DB 覆盖 + 自动派生版本号
  记入 ModelRun），MVP 不做。
- 目录结构 `app/prompts/`：`common.py`（稳定 system + 稳定前缀构建）、
  `rule_extraction.py`、`semantic_review.py`、`subjective_scoring.py`、`report.py`。
- 每个模块两个约定：
  1. `PROMPT_VERSION` 常量（如 `"rule-extraction:2026-08-27:v1"`）写入 ModelRun 的
     `prompt_version`；改 prompt 必须升版；
  2. `build_*_messages(input)` 函数（非字符串常量），内部保证稳定前缀顺序
     （原则二），输入为结构化对象。
- `prompt_version` 只用于 ModelRun 追溯，**不是缓存键**（缓存键是请求内容 hash）；
  微调措辞升版即可，继续吃缓存靠"开头不变"的纪律。
- 回归测试守则：四类规则 messages 前两条（system + 招标全文）必须逐字节相同，
  守护稳定前缀（prompt cache 优化）不被破坏。

## 决策记录：后台化与进度暴露（2026-08-27）

- 规则提取走 **Celery**（`app/tasks/rule_extraction.py`），与解析链路同构：
  API 原子认领状态后入队、DB 状态列是 async 与 sync 世界的唯一契约、失败批次
  单独重跑、卡住兜底沿用 worker 侧对账思路。
- **独立 `review` 队列**：与 `parsing`（CPU 密集 docling）分离，`review` 为 LLM 密集
  队列（规则提取/逐项审核/评分共用），concurrency 按模型限流调整。
- **不做 SSE**：MVP 进度 = `202 + GET /jobs/{job_id}` 轮询（总批数/完成批/失败批/状态）。
  规则提取进度粒度粗（每章一批，10-60s/批），轮询 3-5s 体验相同；SSE 需跨进程
  事件推送（worker → FastAPI），引入连接生命周期/断线恢复成本。SSE 留到
  Task 10 逐项审核（每条 finding 实时推送有价值）再评估。
- 测试用 eager 模式（`task_always_eager = True` 直接 `.delay()`），保持无网络
  （沿用 `tests/services/test_parsing_tasks.py` 套路）。

## 验收

`uv run pytest tests/llm tests/rules -q` PASS，普通测试无网络访问；
规则提取接口：四类规则、来源片段、evaluation_method、condition、scoring_method、
跨段去重、人工补充规则均验证通过。
