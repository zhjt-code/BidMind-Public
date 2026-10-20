"""招标要求提取步骤契约的同源性防御测试。

职责: 锁定 `app.core.tasks.extract_progress` 的结构不变量, 并验证两个消费点
 (`extract_task` 心跳、`requirement_service` 状态接口 steps[]) 确实来自
 该契约而非各自维护一份映射。
不做: 不启动后台任务、不访问数据库/Redis、不校验前端实现。

为何需要: 阶段映射此前分散在三处(后端两处 + 前端一处)人工对齐, 评分项这一
独立长阶段因此长期被折叠进"保存结果"步内(的展示侧诱因)。这里把
"唯一同源"变成机械可验证的断言。
"""

from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.tasks import extract_task  # noqa: E402
from app.core.tasks.extract_progress import (  # noqa: E402
    CHILD_COMPLETED_MAX_PROGRESS,
    EXTRACT_PROGRESS_STEPS,
    EXTRACT_STAGE_COMPLETED,
    EXTRACT_STAGE_WEIGHTS,
    fold_extract_stage_progress,
    normalize_extract_heartbeat_stage,
    step_index_for_stage,
)
from app.services.requirement_service import (  # noqa: E402
    _build_requirement_extract_steps,
)

pytestmark = pytest.mark.scoring

# `run_extract_task` 中与进度无关的生命周期/终态 stage: 不参与步骤区间映射。
_LIFECYCLE_STAGES = {"启动", "失败", "cancelled", "failed", EXTRACT_STAGE_COMPLETED}


# ===== 契约自身的结构不变量 =====


def test_step_ranges_are_contiguous_from_zero_to_hundred() -> None:
    """步骤区间必须首尾相接、起于 0 终于 100, 否则整体进度会出现空洞或倒退。"""
    assert EXTRACT_PROGRESS_STEPS[0].start == 0
    assert EXTRACT_PROGRESS_STEPS[-1].end == 100
    for prev, nxt in zip(EXTRACT_PROGRESS_STEPS, EXTRACT_PROGRESS_STEPS[1:]):
        assert prev.end == nxt.start, f"步骤 {prev.key} 与 {nxt.key} 之间区间不连续"
        assert prev.start < prev.end, f"步骤 {prev.key} 区间非递增"


def test_step_keys_and_stages_are_unique() -> None:
    """key 是前端绑定标识, stage 是心跳到步骤的唯一索引, 二者都不得重复。"""
    keys = [step.key for step in EXTRACT_PROGRESS_STEPS]
    assert len(keys) == len(set(keys))

    stages = [stage for step in EXTRACT_PROGRESS_STEPS for stage in step.stages]
    assert len(stages) == len(set(stages)), "同一 stage 落到多个步骤会使高亮位置不确定"


def test_mandatory_coverage_is_its_own_second_step_before_consolidate() -> None:
    """: 高风险核验在合并前执行(补提项进入同一收敛栈), 必须是独立第二步。"""
    coverage_idx = step_index_for_stage("mandatory_coverage")
    assert coverage_idx == 1
    coverage_step = EXTRACT_PROGRESS_STEPS[coverage_idx]
    assert coverage_step.stages == ("mandatory_coverage",)
    assert coverage_idx > step_index_for_stage("extracting")
    assert coverage_idx < step_index_for_stage("merging")
    assert coverage_step.end < CHILD_COMPLETED_MAX_PROGRESS


def test_scoring_extraction_is_its_own_fourth_step() -> None:
    """评分项提取必须是独立的第四步, 不得再与保存/合并/核验共用步骤。"""
    scoring_idx = step_index_for_stage("scoring_extract")
    assert scoring_idx == 3
    scoring_step = EXTRACT_PROGRESS_STEPS[scoring_idx]
    assert scoring_step.stages == ("scoring_extract",), "评分步不得混入其他阶段"
    assert step_index_for_stage("saving") != scoring_idx
    assert step_index_for_stage("mandatory_coverage") != scoring_idx


def test_consolidate_bands_widen_merging_for_step_bar() -> None:
    """: merging 带加宽到 55-76, saving 承接到评分步起点; 钳制仍停在步终点前。"""
    assert EXTRACT_STAGE_WEIGHTS["merging"] == (55, 76)
    assert EXTRACT_STAGE_WEIGHTS["dedup"] == (55, 76)
    assert EXTRACT_STAGE_WEIGHTS["saving"] == (76, 90)
    assert CHILD_COMPLETED_MAX_PROGRESS == 89


