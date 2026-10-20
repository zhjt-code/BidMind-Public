"""Prompt 版本管理服务

支持 Prompt 模板的版本化 CRUD 和加载（DB 优先，代码回退）。
"""

from typing import Any, Optional

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ServiceError
from app.models.prompt_version import PromptVersion


class PromptService:
    """Prompt 版本管理服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_active(self, purpose: str) -> Optional[PromptVersion]:
        """获取指定 purpose 的激活版本"""
        result = await self.db.execute(
            select(PromptVersion).where(
                PromptVersion.purpose == purpose,
                PromptVersion.is_active == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    async def get_active_content(self, purpose: str, fallback: str) -> str:
        """获取激活版本的内容，无记录时返回 fallback"""
        pv = await self.get_active(purpose)
        if pv:
            return pv.content
        return fallback

    async def list_versions(
        self,
        purpose: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """分页查询 Prompt 版本"""
        query = select(PromptVersion)
        count_query = select(func.count(PromptVersion.id))

        if purpose:
            query = query.where(PromptVersion.purpose == purpose)
            count_query = count_query.where(PromptVersion.purpose == purpose)

        total = (await self.db.execute(count_query)).scalar() or 0
        offset = (page - 1) * page_size
        query = query.order_by(PromptVersion.purpose, PromptVersion.version.desc())
        query = query.offset(offset).limit(page_size)

        result = await self.db.execute(query)
        items = result.scalars().all()

        return {
            "items": list(items),
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }

    async def create(
        self,
        purpose: str,
        content: str,
        name: Optional[str] = None,
        activate: bool = False,
    ) -> PromptVersion:
        """创建新版本"""
        max_ver_result = await self.db.execute(
            select(func.max(PromptVersion.version)).where(
                PromptVersion.purpose == purpose
            )
        )
        max_ver = max_ver_result.scalar() or 0

        pv = PromptVersion(
            purpose=purpose,
            version=max_ver + 1,
            name=name or f"{purpose} v{max_ver + 1}",
            content=content,
            is_active=False,
        )
        self.db.add(pv)
        await self.db.flush()

        if activate:
            await self._activate(pv)

        await self.db.refresh(pv)
        logger.info(f"创建 Prompt 版本: purpose={purpose}, version={pv.version}, active={activate}")
        return pv

    async def activate(self, version_id: int) -> PromptVersion:
        """激活指定版本（同 purpose 下其他版本自动取消激活）"""
        result = await self.db.execute(
            select(PromptVersion).where(PromptVersion.id == version_id)
        )
        pv = result.scalar_one_or_none()
        if not pv:
            raise NotFoundError(f"Prompt 版本不存在: id={version_id}")

        await self._activate(pv)
        await self.db.flush()
        await self.db.refresh(pv)
        logger.info(f"激活 Prompt 版本: purpose={pv.purpose}, version={pv.version}")
        return pv

    async def delete(self, version_id: int) -> None:
        """删除指定版本"""
        result = await self.db.execute(
            select(PromptVersion).where(PromptVersion.id == version_id)
        )
        pv = result.scalar_one_or_none()
        if not pv:
            raise NotFoundError(f"Prompt 版本不存在: id={version_id}")
        if pv.is_active:
            raise ServiceError("不能删除激活中的版本，请先激活其他版本")

        await self.db.delete(pv)
        await self.db.flush()
        logger.info(f"删除 Prompt 版本: id={version_id}, purpose={pv.purpose}, version={pv.version}")

    async def _activate(self, pv: PromptVersion) -> None:
        """内部方法：激活指定版本，取消同 purpose 下其他版本"""
        others = await self.db.execute(
            select(PromptVersion).where(
                PromptVersion.purpose == pv.purpose,
                PromptVersion.is_active == True,  # noqa: E712
                PromptVersion.id != pv.id,
            )
        )
        for other in others.scalars().all():
            other.is_active = False
        pv.is_active = True
