from app.models.document import DocumentBlock, DocumentVersion


def test_document_version_parse_columns() -> None:
    cols = DocumentVersion.__table__.columns
    assert cols["parse_job_id"].type.python_type is str
    assert "parsed_object_key" in cols
    assert "parsed_markdown_object_key" in cols
    assert "parse_error" in cols
    assert "parsing_started_at" in cols
    assert "parsed_at" in cols


def test_document_block_columns() -> None:
    cols = DocumentBlock.__table__.columns
    assert "version_id" in cols
    assert "order_index" in cols
    assert "block_type" in cols
    assert "text" in cols
    assert "page_no" in cols