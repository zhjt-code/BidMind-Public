"""写标生成阶段 token 预算估算 

职责: 按目录节点与目标字数估算写标生成阶段的剩余 token 预算
不做: 预占、结算、报价、读取钱包
"""

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


async def estimate_compose_token_budget(
    db: AsyncSession,
    project_id: int,
    *,
    node_id: int | None = None,
) -> tuple[int, int]:
    """估算全文/单章生成阶段的 (input_tokens, output_tokens) 预算。

    - 全文(node_id=None): 非 fixed_template 节点目标字数求和推算;
      无目录或字数为零时回退按文档页数推算。
    - 单章(node_id 非空): 仅按该节点目标字数推算; 无字数返回 (0, 0),
      由计费门的通用重估(已发生增量 + 页公式)兜底。

    估算仅供预占补差额判断: 偏高只会多冻结, 结算按实际扣、多余自动释放。
    系数默认值按本地 billing_usages 写标样本校准(input≈25/字, output≈8/字)。
    """
    from app.models.writing_outline_node import OutlineNodeType, WritingOutlineNode

    if node_id is not None:
        node = await db.get(WritingOutlineNode, node_id)
        word_count = int(getattr(node, "target_word_count", 0) or 0)
        if word_count <= 0:
            return 0, 0
        return (
            int(word_count * settings.WRITING_COMPOSE_EST_INPUT_PER_WORD),
            int(word_count * settings.WRITING_COMPOSE_EST_OUTPUT_PER_WORD),
        )

    word_sum = (await db.execute(
        select(func.coalesce(func.sum(WritingOutlineNode.target_word_count), 0))
        .where(
            WritingOutlineNode.project_id == project_id,
            WritingOutlineNode.node_type != OutlineNodeType.fixed_template,
        )
    )).scalar() or 0

    if word_sum > 0:
        return (
            int(word_sum * settings.WRITING_COMPOSE_EST_INPUT_PER_WORD),
            int(word_sum * settings.WRITING_COMPOSE_EST_OUTPUT_PER_WORD),
        )

    from app.models.document import Document

    page_count = (await db.execute(
        select(func.coalesce(func.sum(Document.page_count), 0))
        .where(Document.project_id == project_id)
    )).scalar() or 0
    return (
        int(page_count * settings.WRITING_COMPOSE_EST_INPUT_PER_PAGE),
        int(page_count * settings.WRITING_COMPOSE_EST_OUTPUT_PER_PAGE),
    )
