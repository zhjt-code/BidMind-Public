"""余额不足异常占位。公开版本不会抛出它。"""


class InsufficientBalanceError(Exception):
    def __init__(self, available: int = 0, required: int = 0) -> None:
        self.available = available
        self.required = required
        super().__init__("insufficient balance")


def format_insufficient_balance(exc: InsufficientBalanceError) -> str:
    del exc
    return "余额不足"
