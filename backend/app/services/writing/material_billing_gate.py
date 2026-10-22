"""写标计费预占门(素材入口, 泛化为全部触发入口)

职责: 写标各触发入口(素材批处理/目录/匹配/全文/单章)启动前确保计费预占存在
不做: 不结算、不释放、不采集用量明细(由 BillingGuard 统一承担)
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project


async def ensure_writing_billing_hold(
    db: AsyncSession,
    project: Project,
    user_id: int,
    *,
    extra_input_tokens: int = 0,
    extra_output_tokens: int = 0,
) -> None:
    """写标消耗型任务启动前的计费预占检查。

 复用 ``BillingGuard.pre_check`` 的幂等语义:
 - ``BILLING_ENABLED=false``: 内部直接跳过;
 - free_trial: 幂等占用(同项目已有 ACTIVE 账本则空跑, 不重复扣名额);
 - 项目已有 HOLDING 预占: 按最新用量重估并补差额, 不重复建 hold;
 - hold 已结算后重跑: 新建 hold 承接本次消耗, 余额不足抛 403。

 extra_input_tokens / extra_output_tokens: 生成阶段入口按目录字数追加的
 token 预算(compose_estimate), 其余入口保持默认 0 零行为变化。

 结算不在此处发生: 仍由 compose 完成回调与 hold_sweeper 收口。
 """
    from app.billing.services.balance_gate_error import raise_insufficient_balance
    from app.billing.services.billing_guard import BillingGuard
    from app.billing.services.wallet_service import InsufficientBalanceError

    try:
        await BillingGuard.pre_check(
            db, project, user_id,
            extra_input_tokens=extra_input_tokens,
            extra_output_tokens=extra_output_tokens,
        )
        await db.commit()
    except InsufficientBalanceError as e:
        raise_insufficient_balance(e)


# 兼容别名: 素材批处理入口的历史导入名, 语义等同通用预占门
ensure_material_billing_hold = ensure_writing_billing_hold
