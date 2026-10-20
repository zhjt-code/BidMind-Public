"""标书编写核心模块

提供三层约束提取、行业画像生成、目录树生成、素材匹配、
内容编排、一致性检查和文档渲染等功能。

P2.4 温度常量 (中-2):
- 事实类任务 (约束提取 / 分类 / 裁决 / 摘要) 统一使用 TEMPERATURE_FACT
- 创作类任务 (free_writing 自由论述) 使用 TEMPERATURE_CREATIVE
- writing 流程的 stability_class 规则在 prompt_registry 里兜底强制 0.0
  因此此处常量主要供"不走 Registry 的业务侧"显式使用, 避免 magic number 散布。
"""

TEMPERATURE_FACT: float = 0.1
TEMPERATURE_CREATIVE: float = 0.3

__all__ = [
    "TEMPERATURE_FACT",
    "TEMPERATURE_CREATIVE",
]
