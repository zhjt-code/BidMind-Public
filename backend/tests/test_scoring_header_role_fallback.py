"""表头列角色兜底与序号列拒绝防御测试（归档 /Task2 能力移植 U4）。

职责: 锁定「序号列不得充当 name/group 列」与「rule 词面缺失时含表头补全」两条判据。
不做: 不测整表装配结果（由 T0 全字段基线与语料 golden 覆盖）。

真实缺陷形态:
- holdout/31: name 列取到中文序号列，3 个评分项名为 '一'/'二'/'五'
- validation/02: rule 列词面缺失致仅提出 6 项 78 分（应为 12 项 100 分）
- holdout/34 及副本 37: 仅提出 1 项 12 分（应为 8 项 100 分）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.scoring

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.column_role_inference import (  # noqa: E402
    ColumnRoles,
    RoleResolution,
    group_column,
    resolve_header_roles_with_fallback,
    validate_name_rule_orientation,
)

# 中文序号列在 col0、真实项名在 col1 的表（holdout/31 形态）
_ORDINAL_NAME_ROWS = [
    ["一", "企业综合实力", "按业绩数量计分", "9"],
    ["二", "实施方案", "方案完整性评分", "31"],
    ["五", "谈判报价", "报价评分法", "60"],
]
_ORDINAL_HEADER = ["评审项目", "评审因素", "评审标准", "分值"]


class TestOrdinalColumnRejection:
    """序号列不得充当 name 列或 group 列。"""

    def test_orientation_rejects_ordinal_name_column(self):
        """name 列实为中文序号列时资格赛必须否决，迫使回退分布推断。"""
        assert validate_name_rule_orientation(
            _ORDINAL_NAME_ROWS, name_col=0, rule_col=2
        ) is False

    def test_orientation_accepts_real_name_column(self):
        assert validate_name_rule_orientation(
            _ORDINAL_NAME_ROWS, name_col=1, rule_col=2
        ) is True

    def test_group_column_skips_ordinal_column(self):
        """分组列回溯扫描必须跳过序号列，否则组标签会变成 一/二/五。"""
        assert group_column(
            _ORDINAL_NAME_ROWS, width=4, name_col=1, exclude={2, 3}
        ) is None


class TestResolveHeaderRolesWithFallback:
    """兜底决策的三种形态：回退、补全、原样。"""

    def test_ordinal_name_triggers_fallback_and_repair(self):
        """序号 name 列被拒 → 回退分布推断且 name 列位移 → 强制 canonical 渲染。

        分布可辨需要足够的规则文本证据（holdout/31 真实形态即长规则），
        故此处用带长规则的四行样本。
        """
        rows = [
            ["一", "企业综合实力", "近三年同类业绩每个得3分，最多得9分，需附合同复印件", "9"],
            ["二", "实施方案", "方案完整可行得31分，一般得20分，较差得10分", "31"],
            ["三", "服务承诺", "承诺完备得5分，部分承诺得3分", "5"],
            ["五", "谈判报价", "最低价得60分，每高1%扣2分", "55"],
        ]
        lexical = ColumnRoles(score=3, rule=2, name=0, group=None)
        result = resolve_header_roles_with_fallback(
            rows, _ORDINAL_HEADER, lexical,
            score_supported=True, orientation_ok=False,
        )
        assert isinstance(result, RoleResolution)
        assert result.resolved_roles.name == 1, "序号列不得继续充当 name 列"
        assert result.repair_forced is True

    def test_indistinguishable_distribution_keeps_lexical_fail_visible(self):
        """分布不可辨时保留词面并交调用方告警——不得凭空造出更差的角色。

        三行短名表证据不足，infer_column_roles 返回 None，此时必须原样返回词面角色
        且不置 repair_forced（fail-visible 由 assembler 侧日志承担）。
        """
        lexical = ColumnRoles(score=3, rule=2, name=0, group=None)
        result = resolve_header_roles_with_fallback(
            _ORDINAL_NAME_ROWS, _ORDINAL_HEADER, lexical,
            score_supported=True, orientation_ok=False,
        )
        assert result.resolved_roles == lexical
        assert result.repair_forced is False

    def test_passing_roles_are_left_untouched(self):
        """词面角色完好时不得改写（零回归护栏）。"""
        lexical = ColumnRoles(score=3, rule=2, name=1, group=None)
        result = resolve_header_roles_with_fallback(
            _ORDINAL_NAME_ROWS, _ORDINAL_HEADER, lexical,
            score_supported=True, orientation_ok=True,
        )
        assert result.resolved_roles == lexical
        assert result.repair_forced is False

    def test_rule_missing_fills_from_header_when_quality_gate_passes(self):
        """rule 词面缺失且推断填充不劣于词面时，走 canonical 补全通路。"""
        rows = [
            ["", "技术方案", "方案完整得4分", "4"],
            ["", "实施方案", "措施可行得3分", "3"],
            ["", "项目团队", "配置齐全得3分", "3"],
        ]
        header = ["序号", "评审因素", "评审标准", "分值"]
        lexical = ColumnRoles(score=3, rule=None, name=0, group=None)
        result = resolve_header_roles_with_fallback(
            rows, header, lexical, score_supported=True, orientation_ok=True,
        )
        assert result.canonical_roles is not None, "rule 缺失应触发补全"
        assert result.canonical_roles.rule is not None

    def test_canonical_fill_blocked_when_inferred_name_fills_less(self):
        """质量门禁: 推断 name 列填充少于词面时不得替换（防宽稀疏表错选）。"""
        rows = [
            ["技术方案", "", "方案完整得4分", "4"],
            ["实施方案", "", "措施可行得3分", "3"],
            ["项目团队", "", "配置齐全得3分", "3"],
        ]
        header = ["评审因素", "备注", "评审标准", "分值"]
        lexical = ColumnRoles(score=3, rule=None, name=0, group=None)
        result = resolve_header_roles_with_fallback(
            rows, header, lexical, score_supported=True, orientation_ok=True,
        )
        if result.canonical_roles is not None:
            # 允许补全，但绝不能把填充更少的列选为 name
            assert result.canonical_roles.name == 0
