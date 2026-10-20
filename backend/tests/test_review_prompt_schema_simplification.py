"""REVIEW_PROMPT 输出 schema 精简 + BID_EXTRACT linked_to 引导淡化 + BidExtractor 默认 max_retries 契约测试。

职责: 验证 REVIEW_PROMPT 输出 schema 已移除 8 个代码层兜底字段引导，
 BID_EXTRACT prompt 中 linked_to 引导已淡化（含 "规则 3" 段移除 +
 user 模板注释 + 质量自查清单），BidExtractor 默认 max_retries=3。
不做: 不调用真实 LLM、不验证 reviewer 全流程（Gate Pipeline / CoVe / Judge）、
 不测前端组件渲染。

背景:
 第二轮深入评估发现 REVIEW_PROMPT 的输出 JSON 示例中要求 LLM 填写 7+1
 个由 result_builder 代码兜底的字段（canonical_key / fact_key /
 family_type / attribution_type / evidence_relation / consistency_flag /
 reconcile_note / main_requirement_id）。让 LLM 输出这些字段既浪费 token
 又增加 Literal 枚举的校验失败风险。本次同步淡化 BID_EXTRACT 中
 linked_to 引导（单 requirement 并发模式下 LLM 无法准确填写），并把
 BidExtractor 默认 max_retries 从 2 提到 3（与 tender_extract 对齐）。

本测试是修复后的回归护栏，防止上述精简被未来回潮覆盖。

 续作:
 第一轮只精简了 8 个 Optional 兜底字段，未动 requirement_id / bid_response_id /
 category / requirement_summary 四个必填字段——因为当时没有装配层，移除必填字段
 会让 ExtractedReviewResult 构造失败。新增 reviewer_parts/result_assembly.py 后
 这四个字段改由服务端以 pair 侧权威数据装配，故本轮从输出示例中移除，护栏断言
 同步更新为「核心判定字段仍在 + 服务端装配字段不得回潮」双向锁定。
"""

from __future__ import annotations

import inspect
import json

from app.core.ai.prompts.bid_extract import BID_EXTRACT_SYSTEM, BID_EXTRACT_USER
from app.core.ai.prompts.review import REVIEW_PROMPT
from app.core.ai.validators import OutputValidator
from app.core.extractor.bid_extractor import BidExtractor
from app.schemas.review import ExtractedReviewResult


# ---------------------------------------------------------------------------
# R2 测试点 1: REVIEW_PROMPT 输出 JSON 示例段已移除 8 个代码兜底字段
# ---------------------------------------------------------------------------


# 7 个由 result_builder.create_review_result 代码层兜底的字段 + reconcile_note
_CODE_OVERRIDDEN_FIELDS: tuple[str, ...] = (
    "canonical_key",
    "fact_key",
    "family_type",
    "attribution_type",
    "evidence_relation",
    "consistency_flag",
    "reconcile_note",
    "main_requirement_id",
)


def _extract_output_json_block(prompt: str) -> str:
    """从 REVIEW_PROMPT 中切出最后一段 ```json ... ``` 输出示例块。

    REVIEW_PROMPT 中"判定输出"段落仅有一个 JSON 示例（不含 NO_RESPONSE_PROMPT），
    用最后一个 ```json``` 块作为契约断言对象。仅断言示例段，不误伤
    Golden Rule5 中的"禁止"提示文本。
    """
    marker = "```json"
    start = prompt.rfind(marker)
    assert start >= 0, "REVIEW_PROMPT 未找到 ```json 示例块"
    end = prompt.find("```", start + len(marker))
    assert end > start, "REVIEW_PROMPT 的 JSON 示例块缺少结束标记"
    return prompt[start:end]


def test_review_prompt_output_block_has_no_code_overridden_fields() -> None:
    """REVIEW_PROMPT 的输出 JSON 示例段不应再要求 LLM 输出代码兜底字段。"""
    block = _extract_output_json_block(REVIEW_PROMPT)
    for field in _CODE_OVERRIDDEN_FIELDS:
        token = f'"{field}":'
        assert token not in block, (
            f"REVIEW_PROMPT 输出示例仍含 {token}；该字段由 result_builder 代码层兜底，"
            "不应让 LLM 输出（避免 token 浪费 + Literal 枚举校验失败风险）"
        )


