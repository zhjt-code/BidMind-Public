"""注册风控信号模型"""

from sqlalchemy import Column, Integer, String, ForeignKey
from app.models.base import BaseModel


class RegistrationSignal(BaseModel):
    """注册风控信号

    职责: 记录注册时的设备指纹哈希与来源 IP, 供"设备指纹软上限"统计
    不做: 风险判定与试用发放决策(归 AbuseGuardService)
    """

    __tablename__ = "registration_signals"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="所属用户",
    )
    fingerprint_hash = Column(
        String(64), nullable=True, index=True, comment="设备指纹哈希",
    )
    ip_address = Column(
        String(64), nullable=True, comment="注册来源 IP",
    )
