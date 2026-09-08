"""关键词候选召回基线；分数仅用于排序，不表达符合性或置信度。"""

import math
import re
from collections import Counter

from app.models.document import DocumentBlock

RETRIEVAL_VERSION = "keyword-v1"


def _terms(text: str) -> list[str]:
    terms = re.findall(r"[a-z0-9]+", text.casefold())
    for span in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.extend(span[i : i + 2] for i in range(len(span) - 1))
        if len(span) == 1:
            terms.append(span)
    return terms


def rank_evidence(
    query: str, blocks: list[DocumentBlock], *, limit: int = 10
) -> list[tuple[DocumentBlock, float]]:
    query_terms = set(_terms(query))
    if not query_terms or not blocks:
        return []
    documents = [Counter(_terms(block.text)) for block in blocks]
    frequency = Counter(
        term for doc in documents for term in doc if term in query_terms
    )
    average_length = sum(sum(doc.values()) for doc in documents) / len(documents) or 1
    scored = []
    for block, doc in zip(blocks, documents, strict=True):
        length = sum(doc.values())
        score = 0.0
        for term in sorted(query_terms & doc.keys()):
            idf = math.log(
                1 + (len(documents) - frequency[term] + 0.5) / (frequency[term] + 0.5)
            )
            score += (
                idf
                * doc[term]
                * 2.2
                / (doc[term] + 1.2 * (0.25 + 0.75 * length / average_length))
            )
        if score > 0:
            scored.append((block, round(score, 6)))
    return sorted(scored, key=lambda item: (-item[1], item[0].order_index))[:limit]
