"""metric_catalog 模型: 启动期扫描自动同步的指标目录.

对齐 PromptRegistry 的 DB 持久化方案. 前端从此表消费指标元数据, 动态渲染卡片.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from app.models.base import BaseModel


class MetricCatalog(BaseModel):
    """指标目录 (启动期扫描 upsert).

    字段:
      - name: 全局唯一名称
      - layer: 八层主分类
      - pillar: 四维次级标签
      - business: 所属业务 flow
      - direction: 值增大是好是坏
      - kind: Prometheus 指标类型 (counter/histogram/gauge)
      - description: 一句话描述
      - labels_json: 从函数签名推导的 label 名 JSON
      - thresholds_json: warn / critical 阈值 JSON
      - source_*: 源码位置 (供 Phase 5 一键跳源码)
    """

    __tablename__ = "metric_catalog"
    __table_args__ = (
        UniqueConstraint("name", name="uq_metric_catalog_name"),
    )

    name = Column(String(128), nullable=False, index=True, comment="指标唯一名称")
    layer = Column(String(32), nullable=False, index=True, comment="八层主分类")
    pillar = Column(String(32), nullable=False, index=True, comment="四维次级标签")
    business = Column(String(32), nullable=False, default="all", index=True, comment="业务 flow")
    direction = Column(String(32), nullable=False, default="neutral", comment="higher_better / lower_better / neutral")
    kind = Column(String(16), nullable=False, default="counter", comment="counter / histogram / gauge")
    description = Column(Text, nullable=True)
    labels_json = Column(Text, nullable=True, comment="label 名列表 JSON")
    thresholds_json = Column(Text, nullable=True, comment="warn/critical 阈值 JSON")
    source_module = Column(String(256), nullable=True)
    source_qualname = Column(String(256), nullable=True)
    source_file = Column(String(512), nullable=True)
    source_line = Column(Integer, nullable=True)
    unit = Column(String(32), nullable=True)
