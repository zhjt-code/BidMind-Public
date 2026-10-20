"""
 第一期 S3：目录规格原文对齐校验防御测试

覆盖场景：
 1. 3417 形态 —— partial + 尾部缺失
 2. 幻觉标题 —— source_verified=False / unverified_titles 命中
 3. display_no 三种编号风格（中文序号 / 连字符层级 / 多级连字符）
 4. 空 spec + 空 source —— 不抛异常，status=complete，warning 合理
"""

from __future__ import annotations

import pytest

from app.core.writer.directory_spec_alignment import (
    SpecCoverageReport,
    build_numbered_line_inventory,
    evaluate_spec_coverage,
    verify_spec_against_source,
)
from app.schemas.writing import BidDirectoryItem, BidDirectorySpec, BidVolumeSpec


# ─── 公共测试数据 ──────────────────────────────────────────────────────────────

# 第七章原文（完整 25 行编号条目），3417 形态
_SOURCE_3417 = """\
第七章 投标文件格式
一、资格证明文件格式
1 满足《中华人民共和国政府采购法》第二十二条规定
1-1 营业执照等证明文件
1-2 投标人资格声明书
2 落实政府采购政策需满足的资格要求（如有）
2-1 中小企业政策证明文件
2-1-1 中小企业声明文件
2-1-2 拟分包情况说明及分包意向协议
2-2 其它落实政府采购政策的资格要求（如有）
3 本项目的特定资格要求（如有）
3-1 联合体协议（如有）
3-2 其他特定资格要求
4 投标保证金凭证/交款单据电子件
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


def _make_3417_spec() -> BidDirectorySpec:
    """模拟 3417 落库：资格卷完整，商务技术卷仅含前 4 项。"""
    qual_items = [
        BidDirectoryItem(
            order=1,
            title="满足《中华人民共和国政府采购法》第二十二条规定",
            children=[
                BidDirectoryItem(order=1, title="营业执照等证明文件"),
                BidDirectoryItem(order=2, title="投标人资格声明书"),
            ],
        ),
        BidDirectoryItem(
            order=2,
            title="落实政府采购政策需满足的资格要求（如有）",
            children=[
                BidDirectoryItem(
                    order=1,
                    title="中小企业政策证明文件",
                    children=[
                        BidDirectoryItem(order=1, title="中小企业声明文件"),
                        BidDirectoryItem(order=2, title="拟分包情况说明及分包意向协议"),
                    ],
                ),
                BidDirectoryItem(order=2, title="其它落实政府采购政策的资格要求（如有）"),
            ],
        ),
        BidDirectoryItem(
            order=3,
            title="本项目的特定资格要求（如有）",
            children=[
                BidDirectoryItem(order=1, title="联合体协议（如有）"),
                BidDirectoryItem(order=2, title="其他特定资格要求"),
            ],
        ),
        BidDirectoryItem(order=4, title="投标保证金凭证/交款单据电子件"),
    ]
    tech_items = [
        BidDirectoryItem(order=1, title="投标书（实质性格式）"),
        BidDirectoryItem(order=2, title="授权委托书（实质性格式）"),
        BidDirectoryItem(order=3, title="开标一览表（实质性格式）"),
        BidDirectoryItem(order=4, title="投标分项报价表（实质性格式）"),
    ]
    return BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            BidVolumeSpec(
                volume_type="qualification",
                volume_title="资格证明文件格式",
                items=qual_items,
            ),
            BidVolumeSpec(
                volume_type="technical",
                volume_title="商务技术文件格式",
                items=tech_items,
            ),
        ],
    )


# ─── 测试用例 ──────────────────────────────────────────────────────────────────

class TestBuildNumberedLineInventory:
    """编号行识别与去重逻辑。"""

    def test_3417_source_yields_correct_count(self) -> None:
        """25 条不重复编号行（含 一、二、及 1-11 各项）。"""
        inv = build_numbered_line_inventory(_SOURCE_3417)
        # 一、...共13条 + 二、...共12条 = 25
        assert len(inv) == 25

    def test_chinese_numeral_captured(self) -> None:
        inv = build_numbered_line_inventory(_SOURCE_3417)
        nos = {item.display_no for item in inv}
        assert "一、" in nos
        assert "二、" in nos

    def test_hyphen_hierarchy_captured(self) -> None:
        inv = build_numbered_line_inventory(_SOURCE_3417)
        nos = {item.display_no for item in inv}
        assert "1-1" in nos
        assert "2-1-1" in nos

    def test_dedup_by_normalized_title(self) -> None:
        """同一标题在原文中重复出现时只保留首次。"""
        text = "一、资格证明文件格式\n1 营业执照\n一、资格证明文件格式\n1 营业执照"
        inv = build_numbered_line_inventory(text)
        assert len(inv) == 2  # 去重后仅 2 条

    def test_empty_source_returns_empty(self) -> None:
        assert build_numbered_line_inventory("") == []

    def test_line_without_numbering_excluded(self) -> None:
        """第七章 等无编号行不入 inventory。"""
        inv = build_numbered_line_inventory("第七章 投标文件格式\n")
        assert inv == []


class TestVerifySpecAgainstSource:
    """原文对齐：source_verified 与 display_no 填充。"""

    def test_all_qual_items_verified(self) -> None:
        """资格卷全部条目均可在原文核验。"""
        spec = _make_3417_spec()
        verified = verify_spec_against_source(spec, _SOURCE_3417)
        qual_vol = verified.volumes[0]

        def _check(items: list[BidDirectoryItem]) -> None:
            for item in items:
                assert item.source_verified is True, f"未核验: {item.title}"
                _check(item.children)

        _check(qual_vol.items)

    def test_hallucinated_title_not_verified(self) -> None:
        """幻觉标题在原文中不存在，source_verified 应为 False。"""
        spec = BidDirectorySpec(
            has_explicit_directory=True,
            volumes=[
                BidVolumeSpec(
                    volume_type="other",
                    volume_title="测试卷",
                    items=[BidDirectoryItem(order=1, title="火星基地建设方案")],
                )
            ],
        )
        verified = verify_spec_against_source(spec, _SOURCE_3417)
        item = verified.volumes[0].items[0]
        assert item.source_verified is False

    def test_display_no_three_styles(self) -> None:
        """中文序号、连字符层级、多级连字符三种编号风格均能正确回填。"""
        source = (
            "一、资格证明文件格式\n"
            "1-1 营业执照等证明文件\n"
            "2-1-1 中小企业声明文件\n"
        )
        spec = BidDirectorySpec(
            has_explicit_directory=True,
            volumes=[
                BidVolumeSpec(
                    volume_type="qualification",
                    volume_title="投标文件格式",
                    items=[
                        BidDirectoryItem(
                            order=1,
                            title="资格证明文件格式",
                            children=[
                                BidDirectoryItem(order=1, title="营业执照等证明文件"),
                                BidDirectoryItem(order=2, title="中小企业声明文件"),
                            ],
                        )
                    ],
                )
            ],
        )
        verified = verify_spec_against_source(spec, source)
        root_item = verified.volumes[0].items[0]
        assert root_item.display_no == "一、", f"期望 一、，实际 {root_item.display_no}"
        child_1 = root_item.children[0]
        assert child_1.display_no == "1-1", f"期望 1-1，实际 {child_1.display_no}"
        child_2 = root_item.children[1]
        assert child_2.display_no == "2-1-1", f"期望 2-1-1，实际 {child_2.display_no}"

    def test_does_not_mutate_original_spec(self) -> None:
        """原始 spec 对象不被修改（model_copy 返回新对象）。"""
        spec = _make_3417_spec()
        _ = verify_spec_against_source(spec, _SOURCE_3417)
        assert spec.volumes[0].items[0].source_verified is None

    def test_empty_source_marks_all_unverified(self) -> None:
        """空原文时所有条目 source_verified=False。"""
        spec = _make_3417_spec()
        verified = verify_spec_against_source(spec, "")
        item = verified.volumes[0].items[0]
        assert item.source_verified is False


class TestEvaluateSpecCoverage:
    """覆盖率评估：status / missing_titles / unverified_titles / warning。"""

    def test_3417_is_partial(self) -> None:
        """3417 场景：商务技术卷仅 4/11 项 → partial。"""
        spec = _make_3417_spec()
        verified = verify_spec_against_source(spec, _SOURCE_3417)
        inventory = build_numbered_line_inventory(_SOURCE_3417)
        report = evaluate_spec_coverage(verified, inventory)
        assert report.status == "partial"

    def test_3417_missing_tail_items(self) -> None:
        """5-11 项尾部条目必须出现在 missing_titles 中。"""
        spec = _make_3417_spec()
        verified = verify_spec_against_source(spec, _SOURCE_3417)
        inventory = build_numbered_line_inventory(_SOURCE_3417)
        report = evaluate_spec_coverage(verified, inventory)

        assert "合同条款偏离表（实质性格式）" in report.missing_titles
        assert "技术方案" in report.missing_titles

    def test_3417_warning_mentions_count(self) -> None:
        """warning 中应提及 inventory 总数与 extracted 数量。"""
        spec = _make_3417_spec()
        verified = verify_spec_against_source(spec, _SOURCE_3417)
        inventory = build_numbered_line_inventory(_SOURCE_3417)
        report = evaluate_spec_coverage(verified, inventory)
        assert str(report.inventory_count) in report.warning
        assert str(report.extracted_count) in report.warning

    def test_hallucinated_title_in_unverified(self) -> None:
        """幻觉标题经 verify 后 source_verified=False，应出现在 unverified_titles。"""
        spec = BidDirectorySpec(
            has_explicit_directory=True,
            volumes=[
                BidVolumeSpec(
                    volume_type="other",
                    volume_title="测试卷",
                    items=[BidDirectoryItem(order=1, title="火星基地建设方案")],
                )
            ],
        )
        verified = verify_spec_against_source(spec, _SOURCE_3417)
        inventory = build_numbered_line_inventory(_SOURCE_3417)
        report = evaluate_spec_coverage(verified, inventory)
        assert "火星基地建设方案" in report.unverified_titles

    def test_empty_spec_empty_source_complete(self) -> None:
        """空 spec + 空 source → status=complete，不抛异常，warning 说明无编号行。"""
        spec = BidDirectorySpec(has_explicit_directory=False, volumes=[])
        inventory = build_numbered_line_inventory("")
        report = evaluate_spec_coverage(spec, inventory)
        assert report.status == "complete"
        assert report.inventory_count == 0
        assert report.extracted_count == 0
        assert report.missing_titles == []
        assert "无编号行" in report.warning or report.warning == "原文中未检测到编号行，目录清单为空"

    def test_empty_inventory_nonempty_spec_complete(self) -> None:
        """空 inventory + 非空 spec → missing_titles=[]，status=complete。"""
        spec = _make_3417_spec()
        report = evaluate_spec_coverage(spec, [])
        assert report.status == "complete"
        assert report.missing_titles == []
        assert report.extracted_count > 0

    def test_complete_spec_ignores_numbered_paragraphs_inside_chunk_text(self) -> None:
        """: 模板正文编号不能把完整目录误判为 partial。"""

        source_text = '1 投标书(实质性格式) 投标书致:采购人\n1. 我方已详细审查全部招标文件。\n(1) 本投标有效期为三十日。\n\n2 授权委托书(实质性格式) 授权委托书本人<PERSON_A>\n2. 若投标文件均由法定代表人签署，可不提供本授权书。\n'
        spec = BidDirectorySpec(
            has_explicit_directory=True,
            volumes=[
                {
                    "volume_type": "commercial",
                    "volume_title": "商务技术文件格式",
                    "items": [
                        {"order": 1, "title": "投标书(实质性格式)"},
                        {"order": 2, "title": "授权委托书(实质性格式)"},
                    ],
                }
            ],
        )

        verified = verify_spec_against_source(spec, source_text)
        inventory = build_numbered_line_inventory(source_text)
        report = evaluate_spec_coverage(verified, inventory)

        assert [item.display_no for item in inventory] == ["1", "2"]
        assert report.status == "complete"
        assert report.missing_titles == []
