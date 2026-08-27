from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.integrations.llm.base import (
    LLMClient,
    LLMError,
    LLMMessage,
    LLMResult,
    OutputValidationError,
    validate_model,
)


class OpenAICompatibleClient(LLMClient):
    """基于官方 openai SDK(async) 的 OpenAI-compatible 适配器。

    - `response_format=json_object` 普适回退(DeepSeek/通义/智谱/Kimi/Ollama/vLLM 均支持);
    - 输出校验失败抛 OutputValidationError,由外部(model_runs 的 attempt 链)重试,不在库内重试;
    - `context_length_limit` 供上层切分/缓存策略使用(不在此实现分段)。
    """

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        context_length_limit: int | None = None,
    ) -> None:
        self._client = client
        self.model = model
        self.context_length_limit = context_length_limit

    def _build_messages(self, messages: list[LLMMessage]) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    @staticmethod
    def _input_hash(messages: list[LLMMessage]) -> str:
        canonical = json.dumps(
            [m.model_dump(mode="json") for m in messages],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    async def structured[T](
        self,
        *,
        operation: str,
        messages: list[LLMMessage],
        output_schema: type[T],
    ) -> LLMResult[T]:
        started = time.monotonic()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(messages),
            "response_format": {"type": "json_object"},
        }
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"llm transport error for {operation}: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        raw = (response.choices[0].message.content or "").strip()
        usage = getattr(response, "usage", None)
        try:
            payload = json.loads(raw)
            data = validate_model(output_schema, payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise OutputValidationError(
                f"invalid output for {operation}: {raw[:500]}"
            ) from exc

        return LLMResult(
            data=data,
            raw=raw,
            model=self.model,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            latency_ms=latency_ms,
        )
