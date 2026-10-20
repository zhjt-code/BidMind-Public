"""注册风控服务

职责: 记录注册时的设备指纹和 IP
不做: 鉴权、邮箱验证、试用发放
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.registration_signal import RegistrationSignal

class AbuseGuardService:
    """设备指纹/IP 风控"""

    @staticmethod
    async def record_registration_signal(
        db: AsyncSession,
        user_id: int,
        fingerprint: str | None,
        ip: str | None,
    ) -> None:
        """记录一条注册风控信号(指纹/IP)"""
        db.add(RegistrationSignal(
            user_id=user_id,
            fingerprint_hash=(fingerprint or None),
            ip_address=(ip or None),
        ))
        await db.flush()
