"""标书编写 ground truth schema。

字段对应 docs/regression_sample_spec.md §3.3。
"""

from pydantic import BaseModel, Field

from .common import FailureStateEnum, NodeTypeEnum


class WriterExpected(BaseModel):
    """单章节的标书编写 ground truth。"""

    outline_node_id: int = Field(..., description="大纲节点 ID")

    expected_node_type: NodeTypeEnum = Field(
        ..., description="理想章节分类"
    )
    expected_material_ids: list[int] = Field(
        default_factory=list, description="应引用的素材 ID 列表"
    )
    expected_placeholder_density: float = Field(
        default=0.0,
        ge=0.0,
        description="可接受的 [待补充] 密度（每千字）",
    )

    expected_failure_state: FailureStateEnum = Field(
        default=FailureStateEnum.COMPLETED,
        description="理想失败合同状态",
    )

    has_fabrication_risk: bool = Field(
        default=False, description="高幻觉风险章节"
    )
    expert_note: str = Field(default="", description="标注理由")
    bug_trace: str | None = Field(
        default=None, description="Level 3 来源 BUG 编号"
    )
