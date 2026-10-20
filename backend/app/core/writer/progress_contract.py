"""标书编写进度契约常量。

收口写作流程中任务层 heartbeat 百分比与服务层步骤区间，避免双处维护。
"""

from typing import Any


WRITING_EXTRACT_STEP_CONFIG: list[dict[str, Any]] = [
    {
        "label": "约束提取",
        "stages": {
            "starting",
            "extracting",
            "extracting_hard_constraint",
            "extracting_structure_constraint",
            "extracting_generation_guide",
            "dedup",
            "saving",
        },
        "range": (0, 80),
    },
    {
        "label": "评分项提取",
        "stages": {"scoring_authority"},
        "range": (80, 90),
    },
    {
        "label": "格式配置解析",
        "stages": {"render_profile"},
        "range": (90, 93),
    },
    {
        "label": "行业画像生成",
        "stages": {"industry_profile"},
        "range": (93, 96),
    },
    {
        "label": "结构分析",
        "stages": {"structure_analysis", "directory_extract"},
        "range": (96, 100),
    },
]


WRITING_EXTRACT_STAGE_WEIGHTS: dict[str, tuple[int, int]] = {
    "extracting_hard_constraint": (0, 20),
    "extracting_structure_constraint": (20, 40),
    "extracting_generation_guide": (40, 60),
    "dedup": (60, 75),
    "saving": (75, 80),
    "scoring_authority": (80, 90),
    "render_profile": (90, 93),
    "industry_profile": (93, 96),
    "structure_analysis": (96, 98),
    "directory_extract": (98, 99),
    "completed": (100, 100),
}


def get_stage_start_pct(stage: str, default: int = 0) -> int:
    """返回阶段区间起点百分比。"""

    return WRITING_EXTRACT_STAGE_WEIGHTS.get(stage, (default, default))[0]


def get_stage_weight_range(
    stage: str,
    default: tuple[int, int] = (0, 100),
) -> tuple[int, int]:
    """返回 heartbeat 阶段权重区间。"""

    return WRITING_EXTRACT_STAGE_WEIGHTS.get(stage, default)
