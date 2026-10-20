"""标书编写进度组装纯函数

从 ``writing_service`` 上层逻辑中分离出来的进度 / 心跳 / 步骤构造函数：

- ``_parse_percent_detail`` / ``_parse_fraction_progress`` 解析心跳进度字符串
- ``_is_heartbeat_fresh`` 业务心跳新鲜度判定
- ``_step_status`` 单步状态枚举
- ``_build_upload_doc_steps`` / ``_build_upload_extract_steps`` 上传阶段统一进度组装
  （未跳过 VL 为 6 步；``vl_skipped=True`` 时省略「图片识别」，对齐前端 mapDocProcessToUploadSteps）
- ``_infer_writing_progress_phase`` 心跳与项目状态联合推断当前阶段
- ``_build_writing_steps`` 阶段步骤进度填充

这些函数都是纯函数（不持有 DB / 事务），可独立单测；
被 ``backend/tests/test_progress_standardization.py`` 当作契约锚点直接 import。

历史 import 路径 ``from app.services.writing_service import _build_upload_doc_steps``
通过 ``writing_service`` 顶部 re-export 兼容（Task 5 Step 1）。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.writer.progress_contract import WRITING_EXTRACT_STEP_CONFIG
from app.models.project import ProjectStatus


def _parse_percent_detail(progress_raw: str) -> tuple[int, str]:
    """解析 heartbeat 的 `xx%|message` 进度格式。"""

    if not progress_raw:
        return 0, ""

    progress_pct = 0
    detail = progress_raw

    if "|" in progress_raw:
        pct_part, detail_part = progress_raw.split("|", 1)
        detail = detail_part
    else:
        pct_part = progress_raw

    pct_part = pct_part.strip()
    if pct_part.endswith("%"):
        pct_part = pct_part[:-1]
    try:
        progress_pct = int(pct_part)
    except ValueError:
        progress_pct = 0

    return max(0, min(progress_pct, 100)), detail.strip()


def _is_heartbeat_fresh(heartbeat: dict[str, Any] | None, timeout_sec: int) -> bool:
    """判定 Redis/内存业务心跳是否仍新鲜。"""
    if not heartbeat:
        return False
    raw_ts = heartbeat.get("last_heartbeat")
    try:
        last_ts = float(raw_ts) if raw_ts is not None else 0.0
    except (TypeError, ValueError):
        return False
    return last_ts > 0 and (time.time() - last_ts) <= timeout_sec


def _step_status(
    *,
    is_done: bool,
    is_current: bool,
    is_failed: bool,
) -> str:
    if is_failed:
        return "failed"
    if is_done:
        return "completed"
    if is_current:
        return "running"
    return "waiting"


_WRITING_PROGRESS_CONFIG: dict[str, list[dict[str, Any]]] = {
    "extract": WRITING_EXTRACT_STEP_CONFIG,
    "outline": [
        {
            "label": "环境准备",
            "stages": {"starting", "cleaning"},
            "range": (0, 10),
        },
        {
            "label": "目录生成",
            "stages": {"generating"},
            "range": (10, 90),
        },
        {
            "label": "章节画像",
            "stages": {"profiling"},
            "range": (90, 100),
        },
    ],
    "match": [
        {
            "label": "目录向量化",
            "stages": {"starting", "matching", "vectorizing"},
            "range": (0, 12),
        },
        {
            "label": "约束分类",
            "stages": {"classifying"},
            "range": (12, 25),
        },
        {
            "label": "章节裁决",
            "stages": {"retrieving"},
            "range": (25, 60),
        },
        {
            "label": "素材关联",
            "stages": {"materials"},
            "range": (60, 80),
        },
        {
            "label": "一致性校验",
            "stages": {"validating", "saving"},
            "range": (80, 100),
        },
    ],
    "compose": [
        {
            "label": "内容生成",
            "stages": {"starting", "composing"},
            "range": (0, 100),
        },
    ],
}

_WRITING_UPLOAD_DOC_LABELS = {
    "parsing": "文档解析",
    "vl_recognition": "图片识别",
    "chunking": "分块索引",
    "vectorizing": "分块索引",
    "completed": "文档处理完成",
}


def resolve_writing_upload_vl_skipped(
    *,
    heartbeat_vl_skipped: Any = None,
    document_vl_skipped: Any = None,
) -> bool:
    """解析写标上传进度是否应省略「图片识别」步。

    职责: 统一心跳瞬时标记与文档持久化快照的 skip-vl 真值。
    不做: 不访问 DB/Redis；不改写心跳。

    优先心跳（处理中实时），否则回落 ``documents.vl_skipped``；
    显式真值（bool True / JSON true / 历史 Redis ``"True"``）视为跳过；
    null/未知保持展示 VL 步，避免误藏。
    """
    from app.core.tasks.heartbeat_codec import is_explicit_true

    return is_explicit_true(heartbeat_vl_skipped) or is_explicit_true(document_vl_skipped)



def _parse_fraction_progress(progress_raw: str) -> tuple[int, int]:
    """解析 `current/total` 形态的进度字符串。"""

    if "/" not in progress_raw:
        return 0, 0
    try:
        current_str, total_str = progress_raw.split("/", 1)
        return int(current_str), int(total_str)
    except (TypeError, ValueError):
        return 0, 0


def _build_upload_doc_steps(
    *,
    doc_stage: str,
    progress_raw: str,
    vl_skipped: bool,
    overall_status: str,
    current_detail: str,
) -> tuple[list[dict[str, Any]], int, str, str]:
    """构建上传页文档处理阶段统一进度。

    职责: 按文档处理阶段组装上传页前半程 steps[]。
    不做: 不读写心跳/DB；不推断提取阶段。

    ``vl_skipped=True`` 时省略「图片识别」步，区间与前端
    ``mapDocProcessToUploadSteps(..., vlSkipped=True)`` 对齐。
    """

    current, total = _parse_fraction_progress(progress_raw)
    stage_ratio = current / total if total > 0 else 0
    stage_ratio = max(0.0, min(stage_ratio, 1.0))

    doc_ranges = (
        {
            "parsing": (0, 25),
            "chunking": (25, 40),
            "vectorizing": (40, 50),
        }
        if vl_skipped
        else {
            "parsing": (0, 15),
            "vl_recognition": (15, 40),
            "chunking": (40, 45),
            "vectorizing": (45, 50),
        }
    )
    start, end = doc_ranges.get(doc_stage, (0, 25 if vl_skipped else 15))
    upload_pct = 50 if doc_stage == "completed" else start + (end - start) * stage_ratio

    parsing_denom = 25 if vl_skipped else 15
    chunk_start = 25 if vl_skipped else 40
    chunk_span = 15 if vl_skipped else 10
    parsing_target = int(max(0.0, min(upload_pct / parsing_denom, 0.95)) * 100)
    vl_target = int(max(0.0, min((upload_pct - 15) / 25, 0.95)) * 100)
    chunk_target = int(max(0.0, min((upload_pct - chunk_start) / chunk_span, 0.95)) * 100)

    def _step(label: str, status: str, progress: int = 0, detail: str | None = None) -> dict[str, Any]:
        return {
            "label": label,
            "status": status,
            "progress": progress,
            "detail": detail,
        }

    pending_tail = [
        _step("约束提取", "waiting", 0),
        _step("评分项提取", "waiting", 0),
        _step("分析画像", "waiting", 0),
    ]

    def _doc_head(
        parsing: tuple[str, int, str | None],
        chunk: tuple[str, int, str | None],
        vl: tuple[str, int, str | None] | None = None,
    ) -> list[dict[str, Any]]:
        head = [_step("文档解析", parsing[0], parsing[1], parsing[2])]
        if not vl_skipped:
            assert vl is not None
            head.append(_step("图片识别", vl[0], vl[1], vl[2]))
        head.append(_step("分块索引", chunk[0], chunk[1], chunk[2]))
        return head

    if overall_status == "completed" or doc_stage == "completed":
        steps = [
            *_doc_head(
                ("completed", 100, None),
                ("completed", 100, None),
                None if vl_skipped else ("completed", 100, None),
            ),
            *pending_tail,
        ]
        return steps, 50, "doc_completed", current_detail or "文档处理完成，准备提取约束", "文档处理完成，准备提取约束"

    if overall_status == "failed":
        failed_label = _WRITING_UPLOAD_DOC_LABELS.get(doc_stage, "文档处理")
        parsing_failed = doc_stage == "parsing"
        vl_failed = doc_stage == "vl_recognition"
        chunk_failed = doc_stage in {"chunking", "vectorizing"}
        steps = [
            *_doc_head(
                (
                    "failed" if parsing_failed else "completed",
                    0 if parsing_failed else 100,
                    current_detail if parsing_failed else None,
                ),
                (
                    "failed" if chunk_failed else "waiting",
                    0,
                    current_detail if chunk_failed else None,
                ),
                None
                if vl_skipped
                else (
                    "failed" if vl_failed else ("completed" if chunk_failed else "waiting"),
                    100 if chunk_failed else 0,
                    current_detail if vl_failed else None,
                ),
            ),
            *pending_tail,
        ]
        return steps, int(upload_pct), doc_stage, current_detail or failed_label, current_detail or failed_label

    if vl_skipped:
        if doc_stage in {"chunking", "vectorizing"}:
            steps = [
                *_doc_head(
                    ("completed", 100, None),
                    ("running", chunk_target, current_detail or "分块索引中"),
                ),
                *pending_tail,
            ]
        else:
            # skip-vl 时不应进入 vl_recognition；若心跳异常仍落到该 stage，按解析中展示
            steps = [
                *_doc_head(
                    ("running", parsing_target, current_detail or "文档解析中"),
                    ("waiting", 0, None),
                ),
                *pending_tail,
            ]
    elif doc_stage == "vl_recognition":
        steps = [
            *_doc_head(
                ("completed", 100, None),
                ("waiting", 0, None),
                ("running", vl_target, current_detail or "图片识别中"),
            ),
            *pending_tail,
        ]
    elif doc_stage in {"chunking", "vectorizing"}:
        steps = [
            *_doc_head(
                ("completed", 100, None),
                ("running", chunk_target, current_detail or "分块索引中"),
                ("completed", 100, None),
            ),
            *pending_tail,
        ]
    else:
        steps = [
            *_doc_head(
                ("running", parsing_target, current_detail or "文档解析中"),
                ("waiting", 0, None),
                ("waiting", 0, None),
            ),
            *pending_tail,
        ]
    detail = current_detail or _WRITING_UPLOAD_DOC_LABELS.get(doc_stage, "处理中")
    return steps, int(upload_pct), doc_stage, detail, detail


def _build_upload_extract_steps(
    *,
    current_stage: str,
    extract_overall_progress: int,
    current_detail: str,
    overall_status: str,
    vl_skipped: bool = False,
) -> tuple[list[dict[str, Any]], int, str]:
    """构建上传页提取阶段统一进度（含独立评分项提取）。

 职责: 以前半文档步已完成为前提，组装约束/评分/画像进度。
 不做: 不驱动文档处理阶段；不读写心跳/DB。

 ``vl_skipped=True`` 时省略已完成的「图片识别」步。
 """

    clamped_pct = max(0, min(100, extract_overall_progress))
    upload_pct = 50 + clamped_pct * 0.5
    extract_target = int(max(0.0, min(clamped_pct / 80, 0.95)) * 100)
    scoring_target = int(max(0.0, min((clamped_pct - 80) / 10, 0.95)) * 100)
    analysis_target = int(max(0.0, min((clamped_pct - 90) / 10, 0.95)) * 100)
    scoring_stages = {"scoring_authority"}
    analysis_stages = {
        "render_profile",
        "industry_profile",
        "structure_analysis",
        "directory_extract",
    }

    def _step(label: str, status: str, progress: int = 0, detail: str | None = None) -> dict[str, Any]:
        return {
            "label": label,
            "status": status,
            "progress": progress,
            "detail": detail,
        }

    completed_doc_steps = [
        _step("文档解析", "completed", 100),
        *([] if vl_skipped else [_step("图片识别", "completed", 100)]),
        _step("分块索引", "completed", 100),
    ]

    if overall_status == "completed" or current_stage == "completed":
        steps = [
            *completed_doc_steps,
            _step("约束提取", "completed", 100),
            _step("评分项提取", "completed", 100),
            _step("分析画像", "completed", 100),
        ]
        return steps, 100, "处理完成"

    if overall_status == "failed":
        failed_on_scoring = current_stage in scoring_stages
        failed_on_analysis = current_stage in analysis_stages
        extract_done = failed_on_scoring or failed_on_analysis
        scoring_done = failed_on_analysis
        steps = [
            *completed_doc_steps,
            _step(
                "约束提取",
                "completed" if extract_done else "failed",
                100 if extract_done else 0,
                None if extract_done else current_detail,
            ),
            _step(
                "评分项提取",
                "failed" if failed_on_scoring else ("completed" if scoring_done else "waiting"),
                100 if scoring_done else 0,
                current_detail if failed_on_scoring else None,
            ),
            _step(
                "分析画像",
                "failed" if failed_on_analysis else "waiting",
                0,
                current_detail if failed_on_analysis else None,
            ),
        ]
        return steps, int(max(50, min(upload_pct, 100))), current_detail or "处理失败"

    if current_stage in analysis_stages or upload_pct >= 95:
        steps = [
            *completed_doc_steps,
            _step("约束提取", "completed", 100),
            _step("评分项提取", "completed", 100),
            _step("分析画像", "running", analysis_target, current_detail or "分析画像中"),
        ]
    elif current_stage in scoring_stages or upload_pct >= 90:
        steps = [
            *completed_doc_steps,
            _step("约束提取", "completed", 100),
            _step("评分项提取", "running", scoring_target, current_detail or "评分项提取中"),
            _step("分析画像", "waiting", 0),
        ]
    else:
        steps = [
            *completed_doc_steps,
            _step("约束提取", "running", extract_target, current_detail or "约束提取中"),
            _step("评分项提取", "waiting", 0),
            _step("分析画像", "waiting", 0),
        ]

    return steps, int(max(50, min(upload_pct, 100))), current_detail


def _infer_writing_progress_phase(
    project_status: str,
    heartbeat_stage: str,
) -> str | None:
    if project_status in {
        ProjectStatus.EXTRACTING.value,
        ProjectStatus.EXTRACTED.value,
    } or heartbeat_stage in set().union(
        *[step["stages"] for step in _WRITING_PROGRESS_CONFIG["extract"]]
    ):
        return "extract"
    if project_status in {
        ProjectStatus.OUTLINING.value,
        ProjectStatus.OUTLINED.value,
    } or heartbeat_stage in set().union(
        *[step["stages"] for step in _WRITING_PROGRESS_CONFIG["outline"]]
    ):
        return "outline"
    if project_status in {
        ProjectStatus.MATCHING.value,
        ProjectStatus.MATCHED.value,
    } or heartbeat_stage in set().union(
        *[step["stages"] for step in _WRITING_PROGRESS_CONFIG["match"]]
    ):
        return "match"
    if project_status in {
        ProjectStatus.WRITING.value,
        ProjectStatus.COMPOSED.value,
        ProjectStatus.FORMATTING.value,
        ProjectStatus.COMPLETED.value,
    } or heartbeat_stage in set().union(
        *[step["stages"] for step in _WRITING_PROGRESS_CONFIG["compose"]]
    ):
        return "compose"
    return None


def _build_writing_steps(
    *,
    phase: str | None,
    current_stage: str,
    overall_progress: int,
    current_detail: str,
    overall_status: str,
) -> list[dict[str, Any]]:
    if not phase:
        return []

    steps = _WRITING_PROGRESS_CONFIG[phase]
    if overall_status == "completed":
        return [
            {
                "label": step["label"],
                "status": "completed",
                "progress": 100,
                "detail": None,
            }
            for step in steps
        ]

    result: list[dict[str, Any]] = []
    active_idx = -1
    for idx, step in enumerate(steps):
        if current_stage in step["stages"]:
            active_idx = idx
            break

    if active_idx == -1 and overall_progress > 0:
        for idx in range(len(steps) - 1, -1, -1):
            start, _end = steps[idx]["range"]
            if overall_progress >= start:
                active_idx = idx
                break

    for idx, step in enumerate(steps):
        start, end = step["range"]
        step_pct = 0
        if end > start:
            step_pct = int(max(0, min((overall_progress - start) / (end - start), 1)) * 100)
        elif overall_progress >= end:
            step_pct = 100

        is_current = idx == active_idx and overall_status == "running"
        if is_current:
            is_done = False
        elif active_idx != -1:
            is_done = idx < active_idx
        else:
            is_done = overall_progress >= end and overall_status != "failed"
        result.append(
            {
                "label": step["label"],
                "status": _step_status(
                    is_done=is_done,
                    is_current=is_current,
                    is_failed=overall_status == "failed" and idx == active_idx,
                ),
                "progress": 100 if is_done else (step_pct if is_current else 0),
                "detail": current_detail if is_current and current_detail else None,
            }
        )

    return result


__all__ = [
    "_parse_percent_detail",
    "_is_heartbeat_fresh",
    "_step_status",
    "_parse_fraction_progress",
    "_build_upload_doc_steps",
    "_build_upload_extract_steps",
    "resolve_writing_upload_vl_skipped",
    "_infer_writing_progress_phase",
    "_build_writing_steps",
    "_WRITING_PROGRESS_CONFIG",
    "_WRITING_UPLOAD_DOC_LABELS",
]
