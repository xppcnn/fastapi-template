from __future__ import annotations

from app.integrations.llm.base import LLMMessage

PROMPT_VERSION = "rule-extraction:2026-08-27:v1"

# 通用提取约束:恒定不变,作为稳定前缀的一部分(同一招标版本所有批次逐字节相同)。
SYSTEM_MESSAGE = (
    "你是招投标文件合规审核助手,负责从招标文件中提取审核规则。\n"
    "硬性要求:\n"
    "1. 只提取招标文件明确提到或使用同义表达的内容,禁止依据行业经验自行外推、补全或猜测;\n"
    "2. 若任务指定的片段中没有提及任何规则,请返回 mentioned=false;某一段没有提及只代表该段没有,不代表全文没有;\n"
    "3. 只输出 JSON,不要输出任何解释、前后缀或 Markdown 代码块;\n"
    "4. 所有规则必须引用任务给出的真实片段 UUID,禁止编造 UUID;\n"
    "5. 使用简体中文输出标题与描述。"
)

_FULL_TEXT_TEMPLATE = (
    "以下是招标文件全文(用于全局上下文):\n<document>\n{text}\n</document>"
)


def build_full_text_message(tender_text: str) -> LLMMessage:
    """招标全文消息:同一招标版本内逐字节相同 → 服务商 prompt cache 的稳定前缀。"""
    return LLMMessage(role="user", content=_FULL_TEXT_TEMPLATE.format(text=tender_text))
