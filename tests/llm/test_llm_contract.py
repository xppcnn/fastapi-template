import asyncio
from types import SimpleNamespace
from typing import Any, ClassVar
from uuid import UUID

import pytest

from app.integrations.llm.base import (
    LLMError,
    LLMMessage,
    OutputValidationError,
)
from app.integrations.llm.fake import FakeLLMClient
from app.integrations.llm.openai_compatible import OpenAICompatibleClient
from app.schemas.llm import BatchExtractionResult, ExtractedRule

_SEGMENT_UUID = "00000000-0000-0000-0000-000000000001"


def _messages() -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="constraints"),
        LLMMessage(role="user", content="document"),
    ]


def _success_payload() -> dict:
    return {
        "batch_order": 1,
        "mentioned": True,
        "rules": [
            {
                "rule_type": "response",
                "title": "技术参数应答",
                "description": "须对技术参数逐条应答",
                "source_segment_ids": [_SEGMENT_UUID],
            }
        ],
    }


def test_fake_returns_schema_validated_result() -> None:
    fake = FakeLLMClient(outcomes=[_success_payload()])

    result = asyncio.run(
        fake.structured(
            operation="op", messages=_messages(), output_schema=BatchExtractionResult
        )
    )

    assert isinstance(result.data, BatchExtractionResult)
    assert result.data.mentioned is True
    assert isinstance(result.data.rules[0], ExtractedRule)
    assert result.data.rules[0].source_segment_ids == [UUID(_SEGMENT_UUID)]
    assert fake.remaining == 0
    assert len(fake.calls) == 1


def test_fake_simulates_timeout() -> None:
    fake = FakeLLMClient(outcomes=[LLMError("simulated timeout")])

    with pytest.raises(LLMError, match="timeout"):
        asyncio.run(
            fake.structured(
                operation="op",
                messages=_messages(),
                output_schema=BatchExtractionResult,
            )
        )


def test_fake_simulates_rate_limit() -> None:
    fake = FakeLLMClient(outcomes=[LLMError("429 rate limit")])

    with pytest.raises(LLMError, match="429"):
        asyncio.run(
            fake.structured(
                operation="op",
                messages=_messages(),
                output_schema=BatchExtractionResult,
            )
        )


def test_fake_simulates_invalid_json() -> None:
    fake = FakeLLMClient(outcomes=[OutputValidationError("invalid json")])

    with pytest.raises(OutputValidationError):
        asyncio.run(
            fake.structured(
                operation="op",
                messages=_messages(),
                output_schema=BatchExtractionResult,
            )
        )


def test_fake_surfaces_error_then_success_for_external_retry_chain() -> None:
    """Fake 不内置重试:错误抛给外部重试链(model_runs attempt),重试后成功。"""
    fake = FakeLLMClient(
        outcomes=[
            LLMError("timeout"),
            {"batch_order": 1, "mentioned": False, "rules": []},
        ]
    )

    with pytest.raises(LLMError):
        asyncio.run(
            fake.structured(
                operation="op",
                messages=_messages(),
                output_schema=BatchExtractionResult,
            )
        )

    result = asyncio.run(
        fake.structured(
            operation="op", messages=_messages(), output_schema=BatchExtractionResult
        )
    )

    assert result.data.mentioned is False
    assert fake.remaining == 0
    assert len(fake.calls) == 2


def test_fake_is_expected_contract_shape() -> None:
    """Fake 与协议形态一致:model/context_length_limit 属性 + structured 调用。"""
    fake = FakeLLMClient()
    assert fake.model == "fake-model"
    assert fake.context_length_limit is None
    assert callable(getattr(fake, "structured", None))


class _FakeChat:
    async def create(self, **kwargs: Any) -> Any:
        content = kwargs.get("_content") or "{}"
        resp = SimpleNamespace()
        resp.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        resp.usage = SimpleNamespace(prompt_tokens=11, completion_tokens=22)
        return resp


class _FakeClient:
    chat: ClassVar = SimpleNamespace(completions=_FakeChat())


def test_openai_client_exposes_context_length_limit() -> None:
    with pytest.raises(TypeError):
        OpenAICompatibleClient(model="m")  # client 必填

    client = OpenAICompatibleClient(
        client=object(), model="m", context_length_limit=32_000
    )
    assert client.model == "m"
    assert client.context_length_limit == 32_000
    assert (
        OpenAICompatibleClient(client=object(), model="m").context_length_limit is None
    )


def test_openai_client_parses_and_validates_output() -> None:
    client = OpenAICompatibleClient(client=_FakeClient(), model="m")

    async def _create(**kwargs: Any) -> Any:
        resp = SimpleNamespace()
        resp.choices = [
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"batch_order": 1, "mentioned": true, "rules": []}'
                )
            )
        ]
        resp.usage = SimpleNamespace(prompt_tokens=11, completion_tokens=22)
        return resp

    _FakeClient.chat.completions.create = _create  # type: ignore[method-assign]

    result = asyncio.run(
        client.structured(
            operation="op", messages=_messages(), output_schema=BatchExtractionResult
        )
    )

    assert result.data.batch_order == 1
    assert result.data.mentioned is True
    assert result.model == "m"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 22


def test_openai_client_raises_output_validation_on_invalid_json() -> None:
    async def _create(**kwargs: Any) -> Any:
        resp = SimpleNamespace()
        resp.choices = [SimpleNamespace(message=SimpleNamespace(content="not json"))]
        resp.usage = SimpleNamespace(prompt_tokens=None, completion_tokens=None)
        return resp

    _FakeClient.chat.completions.create = _create  # type: ignore[method-assign]
    client = OpenAICompatibleClient(client=_FakeClient(), model="m")

    with pytest.raises(OutputValidationError):
        asyncio.run(
            client.structured(
                operation="op",
                messages=_messages(),
                output_schema=BatchExtractionResult,
            )
        )


def test_openai_client_raises_llm_error_on_transport_failure() -> None:
    async def _create(**kwargs: Any) -> Any:
        raise RuntimeError("connection refused")

    _FakeClient.chat.completions.create = _create  # type: ignore[method-assign]
    client = OpenAICompatibleClient(client=_FakeClient(), model="m")

    with pytest.raises(LLMError, match="connection refused"):
        asyncio.run(
            client.structured(
                operation="op",
                messages=_messages(),
                output_schema=BatchExtractionResult,
            )
        )
