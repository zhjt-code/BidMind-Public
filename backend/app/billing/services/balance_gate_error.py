"""把余额不足异常转成结构化 403。公开版本的预检不会触发它。"""

from typing import NoReturn

from app.billing.services.wallet_service import (
    InsufficientBalanceError,
    format_insufficient_balance,
)
from app.core.exceptions import ServiceError

INSUFFICIENT_BALANCE_ERROR_CODE = "INSUFFICIENT_BALANCE"


def raise_insufficient_balance(exc: InsufficientBalanceError) -> NoReturn:
    raise ServiceError(
        f"余额不足，{format_insufficient_balance(exc)}",
        status_code=403,
        error_code=INSUFFICIENT_BALANCE_ERROR_CODE,
        error_data={
            "available": getattr(exc, "available", None),
            "required": getattr(exc, "required", None),
        },
    ) from exc
