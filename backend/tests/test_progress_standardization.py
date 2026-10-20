"""进度标准化相关的纯函数测试。"""

import pytest

from app.api.routers.collusion import (
    _fractional_progress_from_heartbeat,
    _group_fractional_progress_from_heartbeat,
)
from app.services.requirement_service import _build_requirement_extract_steps
from app.services.writing_service import (
    _build_upload_doc_steps,
    _build_upload_extract_steps,
    _build_writing_steps,
)

pytestmark = pytest.mark.scoring


def test_collusion_pair_fractional_progress_uses_heartbeat_stage_pct() -> None:
    heartbeat = {"stage_progress_pct": 55, "message": "多维度并行分析中"}

    progress = _fractional_progress_from_heartbeat(
        completed_count=2,
        total_count=5,
        heartbeat=heartbeat,
    )

    assert progress == 51


def test_collusion_group_fractional_progress_uses_current_group() -> None:
    heartbeat = {
        "current": 1,
        "total": 4,
        "stage_progress_pct": 60,
        "message": "正在研判第 2/4 个群体",
    }

    progress = _group_fractional_progress_from_heartbeat(
        heartbeat=heartbeat,
        fallback_progress=25,
    )

    assert progress == 40


def test_requirement_extract_steps_support_standardized_running_step() -> None:
    # 步骤契约为五步(要求提取/高风险条款核验/合并与保存/评分项提取/归并核验),
    # 区间见 app.core.tasks.extract_progress.EXTRACT_PROGRESS_STEPS。
    steps = _build_requirement_extract_steps(
        stage="dedup",
        progress=69,
        message="合并去重中",
        status="running",
    )

    assert [step["status"] for step in steps] == [
        "completed",
        "completed",
        "running",
        "waiting",
        "waiting",
    ]
    # 第三步区间 [55, 90): (69-55)/(90-55) = 40%
    assert steps[2]["progress"] == 40
    assert steps[2]["detail"] == "合并去重中"


def test_writing_steps_build_extract_phase_details() -> None:
    steps = _build_writing_steps(
        phase="extract",
        current_stage="render_profile",
        overall_progress=91,
        current_detail="解析渲染配置",
        overall_status="running",
    )

    assert [step["status"] for step in steps] == [
        "completed",
        "completed",
        "running",
        "waiting",
        "waiting",
    ]
    # 格式配置解析区间 [90, 93): (91-90)/(93-90) ≈ 33%
    assert steps[2]["progress"] == 33
    assert steps[2]["detail"] == "解析渲染配置"


def test_writing_steps_keep_current_stage_running_at_upper_bound() -> None:
    steps = _build_writing_steps(
        phase="extract",
        current_stage="industry_profile",
        overall_progress=94,
        current_detail="等待行业画像模型返回",
        overall_status="running",
    )

    assert [step["status"] for step in steps] == [
        "completed",
        "completed",
        "completed",
        "running",
        "waiting",
    ]
    assert steps[3]["detail"] == "等待行业画像模型返回"


def test_writing_steps_expose_scoring_authority_as_own_step() -> None:
    steps = _build_writing_steps(
        phase="extract",
        current_stage="scoring_authority",
        overall_progress=85,
        current_detail="提取评分项结构",
        overall_status="running",
    )

    assert [step["label"] for step in steps] == [
        "约束提取",
        "评分项提取",
        "格式配置解析",
        "行业画像生成",
        "结构分析",
    ]
    assert [step["status"] for step in steps] == [
        "completed",
        "running",
        "waiting",
        "waiting",
        "waiting",
    ]
    assert steps[1]["detail"] == "提取评分项结构"
    assert steps[1]["progress"] == 50


def test_upload_doc_steps_keep_single_contract_before_extract() -> None:
    steps, overall_progress, current_stage, current_detail, message = _build_upload_doc_steps(
        doc_stage="vectorizing",
        progress_raw="3/10",
        vl_skipped=False,
        overall_status="running",
        current_detail="分块索引中",
    )

    assert [step["label"] for step in steps] == [
        "文档解析",
        "图片识别",
        "分块索引",
        "约束提取",
        "评分项提取",
        "分析画像",
    ]
    assert [step["status"] for step in steps] == [
        "completed",
        "completed",
        "running",
        "waiting",
        "waiting",
        "waiting",
    ]
    assert overall_progress == 46
    assert current_stage == "vectorizing"
    assert current_detail == "分块索引中"
    assert message == "分块索引中"


def test_upload_doc_steps_omit_vl_when_skipped() -> None:
    """: skip-vl 时文档处理进度不得展示「图片识别」。"""
    steps, overall_progress, current_stage, _, message = _build_upload_doc_steps(
        doc_stage="parsing",
        progress_raw="2/10",
        vl_skipped=True,
        overall_status="running",
        current_detail="文档解析中",
    )

    assert [step["label"] for step in steps] == [
        "文档解析",
        "分块索引",
        "约束提取",
        "评分项提取",
        "分析画像",
    ]
    assert steps[0]["status"] == "running"
    assert steps[1]["status"] == "waiting"
    assert "图片识别" not in [step["label"] for step in steps]
    assert current_stage == "parsing"
    assert message == "文档解析中"
    assert 0 < overall_progress < 25


