"""公开版本的任务回调占位。

职责: 让审查、串标和写标的原有调用点继续运行
不做: 计价、冻结、扣费、试用和支付
"""


class BillingGuard:
    @staticmethod
    async def pre_check(
        db,
        project,
        user_id,
        *,
        extra_input_tokens: int = 0,
        extra_output_tokens: int = 0,
    ) -> None:
        del db, project, user_id, extra_input_tokens, extra_output_tokens

    @staticmethod
    async def on_completed(db, project) -> None:
        del db, project

    @staticmethod
    async def on_failed(db, project, refund_trial: bool = True) -> None:
        del db, project, refund_trial