def test_stage_weights_derive_from_steps_and_include_terminal() -> None:
    """权重表必须由步骤契约派生, 不允许出现契约外的 stage。"""
    contract_stages = {
        stage for step in EXTRACT_PROGRESS_STEPS for stage in step.stages
    }
    assert contract_stages | {EXTRACT_STAGE_COMPLETED} == set(EXTRACT_STAGE_WEIGHTS)

    for step in EXTRACT_PROGRESS_STEPS:
        for band in step.bands:
            for stage in band.stages:
                assert EXTRACT_STAGE_WEIGHTS[stage] == (band.start, band.end)


def test_child_completed_clamped_inside_consolidate_step() -> None:
    """: 子提取器 completed 必须落在"合并与保存"步内且不触达其终点。

 钳制值一旦 >= 该步终点, 状态接口会把该步判 completed 并连带把后续步骤
 显示成已完成——正是后继长阶段被"跳过"的展示形态。
 """
    stage, progress = normalize_extract_heartbeat_stage("completed", 100)

    consolidate = EXTRACT_PROGRESS_STEPS[step_index_for_stage(stage)]
    assert consolidate.key == "consolidate"
    assert progress == CHILD_COMPLETED_MAX_PROGRESS
    assert consolidate.start <= progress < consolidate.end


def test_non_completed_stage_passes_through_unchanged() -> None:
    """归一只针对子提取器 completed, 其他阶段原样透传。"""
    assert normalize_extract_heartbeat_stage("scoring_extract", 93) == (
        "scoring_extract",
        93,
    )
    assert normalize_extract_heartbeat_stage("mandatory_coverage", 52) == (
        "mandatory_coverage",
        52,
    )


# ===== 两个消费点确实同源 =====


def _emitted_heartbeat_stages() -> set[str]:
    """扫描 run_extract_task 源码中写入心跳的 stage 字面量。"""
    source = inspect.getsource(extract_task)
    return set(re.findall(r'stage="([^"]+)"', source))


def test_extract_task_emits_only_contract_or_lifecycle_stages() -> None:
    """任务发射的进度类 stage 必须全部在契约内, 否则前端无步骤可对应。"""
    contract_stages = set(EXTRACT_STAGE_WEIGHTS)
    unknown = _emitted_heartbeat_stages() - _LIFECYCLE_STAGES - contract_stages
    assert not unknown, f"发射了契约外的心跳阶段: {sorted(unknown)}"


def test_extract_task_no_longer_reuses_saving_for_scoring_phase() -> None:
    """评分项阶段必须走 scoring_extract, 不得再借用 saving 字面量冒充。"""
    source = inspect.getsource(extract_task)
    assert 'stage="saving"' not in source
    assert extract_task._STAGE_SCORING_EXTRACT in EXTRACT_STAGE_WEIGHTS
    assert extract_task._STAGE_FINALIZING in EXTRACT_STAGE_WEIGHTS
    assert source.count("stage=_STAGE_SCORING_EXTRACT") == 2, (
        "评分阶段入口心跳与长阶段保活必须使用同一阶段"
    )


def test_mandatory_coverage_emits_contract_stage_from_extractor_not_task() -> None:
    """: 高风险核验在 extractor 合并前执行并发射契约内独立 stage;
 extract_task 不再持有落库后的核验路径(那条路径绕过合并收敛栈)。"""
    from app.core.extractor.mandatory import apply_coverage, atomic_extract

    assert "mandatory_coverage" in EXTRACT_STAGE_WEIGHTS
    for module in (apply_coverage, atomic_extract):
        module_source = inspect.getsource(module)
        assert 'progress_callback("mandatory_coverage"' in module_source
        assert "merging" not in module_source

    task_source = inspect.getsource(extract_task)
    assert "apply_mandatory_atomic_coverage" not in task_source
    assert "_mandatory_coverage_report" in task_source


