"""技术测试任务覆盖检查测试。"""

from __future__ import annotations

from types import SimpleNamespace


def test_missing_listed_test_tasks_are_reported() -> None:
    """招标约束列明多项测试任务时, 正文缺项必须进入一致性问题。"""
    from app.core.writer.technical_task_coverage_checker import (
        build_technical_task_coverage_issues,
    )

    clause = SimpleNamespace(
        id=1,
        sub_type="emphasis_point",
        content=(
            "招标人强调服务内容需覆盖全部8项测试分析项目，包括地层水离子特征分析测试、"
            "地层水细菌分布特征分析测试、腐蚀产物演化特征分析测试、多温度梯度下材料腐蚀特征分析测试。"
        ),
    )
    drafts = [
        SimpleNamespace(
            content="本章详细说明多温度梯度下材料腐蚀特征分析测试和地层水离子特征分析测试。"
        )
    ]

    issues = build_technical_task_coverage_issues([clause], drafts)

    assert len(issues) == 1
    assert issues[0]["type"] == "technical_task_coverage"
    assert issues[0]["severity"] == "high"
    assert "地层水细菌分布特征分析测试" in issues[0]["message"]
    assert "腐蚀产物演化特征分析测试" in issues[0]["message"]
    assert "全部8项测试分析项目" not in issues[0]["message"]


def test_all_listed_test_tasks_present_passes() -> None:
    from app.core.writer.technical_task_coverage_checker import (
        build_technical_task_coverage_issues,
    )

    clause = SimpleNamespace(
        id=1,
        sub_type="emphasis_point",
        content="服务内容包括地层水离子特征分析测试、地层水细菌分布特征分析测试。",
    )
    drafts = [
        SimpleNamespace(content="地层水离子特征分析测试方案。"),
        SimpleNamespace(content="地层水细菌分布特征分析测试方案。"),
    ]

    assert build_technical_task_coverage_issues([clause], drafts) == []
