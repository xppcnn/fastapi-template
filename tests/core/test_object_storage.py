import io

from app.core.object_storage import put_object


class FakePutResult:
    object_key: str


class FakeMinioClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, int, str]] = []

    def put_object(
        self, bucket: str, object_key: str, data, length: int, content_type: str
    ):
        self.calls.append((object_key, data, length, content_type))
        return FakePutResult()


def test_put_object_accepts_bytes(monkeypatch) -> None:
    fake = FakeMinioClient()
    monkeypatch.setattr("app.core.object_storage.get_client", lambda: fake)

    import asyncio

    asyncio.run(
        put_object(
            "parsed/x/parsed.md",
            b"# hello",
            7,
            content_type="text/markdown",
        )
    )

    key, data, length, content_type = fake.calls[0]
    assert key == "parsed/x/parsed.md"
    assert isinstance(data, io.BytesIO)
    assert length == 7
    assert content_type == "text/markdown"
