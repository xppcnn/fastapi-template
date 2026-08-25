from dataclasses import dataclass
from pathlib import Path

from docling.datamodel.base_models import OutputFormat
from docling.datamodel.pipeline_options import TableFormerMode
from docling.datamodel.service.options import ConvertDocumentsOptions
from docling.datamodel.service.targets import InBodyTarget
from docling.service_client import AsyncDoclingServiceClient, StatusWatcherKind
from docling.service_client.job import AsyncConversionJob

from app.core.config import Settings


@dataclass(slots=True)
class ParsedConversion:
    status: str  # success / partial_success / ...
    markdown: str
    document_json: dict
    errors: list[str]


def build_convert_options(settings: Settings) -> ConvertDocumentsOptions:
    return ConvertDocumentsOptions(
        from_formats=[],  # 不预筛,交给服务端按文件类型
        to_formats=[OutputFormat.MARKDOWN, OutputFormat.JSON],
        do_ocr=settings.docling_do_ocr,
        table_mode=(
            TableFormerMode.FAST
            if settings.docling_table_mode == "fast"
            else TableFormerMode.ACCURATE
        ),
    )


def open_client(settings: Settings) -> AsyncDoclingServiceClient:
    """返回可 async with 的官方客户端;job 超时 = 配置的解析超时。"""
    return AsyncDoclingServiceClient(
        url=settings.docling_serve_base_url,
        api_key=settings.docling_serve_api_key,
        status_watcher=StatusWatcherKind.POLLING,
        job_timeout=settings.docling_parse_timeout_minutes * 60.0,
        http_connect_timeout=10.0,
        http_read_timeout=600.0,
    )


async def submit_document(
    client: AsyncDoclingServiceClient,
    *,
    file_path: str,
    file_name: str,
    settings: Settings,
) -> AsyncConversionJob:
    """提交文件转换任务(上传 multipart),返回 Job(task_id 立即可用)。"""
    return await client.submit(
        source=Path(file_path),
        options=build_convert_options(settings),
        target=InBodyTarget(),
    )


def to_parsed_conversion(conversion_result) -> ParsedConversion:
    """把官方 ConversionResult 映射为项目内类型。"""
    document = conversion_result.document
    return ParsedConversion(
        status=getattr(
            conversion_result.status, "value", str(conversion_result.status)
        ),
        markdown=document.export_to_markdown(),
        document_json=document.export_to_dict(),
        errors=[
            getattr(e, "error_message", None) or str(e)
            for e in (conversion_result.errors or [])
        ],
    )
