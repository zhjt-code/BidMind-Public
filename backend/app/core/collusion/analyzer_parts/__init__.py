"""串标分析器拆分子包

Task 8 治理：按职责将 `analyzer.py` 拆为子模块。本子包仅供 `analyzer.py`
主模块内部使用，外部调用方继续走 `from app.core.collusion.analyzer import ...`。

子模块计划（详见 docs/refactor_execution_plan.md Task 8 卡片）：
- constants.py            : 模块级常量 + ExtractionQualityError + 顶部小工具
- extraction_validation.py: 抽取相关纯函数（_is_empty_extraction 等）
- algorithmic.py          : _algorithm_compare / _algorithm_d4_score / _inject_d8_from_metadata 等
- findings.py             : _extract_structured_findings / _derive_overall_risk_from_dims 等
- synthesis_helpers.py    : _simple_synthesis / _build_project_summary_payload 等
- extraction_stage.py     : S1 阶段类内方法（接收 analyzer 入参）
- pair_compare.py         : S3 核心比对方法
- pair_dimensions.py      : S3 维度 LLM 方法
- pair_evidence.py        : S3 证据回填方法
- pair_persistence.py     : S3 持久化方法
- group_stage.py          : S3.5/S4 群组方法
- synthesis_stage.py      : S4 综合研判方法
"""
