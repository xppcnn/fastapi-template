import json
import logging

import structlog

from app.core.logging import bind_request_id, configure_logging, reset_request_id


def _restore_root_logger(
    handlers: list[logging.Handler], level: int
) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers = handlers
    root_logger.setLevel(level)


def test_structlog_json_contains_standard_fields_and_request_context(capsys) -> None:
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    try:
        configure_logging("INFO", json_output=True)
        tokens = bind_request_id("4fdfec06-719a-4fca-a96d-1e7472d4260f")
        try:
            structlog.get_logger("tests.logging.fields").info(
                "http_request_completed",
                message="HTTP request completed",
                method="GET",
                path="/api/v1/health",
                status_code=200,
                duration_ms=1.25,
            )
        finally:
            reset_request_id(tokens)

        record = json.loads(capsys.readouterr().out)
        assert record["timestamp"].endswith("Z")
        assert record["level"] == "info"
        assert record["logger"] == "tests.logging.fields"
        assert record["event"] == "http_request_completed"
        assert record["message"] == "HTTP request completed"
        assert record["request_id"] == "4fdfec06-719a-4fca-a96d-1e7472d4260f"
        assert record["method"] == "GET"
        assert record["path"] == "/api/v1/health"
        assert record["status_code"] == 200
        assert record["duration_ms"] == 1.25
    finally:
        _restore_root_logger(original_handlers, original_level)


def test_structlog_json_contains_exception_details(capsys) -> None:
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    try:
        configure_logging("INFO", json_output=True)
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            structlog.get_logger("tests.logging.exception").exception(
                "unhandled_error", message="Unhandled error"
            )

        record = json.loads(capsys.readouterr().out)
        assert record["event"] == "unhandled_error"
        assert "RuntimeError: boom" in record["exception"]
    finally:
        _restore_root_logger(original_handlers, original_level)


def test_reset_request_context_prevents_id_leaking_to_next_log(capsys) -> None:
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    try:
        configure_logging("INFO", json_output=True)
        tokens = bind_request_id("4fdfec06-719a-4fca-a96d-1e7472d4260f")
        reset_request_id(tokens)

        structlog.get_logger("tests.logging.reset").info("background_event")

        record = json.loads(capsys.readouterr().out)
        assert record["request_id"] is None
    finally:
        _restore_root_logger(original_handlers, original_level)


def test_standard_logging_uses_the_structlog_json_pipeline(capsys) -> None:
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    try:
        configure_logging("INFO", json_output=True)

        logging.getLogger("tests.logging.stdlib").info("Dependency ready")

        record = json.loads(capsys.readouterr().out)
        assert record["event"] == "Dependency ready"
        assert record["message"] == "Dependency ready"
        assert record["logger"] == "tests.logging.stdlib"
        assert record["level"] == "info"
    finally:
        _restore_root_logger(original_handlers, original_level)


def test_configure_logging_uses_processor_formatter() -> None:
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    try:
        configure_logging("DEBUG", json_output=True)

        assert root_logger.level == logging.DEBUG
        assert len(root_logger.handlers) == 1
        assert isinstance(
            root_logger.handlers[0].formatter,
            structlog.stdlib.ProcessorFormatter,
        )
    finally:
        _restore_root_logger(original_handlers, original_level)


def test_configure_logging_routes_uvicorn_loggers_through_root() -> None:
    logger_names = ("uvicorn", "uvicorn.error")
    saved_state = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).propagate,
            logging.getLogger(name).level,
        )
        for name in logger_names
    }
    try:
        for name in logger_names:
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.handlers = [logging.NullHandler()]
            uvicorn_logger.propagate = False
            uvicorn_logger.setLevel(logging.INFO)

        configure_logging("INFO", json_output=True)

        for name in logger_names:
            uvicorn_logger = logging.getLogger(name)
            assert uvicorn_logger.handlers == []
            assert uvicorn_logger.propagate is True
            assert uvicorn_logger.level == logging.NOTSET
    finally:
        for name, (handlers, propagate, level) in saved_state.items():
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.handlers = handlers
            uvicorn_logger.propagate = propagate
            uvicorn_logger.setLevel(level)


def test_configure_logging_disables_duplicate_uvicorn_access_log(capsys) -> None:
    root_logger = logging.getLogger()
    original_root_handlers = root_logger.handlers[:]
    original_root_level = root_logger.level
    access_logger = logging.getLogger("uvicorn.access")
    original_access_state = (
        access_logger.handlers[:],
        access_logger.propagate,
        access_logger.level,
        access_logger.disabled,
    )
    try:
        access_logger.handlers.clear()
        access_logger.propagate = True
        access_logger.setLevel(logging.INFO)
        access_logger.disabled = False

        configure_logging("INFO", json_output=True)
        access_logger.info('127.0.0.1 - "GET /api/v1/health HTTP/1.1" 200')

        assert access_logger.disabled is True
        assert capsys.readouterr().out == ""
    finally:
        _restore_root_logger(original_root_handlers, original_root_level)
        handlers, propagate, level, disabled = original_access_state
        access_logger.handlers = handlers
        access_logger.propagate = propagate
        access_logger.setLevel(level)
        access_logger.disabled = disabled