def test_review_prompt_explicit_do_not_output_notice_present() -> None:
    """REVIEW_PROMPT 必须在示例段上方明示这些字段由系统判定，不要输出。"""
    assert "系统后处理自动判定" in REVIEW_PROMPT, (
        "REVIEW_PROMPT 缺少 '系统后处理自动判定' 显式禁止说明，"
        "LLM 可能仍习惯输出 canonical_key 等字段"
    )
    # 至少包含一个被禁止字段名作为锚点（兼容用斜杠/逗号等不同分隔符）
    assert "canonical_key" in REVIEW_PROMPT
    assert "main_requirement_id" in REVIEW_PROMPT


def test_review_prompt_still_requires_core_decision_fields() -> None:
    """LLM 仍必须输出的核心判定字段不能被误删。

 起 requirement_id / bid_response_id / category / requirement_summary
 移交 result_assembly 装配，不再属于本清单；见
 test_review_prompt_output_block_has_no_server_assembled_fields。
 """
    block = _extract_output_json_block(REVIEW_PROMPT)
    for required in (
        "response_summary",
        "is_compliant",
        "compliance_level",
        "score",
        "comment",
        "risk_level",
        "relevance_score",
        "relevance_note",
    ):
        assert f'"{required}":' in block, (
            f"REVIEW_PROMPT 输出示例缺少核心字段 {required}，"
            "可能精简过度，需要补回"
        )


# 由 result_assembly 用 pair 侧权威数据装配的字段。让 LLM 回显它们既挤占
# 输出预算（导致真正的判断字段被 max_tokens 截断），又把纯长度问题升级为整条
# LLM 调用失败（requirement_summary 的 max_length=50 曾造成 46 次校验失败）。
_SERVER_ASSEMBLED_FIELDS: tuple[str, ...] = (
    "requirement_id",
    "bid_response_id",
    "category",
    "requirement_summary",
)


def test_review_prompt_output_block_has_no_server_assembled_fields() -> None:
    """REVIEW_PROMPT 输出示例不得要求 LLM 回显服务端装配字段（防精简回潮）。"""
    block = _extract_output_json_block(REVIEW_PROMPT)
    for field in _SERVER_ASSEMBLED_FIELDS:
        token = f'"{field}":'
        assert token not in block, (
            f"REVIEW_PROMPT 输出示例仍含 {token}；该字段由 result_assembly 以 pair 侧"
            "权威数据装配，落库时也不采信模型回显，让模型输出纯属浪费输出预算"
        )


# ---------------------------------------------------------------------------
# R2 测试点 2: 缺失这 8 个字段的 LLM 输出仍能通过 schema 校验
#              （依赖 Optional + default None；下游 result_builder 用代码兜底）
# ---------------------------------------------------------------------------


def _llm_payload_without_code_overridden_fields(**overrides) -> dict:
    """构造仅含 LLM 必须输出字段的最小 payload。"""
    base = {
        "requirement_id": 1001,
        "bid_response_id": 5001,
        "category": "qualification",
        "requirement_summary": "投标人必须具备 ISO9001 认证",
        "response_summary": "投标人已提供 ISO9001 证书扫描件",
        "is_compliant": True,
        "compliance_level": "fully",
        "score": 95,
        "comment": "投标人在第 10 页提供了 ISO9001 证书扫描件，覆盖招标项目领域，符合要求",
        "risk_level": "none",
        "relevance_score": 90,
        "relevance_note": None,
    }
    base.update(overrides)
    return base


