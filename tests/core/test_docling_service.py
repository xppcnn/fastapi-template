from app.core.config import get_settings
from app.core.docling_service import build_convert_options, to_parsed_conversion


class FakeJob:
    task_id = "task-1"

    async def result(self, timeout=None):
        return _fake_result()


def _fake_result():
    class FakeDoc:
        def export_to_markdown(self) -> str:
            return "# Title"

        def export_to_dict(self) -> dict:
            return {
                "texts": [{"text": "x", "label": "paragraph", "prov": [{"page_no": 1}]}]
            }

    class FakeResult:
        status = "success"
        errors = ()
        document = FakeDoc()

    return FakeResult()


def test_build_convert_options_from_settings() -> None:
    options = build_convert_options(get_settings())
    assert options.do_ocr is True
    assert options.table_mode == "fast"
    assert len(options.to_formats) == 2  # markdown + json


def test_to_parsed_conversion_maps_result() -> None:
    result = to_parsed_conversion(_fake_result())
    assert result.markdown == "# Title"
    assert result.document_json["texts"][0]["text"] == "x"
    assert result.status == "success"
