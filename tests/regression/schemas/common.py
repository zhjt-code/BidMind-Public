"""跨流程共享的 ground truth 类型定义。

FailureStateEnum 的字符串值必须与 docs/failure_contract.md §2.1 完全一致。
B1 落地 ORM 枚举时不得偏离此处定义。
"""

from enum import Enum


class FailureStateEnum(str, Enum):
    """统一失败合同状态（ground truth 标注用）。

    严格按从轻到重排序：
        completed < degraded < needs_review < inconclusive < failed
    """

    COMPLETED = "completed"
    DEGRADED = "degraded"
    NEEDS_REVIEW = "needs_review"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"


class ComplianceLevelEnum(str, Enum):
    FULLY = "fully"
    PARTIALLY = "partially"
    NOT = "not"
    MISSING = "missing"


class RiskLevelEnum(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"
    INCONCLUSIVE = "inconclusive"


class EvidenceLevelEnum(str, Enum):
    FIRST = "first"
    SECOND = "second"
    THIRD = "third"


class NodeTypeEnum(str, Enum):
    FIXED_TEMPLATE = "fixed_template"
    DATA_FILL = "data_fill"
    FREE_WRITING = "free_writing"
