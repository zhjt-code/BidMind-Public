"""
职责: 幂等记录当前用户对内置欢迎信的确认时间。
不做: 不负责认证、用户资料编辑、前端展示或通用公告管理。
"""

from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserWelcomeService:
    """
    职责: 管理单个用户的欢迎信确认状态。
    不做: 不签发令牌、不读取 HTTP 请求、不管理其他通知。
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def acknowledge_welcome_letter(self, user: User) -> User:
        """首次确认时原子写入 UTC 时间；并发/重复请求返回数据库中的首次时间。

        使用 UPDATE ... WHERE welcome_letter_acknowledged_at IS NULL 消除并发竞争：
        - rowcount == 1：本请求成功写入，提交事务，调用 refresh 从 DB 回填属性。
        - rowcount == 0：并发请求已先写入，不提交，调用 refresh 从 DB 读取既有首次时间。

        两个分支都通过 refresh 保持 ORM 对象干净，不手动赋值属性。
        """
        if user.welcome_letter_acknowledged_at is not None:
            return user

        result = await self._db.execute(
            update(User)
            .where(User.id == user.id, User.welcome_letter_acknowledged_at.is_(None))
            .values(welcome_letter_acknowledged_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )

        if result.rowcount == 1:
            await self._db.commit()

        await self._db.refresh(user, attribute_names=["welcome_letter_acknowledged_at"])
        return user
