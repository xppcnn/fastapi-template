import asyncio
from types import TracebackType

import pytest

from app.core import database


class ObservedSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


class ObservedSessionContext:
    def __init__(self, session: ObservedSession) -> None:
        self.session = session
        self.exited = False
        self.exit_exception_type: type[BaseException] | None = None

    async def __aenter__(self) -> ObservedSession:
        return self.session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exited = True
        self.exit_exception_type = exc_type


def test_get_db_commits_and_closes_session_after_success(monkeypatch) -> None:
    session = ObservedSession()
    context = ObservedSessionContext(session)
    monkeypatch.setattr(database, "async_session_factory", lambda: context)

    async def run_dependency() -> None:
        dependency = database.get_db()
        assert await anext(dependency) is session
        with pytest.raises(StopAsyncIteration):
            await anext(dependency)

    asyncio.run(run_dependency())

    assert session.commit_calls == 1
    assert session.rollback_calls == 0
    assert context.exited is True
    assert context.exit_exception_type is None


def test_get_db_rolls_back_and_closes_session_after_error(monkeypatch) -> None:
    session = ObservedSession()
    context = ObservedSessionContext(session)
    monkeypatch.setattr(database, "async_session_factory", lambda: context)
    error = RuntimeError("write failed")

    async def run_dependency() -> None:
        dependency = database.get_db()
        assert await anext(dependency) is session
        with pytest.raises(RuntimeError, match="write failed"):
            await dependency.athrow(error)

    asyncio.run(run_dependency())

    assert session.commit_calls == 0
    assert session.rollback_calls == 1
    assert context.exited is True
    assert context.exit_exception_type is RuntimeError
