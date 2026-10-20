"""CoVe 验证问题硬编码模板（B5.9）。

D-021 三类触发点各有独立模板。问题完全确定性化，占位符仅从结构化
上下文填充，禁止 LLM 改写。
"""

from __future__ import annotations

from dataclasses import dataclass

# CoVe 答案期望的结构化字段（裸 JSON Schema，仅供文档/本地校验参考）
# 注意：不可作为 OpenAI 兼容 response_format 直接下发，根节点 type="object"
# 在多家云端 provider 上会被判为非法 response_format.type。
ANSWER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["yes", "no", "abstain"]},
        "evidence_page": {"type": "integer"},
        "quote": {"type": "string", "maxLength": 500},
    },
    "required": ["answer", "evidence_page", "quote"],
}

# OpenAI 兼容的 response_format，安全下发到多家 provider。
# CloudClient.chat_json 的 json_schema 参数本质是 response_format，需传此形态。
ANSWER_RESPONSE_FORMAT: dict = {"type": "json_object"}


@dataclass(frozen=True)
class CoveQuestion:
    """单条验证问题。"""
    question_id: str
    system_prompt: str
    user_prompt_template: str


# CoVe 公共 system 提示：为通过 qwen 等 provider 的 json_object 内容约束，
# 明确要求以 JSON 返回固定字段。
_COVE_JSON_HINT = (
    "必须以 JSON 对象返回，字段为 {answer, evidence_page, quote}，"
    "answer 取值为 yes/no/abstain。"
)


INVESTIGATE_NOT_MANDATORY = [
    CoveQuestion(
        question_id="inv_q1",
        system_prompt=(
            "你是一位投标文件审查专家。根据给定的原文片段回答以下是非题，必须引用原文。"
            + _COVE_JSON_HINT
        ),
        user_prompt_template=(
            "投标文件在第 {chunk_page} 页是否明确响应了招标要求中的 '{clause_keyword}'？"
            "请返回 YES/NO 并引用原文，以 JSON 形式输出。"
        ),
    ),
    CoveQuestion(
        question_id="inv_q2",
        system_prompt=(
            "你是一位投标文件审查专家。根据给定的原文片段回答以下是非题，必须引用原文。"
            + _COVE_JSON_HINT
        ),
        user_prompt_template=(
            "该响应与招标要求的 '{mandatory_value}' 是否严格一致？请以 JSON 形式输出。"
        ),
    ),
    CoveQuestion(
        question_id="inv_q3",
        system_prompt=(
            "你是一位投标文件审查专家。根据给定的原文片段回答以下是非题，必须引用原文。"
            + _COVE_JSON_HINT
        ),
        user_prompt_template=(
            "是否存在其他章节对此响应的补充或矛盾内容？请以 JSON 形式输出。"
        ),
    ),
]


COLLUSION_HIGH = [
    CoveQuestion(
        question_id="col_q1",
        system_prompt=(
            "你是一位串标审查专家。根据给定的比对证据回答以下是非题，必须引用原文。"
            + _COVE_JSON_HINT
        ),
        user_prompt_template=(
            "投标文件 A（第 {page_a} 页）与投标文件 B（第 {page_b} 页）"
            "在 '{dimension}' 维度上是否存在实质性雷同？请以 JSON 形式输出。"
        ),
    ),
    CoveQuestion(
        question_id="col_q2",
        system_prompt=(
            "你是一位串标审查专家。根据给定的比对证据回答以下是非题，必须引用原文。"
            + _COVE_JSON_HINT
        ),
        user_prompt_template=(
            "该雷同是否可以由行业通用模板或标准格式解释？请以 JSON 形式输出。"
        ),
    ),
    CoveQuestion(
        question_id="col_q3",
        system_prompt=(
            "你是一位串标审查专家。根据给定的比对证据回答以下是非题，必须引用原文。"
            + _COVE_JSON_HINT
        ),
        user_prompt_template=(
            "两份投标文件在其他维度（人员/报价/格式）是否也存在异常一致？请以 JSON 形式输出。"
        ),
    ),
]


WRITER_COMMITMENT = [
    CoveQuestion(
        question_id="wrt_q1",
        system_prompt=(
            "你是一位标书编写审查专家。根据给定的章节内容回答以下是非题，必须引用原文。"
            + _COVE_JSON_HINT
        ),
        user_prompt_template=(
            "该章节中关于 '{commitment_keyword}' 的承诺是否有对应的素材支撑？"
            "请以 JSON 形式输出。"
        ),
    ),
    CoveQuestion(
        question_id="wrt_q2",
        system_prompt=(
            "你是一位标书编写审查专家。根据给定的章节内容回答以下是非题，必须引用原文。"
            + _COVE_JSON_HINT
        ),
        user_prompt_template=(
            "该承诺中的数字（金额/时间/数量）是否与招标要求一致？请以 JSON 形式输出。"
        ),
    ),
    CoveQuestion(
        question_id="wrt_q3",
        system_prompt=(
            "你是一位标书编写审查专家。根据给定的章节内容回答以下是非题，必须引用原文。"
            + _COVE_JSON_HINT
        ),
        user_prompt_template=(
            "该章节是否存在与其他章节的数据矛盾？请以 JSON 形式输出。"
        ),
    ),
]

TEMPLATES_BY_TRIGGER = {
    "investigate_not_mandatory": INVESTIGATE_NOT_MANDATORY,
    "collusion_high": COLLUSION_HIGH,
    "writer_commitment": WRITER_COMMITMENT,
}
