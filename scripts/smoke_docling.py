"""docling-serve 冒烟脚本:起 serve 后跑一次真实转换。

用法:
    uv run python scripts/smoke_docling.py                     # 生成最小 PDF 冒烟
    uv run python scripts/smoke_docling.py --file real.pdf     # 用真实文件
    uv run python scripts/smoke_docling.py --url http://docling:5001 --api-key xxx
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from docling.datamodel.base_models import OutputFormat
from docling.datamodel.service.options import ConvertDocumentsOptions
from docling.datamodel.service.targets import InBodyTarget
from docling.service_client import AsyncDoclingServiceClient, StatusWatcherKind


def make_minimal_pdf(path: Path) -> None:
    content = b"BT /F1 24 Tf 72 700 Td (Hello Docling Smoke Test) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        (
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"endstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode()
    )
    path.write_bytes(out)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:5001")
    parser.add_argument(
        "--api-key", default=os.environ.get("DOCLING_SERVE_API_KEY", "")
    )
    parser.add_argument("--file", default=None)
    args = parser.parse_args()

    file_path = Path(args.file) if args.file else Path("/tmp/docling-smoke.pdf")
    if args.file is None:
        make_minimal_pdf(file_path)
        print(f"generated smoke pdf: {file_path}")

    options = ConvertDocumentsOptions(
        to_formats=[OutputFormat.MARKDOWN, OutputFormat.JSON],
        do_ocr=False,
    )
    async with AsyncDoclingServiceClient(
        url=args.url,
        api_key=args.api_key,
        status_watcher=StatusWatcherKind.POLLING,
        job_timeout=120.0,
    ) as client:
        job = await client.submit(
            source=file_path, options=options, target=InBodyTarget()
        )
        print("task_id:", job.task_id)
        result = await job.result()
        print("status:", result.status)
        print("----- markdown -----")
        print(result.document.export_to_markdown()[:500])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
