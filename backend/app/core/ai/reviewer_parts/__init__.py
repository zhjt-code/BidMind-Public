"""ReviewOrchestrator 子模块包（R2-3 拆分）

按职责拆分 `app.core.ai.reviewer.ReviewOrchestrator` 巨型类。每个子模块为
纯函数集合，接收 `self`（`ReviewOrchestrator` 实例）作为第一参数，沿用 v1
Task 8 (CollusionAnalyzer) 的「self 不替换 + thin forwarder」模式。

子模块划分（与 R2-3 卡片建议一致，按职责自然边界）：
    - evidence_prefetch : 证据预取阶段（_prefetch_evidence + 索引/owner 查询）
    - pair_review       : 单对审查（LLM 调用 + Gate skip + prompt 构造）
    - cove_judge        : CoVe 自我验证 + Judge investigate
    - result_builder    : 审查结果构造（含失败兜底 + 算法证据）
    - fact_graph        : 事实图构造 + 证据关系
    - consistency_postcheck : 一致性后检查 + family rules + domain keywords

外部调用方仍通过 `from app.core.ai.reviewer import ReviewOrchestrator` 导入，
本子包仅供 `reviewer.py` 内部委托使用（与 R2-1 retry / Task 7 process 子包
策略一致）。
"""
