from typing import Literal, Protocol, TypeVar, cast

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def validate_model[T](output_schema: type[T], payload: object) -> T:
    """带界 TypeVar 的 type[T] 在 mypy 下不透传类方法,经 type[BaseModel] 桥接。"""
    validator = cast(type[BaseModel], output_schema)
    return cast(T, validator.model_validate(payload))


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMResult[T](BaseModel):
    """PEP 695 类型参数,替代 Generic[T] 子类写法。"""

    data: T
    raw: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int = 0


class LLMError(Exception):
    """传输层错误:超时/限流/服务不可达,可进入外部重试链。"""


class OutputValidationError(LLMError):
    """模型输出无法通过 Schema 校验或 JSON 解析,可进入外部重试链(不收敛则批次失败)。"""


class LLMClient(Protocol):
    """薄协议:业务代码不感知具体 provider,换模型只换 base_url + api_key + model。"""

    model: str
    context_length_limit: int | None

    async def structured[T](
        self,
        *,
        operation: str,
        messages: list[LLMMessage],
        output_schema: type[T],
    ) -> LLMResult[T]: ...