def test_status_steps_expose_stable_keys_matching_contract() -> None:
    """状态接口 steps[] 必须携带与契约同名的 key 供前端绑定。"""
    steps = _build_requirement_extract_steps(
        stage="extracting", progress=10, message="提取中", status="running",
    )
    assert [s["key"] for s in steps] == [s.key for s in EXTRACT_PROGRESS_STEPS]
    assert [s["label"] for s in steps] == [s.label for s in EXTRACT_PROGRESS_STEPS]

    done = _build_requirement_extract_steps(
        stage="completed", progress=100, message="", status="completed",
    )
    assert [s["key"] for s in done] == [s.key for s in EXTRACT_PROGRESS_STEPS]


def test_mandatory_coverage_heartbeat_renders_second_step_running() -> None:
    """高风险核验心跳下, 第二步 running, 合并/评分/归并仍等待。"""
    steps = _build_requirement_extract_steps(
        stage="mandatory_coverage",
        progress=52,
        message="核验否决与资格条款 1/4",
        status="running",
    )

    assert [s["status"] for s in steps] == [
        "completed",
        "running",
        "waiting",
        "waiting",
        "waiting",
    ]
    assert steps[1]["key"] == "mandatory_coverage"
    assert steps[1]["detail"] == "核验否决与资格条款 1/4"


def test_active_step_stays_running_at_own_band_end_mandatory() -> None:
    """: 当前步触达 band 终点仍 running，不得显示完成。"""
    steps = _build_requirement_extract_steps(
        stage="mandatory_coverage",
        progress=55,
        message="核验否决与资格条款 5/5",
        status="running",
    )

    assert [s["status"] for s in steps] == [
        "completed",
        "running",
        "waiting",
        "waiting",
        "waiting",
    ]
    assert steps[1]["progress"] == 100
    assert steps[1]["detail"] == "核验否决与资格条款 5/5"


def test_active_step_stays_running_at_own_band_end_scoring() -> None:
    """同形态第二例: 评分步触达 96 仍 running，归并不得提前完成。"""
    steps = _build_requirement_extract_steps(
        stage="scoring_extract",
        progress=96,
        message="评分项提取 10/10",
        status="running",
    )

    assert [s["status"] for s in steps] == [
        "completed",
        "completed",
        "completed",
        "running",
        "waiting",
    ]
    assert steps[3]["progress"] == 100
    assert steps[3]["detail"] == "评分项提取 10/10"


def test_scoring_heartbeat_renders_fourth_step_running() -> None:
    """评分项心跳下, 第四步 running 且第五步仍等待(不得提前显示完成)。"""
    steps = _build_requirement_extract_steps(
        stage="scoring_extract", progress=93, message="评分项提取中", status="running",
    )

    assert [s["status"] for s in steps] == [
        "completed",
        "completed",
        "completed",
        "running",
        "waiting",
    ]
    assert steps[3]["key"] == "scoring"
    assert steps[3]["detail"] == "评分项提取中"


def test_finalizing_heartbeat_renders_fifth_step_running() -> None:
    """归并核验心跳下, 第五步 running 且评分步已完成。"""
    steps = _build_requirement_extract_steps(
        stage="finalizing", progress=97, message="整理提取结果", status="running",
    )

    assert [s["status"] for s in steps] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "running",
    ]
    assert steps[4]["key"] == "finalize"


def test_finalizing_with_lagging_progress_still_completes_scoring_step() -> None:
    """: stage 已到归并核验时, overall 仍落在评分带内不得把评分步打成 waiting。

 生产心跳曾发 stage=finalizing + 95%|评分条款归并; 评分带为 90-96。
 """
    steps = _build_requirement_extract_steps(
        stage="finalizing",
        progress=95,
        message="评分条款归并",
        status="running",
    )

    assert [s["status"] for s in steps] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "running",
    ]
    assert steps[3]["key"] == "scoring"
    assert steps[3]["progress"] == 100
    assert steps[4]["key"] == "finalize"
    assert steps[4]["detail"] == "评分条款归并"


def test_scoring_with_lagging_progress_still_completes_consolidate_step() -> None:
    """同形态第二例: stage 已到评分项提取, overall 仍低于评分带起点。"""
    steps = _build_requirement_extract_steps(
        stage="scoring_extract",
        progress=88,
        message="评分项提取中",
        status="running",
    )

    assert [s["status"] for s in steps] == [
        "completed",
        "completed",
        "completed",
        "running",
        "waiting",
    ]
    assert steps[2]["key"] == "consolidate"
    assert steps[2]["progress"] == 100
    assert steps[3]["key"] == "scoring"
    assert steps[3]["detail"] == "评分项提取中"


