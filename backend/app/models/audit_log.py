"""审计日志模型

记录用户在系统中的关键操作，供管理员查看和合规审计。
"""

from sqlalchemy import Column, ForeignKey, Integer, String, Text

from app.models.base import BaseModel


class AuditLog(BaseModel):
    """审计日志

    字段说明:
      - username 冗余存储，确保用户删除后审计记录仍可读
      - category: bid_review / collusion_check / bid_writing / system (业务分类)
      - action: create / delete / update / confirm / start_review / download / login / register
      - resource_type: project / requirement / review / report / user / collusion_analysis / ...
    """

    __tablename__ = "audit_logs"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username = Column(String(50), nullable=True)
    category = Column(String(30), nullable=False, index=True, server_default="system")
    action = Column(String(50), nullable=False, index=True)
    resource_type = Column(String(50), nullable=False, index=True)
    resource_id = Column(Integer, nullable=True)
    detail = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
