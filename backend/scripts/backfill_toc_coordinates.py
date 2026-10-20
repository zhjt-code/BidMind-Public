r"""重建存量文档目录页码坐标。

职责: 重新解析存量 PDF 的目录页码, 回填 v2 toc_map 中的物理页坐标。
不做: 不重建 chunks、不重新向量化、不触发审查或写标流程。

用法(PowerShell):
 .\.venv\Scripts\python.exe backend\scripts\backfill_toc_coordinates.py --project-id 1506 --dry-run
 .\.venv\Scripts\python.exe backend\scripts\backfill_toc_coordinates.py --document-id 1305 --apply
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import sys

from sqlalchemy import select

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
os.chdir(_BACKEND_ROOT)

from app.core.database import async_session_factory  # noqa: E402
from app.core.parser.pdf_parser import PdfParser  # noqa: E402
from app.core.parser.toc_parser import TocParser, serialize_toc_map  # noqa: E402
from app.models.document import Document  # noqa: E402


@dataclass(frozen=True)
class BackfillResult:
    """单文档目录页码坐标重建结果。"""

    document_id: int
    filename: str
    status: str
    offset: int | None = None
    confidence: str | None = None
    entries: int = 0
    message: str = ""


def _resolve_file_path(file_path: str) -> Path | None:
    """解析文档路径，兼容绝对路径和相对 backend/repo 根目录路径。"""
    raw = Path(file_path)
    candidates = [raw]
    if not raw.is_absolute():
        candidates.append(_BACKEND_ROOT / raw)
        candidates.append(_BACKEND_ROOT.parent / raw)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def rebuild_toc_map_for_file(file_path: Path) -> dict:
    """从 PDF 文件重建 v2 toc_map。"""
    parser = PdfParser(extract_tables=False, extract_images=False)
    parsed_doc = parser.parse(file_path)
    return TocParser().parse_resolved(parsed_doc.pages)


async def _load_documents(project_id: int | None, document_id: int | None) -> list[Document]:
    async with async_session_factory() as db:
        stmt = select(Document).where(Document.parsed == 1).order_by(Document.id)
        if project_id is not None:
            stmt = stmt.where(Document.project_id == project_id)
        if document_id is not None:
            stmt = stmt.where(Document.id == document_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())


async def backfill_toc_coordinates(
    *,
    project_id: int | None,
    document_id: int | None,
    apply: bool,
) -> list[BackfillResult]:
    """对符合条件的文档执行 dry-run 或实际回填。"""
    documents = await _load_documents(project_id, document_id)
    results: list[BackfillResult] = []

    async with async_session_factory() as db:
        for doc in documents:
            path = _resolve_file_path(doc.file_path)
            if path is None:
                results.append(
                    BackfillResult(
                        document_id=doc.id,
                        filename=doc.filename,
                        status="skipped",
                        message=f"文件不存在: {doc.file_path}",
                    )
                )
                continue

            try:
                toc_map = await asyncio.to_thread(rebuild_toc_map_for_file, path)
            except Exception as exc:  # noqa: BLE001 - 脚本逐文档容错
                results.append(
                    BackfillResult(
                        document_id=doc.id,
                        filename=doc.filename,
                        status="failed",
                        message=str(exc)[:160],
                    )
                )
                continue

            if not toc_map:
                results.append(
                    BackfillResult(
                        document_id=doc.id,
                        filename=doc.filename,
                        status="skipped",
                        message="未识别目录",
                    )
                )
                continue

            status = "updated" if apply else "would_update"
            if apply:
                target = await db.get(Document, doc.id)
                if target is not None:
                    target.toc_map = serialize_toc_map(toc_map)

            results.append(
                BackfillResult(
                    document_id=doc.id,
                    filename=doc.filename,
                    status=status,
                    offset=toc_map.get("offset"),
                    confidence=str(toc_map.get("confidence")),
                    entries=int(toc_map.get("total_entries") or 0),
                )
            )

        if apply:
            await db.commit()

    return results


def _print_results(results: list[BackfillResult], *, apply: bool) -> None:
    for item in results:
        print(
            f"doc={item.document_id} file={item.filename} status={item.status} "
            f"entries={item.entries} offset={item.offset} confidence={item.confidence} "
            f"{item.message}".rstrip()
        )
    affected = sum(1 for item in results if item.status in {"updated", "would_update"})
    mode = "已更新" if apply else "预计更新(dry-run)"
    print(f"完成: {mode} {affected} 个文档, 扫描 {len(results)} 个文档")
    if not apply:
        print("提示: 加 --apply 实际写入 documents.toc_map")


async def main() -> None:
    parser = argparse.ArgumentParser(description="重建存量文档目录页码坐标")
    parser.add_argument("--project-id", type=int, default=None, help="仅处理指定项目")
    parser.add_argument("--document-id", type=int, default=None, help="仅处理指定文档")
    parser.add_argument("--dry-run", action="store_true", help="仅报告不写库(默认)")
    parser.add_argument("--apply", action="store_true", help="实际写入 documents.toc_map")
    args = parser.parse_args()

    if args.apply and args.project_id is None and args.document_id is None:
        parser.error("--apply 必须指定 --project-id 或 --document-id，禁止全库操作")

    apply = bool(args.apply and not args.dry_run)
    results = await backfill_toc_coordinates(
        project_id=args.project_id,
        document_id=args.document_id,
        apply=apply,
    )
    _print_results(results, apply=apply)


if __name__ == "__main__":
    asyncio.run(main())
