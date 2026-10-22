"""评分样本语料生成器（阶段0.1）。

职责: 把评分样本 PDF 解析为页级 JSON fixture（page_num/text/tables），供
      backend/tests/test_scoring_sample_corpus.py 离线回归，PDF 本体不入库。
不做: 不提取评分项、不做断言、不访问数据库。

用法（PowerShell）:
  .venv\\Scripts\\python.exe scripts\\build_scoring_sample_corpus.py `
      --samples-dir "<样本目录>" --out-dir backend/tests/fixtures/scoring/sample_corpus
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

DEFAULT_INDEX_SPEC = "1-21"
SAMPLE_INDEXES = list(range(1, 22))
DEFAULT_NAME_TEMPLATE = "评分表结构-{index}.pdf"
DEFAULT_MERGED_NAME = "评分表结构-合并.pdf"


def parse_index_spec(raw: str) -> list[int]:
    """解析样本编号表达式（如 1-21,27,28）。

    职责: 将命令行编号范围转换为升序唯一整数列表。
    不做: 不访问文件系统、不判断样本是否存在。
    """
    indexes: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            left, _, right = token.partition("-")
            start = int(left.strip())
            end = int(right.strip())
            if start > end:
                raise ValueError(f"invalid index range: {token}")
            indexes.update(range(start, end + 1))
        else:
            indexes.add(int(token))
    if not indexes:
        raise ValueError("indexes must not be empty")
    return sorted(indexes)


def build_targets(
    samples_dir: Path,
    indexes: list[int],
    name_template: str,
    merged_name: str,
    include_merged: bool,
    extras: list[str],
) -> list[tuple[str, Path]]:
    """构造待序列化 PDF 目标列表。

    职责: 根据编号、命名模板、merged开关和额外样本生成目标路径。
    不做: 不解析 PDF、不检查文件存在、不写 fixture。
    """
    targets: list[tuple[str, Path]] = [
        (f"{index:02d}", samples_dir / name_template.format(index=index))
        for index in indexes
    ]
    if include_merged:
        targets.append(("merged", samples_dir / merged_name))
    for extra in extras:
        key, _, raw_path = str(extra).partition("=")
        if key.strip() and raw_path.strip():
            targets.append((key.strip(), Path(raw_path.strip())))
    return targets


def serialize_pdf(pdf_path: Path) -> dict:
    """解析单个样本 PDF 为页级 fixture 数据。

 表格序列化为 {data, cell_grid}: cell_grid 是单元格bbox槽网格,
 承载合并单元格跨度物理证据, 与生产链路 ExtractedTable 形态一致。
 """
    from app.core.parser.pdf_parser import PdfParser

    parser = PdfParser(extract_tables=True, extract_images=False)
    doc = parser.parse(pdf_path)
    return {
        "source": pdf_path.name,
        "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "page_count": doc.page_count,
        "pages": [
            {
                "page_num": page.page_num,
                "text": page.text or "",
                "tables": [
                    {
                        "data": list(table.data or []),
                        "cell_grid": getattr(table, "cell_grid", None),
                    }
                    for table in (page.tables or [])
                ],
            }
            for page in doc.pages
        ],
    }


def main() -> int:
    argp = argparse.ArgumentParser(description="评分样本页级语料生成器")
    argp.add_argument("--samples-dir", required=True, help="样本 PDF 所在目录")
    argp.add_argument(
        "--out-dir",
        default=str(REPO_ROOT / "backend" / "tests" / "fixtures" / "scoring" / "sample_corpus"),
        help="fixture 输出目录",
    )
    argp.add_argument("--name-template", default=DEFAULT_NAME_TEMPLATE)
    argp.add_argument("--merged-name", default=DEFAULT_MERGED_NAME)
    argp.add_argument(
        "--indexes",
        default=DEFAULT_INDEX_SPEC,
        help="样本编号列表/范围，如 1-21 或 23,24,25,26,29-34",
    )
    argp.add_argument(
        "--no-merged",
        action="store_true",
        help="不尝试生成 merged.json（留出集等分目录通常没有合并PDF）",
    )
    argp.add_argument(
        "--extra",
        action="append",
        default=[],
        help="额外样本, 格式 key=pdf路径 (如 22=storage/uploads/1700/xxx.pdf)",
    )
    args = argp.parse_args()

    from loguru import logger

    logger.remove()

    samples_dir = Path(args.samples_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    targets = build_targets(
        samples_dir=samples_dir,
        indexes=parse_index_spec(args.indexes),
        name_template=args.name_template,
        merged_name=args.merged_name,
        include_merged=not args.no_merged,
        extras=list(args.extra),
    )

    for key, pdf_path in targets:
        if not pdf_path.exists():
            print(f"SKIP {key}: {pdf_path} 不存在")
            continue
        data = serialize_pdf(pdf_path)
        out_path = out_dir / f"{key}.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        written.append(out_path.name)
        print(f"WROTE {out_path.name}: pages={data['page_count']}")

    print(f"完成: {len(written)} 个 fixture -> {out_dir}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
