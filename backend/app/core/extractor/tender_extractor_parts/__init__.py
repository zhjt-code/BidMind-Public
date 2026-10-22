"""TenderExtractor 按业务阶段拆分的子包（R2-2 重构产出）。

| 子模块 | 责任 |
|---|---|
| `extraction_stage.py` | 单分块 LLM 提取 + salvage 解析 + provenance 合并 + 源页对齐 + 本地预去重 |
| `merge_category_stage.py` | 类别合并三套（路由 + 单轮 + 分批 + 高风险索引选择） |
| `secondary_merge_stage.py` | 二次合并三套（编排 + decision/JSON 工具 + 单轮 + 分批 + 重试缩减） |
| `dedup_stage.py` | 跨类别 LLM 反思去重 + 本地相似度兜底 |
| `quality_check.py` | 精度质量检查 + 复合要求拆分 + 业绩双归因对齐 + 类内推断字段 |

设计原则（沿用 v1 Task 8 analyzer_parts 模式）：

1. 子模块函数为纯模块级函数，**第一参数 `self` 保留**（对应 TenderExtractor 实例），
   函数名相对原 method **去前导下划线**（如 `_extract_from_chunk` → `extract_from_chunk`）。
2. 子模块内 `self.xxx` 跨阶段调用通过主类 thin forwarder 触发，兼容外部测试 monkey patch。
3. 模块级符号（`CATEGORY_MAP` / `infer_*` / prompt registry / `settings`）通过反向 import
   `from app.core.extractor import tender_extractor as tem` 引用，避免 fake `app.core.ai`
   模块注入时 import 失效。
4. 子模块**不通过本 `__init__.py` 对外暴露**——仅供主模块 `tender_extractor.py` 的 thin
   forwarder 内部使用，外部调用方继续走主模块公共接口。
"""
