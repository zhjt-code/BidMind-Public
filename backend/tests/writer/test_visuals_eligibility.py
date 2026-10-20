""": 图示资格闸门不得用画像 rejects 子串误杀。"""

from app.core.writer.visuals.eligibility_policy import (
    filter_visual_intents,
    _is_visual_allowed,
)


def test_rejects_containing_zhengming_do_not_kill_flowchart() -> None:
    visual = {
        "diagram_type": "flowchart",
        "title": "多相流环境下套管材料界面腐蚀行为分析测试流程图",
        "purpose": "直观展示测试步骤",
    }
    profile = {
        "semantic_role": "详细描述测试方案",
        "expects": ["测试目的描述"],
        "rejects": ["业绩证明"],
    }
    assert _is_visual_allowed(
        visual,
        node_title="多相流环境下套管材料界面腐蚀行为分析测试方案",
        chapter_profile=profile,
    ) is True


def test_rejects_containing_zhengshu_do_not_kill_gantt() -> None:
    visual = {
        "diagram_type": "gantt",
        "title": "宁东油田防腐性能测试项目90天进度计划甘特图",
        "purpose": "直观展示90天工期内的阶段划分",
    }
    profile = {
        "semantic_role": "用于证明投标人能够按时完成项目",
        "expects": ["工期保障措施描述"],
        "rejects": ["资质证书"],
    }
    assert _is_visual_allowed(
        visual,
        node_title="工期保障措施及进度计划",
        chapter_profile=profile,
    ) is True


def test_identity_proof_in_rejects_does_not_kill_gantt() -> None:
    visual = {
        "diagram_type": "gantt",
        "title": "90天工期保障甘特图",
        "purpose": "支撑并行测试排期论述",
    }
    profile = {
        "semantic_role": "90天工期保障",
        "expects": ["free_writing"],
        "rejects": ["fixed_template（法定代表人身份证明）"],
    }
    assert _is_visual_allowed(
        visual,
        node_title="工期保障方案（90天内交付）",
        chapter_profile=profile,
    ) is True


def test_chengnuo_in_title_does_not_kill_flowchart_with_liucheng() -> None:
    visual = {
        "diagram_type": "flowchart",
        "title": "成果报告交付流程图",
        "purpose": "支撑报告盖章交付措施论述",
    }
    profile = {
        "semantic_role": "成果报告交付承诺的章节",
        "expects": ["成果报告交付承诺"],
        "rejects": [],
    }
    assert _is_visual_allowed(
        visual,
        node_title="成果报告交付承诺",
        chapter_profile=profile,
    ) is True


def test_company_intro_still_rejects_diagram() -> None:
    result = filter_visual_intents(
        {
            "visuals": [
                {
                    "visual_id": "vis_1",
                    "diagram_type": "flowchart",
                    "title": "公司简介流程图",
                    "purpose": "展示企业概况",
                }
            ],
        },
        node_title="公司简介",
        chapter_profile={"semantic_role": "介绍投标人基本情况的文字章节"},
    )
    assert result.payload["visuals"] == []
    assert result.patches[0]["kind"] == "visual_policy_rejected"
    assert len(result.rejected) == 1


def test_org_chapter_does_not_qualify_as_flowchart() -> None:
    visual = {
        "diagram_type": "flowchart",
        "title": "项目组织架构图",
        "purpose": "展示汇报关系",
    }
    assert _is_visual_allowed(
        visual,
        node_title="项目组织架构与职责",
        chapter_profile={"semantic_role": "说明项目团队层级"},
    ) is False


def test_site_layout_chapter_rejects_flowchart() -> None:
    visual = {
        "diagram_type": "flowchart",
        "title": "施工平面布置流程图",
        "purpose": "展示场地布置",
    }
    assert _is_visual_allowed(
        visual,
        node_title="施工平面布置",
        chapter_profile={"semantic_role": "说明现场平面布置"},
    ) is False


def test_construction_process_chapter_still_allows_flowchart() -> None:
    visual = {
        "diagram_type": "flowchart",
        "title": "主体施工工艺流程图",
        "purpose": "展示工序先后",
    }
    assert _is_visual_allowed(
        visual,
        node_title="施工工艺",
        chapter_profile={"semantic_role": "说明关键工序"},
    ) is True


def test_spatial_drawing_chapters_reject_all_generated_diagrams() -> None:
    visual = {
        "diagram_type": "flowchart",
        "title": "实施步骤流程图",
        "purpose": "展示实施步骤",
    }
    for title in ("基坑支护", "脚手架专项方案", "BIM效果展示"):
        assert _is_visual_allowed(
            visual,
            node_title=title,
            chapter_profile={"semantic_role": "工程图示章节"},
        ) is False
