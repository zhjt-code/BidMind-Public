""" Level 3 合成样本：格式章尾部被 30 块窗口截断。

样本布局复刻项目 3417 / doc 2486：
- page 12 行首「第七章 投标文件格式」
- page 12-36 为格式章前部（对应旧窗口内的第 1-4 项）
- page 37-48 为格式章尾部（合同条款偏离表 … 技术方案）
断言新选段覆盖 p37-48，且 4/11 提取被覆盖率门控判为 partial。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.writer.directory_spec_alignment import (
    build_numbered_line_inventory,
    evaluate_spec_coverage,
    verify_spec_against_source,
)
from app.core.writer.directory_spec_context import (
    select_directory_spec_chunks_with_fallback,
)
from app.schemas.writing import BidDirectorySpec


def _chunk(page_num: int, chunk_index: int, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        page_num=page_num,
        chunk_index=chunk_index,
        content=content,
    )


_TAIL_TITLES = [
    (37, "5 合同条款偏离表（实质性格式）"),
    (38, "6 采购需求偏离表（实质性格式）"),
    (39, "7 本国产品标准证明文件（适用于货物类）"),
    (41, "8 中小企业证明文件"),
    (45, "9 拟分包情况说明"),
    (48, "10 招标文件要求提供或投标人认为应附的其他材料"),
    (48, "11 技术方案"),
]

_DIRECTORY_LISTING = """第七章 投标文件格式
二、商务技术文件格式
1 投标书（实质性格式）
2 授权委托书（实质性格式）
3 开标一览表（实质性格式）
4 投标分项报价表（实质性格式）
5 合同条款偏离表（实质性格式）
6 采购需求偏离表（实质性格式）
7 本国产品标准证明文件（适用于货物类）
8 中小企业证明文件
9 拟分包情况说明
10 招标文件要求提供或投标人认为应附的其他材料
11 技术方案
"""


def _bug904_chunks() -> list[SimpleNamespace]:
    """构造超过 30 块的格式章，尾部条目落在 p37-48。"""

    chunks = [
        _chunk(2, 0, "目 录\n第七章 投标文件格式................................................48"),
        _chunk(8, 1, "投标人须知 按照第七章 投标文件格式编制投标文件。"),
    ]
    chunks.append(
        _chunk(12, 2, "第七章 投标文件格式\n投标人编制文件须知\n" + _DIRECTORY_LISTING)
    )
    index = 3
    for page in range(13, 37):
        chunks.append(_chunk(page, index, f"格式章正文页{page} 模板填写说明"))
        index += 1
    for page, title in _TAIL_TITLES:
        chunks.append(_chunk(page, index, title))
        index += 1
    chunks.append(_chunk(8, index, "表格第1行：序号=总价（元）"))
    return chunks


def test_level3_selection_covers_format_chapter_tail() -> None:
    selected = select_directory_spec_chunks_with_fallback(_bug904_chunks(), toc_map=None)
    pages = {chunk.page_num for chunk in selected}

    assert 12 in pages
    assert 2 not in pages
    assert {37, 38, 39, 41, 45, 48}.issubset(pages)
    assert len(selected) > 30
    joined = "\n".join(chunk.content or "" for chunk in selected)
    assert "11 技术方案" in joined
    assert "5 合同条款偏离表" in joined


def test_level3_toc_anchor_covers_format_chapter_tail() -> None:
    """: 可靠 toc 只定位起点，不能把格式章再次截成两页。"""

    toc_map = {
        "version": 2,
        "coordinate_system": "physical_page",
        "enabled": True,
        "entries": [
            {"title": "第七章 投标文件格式", "physical_page": 12},
        ],
    }
    selected = select_directory_spec_chunks_with_fallback(
        _bug904_chunks(), toc_map=toc_map,
    )
    joined = "\n".join(chunk.content or "" for chunk in selected)

    assert len(selected) > 30
    assert "5 合同条款偏离表" in joined
    assert "11 技术方案" in joined


def test_level3_coverage_gate_flags_truncated_spec() -> None:
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "technical",
                "volume_title": "商务技术文件格式",
                "items": [
                    {"order": 1, "title": "投标书（实质性格式）"},
                    {"order": 2, "title": "授权委托书（实质性格式）"},
                    {"order": 3, "title": "开标一览表（实质性格式）"},
                    {"order": 4, "title": "投标分项报价表（实质性格式）"},
                ],
            }
        ],
    )
    verified = verify_spec_against_source(spec, _DIRECTORY_LISTING)
    report = evaluate_spec_coverage(
        verified, build_numbered_line_inventory(_DIRECTORY_LISTING),
    )

    assert report.status == "partial"
    assert any("技术方案" in title for title in report.missing_titles)
    assert any("合同条款偏离表" in title for title in report.missing_titles)
    assert "技术方案" in report.warning
