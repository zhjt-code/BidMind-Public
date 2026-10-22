"""P1.2 Judge 按 flow 分流的 Prompt 模板。

与 judge.py 的单入口 run_judge 配合: run_judge 按 flow 取对应的 system/user
模板, 通过 registry_first 注入 {original_output} / {context_summary} /
{evidence_text} 后调用 judge 池。

flow 维度:
- review: 投标审查复核(评估审查结论是否合理)
- collusion: 串标审查复核(评估维度判定是否合理)
- writer: 标书编写复核(评估章节是否忠实素材、是否违反约束、是否杜撰事实)
- generic: 兜底, 行为等价于 P1.2 之前的通用 prompt (保持向后兼容)

注意:
- 所有模板必须包含占位符 {original_output} / {context_summary} / {evidence_text};
  evidence 可为空, 但占位符必须存在, 否则 .format 会报 KeyError 引导 fallback。
- 输出 schema 由 judge.JudgeVerdict 统一约束为 {verdict, confidence, reason}。
"""

from __future__ import annotations

from app.core.ai.critic.flows import (
    FLOW_COLLUSION,
    FLOW_GENERIC,
    FLOW_REVIEW,
    FLOW_WRITER,
    resolve_judge_template_flow,
)

# ──────────────────────────────────────────────────────────────
# Generic fallback (保持与 P1.2 前通用 prompt 等价)
# ──────────────────────────────────────────────────────────────

JUDGE_GENERIC_SYSTEM = (
    "你是一位独立的审查复核专家。请评估以下 AI 审查结论是否合理。\n"
    "返回 agree(同意) / disagree(不同意) / need_review(需人工复核), "
    "并给出置信度(0-1)和理由。\n"
    "必须以 JSON 对象返回, 字段为 {{verdict, confidence, reason}}。"
)

JUDGE_GENERIC_USER = (
    "审查上下文摘要:\n{context_summary}\n\n"
    "AI 审查结论:\n{original_output}\n\n"
    "附加证据:\n{evidence_text}\n\n"
    "请评估上述结论是否合理, 并以 JSON 形式输出结果。"
)


# ──────────────────────────────────────────────────────────────
# Review (投标审查)
# ──────────────────────────────────────────────────────────────

JUDGE_REVIEW_SYSTEM = (
    "你是资深投标审查复核专家, 负责评估另一 AI 对招标要求-投标响应对比"
    "审查结论的合理性。判定维度:\n"
    "- 是否正确识别了响应的充分性/偏离/缺失;\n"
    "- 是否对证据(页码/原文/条款号)做了合理引用;\n"
    "- 是否存在把结构性问题和内容性问题混淆。\n"
    "严格返回 JSON: {{verdict, confidence, reason}}。"
    "verdict ∈ agree|disagree|need_review。"
)

JUDGE_REVIEW_USER = (
    "审查上下文:\n{context_summary}\n\n"
    "原 AI 审查结论:\n{original_output}\n\n"
    "补充证据(招标原文/投标响应片段):\n{evidence_text}\n\n"
    "请评估原结论是否合理, 以 JSON 返回。"
)


# ──────────────────────────────────────────────────────────────
# Collusion (串标审查)
# ──────────────────────────────────────────────────────────────

JUDGE_COLLUSION_SYSTEM = (
    "你是串标围标嫌疑复核专家, 负责评估另一 AI 对多家投标文件间"
    "相似性/关联性判定的合理性。判定维度:\n"
    "- 是否把'同行业通用表述'误判为'串标信号';\n"
    "- 是否正确区分了'同源材料'与'抄袭';\n"
    "- 数值对比/相似度结论是否与证据片段一致。\n"
    "严格返回 JSON: {{verdict, confidence, reason}}。"
)

JUDGE_COLLUSION_USER = (
    "维度上下文:\n{context_summary}\n\n"
    "原 AI 维度判定:\n{original_output}\n\n"
    "补充证据(对比片段/通用表达库参考):\n{evidence_text}\n\n"
    "请评估原维度判定是否合理, 以 JSON 返回。"
)


# ──────────────────────────────────────────────────────────────
# Writer (标书编写)
# ──────────────────────────────────────────────────────────────

JUDGE_WRITER_SYSTEM = (
    "你是资深投标文件内容审阅专家, 负责评估 AI 生成的某一章节正文的质量。"
    "判定维度(务必严格):\n"
    "- 忠实素材: 章节中的具体事实(公司名/证书号/项目名/金额/日期/人名)"
    "必须在绑定素材中有依据, 不得凭空杜撰;\n"
    "- 符合约束: 是否违反硬约束(禁止条款)或结构约束(目录层级/字数);\n"
    "- 内容落地: 是否至少引用了 2-3 个明确事实点, 而非通用话术堆砌;\n"
    "- 无捏造占位: 未提供的具体事实应使用 [待补充:字段名], 不得自行编造。\n"
    "严格返回 JSON: {{verdict, confidence, reason}}。"
    "verdict ∈ agree|disagree|need_review。"
    "disagree 请在 reason 中指出具体违规位置。"
)

JUDGE_WRITER_USER = (
    "章节上下文:\n{context_summary}\n\n"
    "生成结果摘要(含正文与元数据):\n{original_output}\n\n"
    "绑定素材片段 + 硬约束文本:\n{evidence_text}\n\n"
    "请评估该章节质量, 以 JSON 返回。"
)


# 按 flow 取模板的便捷映射
# reviewer 主链路使用 flow="investigate"（与指标 label 对齐）。
# 此处将 investigate 显式 alias 到 review 复核模板，避免回退 generic 导致的
# 投标审查复核质量打折；suffix 仍为 "review"，确保 prompt_registry 节点 key
# 维持 judge.review 不变。
FLOW_TEMPLATE_MAP: dict[str, tuple[str, str, str]] = {
    # flow: (node_key_suffix, system_template, user_template)
    FLOW_REVIEW: ("review", JUDGE_REVIEW_SYSTEM, JUDGE_REVIEW_USER),
    FLOW_COLLUSION: ("collusion", JUDGE_COLLUSION_SYSTEM, JUDGE_COLLUSION_USER),
    FLOW_WRITER: ("writer", JUDGE_WRITER_SYSTEM, JUDGE_WRITER_USER),
    FLOW_GENERIC: ("generic", JUDGE_GENERIC_SYSTEM, JUDGE_GENERIC_USER),
}


def get_judge_templates(flow: str) -> tuple[str, str, str]:
    """按 flow 返回 (node_key, system_template, user_template)。

    flow 未在映射中时回退 generic; 保证永远返回可直接 .format 的模板。
    """
    template_flow = resolve_judge_template_flow(flow)
    entry = FLOW_TEMPLATE_MAP.get(template_flow)
    if entry is None:
        return ("generic", JUDGE_GENERIC_SYSTEM, JUDGE_GENERIC_USER)
    suffix, system, user = entry
    return (suffix, system, user)


__all__ = [
    "JUDGE_GENERIC_SYSTEM",
    "JUDGE_GENERIC_USER",
    "JUDGE_REVIEW_SYSTEM",
    "JUDGE_REVIEW_USER",
    "JUDGE_COLLUSION_SYSTEM",
    "JUDGE_COLLUSION_USER",
    "JUDGE_WRITER_SYSTEM",
    "JUDGE_WRITER_USER",
    "FLOW_TEMPLATE_MAP",
    "get_judge_templates",
]
