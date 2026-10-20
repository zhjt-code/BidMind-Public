"""metric_rule 模型: 趋势判断规则 (Phase 4).

规则驱动的 verdict 引擎, 避免把"好/坏"判定硬编码到代码中.
规则可运行期修改, 下一次 summary 即生效.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, Integer, String, Text

from app.models.base import BaseModel


class MetricRule(BaseModel):
    """指标趋势判断规则.

    字段:
      - metric_name: 关联的指标名 (对应 metric_catalog.name)
      - rule_type: trend / threshold / changepoint / baseline_diff / composite
      - algorithm: mk / ewma / cusum / ratio / abs
      - params_json: 算法参数 JSON (window/baseline/k/sigma 等)
      - verdict_map_json: 从 AlgoResult 映射到 verdict 的规则 JSON
      - enabled: 是否启用
      - updated_by: 修改人
    """

    __tablename__ = "metric_rule"

    metric_name = Column(String(128), nullable=False, index=True)
    rule_type = Column(String(32), nullable=False, default="trend")
    algorithm = Column(String(32), nullable=False, default="ratio")
    params_json = Column(Text, nullable=True, comment='{"window":"7d","baseline":"28d","k":3,"sigma":3}')
    verdict_map_json = Column(Text, nullable=True, comment='{"degrading":"warn","hard_drop":"critical"}')
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    updated_by = Column(String(64), nullable=True)
    description = Column(Text, nullable=True)
