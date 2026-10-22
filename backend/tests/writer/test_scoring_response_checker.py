"""S3.1 防御测试: 评分项正文响应三级检查 scoring_response_checker + 覆盖通道防双报。

映射风险(用户诉求三): 评分项直接决定得分, 未响应/占位/非正文响应必须分级明确提示,
不能与普通约束混为一条 warning 默认折叠。锁定:
- 有分值未响应 → high(将失分); 占位/生成失败 → medium; 价格分/非正文标记 → info(事实告知)。
- 豁免优先于未响应; 无分值说明项不计入失分缺口; 未确认项不检查。
- coverage 通道排除 scoring_criterion, 防同一评分项双报。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from app.core.writer.clause_coverage_checker import build_clause_coverage_issues  # noqa: E402
from app.core.writer.scoring_response_checker import build_scoring_response_issues  # noqa: E402

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


def _types(issues):
    return {i["type"] for i in issues}


def test_unresponded_scoring_item_is_high():
    issues = build_scoring_response_issues([_clause(1, score_weight=10.0)], [])
    assert len(issues) == 1
    assert issues[0]["type"] == "scoring_unresponded"
    assert issues[0]["severity"] == "high"
    assert "满分10分" in issues[0]["message"]


def test_responded_by_real_content_no_issue():
    clause = _clause(1)
    drafts = [_draft([1], content="本方案覆盖全部技术指标，包含详细实施路径")]
    assert build_scoring_response_issues([clause], drafts) == []


def test_scoring_item_covered_by_equivalent_clause_and_body_fact_no_unresponded_issue():
    scoring = _clause(
        1,
        content="自合同生效之日起90天内完成相关测试并提交成果报告得6分",
        score_weight=6.0,
        metadata={
            "score_item_name": "工期保障",
            "max_score": 6.0,
            "source": "scoring_table",
        },
    )
    equivalent = SimpleNamespace(
        id=2,
        sub_type="disqualification",
        content="工期保障评分项：自合同生效之日起90天内完成相关测试并提交成果报告得6分",
        score_weight=6.0,
        confirmed=1,
        metadata_json={},
    )
    drafts = [
        _draft(
            [2],
            content=(
                "我方承诺自合同生效之日起90天内完成全部测试任务，"
                "并按招标文件要求提交正式成果报告。"
            ),
            node_id=6782,
        )
    ]

    issues = build_scoring_response_issues([scoring, equivalent], drafts)

    assert not any(issue["type"] == "scoring_unresponded" for issue in issues)


def test_same_score_unrelated_clause_does_not_hide_scoring_gap():
    scoring = _clause(
        1,
        content="工期保障：90天内完成得6分",
        score_weight=6.0,
        metadata={"score_item_name": "工期保障"},
    )
    unrelated = SimpleNamespace(
        id=2,
        sub_type="content_required",
        content="设备仪器配置完整得6分",
        score_weight=6.0,
        confirmed=1,
        metadata_json={},
    )
    drafts = [_draft([2], content="我方已配置完整设备仪器。")]

    issues = build_scoring_response_issues([scoring, unrelated], drafts)

    assert any(issue["type"] == "scoring_unresponded" for issue in issues)


def test_performance_scoring_item_covered_by_performance_material_clause():
    scoring = _clause(
        1,
        content="施工业绩满分12分",
        score_weight=12.0,
        metadata={"score_item_name": "施工业绩"},
    )
    performance_clause = SimpleNamespace(
        id=2,
        sub_type="table_format",
        content="业绩汇总表格式：招标人、项目名称、服务质量，并附业绩证明材料",
        score_weight=None,
        confirmed=1,
        metadata_json={"material_kind": "业绩"},
    )
    drafts = [_draft([2], content="已按业绩汇总表列明项目名称，并附相关业绩证明材料。", material_ids=[729])]

    issues = build_scoring_response_issues([scoring, performance_clause], drafts)

    assert not any(issue["type"] == "scoring_unresponded" for issue in issues)


def test_shared_evidence_keyword_with_different_scores_does_not_hide_gap():
    scoring = _clause(
        1,
        content="类似工程业绩满分10分",
        score_weight=10.0,
        metadata={"score_item_name": "类似工程业绩"},
    )
    other_scoring_clause = SimpleNamespace(
        id=2,
        sub_type="scoring_criterion",
        content="项目经理业绩满分5分",
        score_weight=5.0,
        confirmed=1,
        metadata_json={"score_item_name": "项目经理业绩"},
    )
    drafts = [_draft([2], content="项目经理具备相关业绩证明材料。")]

    issues = build_scoring_response_issues([scoring, other_scoring_clause], drafts)

    assert any(issue["type"] == "scoring_unresponded" for issue in issues)


def test_scoring_fact_match_without_action_terms_counts_as_response():
    scoring = _clause(
        1,
        content="质量认证证书得5分",
        score_weight=5.0,
        metadata={"score_item_name": "质量认证"},
    )
    drafts = [_draft([], content="我方已提供质量认证证书复印件并加盖公章。")]

    issues = build_scoring_response_issues([scoring], drafts)

    assert not any(issue["type"] == "scoring_unresponded" for issue in issues)


def test_placeholder_without_materials_is_missing_material():
    # 占位且未使用素材 → 提示"缺素材, 需上传绑定"(项2: 素材未上传/未绑定)
    clause = _clause(1)
    drafts = [_draft([1], content="[待补充: 技术方案]", node_id=205)]
    issues = build_scoring_response_issues([clause], drafts)
    assert len(issues) == 1
    assert issues[0]["type"] == "scoring_weak_support"
    assert issues[0]["severity"] == "medium"
    assert issues[0]["node_id"] == 205
    assert "缺素材" in issues[0]["message"]


def test_long_scoring_response_with_local_placeholders_is_not_missing_material():
    # 长篇论述正文中含少量局部待补充字段，不应被误判为整章占位/缺素材。
    clause = _clause(
        1,
        content="实验方案：需说明测试目的、实验条件、使用仪器和测试步骤",
        score_weight=15.0,
        metadata={"score_item_name": "实验方案"},
    )
    content = (
        "<p>针对宁东油田防腐药剂及防腐工具性能测试项目，我方制定系统测试方案。</p>"
        "<p>本方案覆盖腐蚀行为分析、缓蚀剂性能评价、阴极保护器效能测试等内容，"
        "逐项说明测试目的、实验条件、测试方法、数据记录和质量控制措施。</p>"
        "<p>实验条件包含温度[待补充:具体温度范围]、压力[待补充:具体压力范围]，"
        "使用仪器型号[待补充:设备型号]需要人工按最终设备参数补齐，"
        "但章节正文已经形成有效响应。</p>"
    )
    drafts = [_draft([1], content=content, node_id=209)]

    assert build_scoring_response_issues([clause], drafts) == []


def test_material_evidence_scoring_with_unfilled_fields_is_missing_material():
    # 人员/证书等素材证明类评分项，正文含待填证书字段且未使用素材时仍应提示缺素材。
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

    assert len(issues) == 1
    assert issues[0]["type"] == "scoring_weak_support"
    assert issues[0]["node_id"] == 210
    assert "缺素材" in issues[0]["message"]


def test_placeholder_with_materials_is_generation_failure():
    # 占位但已用素材 → 提示"生成失败, 请重试"(项2: 区别于缺素材)
    clause = _clause(1)
    drafts = [_draft([1], content="[内容生成失败]", node_id=207, material_ids=[9, 10])]
    issues = build_scoring_response_issues([clause], drafts)
    assert len(issues) == 1
    assert issues[0]["type"] == "scoring_weak_support"
    assert "生成失败" in issues[0]["message"]
    assert "缺素材" not in issues[0]["message"]


def test_manual_non_body_flag_is_info_with_manual_wording():
    # 人工标记(无 price 卷) → info, 文案为"已人工标记", 且引导可取消标记走素材响应
    clause = _clause(1, metadata={"score_item_name": "报价", "non_body_response": True})
    issues = build_scoring_response_issues([clause], [])
    assert len(issues) == 1
    assert issues[0]["type"] == "scoring_non_body"
    assert issues[0]["severity"] == "info"
    assert "已人工标记为非正文响应" in issues[0]["message"]
    assert "素材" in issues[0]["detail"]


def test_price_volume_is_info():
    clause = _clause(1, score_weight=30.0, metadata={"score_item_name": "投标报价", "volume": "price"})
    issues = build_scoring_response_issues([clause], [])
    assert issues[0]["type"] == "scoring_non_body"
    assert issues[0]["severity"] == "info"
    assert "价格分" in issues[0]["message"]
    # 项1: 不再暗示"资格性材料由...决定", 避免误导资格分不用素材响应
    assert "资格" not in issues[0]["detail"]
    assert "资质" not in issues[0]["detail"]


def test_no_score_explanatory_item_not_counted_as_gap():
    # 无分值说明项(资格性/符合性)未响应不构成失分缺口
    clause = _clause(1, score_weight=None)
    assert build_scoring_response_issues([clause], []) == []


def test_unconfirmed_scoring_item_skipped():
    clause = _clause(1, confirmed=0)
    assert build_scoring_response_issues([clause], []) == []


def test_coverage_excludes_scoring_criterion_when_specified():
    # confirmed scoring_criterion 未覆盖时, coverage 通道在排除后不再报(交由专项检查)
    scoring = SimpleNamespace(
        id=1, sub_type="scoring_criterion", content="评分项X",
        confirmed=1, source_page=5,
    )
    coverage = build_clause_coverage_issues(
        [scoring], [], exclude_sub_types={"scoring_criterion"}
    )
    assert coverage == []
    # 不传排除集时仍按原逻辑报(回退兼容)
    fallback = build_clause_coverage_issues([scoring], [])
    assert _types(fallback) == {"clause_coverage"}
