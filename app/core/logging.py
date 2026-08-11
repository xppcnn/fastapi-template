import logging
import sys
from collections.abc import Mapping, MutableMapping
from contextvars import Token
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, merge_contextvars, reset_contextvars
from structlog.typing import EventDict, Processor, WrappedLogger

ContextTokens = Mapping[str, Token[Any]]


def bind_request_id(request_id: str) -> ContextTokens:
    return bind_contextvars(request_id=request_id)


def reset_request_id(tokens: ContextTokens) -> None:
    reset_contextvars(**tokens)


def _ensure_log_schema(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> MutableMapping[str, Any]:
    event_dict.setdefault("message", event_dict.get("event"))
    event_dict.setdefault("request_id", None)
    return event_dict


def _shared_processors() -> list[Processor]:
    return [
        merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _ensure_log_schema,
    ]


def configure_logging(
    level: str = "INFO",
    *,
    json_output: bool = False,
) -> None:
    shared_processors = _shared_processors()
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    renderer: Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer(
            ensure_ascii=False, separators=(",", ":")
        )
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level.upper())

    for logger_name in ("uvicorn", "uvicorn.error"):
        framework_logger = logging.getLogger(logger_name)
        framework_logger.handlers.clear()
        framework_logger.propagate = True
        framework_logger.setLevel(logging.NOTSET)

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True