def test_scoring_progress_overshoot_does_not_complete_finalize_step() -> None:
    """已知当前步时, overall 触达 100 也不得把尚未到达的后一步标完成。"""
    steps = _build_requirement_extract_steps(
        stage="scoring_extract",
        progress=100,
        message="评分项提取中",
        status="running",
    )

    assert [s["status"] for s in steps] == [
        "completed",
        "completed",
        "completed",
        "running",
        "waiting",
    ]
    assert steps[4]["key"] == "finalize"
    assert steps[4]["progress"] == 0


def test_reconcile_heartbeat_progress_uses_finalize_band_start() -> None:
    """归并核验入口心跳的百分数必须从契约 band 起点派生, 禁止沿用旧 95。"""
    source = inspect.getsource(extract_task)
    assert 'progress="95%|评分条款归并"' not in source
    assert "EXTRACT_STAGE_WEIGHTS[_STAGE_FINALIZING]" in source
    assert "%|评分条款归并" in source


def test_child_completed_heartbeat_keeps_later_steps_waiting() -> None:
    """归一后的子完成心跳不得让核验/评分步显示成已完成(端到端串联两处逻辑)。"""
    stage, progress = normalize_extract_heartbeat_stage("completed", 100)
    steps = _build_requirement_extract_steps(
        stage=stage, progress=progress, message="提取完成", status="running",
    )

    assert steps[1]["status"] == "completed", "高风险核验在合并前已完成"
    assert steps[2]["status"] == "running", "合并与保存步应仍在进行"
    assert steps[3]["status"] == "waiting", "评分项提取尚未开始"
    assert steps[4]["status"] == "waiting"


def test_fold_extract_stage_progress_matches_contract_bands() -> None:
    """心跳 percent 必须由契约 band 折算，禁止各调用点各自写公式。"""
    assert fold_extract_stage_progress("merging", 100, 100) == 76
    assert fold_extract_stage_progress("saving", 0, 100) == 76
    assert fold_extract_stage_progress("saving", 10, 100) == 77
    assert fold_extract_stage_progress("saving", 85, 100) == 87
    assert fold_extract_stage_progress("scoring_extract", 0, 100) == 90


def test_consolidate_saving_work_sequence_is_monotonic() -> None:
    """合并完成到评审意义再到落库，整体 percent 只升不降。"""
    sequence = [
        ("merging", 100, 100),
        ("saving", 0, 100),
        ("saving", 10, 100),
        ("saving", 80, 100),
        ("saving", 85, 100),
    ]
    values = [fold_extract_stage_progress(stage, current, total) for stage, current, total in sequence]
    assert values == sorted(values)
    assert values[0] == values[1]


def test_observational_placeholder_before_real_work_regresses() -> None:
    """同形态第二例：观测占位把同一步 current 抬高后，真实工作从更低 current 起步。

    merging/saving 同属 consolidate，心跳按 overall 防回退，此序列会丢掉新文案。
    """
    bump = fold_extract_stage_progress("saving", 20, 100)
    real = fold_extract_stage_progress("saving", 10, 100)
    assert real < bump


def test_extract_from_document_does_not_emit_observational_saving_message() -> None:
    """观测元数据不得写成用户可见 saving 文案，也不得把 current 抬到评审意义之上。"""
    from app.core.extractor.tender_extractor import TenderExtractor

    source = inspect.getsource(TenderExtractor.extract_from_document)
    assert "输出精度闸门元信息" not in source
    assert re.search(r'_report_progress\(\s*"saving",\s*20\s*,', source) is None
    assert 'self._report_progress("saving", 10, 100, "评审意义判定")' in source


def test_extract_task_folds_stage_progress_via_contract_helper() -> None:
    """extract_task 心跳折算必须走 fold_extract_stage_progress，避免与契约 band 再分叉。"""
    source = inspect.getsource(extract_task)
    assert "fold_extract_stage_progress(" in source
    assert "EXTRACT_STAGE_WEIGHTS.get(stage, (0, 100))" not in source
