"""metric_snapshot 模型: 指标时序温层 (90 天 / 1 小时分桶).

从 Redis ZSet 热层 flush 滚动聚合写入. 供趋势判断算法 (Phase 4) 读取.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, Integer, String

from app.models.base import BaseModel


class MetricSnapshot(BaseModel):
    """指标时序快照 (温层)."""

    __tablename__ = "metric_snapshots"

    name = Column(String(128), nullable=False, index=True, comment="指标名")
    labels_hash = Column(String(32), nullable=False, index=True, comment="label 组合哈希")
    bucket_ts = Column(DateTime(timezone=True), nullable=False, index=True, comment="分桶时间")
    bucket_seconds = Column(Integer, nullable=False, default=300, comment="桶宽 (秒)")
    count = Column(Integer, nullable=False, default=0)
    sum = Column(Float, nullable=False, default=0.0)
    avg = Column(Float, nullable=False, default=0.0)
    p50 = Column(Float, nullable=True)
    p95 = Column(Float, nullable=True)
