"""未响应路径 Prompt 与 ExtractedReviewResult schema 契约测试。

职责: 验证 NO_RESPONSE_PROMPT 与 REVIEW_PROMPT 不要求模型回显服务端装配字段，
 并保证 ExtractedReviewResult schema 对 int / str 两种数字输入均能稳定校验通过。
不做: 不调用真实 LLM、不验证 reviewer 全流程、不测 Gate Pipeline。

背景:
 早期两个模板都在示例 JSON 里让模型回显 requirement_id，且 NO_RESPONSE_PROMPT
 用字符串引号包围而 REVIEW_PROMPT 不用，格式不一致诱导 LLM 把 score 等数值字段
 也字符串化。 的实测进一步证明回显本身才是主因：模型先输出服务端已知字段，
 耗尽 max_tokens 后判断字段被截断，158 条结果消耗了 263 次主审调用。
 修复方案是把 requirement_id / bid_response_id / category / requirement_summary /
 source_pages 全部移出输出契约，改由服务端装配，模板只声明"请勿输出"。

 因此本测试守护的契约已从"占位符格式一致"升级为"服务端字段不得进入模型输出面"
 （: 该升级当时未同步本文件，使断言停留在旧契约上长期失败）。
"""

from __future__ import annotations

import json

import pytest

from app.core.ai.prompts.review import NO_RESPONSE_PROMPT, REVIEW_PROMPT
from app.core.ai.validators import OutputValidator
from app.schemas.review import ExtractedReviewResult

# 由服务端权威装配、禁止出现在模型输出契约中的字段
SERVER_ASSEMBLED_FIELDS = (
    "requirement_id",
    "bid_response_id",
    "category",
    "requirement_summary",
    "source_pages",
)


# ---------------------------------------------------------------------------
# 测试点 1: 模板文本契约 -- 服务端装配字段不得出现在输出示例中
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt_name,prompt_text",
    [("NO_RESPONSE_PROMPT", NO_RESPONSE_PROMPT), ("REVIEW_PROMPT", REVIEW_PROMPT)],
)
def test_prompt_does_not_request_server_assembled_fields(
    prompt_name: str, prompt_text: str
) -> None:
    """模板的 JSON 示例不得把服务端装配字段列为模型输出项。

    这些字段一旦进入输出面就会挤占 max_tokens 预算，把"能判对"变成"来不及输出"。
    """
    for field in SERVER_ASSEMBLED_FIELDS:
        assert f'"{field}":' not in prompt_text, (
            f"{prompt_name} 的示例 JSON 仍要求模型输出 {field}，"
            "该字段由服务端装配，回显会挤占输出预算并触发整项重试"
        )


@pytest.mark.parametrize(
    "prompt_name,prompt_text",
    [("NO_RESPONSE_PROMPT", NO_RESPONSE_PROMPT), ("REVIEW_PROMPT", REVIEW_PROMPT)],
)
def test_prompt_declares_server_assembled_fields_as_forbidden(
    prompt_name: str, prompt_text: str
) -> None:
    """模板必须显式告知模型这些字段"请勿输出"，而不只是从示例里删掉。

    只删示例不声明时，模型仍会凭惯例补全这些字段。
    """
    assert "请勿输出" in prompt_text, f"{prompt_name} 缺少服务端装配字段的禁止输出声明"
    for field in SERVER_ASSEMBLED_FIELDS:
        assert field in prompt_text, (
            f"{prompt_name} 的禁止输出清单未覆盖 {field}"
        )


# ---------------------------------------------------------------------------
# 测试点 2: 渲染契约 -- 当前变量集合可正常渲染，且渲染后不含服务端字段输出项
# ---------------------------------------------------------------------------


def test_no_response_prompt_renders_with_current_variable_set() -> None:
    """NO_RESPONSE_PROMPT 必须能用当前变量集合渲染，且渲染结果不含服务端字段。

 format 调用本身即契约断言：模板变量集合是公共契约，新增或改名（如
 `category` 改为 `category_label`）会让此处直接 KeyError。 正是
 该改名发生时本文件未同步，使断言长期停留在旧契约上。
 """
    rendered = NO_RESPONSE_PROMPT.format(
        requirement_id=12345,
        category_label="资质要求类",
        is_mandatory="是",
        requirement_description="测试要求",
    )

    assert "- ID: 12345" in rendered
    assert "资质要求类" in rendered
    for field in SERVER_ASSEMBLED_FIELDS:
        assert f'"{field}": 12345' not in rendered
        assert f'"{field}": "12345"' not in rendered


