from __future__ import annotations

import asyncio
import json

from app.integrations.llm.base import (
    LLMClient,
    LLMError,
    LLMMessage,
    LLMResult,
    OutputValidationError,
    validate_model,
)


class FakeLLMClient(LLMClient):
    """可编排的假适配器:按队列依次返回成功 JSON 或抛出指定异常,无网络访问。

    outcomes 每个元素可以是:
    - dict:视为成功响应,交给 schema 校验后返回;
    - LLMError/OutputValidationError 实例:直接抛出,用于模拟超时/限流/无效 JSON。
    """

    model = "fake-model"
    context_length_limit: int | None = None

    def __init__(
        self,
        *,
        outcomes: list[dict | BaseException] | None = None,
        delay_ms: int = 0,
    ) -> None:
        self._outcomes = list(outcomes or [])
        self.delay_ms = delay_ms
        self.calls: list[tuple[str, list[LLMMessage]]] = []

    @property
    def remaining(self) -> int:
        return len(self._outcomes)

    async def structured[T](
        self,
        *,
        operation: str,
        messages: list[LLMMessage],
        output_schema: type[T],
    ) -> LLMResult[T]:
        self.calls.append((operation, messages))
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)
        if not self._outcomes:
            raise OutputValidationError("fake: no scripted outcome")
        outcome: dict | BaseException = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            if isinstance(outcome, LLMError):
                raise outcome
            raise OutputValidationError(str(outcome)) from outcome
        data = validate_model(output_schema, outcome)
        return LLMResult(
            data=data,
            raw=json.dumps(outcome, ensure_ascii=False),
            model=self.model,
            latency_ms=self.delay_ms,
        )
