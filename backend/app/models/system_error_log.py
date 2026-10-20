"""系统错误日志模型

捕获 WARNING 及以上级别的日志,供管理员在 Web 界面查看和定位系统错误。
"""

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.sql import func

from app.models.base import BaseModel


class SystemErrorLog(BaseModel):
    """系统错误日志

    字段说明:
      - level: WARNING / ERROR / CRITICAL
      - source: backend / worker
      - category: bid_review / collusion_check / bid_writing / system (业务分类)
      - task_type: process / extract / review / collusion / writing_*
      - resolved: 管理员标记"已处理"后为 True
    """

    __tablename__ = "system_error_logs"

    level = Column(String(20), nullable=False, index=True)
    message = Column(Text, nullable=False)
    traceback = Column(Text, nullable=True)
    module = Column(String(255), nullable=True, index=True)
    function_name = Column(String(255), nullable=True)
    line_number = Column(Integer, nullable=True)

    request_id = Column(String(64), nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    # 项目元信息快照: 项目删除前回填, 保证已删项目日志仍可按名显示/搜索
    project_name = Column(String(255), nullable=True, index=True)
    owner_username = Column(String(50), nullable=True, index=True)
    category = Column(String(30), nullable=False, index=True, server_default="system")
    task_type = Column(String(50), nullable=True, index=True)
    stage = Column(String(100), nullable=True)

    source = Column(String(20), nullable=False, default="backend")

    # traffic_class: 流量隔离维度, 取值见 app/core/observability/traffic_class.py。
    # 建索引是因为它是列表/统计的默认过滤条件(默认只看 production)。
    # 加列前的历史数据一律为 unknown, 其真实来源不可回溯, 禁止回填成 production。
    traffic_class = Column(
        String(16), nullable=False, index=True,
        default="production", server_default="unknown",
    )
    # occurrence_count: 同指纹回声折叠后的累计发生次数, 首条入库即为 1。
    # 保留计数而非丢弃重复, 否则"这个问题发生了多少次"会永久丢失。
    occurrence_count = Column(
        Integer, nullable=False, default=1, server_default="1",
    )

    message_fingerprint = Column(String(32), nullable=True, index=True)

    # 错误链路与根因定位（阶段一）
    # incident_id: 同一次失败链路的关联键，由 RequestIdMiddleware 或 task 入口生成
    incident_id = Column(String(32), nullable=True, index=True)
    # chain_seq: 同 incident_id 内的发生顺序（0-based 递增），用于按时序还原链路
    chain_seq = Column(Integer, nullable=True)
    # is_root_cause: 该链路第一条入库的日志记为根因
    is_root_cause = Column(Boolean, nullable=False, default=False, index=True)
    # exception_type: traceback 中的异常类型（如 ValueError/TimeoutError），便于按类型聚合
    exception_type = Column(String(128), nullable=True, index=True)
    # origin_*: traceback 末端帧坐标，记录"真正崩溃的代码位置"
    # 区别于 module/function_name/line_number（后者是 logger.error 调用点）
    origin_module = Column(String(255), nullable=True)
    origin_function = Column(String(255), nullable=True)
    origin_line = Column(Integer, nullable=True)

    resolved = Column(Boolean, nullable=False, default=False, index=True)
    resolved_by = Column(String(100), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