def test_review_prompt_renders_with_current_variable_set() -> None:
    """REVIEW_PROMPT 同样必须用当前变量集合完整渲染。"""
    rendered = REVIEW_PROMPT.format(
        requirement_id=67890,
        category_label="技术要求类",
        is_mandatory="是",
        requirement_description="测试要求",
        response_id=1,
        response_content="测试响应",
        evidence_type="proposal",
        source_pages="5,7",
        completeness="full",
        linked_to="null",
        missing_elements="无",
        evidence_anchor="无",
        relevance_score="85",
        relevance_note="无",
    )

    assert "- ID: 67890" in rendered
    assert "技术要求类" in rendered
    assert '"requirement_id": 67890' not in rendered
    assert '"requirement_id": "67890"' not in rendered


# ---------------------------------------------------------------------------
# 测试点 3: ExtractedReviewResult schema 稳定性
#           int 与 str 数字输入均应校验通过（Pydantic 容错）
# ---------------------------------------------------------------------------


def _minimal_review_result_payload(**overrides) -> dict:
    """构造满足 ExtractedReviewResult 必填约束的最小 payload。"""
    base = {
        "requirement_id": 123,
        "bid_response_id": None,
        "category": "qualification",
        "requirement_summary": "测试要求摘要",
        "response_summary": "未提供响应",
        "is_compliant": False,
        "compliance_level": "not",
        "score": 0,
        "comment": "测试场景下未响应的审查意见样例文本",
        "risk_level": "high",
    }
    base.update(overrides)
    return base


def test_schema_accepts_integer_requirement_id() -> None:
    """主形态：requirement_id 为整数，schema 直接校验通过。"""
    validator = OutputValidator(ExtractedReviewResult)
    payload = _minimal_review_result_payload(requirement_id=123, score=0)
    result = validator.validate(json.dumps(payload, ensure_ascii=False))

    assert result.success, f"整数 requirement_id 应通过校验: {result.error}"
    parsed = result.unwrap()
    assert parsed.requirement_id == 123
    assert isinstance(parsed.requirement_id, int)


def test_schema_tolerates_string_numeric_requirement_id() -> None:
    """次优形态：requirement_id 字符串数字，Pydantic 应自动转换。

    这是修复前 LLM 易输出的退化形态。Pydantic int 校验向后兼容
    "123" → 123，所以校验仍应通过；这是兜底保险，并非鼓励 LLM
    输出该形态。修复 NO_RESPONSE_PROMPT 格式后，主形态应稳定为 int。
    """
    validator = OutputValidator(ExtractedReviewResult)
    payload = _minimal_review_result_payload(
        requirement_id="123",  # 字符串数字
        score="0",  # 字符串数字（同源退化）
    )
    result = validator.validate(json.dumps(payload, ensure_ascii=False))

    assert result.success, (
        "字符串数字 requirement_id/score 应被 Pydantic 容错转换；"
        f"若失败说明 schema 兜底退化: {result.error}"
    )
    parsed = result.unwrap()
    assert parsed.requirement_id == 123
    assert parsed.score == 0


@pytest.mark.parametrize(
    "category_input,expected_full",
    [
        ("QUAL", "qualification"),
        ("TECH", "technical"),
        ("COMM", "commercial"),
        ("PERF", "performance"),
        ("STAFF", "staffing"),
        ("FORMAT", "format"),
    ],
)
def test_schema_normalizes_category_abbreviation(
    category_input: str, expected_full: str
) -> None:
    """category 缩写归一化兜底：与 prompt 输出形态保持一致（Fix-03）。

    NO_RESPONSE_PROMPT 中 `"category": "{category}"` 渲染时可能传入
    缩写（QUAL/TECH 等），schema 必须能归一化到全称。
    """
    validator = OutputValidator(ExtractedReviewResult)
    payload = _minimal_review_result_payload(category=category_input)
    result = validator.validate(json.dumps(payload, ensure_ascii=False))

    assert result.success, f"category={category_input} 应被归一化: {result.error}"
    assert result.unwrap().category == expected_full