def test_upload_doc_steps_completed_omit_vl_when_skipped() -> None:
    steps, overall_progress, _, _, _ = _build_upload_doc_steps(
        doc_stage="completed",
        progress_raw="",
        vl_skipped=True,
        overall_status="completed",
        current_detail="文档处理完成，准备提取约束",
    )

    assert [step["label"] for step in steps] == [
        "文档解析",
        "分块索引",
        "约束提取",
        "评分项提取",
        "分析画像",
    ]
    assert overall_progress == 50
    assert all(step["status"] == "completed" for step in steps[:2])
    assert all(step["status"] == "waiting" for step in steps[2:])


def test_upload_extract_steps_omit_vl_when_skipped() -> None:
    """: 提取阶段复用同一 skip-vl 真值，不得回退展示图片识别。"""
    steps, overall_progress, message = _build_upload_extract_steps(
        current_stage="extracting",
        extract_overall_progress=30,
        current_detail="约束提取中",
        overall_status="running",
        vl_skipped=True,
    )

    assert [step["label"] for step in steps] == [
        "文档解析",
        "分块索引",
        "约束提取",
        "评分项提取",
        "分析画像",
    ]
    assert steps[2]["status"] == "running"
    assert overall_progress == 65
    assert message == "约束提取中"


def test_resolve_writing_upload_vl_skipped_prefers_explicit_true() -> None:
    from app.services.writing.progress_steps import resolve_writing_upload_vl_skipped

    assert resolve_writing_upload_vl_skipped(heartbeat_vl_skipped=True) is True
    assert resolve_writing_upload_vl_skipped(document_vl_skipped=True) is True
    assert resolve_writing_upload_vl_skipped(heartbeat_vl_skipped=False, document_vl_skipped=True) is True
    assert resolve_writing_upload_vl_skipped(heartbeat_vl_skipped=None, document_vl_skipped=None) is False
    assert resolve_writing_upload_vl_skipped(document_vl_skipped=False) is False


def test_resolve_writing_upload_vl_skipped_accepts_redis_true_string() -> None:
    """: Redis str(True)=='True' 不得被 is True 判成未跳过。"""
    from app.services.writing.progress_steps import resolve_writing_upload_vl_skipped

    assert resolve_writing_upload_vl_skipped(heartbeat_vl_skipped="True") is True
    assert resolve_writing_upload_vl_skipped(heartbeat_vl_skipped="true") is True
    assert resolve_writing_upload_vl_skipped(heartbeat_vl_skipped="False") is False


def test_upload_doc_steps_queued_omit_vl_when_skipped() -> None:
    """: 入队 queued 阶段若已声明跳过，不得插入图片识别。"""
    steps, _, current_stage, _, _ = _build_upload_doc_steps(
        doc_stage="queued",
        progress_raw="",
        vl_skipped=True,
        overall_status="running",
        current_detail="排队中",
    )

    assert [step["label"] for step in steps] == [
        "文档解析",
        "分块索引",
        "约束提取",
        "评分项提取",
        "分析画像",
    ]
    assert current_stage == "queued"
    assert "图片识别" not in [step["label"] for step in steps]


def test_upload_extract_steps_keep_single_contract_after_doc_phase() -> None:
    steps, overall_progress, message = _build_upload_extract_steps(
        current_stage="industry_profile",
        extract_overall_progress=94,
        current_detail="等待行业画像模型返回",
        overall_status="running",
    )

    assert [step["label"] for step in steps] == [
        "文档解析",
        "图片识别",
        "分块索引",
        "约束提取",
        "评分项提取",
        "分析画像",
    ]
    assert [step["status"] for step in steps] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "running",
    ]
    assert overall_progress == 97
    assert steps[5]["detail"] == "等待行业画像模型返回"
    assert message == "等待行业画像模型返回"


def test_upload_extract_steps_show_scoring_authority_between_extract_and_analysis() -> None:
    steps, overall_progress, message = _build_upload_extract_steps(
        current_stage="scoring_authority",
        extract_overall_progress=85,
        current_detail="提取评分项结构",
        overall_status="running",
    )

    assert [step["status"] for step in steps] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "running",
        "waiting",
    ]
    assert steps[4]["label"] == "评分项提取"
    assert steps[4]["detail"] == "提取评分项结构"
    assert steps[4]["progress"] == 50
    assert overall_progress == 92
    assert message == "提取评分项结构"


def test_writing_steps_mark_phase_complete() -> None:
    steps = _build_writing_steps(
        phase="match",
        current_stage="completed",
        overall_progress=100,
        current_detail="匹配完成",
        overall_status="completed",
    )

    assert all(step["status"] == "completed" for step in steps)
    assert all(step["progress"] == 100 for step in steps)
