"""用户态文案中文化模块。

集中维护"内部枚举值 / 调试参数"到"用户可见中文文案"的映射真源，
并提供 `sanitize_user_facing_text` 出口闸门函数，供三大流程
（投标审查 / 串标审查 / 标书编写）的 router 序列化层调用。

设计目标（对齐 教训）：
- 进度文案、结果文案、AI 自由文本均必须经过此闸门
- 任一新增的内部枚举值，应同步登记到本模块的映射表中
- 禁止页面或 router 直接渲染未经清洗的内部状态码
"""

from app.core.i18n.internal_to_display import (
    DISPLAY_TOKEN_MAP,
    sanitize_user_facing_text,
    translate_token,
)
from app.core.i18n.leak_detector import detect_internal_leaks

__all__ = [
    "DISPLAY_TOKEN_MAP",
    "detect_internal_leaks",
    "sanitize_user_facing_text",
    "translate_token",
]
