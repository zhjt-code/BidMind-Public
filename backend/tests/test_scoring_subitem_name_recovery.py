"""空名计分行子项名称恢复防御测试（漏提专项 M3）。

职责: 锁定"合并单元格下的空名计分子行从规则文本首部 N.标签： 恢复原文名称"
      的行为与保护边界；形态取自样本27 p3 实测（服务方案（承诺）下 5 个 5 分子行）。
不做: 不测试跨页列对齐(M4)、不测试行级页码透传(M1)、不改变行数与分值。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.scoring_item_naming import (  # noqa: E402
    recover_subitem_name_from_rule,
)

pytestmark = pytest.mark.scoring


class TestSubitemNameRecovery:
    """样本27 p3 真实形态: 规则首部 "N.标签：" 是子项原文名。"""

    def test_numbered_label_recovered(self):
        rule = "1.印刷服务方案：针对本项目提供印刷服务方案，服务方案内容合理性、可行性"
        assert recover_subitem_name_from_rule(rule) == "印刷服务方案"

    def test_second_numbered_label(self):
        rule = "2.应急预案：有完善可靠的应急预案，内容措施科学合理，能够保证应急需求"
        assert recover_subitem_name_from_rule(rule) == "应急预案"

    def test_label_without_number(self):
        assert recover_subitem_name_from_rule("质量保证措施：按体系完整性评分") == "质量保证措施"

    def test_half_width_colon(self):
        assert recover_subitem_name_from_rule("3.文件检查方案:提供合理校正方案") == "文件检查方案"

    def test_label_with_parenthesis(self):
        rule = "4.进度计划及人员配备情况：提供本项目的进度计划"
        assert recover_subitem_name_from_rule(rule) == "进度计划及人员配备情况"


class TestRecoveryGuards:
    """保护边界: 无标签/说明性引导词/超窗口/计分残段一律不恢复。"""

    def test_no_colon_returns_empty(self):
        rule = "投标人根据项目现状及采购人的总体项目要求编制本单位的服务方案"
        assert recover_subitem_name_from_rule(rule) == ""

    def test_explanatory_lead_word_rejected(self):
        assert recover_subitem_name_from_rule("说明：本项最高得5分") == ""
        assert recover_subitem_name_from_rule("注：须提供承诺函") == ""

    def test_label_beyond_head_window_rejected(self):
        rule = "本项评审依据投标文件相关章节内容并结合专家现场质询情况综合判定" * 2 + "应急预案：措施"
        assert recover_subitem_name_from_rule(rule) == ""

    def test_score_fragment_label_rejected(self):
        assert recover_subitem_name_from_rule("最高加2分：每提供一项加1分") == ""

    def test_empty_rule_returns_empty(self):
        assert recover_subitem_name_from_rule("") == ""
        assert recover_subitem_name_from_rule(None) == ""


class TestAssemblerIntegration:
    """装配器空名计分行走恢复路径, 而非父名 forward-fill 或占位符。"""

    def test_empty_factor_scored_row_uses_recovered_label(self):
        from types import SimpleNamespace

        from app.core.parser.scoring_table_assembler import assemble_scoring_segments

        table = SimpleNamespace(
            data=[
                ["序号", "评审项目", "评审内容及规则", "分值"],
                ["1", "服务方案（承诺）", "1.印刷服务方案：内容合理性、可行性评审", "5"],
                ["", "", "2.应急预案：措施科学合理，保证应急需求", "5"],
                ["", "", "3.文件检查方案：提供合理校正方案", "5"],
            ],
            cell_grid=None,
        )
        parsed_doc = SimpleNamespace(
            pages=[SimpleNamespace(page_num=1, text="", tables=[table])]
        )
        section = SimpleNamespace(title="评分表", page_start=1, page_end=1)

        segments, _claimed = assemble_scoring_segments(parsed_doc, section)
        assert segments, "应装配出逻辑表段"
        factors = [row.factor for row in segments[0].rows]

        assert "服务方案（承诺）" in factors[0], "首行自带因素名, 不走恢复路径"
        assert "应急预案" in factors, "空名计分行应恢复出原文子项名"
        assert "文件检查方案" in factors
        assert not any("跨页评分项" in name for name in factors), "不得产出占位符名"
        assert all(row.score == 5.0 for row in segments[0].rows), "恢复名称不得改变分值"
