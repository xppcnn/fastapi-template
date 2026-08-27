from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.integrations.llm.base import LLMMessage
from app.models.review_rule import RuleType
from app.prompts.common import SYSTEM_MESSAGE, build_full_text_message

COMMON_STYLE_HINT = (
    ".\n"
    "请按字段要求输出 JSON 数组:rule_type(枚举值见类型说明), title(不超过60字的短标题), "
    "description(完整的判定口径描述,必须能逐句对应到片段原文), "
    "evaluation_method(deterministic 仅当能用 {exists,equals,gte,lte,contains_all} 受控运算符表达,"
    "否则 semantic), condition(仅 evaluation_method=deterministic 时给出), "
    "scoring_method(none/objective/subjective), max_score(仅评分项), "
    "evaluation_criterion(仅评分项,扣分判定口径), source_segment_ids(该规则依据的本批片段 UUID 列表,"
    "至少一个), explicitly_stated(该规则是否确为片段明确提及。若只是你的经验推断,必须写 false)"
)


@dataclass(frozen=True)
class RuleTaskPrompt:
    rule_type: RuleType
    concept_boundary: str
    must_vs_may: str
    scoring_breakdown: str | None = None
    extra: str = ""

    def build_batch_prompt(self, *, chapter: str, batch_uuids: list[UUID]) -> str:
        parts = [
            f"你是规则提取批次任务,本批次对应招标文件章节「{chapter}」。",
            f"只提取“{self.concept_boundary}”类规则。",
            self.must_vs_may,
        ]
        if self.scoring_breakdown:
            parts.append(self.scoring_breakdown)
        parts.append(
            "本批可引用的片段 UUID:"
            + "、".join(str(u) for u in batch_uuids)
            + "。严禁引用本列表之外的 UUID。"
        )
        parts.append("若本段没有任何可提取的该类型规则,输出 mentioned=false。")
        parts.append(self.extra)
        return "\n".join(p for p in parts if p) + COMMON_STYLE_HINT


RULE_PROMPTS: dict[RuleType, RuleTaskPrompt] = {
    RuleType.DISQUALIFICATION: RuleTaskPrompt(
        rule_type=RuleType.DISQUALIFICATION,
        concept_boundary=(
            "否决项(否决投标条件)。以下表达均须归类为否决项:否决投标、不予受理、无效投标、"
            "重大偏差、实质性偏离、废标条件、拒绝其投标。不得把这些与普通资格或评分要求混为一谈"
        ),
        must_vs_may=(
            "关注“投标将被否决/无效”的触发条件,如逾期递交、未按时提交保证金、偏离招标文件的实质要求等。"
        ),
        extra="每条否决项都必须给出可回溯的片段来源,判断失误代价最高,宁可保守也要严格对应原文。",
    ),
    RuleType.QUALIFICATION: RuleTaskPrompt(
        rule_type=RuleType.QUALIFICATION,
        concept_boundary=(
            "资格项(投标主体准入条件)。如经营范围、营业执照、资质等级、安全生产许可、"
            "人员资格证书、同类业绩、财务状况、信用记录等门槛要求"
        ),
        must_vs_may=(
            "注意区分“必须满足”的准入门槛与“如适用/如有则提供”或可选项,不得自行判断某项要求是否适用。"
        ),
    ),
    RuleType.RESPONSE: RuleTaskPrompt(
        rule_type=RuleType.RESPONSE,
        concept_boundary=(
            "响应项(商务或技术要求条款)。如技术参数、供货范围、服务要求、交付期限、"
            "报价要求、保证金提交方式等需要投标文件应答的内容"
        ),
        must_vs_may=(
            "严格区分“必须提供/必须响应/必须满足”与“如适用/可选/如有则/不要求”等限定。"
            "明确标注该项的必须性(must 或 may),不得替你自行判断适用性。"
        ),
        extra="每个响应项在 description 中先用【必须|可选|如有则】标注必须性。",
    ),
    RuleType.SCORING: RuleTaskPrompt(
        rule_type=RuleType.SCORING,
        concept_boundary=(
            "评分项(纳入评分办法的得分条件)。如评审因素、分值范围、评分标准、"
            "加分项、扣分规则。注意区分“评分项”与“评分要求”"
        ),
        must_vs_may=(
            "只提取明确出现在评分办法/评标标准中的内容,不把普通技术要求当成评分项。"
        ),
        scoring_breakdown=(
            "每个评分项必须拆分两个字段:evaluation_method/condition 描述“评价对象”"
            "(需要响应或撰写的内容);evaluation_criterion 描述“扣分判定口径”(通用评审规则、"
            "扣分规则、适用范围),供后续确定性评分判定使用。客观分值写入 max_score。"
        ),
    ),
}


@dataclass(frozen=True)
class SegmentRef:
    """片段目录条目:UUID → 页码/顺序,供模型引用与程序回查。"""

    uuid: UUID
    page_no: int | None
    order_index: int = 0


_BATCH_CATALOG_TEMPLATE = (
    "以下为本批片段的目录(UUID → 页码),[order] 为文档内顺序:\n{lines}"
)


def _format_catalog(refs: list[SegmentRef]) -> str:
    lines = [
        f"- {r.uuid} [{r.order_index}] 第{r.page_no}页"
        if r.page_no is not None
        else f"- {r.uuid} [{r.order_index}] 无页码"
        for r in refs
    ]
    return _BATCH_CATALOG_TEMPLATE.format(lines="\n".join(lines))


def build_extract_messages(
    *,
    rule_type: RuleType,
    tender_text: str,
    batch_refs: list[SegmentRef],
    chapter: str,
) -> list[LLMMessage]:
    """按固定顺序组消息,保证每个批次的公共前缀一致(稳定前缀吃 prompt cache):

    system(通用约束,恒定) → user(招标全文,恒定) → user(本批片段目录) → user(类型任务 prompt)
    """
    prompt = RULE_PROMPTS[rule_type]
    batch_uuids = [r.uuid for r in batch_refs]
    return [
        LLMMessage(role="system", content=SYSTEM_MESSAGE),
        build_full_text_message(tender_text),
        LLMMessage(role="user", content=_format_catalog(batch_refs)),
        LLMMessage(
            role="user",
            content=prompt.build_batch_prompt(chapter=chapter, batch_uuids=batch_uuids),
        ),
    ]
