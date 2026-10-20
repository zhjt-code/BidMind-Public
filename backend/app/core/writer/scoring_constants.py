"""写标评分项共享常量。

职责: 定义写标评分权威链路跨模块复用的稳定字面量。
不做: 不承载评分解析、来源统计或目录分卷逻辑。
"""

from __future__ import annotations

SCORING_AUTHORITY_SOURCE = "scoring_table"

__all__ = ["SCORING_AUTHORITY_SOURCE"]
