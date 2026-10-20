"""D2 防御测试: scoring_response_checker score_rules 关键要素覆盖弱支撑检查。

风险映射: 高分值素材证据类评分项, AI 有时生成语义流畅但完全泛泛的正文, 未覆盖
评分规则中指定的具体关键要素(如"质量认证证书"、"类似工程业绩"等). 现有检查只能
识别占位/生成失败, 无法识别"貌似响应但实际泛泛"的情况。

锁定契约:
- 高分值(>=10) + 素材证据类 + 已有非占位正文 + score_rules 关键短语均未出现在正文
  → scoring_weak_support (medium)
- 正文包含 score_rules 中任一 4 字关键短语 → 不产生误报
- 无 score_rules 的高分值项 → 不触发此检查
- 低分值项 → 不触发此检查 (只检查高分值)
- 回归: 现有占位/缺素材/生成失败等检查行为不变
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.writer.scoring_response_checker import build_scoring_response_issues  # noqa: E402

import pytest

pytestmark = pytest.mark.scoring


def _clause(cid, *, content="技术方案完整性", score_weight=10.0, confirmed=1, metadata=None):
    return SimpleNamespace(
        id=cid,
        sub_type="scoring_criterion",
        content=content,
        score_weight=score_weight,
        confirmed=confirmed,
        metadata_json=metadata or {"score_item_name": content},
    )


def _draft(clause_ids, *, content="正文内容充实完整", node_id=100, material_ids=None):
    return SimpleNamespace(
        source_clause_ids=json.dumps(clause_ids),
        source_material_ids=json.dumps(material_ids or []),
        content=content,
        outline_node_id=node_id,
    )


# ==================== D2: score_rules 关键要素覆盖弱支撑检查 ====================

def test_high_score_material_item_generic_body_gets_rule_keyword_weak_support() -> None:
    """高分值素材证据类评分项, 正文泛泛未覆盖评分规则关键词 → scoring_weak_support。"""
    clause = _clause(
        1,
        content="质量认证证书",
        score_weight=12.0,
        metadata={
            "score_item_name": "质量认证证书",
            "score_rules": ["须提供有效质量认证证书并附复印件"],
        },
    )
    generic_body = (
        "我方高度重视技术管理工作，将按照招标文件要求认真落实各项管理措施，"
        "确保满足招标人提出的全部技术性要求，保障项目顺利推进。"
        "我方承诺配合完成招标要求的各项工作内容，不遗余力地投入资源完成本项目。"
    )
    drafts = [_draft([1], content=generic_body, node_id=300)]

    issues = build_scoring_response_issues([clause], drafts)

    assert len(issues) == 1
    assert issues[0]["type"] == "scoring_weak_support"
    assert issues[0]["severity"] == "medium"
    assert issues[0]["node_id"] == 300


def test_high_score_material_item_specific_body_no_false_positive() -> None:
    """高分值素材证据类评分项, 正文包含评分规则关键词 → 不产生误报。"""
    clause = _clause(
        1,
        content="质量认证证书",
        score_weight=12.0,
        metadata={
            "score_item_name": "质量认证证书",
            "score_rules": ["须提供有效质量认证证书并附复印件"],
        },
    )
    specific_body = (
        "我方已持有有效质量认证证书，证书编号为XXXX，"
        "有效期覆盖本项目执行期间，将随投标文件一并提交复印件，"
        "确保满足评标委员会的审核要求。"
    )
    drafts = [_draft([1], content=specific_body, node_id=301)]

    issues = build_scoring_response_issues([clause], drafts)

    assert not any(i["type"] == "scoring_weak_support" for i in issues), (
        "正文包含评分规则关键短语时不应产生弱支撑误报"
    )


def test_high_score_item_without_score_rules_no_d2_trigger() -> None:
    """无 score_rules 的高分值项: D2 检查不触发, 不误报弱支撑。"""
    clause = _clause(
        1,
        content="质量认证证书",
        score_weight=15.0,
        metadata={
            "score_item_name": "质量认证证书",
        },
    )
    generic_body = (
        "我方将按照招标文件要求提供所有必要材料，"
        "确保满足评标委员会的各项要求，保障项目顺利实施。"
    )
    drafts = [_draft([1], content=generic_body, node_id=302)]

    issues = build_scoring_response_issues([clause], drafts)

    assert issues == [], "无 score_rules 时不应触发 D2 规则词覆盖检查"


def test_low_score_item_with_score_rules_no_d2_trigger() -> None:
    """低分值(<10)的素材证据类评分项: D2 检查不触发。"""
    clause = _clause(
        1,
        content="质量认证证书",
        score_weight=5.0,  # below threshold
        metadata={
            "score_item_name": "质量认证证书",
            "score_rules": ["须提供有效质量认证证书并附复印件"],
        },
    )
    generic_body = (
        "我方高度重视技术管理工作，将按照招标文件要求认真落实。"
        "确保满足招标人提出的全部技术性要求，保障项目顺利推进。"
    )
    drafts = [_draft([1], content=generic_body, node_id=303)]

    issues = build_scoring_response_issues([clause], drafts)

    assert not any(i["type"] == "scoring_weak_support" for i in issues), (
        "低分值项不应触发 D2 规则词覆盖检查"
    )


# ==================== 回归: 现有弱支撑检查行为不变 ====================

def test_regression_placeholder_still_gets_weak_support() -> None:
    """回归: 正文为占位 + 无素材 → 仍报 scoring_weak_support (缺素材)。"""
    clause = _clause(1)
    drafts = [_draft([1], content="[待补充: 技术方案]", node_id=205)]
    issues = build_scoring_response_issues([clause], drafts)
    assert len(issues) == 1
    assert issues[0]["type"] == "scoring_weak_support"
    assert "缺素材" in issues[0]["message"]


def test_regression_material_evidence_unfilled_fields_still_single_issue() -> None:
    """回归: 素材证据类 + 正文有待补充字段 + 无素材 → 仍只报一条 scoring_weak_support。"""
    clause = _clause(1, content="人员素质", score_weight=10.0)
    content = (
        "<p>我方承诺提供不少于6人的团队主要人员，并附相应职称证书扫描件。</p>"
        "<p>团队成员覆盖项目管理、测试执行、数据分析与质量控制等岗位，"
        "能够满足宁东油田防腐药剂及防腐工具性能测试项目的实施要求。"
        "所有人员证书将在投标文件附件中集中提供，并按招标要求加盖公章。</p>"
        "<table><tr><td>项目负责人</td><td>[待补充:姓名1]</td>"
        "<td>高级工程师</td><td>[待补充:证书编号]</td></tr></table>"
    )
    drafts = [_draft([1], content=content, node_id=210)]

    issues = build_scoring_response_issues([clause], drafts)

    assert len(issues) == 1, "素材证据类含待补充字段时只应报一条 issue"
    assert issues[0]["type"] == "scoring_weak_support"
    assert "缺素材" in issues[0]["message"]
