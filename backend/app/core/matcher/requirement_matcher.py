"""招标-投标匹配器

将招标要求与投标响应进行关联匹配，生成匹配对列表供AI审查使用。

匹配策略：
1. 直接关联：投标响应中的requirement_id指向招标要求
2. 未响应处理：标记没有对应响应的招标要求
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability.instrument import instrument as _observability_instrument
from app.core.review_contracts import reviewable_requirement_conditions
from app.models.bid_response import BidResponse
from app.models.requirement import Requirement


@dataclass
class MatchedPair:
    """招标要求-投标响应匹配对
    
    用于AI审查的输入数据结构。
    """
    requirement: Requirement
    response: Optional[BidResponse]
    has_response: bool = field(init=False)
    
    def __post_init__(self):
        """初始化后处理"""
        from app.core.extractor.evidence_verdict import is_unconfirmed_response_content

        content = getattr(self.response, "content", None) if self.response else None
        self.has_response = self.response is not None and not is_unconfirmed_response_content(
            content
        )
    
    @property
    def requirement_id(self) -> int:
        """招标要求ID"""
        return self.requirement.id
    
    @property
    def response_id(self) -> Optional[int]:
        """投标响应ID"""
        return self.response.id if self.response else None
    
    @property
    def category(self) -> str:
        """要求分类"""
        return self.requirement.category.value if self.requirement.category else "unknown"
    
    @property
    def is_mandatory(self) -> bool:
        """是否为否决项"""
        return bool(self.requirement.is_mandatory)


class RequirementMatcher:
    """招标-投标匹配器
    
    将招标要求与投标响应进行关联，生成匹配对列表。
    
    示例:
        >>> matcher = RequirementMatcher(db)
        >>> pairs = await matcher.match_project(project_id)
        >>> for pair in pairs:
        ...     print(f"要求{pair.requirement_id}: {'有响应' if pair.has_response else '无响应'}")
    """
    
    def __init__(self, db: AsyncSession):
        """初始化匹配器
        
        Args:
            db: 数据库会话
        """
        self.db = db
    
    @_observability_instrument(
        name="requirement_match_project_seconds",
        layer="stage",
        pillar="efficiency",
        direction="lower_better",
        business="tender",
        measure="latency",
        description="招标要求与投标响应匹配单次耗时",
    )
    async def match_project(
        self,
        project_id: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_check: Optional[Callable[[], None]] = None,
    ) -> list[MatchedPair]:
        """匹配项目的所有招标要求和投标响应
        
        Args:
            project_id: 项目ID
            
        Returns:
            list[MatchedPair]: 匹配对列表
            
        Raises:
            ValueError: 项目没有已确认的招标要求
        """
        from app.core.tasks.cancellation import invoke_cancel_check

        invoke_cancel_check(cancel_check)
        logger.info(f"开始匹配: project_id={project_id}")
        
        # 1. 获取已确认的招标要求
        requirements = await self._get_confirmed_requirements(project_id)
        invoke_cancel_check(cancel_check)
        if not requirements:
            raise ValueError(f"项目 {project_id} 没有已确认的招标要求")
        
        logger.info(f"招标要求数量: {len(requirements)}")
        if progress_callback:
            progress_callback(
                0,
                len(requirements),
                f"响应装配校验中 0/{len(requirements)}",
            )
        invoke_cancel_check(cancel_check)

        # 2. 获取所有投标响应
        responses = await self._get_bid_responses(project_id)
        invoke_cancel_check(cancel_check)
        logger.info(f"投标响应数量: {len(responses)}")
        
        # 3. 构建响应映射（requirement_id -> response）
        response_map = {r.requirement_id: r for r in responses}
        
        # 4. 生成匹配对
        pairs: list[MatchedPair] = []
        matched_count = 0
        unmatched_count = 0
        
        for req in requirements:
            invoke_cancel_check(cancel_check)
            response = response_map.get(req.id)
            pair = MatchedPair(requirement=req, response=response)
            pairs.append(pair)
            
            if pair.has_response:
                matched_count += 1
            else:
                unmatched_count += 1

            if progress_callback:
                progress_callback(
                    len(pairs),
                    len(requirements),
                    f"响应装配校验中 {len(pairs)}/{len(requirements)}",
                )
            invoke_cancel_check(cancel_check)
        
        logger.info(
            f"匹配完成: 总数={len(pairs)}, "
            f"有响应={matched_count}, 无响应={unmatched_count}"
        )
        
        return pairs
    
    async def _get_confirmed_requirements(
        self,
        project_id: int
    ) -> list[Requirement]:
        """获取项目的已确认且可评审的招标要求（统一闸门口径）"""
        result = await self.db.execute(
            select(Requirement).where(
                Requirement.project_id == project_id,
                *reviewable_requirement_conditions(),
            ).order_by(Requirement.id)
        )
        return list(result.scalars().all())
    
    async def _get_bid_responses(
        self,
        project_id: int
    ) -> list[BidResponse]:
        """获取项目的所有投标响应"""
        # 通过requirement关联获取
        result = await self.db.execute(
            select(BidResponse).join(
                Requirement,
                BidResponse.requirement_id == Requirement.id
            ).where(
                Requirement.project_id == project_id
            )
        )
        return list(result.scalars().all())
    
    def summarize_matches(self, pairs: list[MatchedPair]) -> dict:
        """汇总匹配结果统计
        
        Args:
            pairs: 匹配对列表
            
        Returns:
            dict: 统计信息
        """
        total = len(pairs)
        with_response = sum(1 for p in pairs if p.has_response)
        without_response = total - with_response
        
        # 按类别统计
        by_category: dict[str, dict] = {}
        for pair in pairs:
            cat = pair.category
            if cat not in by_category:
                by_category[cat] = {"total": 0, "matched": 0, "unmatched": 0}
            by_category[cat]["total"] += 1
            if pair.has_response:
                by_category[cat]["matched"] += 1
            else:
                by_category[cat]["unmatched"] += 1
        
        # 否决项统计
        mandatory_total = sum(1 for p in pairs if p.is_mandatory)
        mandatory_matched = sum(1 for p in pairs if p.is_mandatory and p.has_response)
        mandatory_unmatched = mandatory_total - mandatory_matched
        
        return {
            "total": total,
            "with_response": with_response,
            "without_response": without_response,
            "match_rate": with_response / total if total > 0 else 0,
            "by_category": by_category,
            "mandatory": {
                "total": mandatory_total,
                "matched": mandatory_matched,
                "unmatched": mandatory_unmatched
            }
        }


# 工厂函数
def get_requirement_matcher(db: AsyncSession) -> RequirementMatcher:
    """获取匹配器实例
    
    Args:
        db: 数据库会话
        
    Returns:
        RequirementMatcher: 匹配器实例
    """
    return RequirementMatcher(db)
