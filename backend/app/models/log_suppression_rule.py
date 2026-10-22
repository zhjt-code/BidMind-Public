"""日志抑制规则模型

管理员可从错误日志创建抑制规则，后续同类日志在 Sink 层拦截不入库。
规则匹配条件：module 精确 + message 前缀匹配。
"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text

from app.models.base import BaseModel


class LogSuppressionRule(BaseModel):
    """日志抑制规则

    字段说明:
      - module + message_prefix: 核心匹配条件
      - function_name / level / category: 可选的精细匹配
      - source_log_id: 触发创建的原始日志 ID（保留上下文）
      - suppressed_count: 累计被此规则拦截的日志数量
    """

    __tablename__ = "log_suppression_rules"

    module = Column(String(255), nullable=False, index=True)
    function_name = Column(String(255), nullable=True)
    message_prefix = Column(String(200), nullable=False)
    level = Column(String(20), nullable=True)
    category = Column(String(30), nullable=True)

    enabled = Column(Boolean, nullable=False, default=True, index=True)
    reason = Column(Text, nullable=True)
    created_by = Column(String(100), nullable=False)

    source_log_id = Column(Integer, nullable=True)
    suppressed_count = Column(BigInteger, nullable=False, default=0)
    last_suppressed_at = Column(DateTime(timezone=True), nullable=True)
