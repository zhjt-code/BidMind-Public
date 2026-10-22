"""评分项名称装饰剥离防御测试（失败归因专项）。

职责: 锁定"序号前缀"与"尾部括号分值"两类名称装饰的剥离行为及其保护边界，
 形态全部取自 33 份训练语料的真实提取产物（见 scoring_failure_attribution.md）。
不做: 不测试污染名恢复路径（由 test_scoring_item_naming/ 用例覆盖）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.scoring_item_naming import (  # noqa: E402
    normalize_scoring_item_name,
)

pytestmark = pytest.mark.scoring


class TestOrdinalPrefixStripping:
    """序号前缀剥离（样本10 实测形态: 提取名带 "1、" 前缀致 20/21 项匹配失败）。"""

    def test_arabic_ordinal_with_chinese_comma(self):
        assert normalize_scoring_item_name("1、技术参数响应情况1", "") == "技术参数响应情况1"

    def test_arabic_ordinal_with_dot(self):
        assert normalize_scoring_item_name("10.沟通协调", "") == "沟通协调"

    def test_chinese_ordinal(self):
        assert normalize_scoring_item_name("一、企业综合实力", "") == "企业综合实力"

    def test_parenthesized_ordinal(self):
        assert normalize_scoring_item_name("（1）质量控制", "") == "质量控制"

    def test_trailing_digit_in_name_is_preserved(self):
        """关键负例(样本10真实形态): 名称自带的尾部编号是身份的一部分, 不得剥离。"""
        assert normalize_scoring_item_name("人员实力1", "") == "人员实力1"
        assert normalize_scoring_item_name("人员实力2", "") == "人员实力2"

    def test_year_like_prefix_is_preserved(self):
        """数字后非序号分隔符(如"2022年")不是序号前缀。"""
        assert normalize_scoring_item_name("2022年以来业绩", "") == "2022年以来业绩"

    def test_pure_ordinal_name_kept_as_is(self):
        """整名即序号(样本31: 名称取到序号列)剥离后为空, 必须保留原值而非产出空名。"""
        assert normalize_scoring_item_name("一", "") == "一"


class TestTrailingScoreSuffixStripping:
    """尾部括号分值剥离（样本18 实测形态: "体系认证（3分）" 致 13/14 项匹配失败）。"""

    def test_full_width_closed_suffix(self):
        assert normalize_scoring_item_name("体系认证（3分）", "") == "体系认证"

    def test_unclosed_suffix_from_cell_truncation(self):
        """样本18真实形态: 跨单元格截断导致右括号缺失。"""
        assert normalize_scoring_item_name("项目负责人（3分", "") == "项目负责人"

    def test_half_width_suffix(self):
        assert normalize_scoring_item_name("财务状况(2分)", "") == "财务状况"

    def test_decimal_score_suffix(self):
        assert normalize_scoring_item_name("环保管理体系与措施（1.5分）", "") == "环保管理体系与措施"

    def test_disambiguation_suffix_is_preserved(self):
        """关键负例: "(2)" 是去重消歧后缀(无"分"字), 剥离会造成身份重复。"""
        assert normalize_scoring_item_name("跨页评分项(2)", "") == "跨页评分项(2)"

    def test_non_score_parenthesis_is_preserved(self):
        assert normalize_scoring_item_name("资质（含专业资质）", "") == "资质（含专业资质）"

    def test_inner_parenthesis_is_preserved(self):
        name = "信息安全、知识产权管理的方法和措施"
        assert normalize_scoring_item_name(name, "") == name


class TestBareTrailingScoreStripping:
    """裸尾分值剥离（归档 能力移植 U2）。

 validation02 / holdout_round2-36 实测形态: 项名为 "技术性能58分" / "价格30分"，
 分值直接缀在名后且无括号包裹，与徽标分值同屏展示时自相矛盾。
 """

    def test_bare_trailing_score_is_stripped(self):
        assert normalize_scoring_item_name("技术性能58分", "") == "技术性能"
        assert normalize_scoring_item_name("价格30分", "") == "价格"

    def test_decimal_bare_trailing_score(self):
        assert normalize_scoring_item_name("施工方案15.5分", "") == "施工方案"

    def test_score_not_at_end_is_preserved(self):
        """关键负例: "分" 后还有字时不在尾部，"第3分册" 不得被剥成 "第"。"""
        assert normalize_scoring_item_name("第3分册技术要求", "") == "第3分册技术要求"

    def test_non_cjk_prefix_is_preserved(self):
        """关键负例: lookbehind 要求紧前为汉字，纯数字/ASCII 前缀不剥离。"""
        assert normalize_scoring_item_name("A30分", "") == "A30分"

    def test_single_char_result_falls_back(self):
        """最小业务名护栏: 剥离后仅剩单字无法辨识业务含义，回退原值。"""
        assert normalize_scoring_item_name("第3分", "") == "第3分"


class TestCombinedDecoration:
    """前缀与尾缀共存及与既有污染名恢复路径的协同。"""

    def test_prefix_and_suffix_both_stripped(self):
        assert normalize_scoring_item_name("1、体系认证（3分）", "") == "体系认证"

    def test_polluted_name_recovery_still_applies(self):
        """既有污染名恢复路径不得回归: 超长汇总规则名仍从规则尾标签恢复。"""
        polluted = "技术标" + "汇总规则" + "：按各项加权平均计算得分" * 3
        result = normalize_scoring_item_name(polluted, "评标委员会按方案完整性打分；施工方案")
        assert result == "施工方案"

    def test_empty_input_unchanged(self):
        assert normalize_scoring_item_name("", "") == ""


# 阶段3(2026-08-31): 身份守恒用例(TestIdentityConservationAfterStripping)随确定性链
# 去重助手(_dedupe_exact)删除; 命名净化本体用例保留如上。
