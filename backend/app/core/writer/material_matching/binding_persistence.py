"""标书素材匹配 -- 绑定持久化策略。

职责: 清理可重算建议、保留人工决策并幂等写入新的绑定建议。
不做: 不计算语义相似度、不调用LLM、不处理HTTP请求。

设计要点:
- 审核对象是"绑定边"而非"素材实体"。默认重匹配只清理可重算的 ``suggested`` 边，
 人工的 ``confirmed`` / ``rejected`` 边必须原样保留；仅当调用方显式要求
 ``reset_reviewed_bindings`` 时才清空全部。
- 写入阶段以 (binding_type, material_id, clause_id, outline_node_id) 业务键去重，
 跳过与存活人工决策重复的候选，避免在无唯一约束的表上产生重复边、或让被拒绝的
 错误边随重匹配复活。
- 写入边界继承 守卫: 先查取消墓碑，再查 projects 父行，缺失即拒写。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.arq_runtime import ProjectCancelledError
from app.models.project import Project
from app.models.writing_binding import BindingStatus, BindingType, WritingBinding

# 业务键: 唯一标识一条绑定边的语义身份，用于跨重匹配去重。
BindingKey = tuple[str, int | None, int | None, int | None]


def _binding_type_value(binding_type: Any) -> str:
    """兼容枚举与字符串两种 binding_type 表示。"""
    return binding_type.value if hasattr(binding_type, "value") else str(binding_type)


def _binding_row_key(binding: WritingBinding) -> BindingKey:
    """从已入库绑定行提取业务键。"""
    return (
        _binding_type_value(binding.binding_type),
        binding.material_id,
        binding.clause_id,
        binding.outline_node_id,
    )


def _candidate_key(raw_binding: dict) -> BindingKey:
    """从待写入候选提取业务键。"""
    return (
        _binding_type_value(raw_binding["binding_type"]),
        raw_binding.get("material_id"),
        raw_binding.get("clause_id"),
        raw_binding.get("outline_node_id"),
    )


async def prepare_binding_rematch(
    db: AsyncSession,
    project_id: int,
    *,
    reset_reviewed_bindings: bool = False,
    explicit_retry: bool = False,
) -> int:
    """重匹配前按决策粒度清理旧绑定，返回删除行数。

    删除口径（三选一，互斥优先级 reset > explicit_retry > 默认）:
    - ``reset_reviewed_bindings``: 删除该项目全部绑定（用户显式"重置审核并重新匹配"）。
    - ``explicit_retry``: 删除 ``status != confirmed``（失败重试，仅保留已确认成果）。
    - 默认: 仅删除 ``status == suggested``（保留人工 confirmed 与 rejected）。
    """
    conditions = [WritingBinding.project_id == project_id]
    if reset_reviewed_bindings:
        pass  # 删除全部
    elif explicit_retry:
        conditions.append(WritingBinding.status != BindingStatus.confirmed)
    else:
        conditions.append(WritingBinding.status == BindingStatus.suggested)

    result = await db.execute(delete(WritingBinding).where(*conditions))
    await db.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    logger.info(
        "重匹配前清理绑定: project={}, reset={}, explicit_retry={}, deleted={}",
        project_id,
        reset_reviewed_bindings,
        explicit_retry,
        deleted,
    )
    return deleted


@dataclass(slots=True)
class BindingPersistenceDeps:
    """persist_rematched_bindings 的外部依赖注入。

    职责: 让持久化策略在不直接耦合模块级全局的前提下完成取消墓碑判定与阈值决策，
          便于调用方在运行时绑定（含被测试 monkeypatch 的）取消判定函数。
    """

    is_project_cancelled: Callable[[int], bool]
    auto_confirm_threshold: float


async def persist_rematched_bindings(
    db: AsyncSession,
    project_id: int,
    raw_bindings: list[dict],
    deps: BindingPersistenceDeps,
) -> list[WritingBinding]:
    """写入重算后的绑定候选，跳过与存活人工决策重复的边。

 - 不再删除任何 confirmed / rejected 边（删除职责已上移到 prepare_binding_rematch）。
 - 以业务键跳过与存活 confirmed/rejected 重复的候选，以及候选之间的重复。
 - 保留 写入守卫（取消墓碑 + projects 父行存在校验）。
 """
    if deps.is_project_cancelled(project_id):
        raise ProjectCancelledError(
            project_id, detail="material_match canceled before save"
        )
    project_check = await db.execute(
        select(Project.id).where(Project.id == project_id)
    )
    if project_check.scalar_one_or_none() is None:
        raise ProjectCancelledError(
            project_id, detail="project missing in DB before binding insert"
        )

    preserved_result = await db.execute(
        select(WritingBinding).where(
            WritingBinding.project_id == project_id,
            WritingBinding.status != BindingStatus.suggested,
        )
    )
    seen: set[BindingKey] = {
        _binding_row_key(binding) for binding in preserved_result.scalars().all()
    }

    saved: list[WritingBinding] = []
    skipped = 0
    for raw in raw_bindings:
        key = _candidate_key(raw)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)

        binding_type = raw["binding_type"]
        confidence = raw.get("confidence", 0.0)
        if (
            binding_type == BindingType.clause_to_node
            and confidence >= deps.auto_confirm_threshold
            and raw.get("outline_node_id") is not None
        ):
            status = BindingStatus.confirmed
        else:
            status = BindingStatus.suggested

        binding = WritingBinding(
            project_id=project_id,
            outline_node_id=raw.get("outline_node_id"),
            clause_id=raw.get("clause_id"),
            material_id=raw.get("material_id"),
            binding_type=binding_type,
            confidence=confidence,
            source=raw.get("source", "auto"),
            status=status,
            reason=raw.get("reason"),
            scope=raw.get("scope"),
        )
        db.add(binding)
        saved.append(binding)

    await db.commit()
    for binding in saved:
        await db.refresh(binding)

    auto_confirmed = sum(
        1 for binding in saved if binding.status == BindingStatus.confirmed
    )
    logger.info(
        "绑定写入完成: project={}, total={}, auto_confirmed={}, skipped_preserved={}",
        project_id,
        len(saved),
        auto_confirmed,
        skipped,
    )
    return saved


__all__ = [
    "BindingKey",
    "BindingPersistenceDeps",
    "prepare_binding_rematch",
    "persist_rematched_bindings",
]
