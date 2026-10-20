"""text_cleaner 单元测试: 页级降噪.

防御目标 (P1):
- 软换行: 句中断行被合并; 上行有句末标点不合并; 下一行是标题/条款编号不合并.
- 重复行: 跨多页出现的短行 (页眉/页脚) 被剔除; 仅出现一次的行保留.
- denoise_pages 不影响 ParsedPage 其它字段.
"""

from __future__ import annotations

from inspect import signature
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.pdf_parser import ParsedPage
from app.core.parser.text_cleaner import (
    denoise_pages,
    detect_repeated_lines,
    merge_soft_linebreaks,
)


def test_merge_soft_linebreaks_joins_mid_sentence_breaks() -> None:
    text = "投标人应保证设备无故障\n运行时间不少于 8000 小时。"
    merged = merge_soft_linebreaks(text)
    assert merged == "投标人应保证设备无故障运行时间不少于 8000 小时。"


def test_merge_keeps_sentence_end_punctuation_boundary() -> None:
    text = "投标人应保证质量。\n并提供书面承诺。"
    merged = merge_soft_linebreaks(text)
    assert merged == "投标人应保证质量。\n并提供书面承诺。"


def test_merge_does_not_join_when_next_line_is_heading() -> None:
    text = "上一段未完\n第二章 资格要求"
    merged = merge_soft_linebreaks(text)
    assert "上一段未完\n第二章 资格要求" == merged


def test_merge_does_not_join_when_next_line_is_clause_number() -> None:
    text = "前文未完\n3.2.1 投标人须具备相应资质"
    merged = merge_soft_linebreaks(text)
    assert merged == "前文未完\n3.2.1 投标人须具备相应资质"


def test_merge_does_not_join_bullet_lines() -> None:
    text = "上一行未完\n• 第一条要点\n• 第二条要点"
    merged = merge_soft_linebreaks(text)
    assert merged.startswith("上一行未完\n• 第一条要点")


def test_merge_preserves_blank_line_paragraph_separator() -> None:
    text = "第一段第一行\n第一段第二行\n\n第二段第一行"
    merged = merge_soft_linebreaks(text)
    assert "第一段第一行第一段第二行" in merged
    assert "\n\n" in merged


def test_detect_repeated_lines_across_pages() -> None:
    pages = [
        ParsedPage(page_num=i, text=f"招标文件\n第 {i} 页\n正文段落 {i}")
        for i in range(1, 6)
    ]

    repeated = detect_repeated_lines(pages, min_pages=3, ratio_threshold=0.6)

    assert "招标文件" in repeated
    assert "正文段落 1" not in repeated


def test_detect_ignores_lines_appearing_once() -> None:
    pages = [
        ParsedPage(page_num=1, text="只出现一次的短句\n正文 A"),
        ParsedPage(page_num=2, text="另一句\n正文 B"),
        ParsedPage(page_num=3, text="再一句\n正文 C"),
    ]

    repeated = detect_repeated_lines(pages, min_pages=2)

    assert "只出现一次的短句" not in repeated


def test_denoise_pages_removes_repeats_and_merges_linebreaks() -> None:
    # 每页正文不同(模拟真实 PDF), 仅页眉"招标文件"跨页重复
    bodies = [
        "投标人应保证设备\n运行时间不少于 8000 小时。",
        "项目经理须具备一级\n建造师注册资格。",
        "施工现场应配备\n专职安全员不少于 3 人。",
        "投标文件应密封\n递交至招标代理机构。",
        "中标人应在收到\n中标通知书后 7 日内签订合同。",
    ]
    pages = [
        ParsedPage(page_num=i + 1, text=f"招标文件\n{body}")
        for i, body in enumerate(bodies)
    ]

    summary = denoise_pages(pages)

    assert summary["repeats_detected"] >= 1
    assert summary["removed_repeats"] >= 5

    for page in pages:
        assert "招标文件" not in page.text
    # 验证软换行已合并(取第 1 页)
    assert "投标人应保证设备运行时间不少于 8000 小时。" in pages[0].text
    assert "项目经理须具备一级建造师注册资格。" in pages[1].text


def test_denoise_does_not_break_long_unique_lines() -> None:
    long_unique = "这是一段较长的正文内容,不应被识别为页眉页脚,需要完整保留。" * 2
    pages = [
        ParsedPage(page_num=1, text=long_unique),
        ParsedPage(page_num=2, text="另一段正文。"),
    ]

    denoise_pages(pages)

    assert long_unique in pages[0].text


def test_denoise_accepts_known_repeated_lines_from_page_chrome() -> None:
    """Page Chrome 已识别的重复行应由 text_cleaner 作为兜底消费。"""
    assert "known_repeated_lines" in signature(denoise_pages).parameters

    pages = [
        ParsedPage(page_num=1, text="固定页眉\n正文 A"),
        ParsedPage(page_num=2, text="固定页眉\n正文 B"),
    ]

    summary = denoise_pages(pages, known_repeated_lines={"固定页眉"})

    assert summary["removed_repeats"] == 2
    assert "固定页眉" not in pages[0].text
    assert "正文 A" in pages[0].text


def test_denoise_empty_pages_returns_zero_summary() -> None:
    summary = denoise_pages([])
    assert summary == {"removed_repeats": 0, "merged_pages": 0, "repeats_detected": 0}