def test_schema_accepts_payload_without_code_overridden_fields() -> None:
    """LLM 不输出 8 个代码兜底字段时，schema 仍应校验成功，且字段值为 None。"""
    validator = OutputValidator(ExtractedReviewResult)
    payload = _llm_payload_without_code_overridden_fields()
    result = validator.validate(json.dumps(payload, ensure_ascii=False))

    assert result.success, (
        f"缺失代码兜底字段的 payload 应通过 schema 校验（result_builder 会兜底）；"
        f"实际失败: {result.error}"
    )
    parsed = result.unwrap()
    # 所有代码兜底字段必须落到 None，由下游 result_builder 用代码规则填充
    assert parsed.canonical_key is None
    assert parsed.fact_key is None
    assert parsed.family_type is None
    assert parsed.attribution_type is None
    assert parsed.evidence_relation is None
    assert parsed.consistency_flag is None
    assert parsed.reconcile_note is None
    assert parsed.main_requirement_id is None


# ---------------------------------------------------------------------------
# R5 测试点 3: BID_EXTRACT prompt 中 linked_to 引导已淡化
# ---------------------------------------------------------------------------


def test_bid_extract_system_removes_associated_response_rule() -> None:
    """BID_EXTRACT_SYSTEM 不应再含 '规则3:关联响应识别' 段落。

    该段（旧 65-104 行）大段引导 LLM 标注 linked_to=要求ID，但
    BidExtractor 是按 requirement 并发调用，LLM 看不到其它要求的 ID，
    属于伪需求引导。
    """
    assert "规则3:关联响应识别" not in BID_EXTRACT_SYSTEM, (
        "BID_EXTRACT_SYSTEM 仍含 '规则3:关联响应识别' 段落，"
        "该引导属于伪需求（LLM 无法跨 requirement 调用记忆 ID）"
    )
    # 旧示例 JSON 中的 "linked_to": 1 不应再被作为正例展示
    assert '"linked_to": 1' not in BID_EXTRACT_SYSTEM, (
        "BID_EXTRACT_SYSTEM 仍把 'linked_to: 1' 作为正例展示，"
        "可能诱导 LLM 乱填要求 ID"
    )


def test_bid_extract_user_template_marks_linked_to_as_system_judged() -> None:
    """BID_EXTRACT_USER 模板中 linked_to 字段注释必须明示 '系统判定 / 固定 null'。"""
    # 找到 'found': true 之后的 linked_to 行（找到响应的示例）
    assert "linked_to" in BID_EXTRACT_USER
    # 注释应明示由系统判定，不再让 LLM 主动填
    assert "系统判定" in BID_EXTRACT_USER, (
        "BID_EXTRACT_USER 中 linked_to 注释未明示 '系统判定'，"
        "LLM 可能仍尝试主动填该字段"
    )


def test_bid_extract_quality_checklist_drops_linked_to_item() -> None:
    """BID_EXTRACT_USER 的质量自查清单不应再含 '如关联到其他要求,是否标注linked_to' 项。"""
    assert "是否标注linked_to" not in BID_EXTRACT_USER, (
        "BID_EXTRACT_USER 质量自查清单仍引导 LLM 标注 linked_to，"
        "属于伪需求项，应移除"
    )


def test_bid_extract_output_quality_drops_linked_response_must_mark() -> None:
    """BID_EXTRACT_SYSTEM 的"输出质量要求"段不应再含 '关联响应必须标注:linked_to'。"""
    assert "关联响应必须标注" not in BID_EXTRACT_SYSTEM, (
        "BID_EXTRACT_SYSTEM 仍把 '关联响应必须标注:linked_to' 作为必填要求，"
        "应移除"
    )


# ---------------------------------------------------------------------------
# N1 测试点 4: BidExtractor 默认 max_retries == 3
# ---------------------------------------------------------------------------


def test_bid_extractor_default_max_retries_is_three() -> None:
    """BidExtractor.__init__ 默认 max_retries 必须为 3（与 tender_extract 对齐）。

 R3 已把首轮 max_tokens 提到 1500 降低截断率；max_retries 从 2 提到 3
 覆盖罕见的 JSON 多策略修复场景（参考 历史教训）。
 """
    sig = inspect.signature(BidExtractor.__init__)
    default = sig.parameters["max_retries"].default
    assert default == 3, (
        f"BidExtractor.__init__ 默认 max_retries={default}，预期 3。"
        "如确需调整，请同步评估 R3 max_tokens=1500 + JSON 修复链路的截断率"
    )
