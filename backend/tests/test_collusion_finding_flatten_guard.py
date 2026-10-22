"""防御测试：串标 LLM finding 拍平结构识别与兜底合并

对应 的"未清理尾巴" -- LLM 偶发把单条 finding 的字段拍平为
`["summary: ...", "detail: ...", "quote_a: ..."]` 字符串列表时，必须：

1. Schema 层（normalize_finding_list_strict）抛 ValueError 触发重试；
2. 单条孤立旧字符串 finding 仍兼容；
3. analyzer 兜底 `_recover_flattened_findings` 能合并为结构化 finding；
4. `_extract_structured_findings` 输出的 quality_flags 含 `flat_finding_recovered`。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.collusion.analyzer import (
    _FLAT_FINDING_RECOVERED_FLAG,
    _extract_structured_findings,
    _recover_flattened_findings,
)
from app.schemas.collusion import (
    FLATTENED_FINDING_REJECT_THRESHOLD,
    LLMCompareResult,
    LLMDimensionScore,
    LLMSingleDimResult,
    is_flattened_field_string,
    normalize_finding_list_strict,
)

FLATTENED_LIST_5_FIELDS = [
    "summary: 发现两份投标文件中存在同名同角色的关键人员。",
    "detail: 在文档bid_document2.pdf (doc_id=981) 和 bid_document1.pdf 中均出现",
    "quote_a: 王博士，项目经理",
    "page_a: 3",
    "quote_b: 王博士，项目经理",
]


# ---------------------------------------------------------------------------
# 1. is_flattened_field_string 单元
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("summary: foo", True),
        ("detail: bar", True),
        ("quote_a: 王博士，项目经理", True),
        ("page_a: 3", True),
        ("Quote_B：王博士", True),
        ("similarity: 0.95", True),
        ("正常的发现描述", False),
        ("无关键人员", False),
        ("", False),
        ("page_c: 3", False),
        (None, False),
        ({"summary": "foo"}, False),
    ],
)
def test_is_flattened_field_string(value, expected) -> None:
    assert is_flattened_field_string(value) is expected


# ---------------------------------------------------------------------------
# 2. Schema 层：normalize_finding_list_strict / 各 LLMxxxResult 校验器
# ---------------------------------------------------------------------------


def test_normalize_finding_list_strict_rejects_flattened() -> None:
    """list 中拍平字符串数量 >= 阈值时必须抛 ValueError"""
    with pytest.raises(ValueError, match="拍平"):
        normalize_finding_list_strict(FLATTENED_LIST_5_FIELDS, dict_no_keys_to_str=True)


def test_normalize_finding_list_strict_keeps_isolated_string() -> None:
    """单条孤立字符串保留旧兼容（包成 dict）"""
    result = normalize_finding_list_strict(
        ["发现某种特征"], dict_no_keys_to_str=False
    )
    assert result == [{"summary": "发现某种特征", "detail": "发现某种特征"}]


def test_normalize_finding_list_strict_keeps_normal_dict_list() -> None:
    """正常 dict 列表保持不变"""
    payload = [
        {"summary": "发现", "detail": "详细", "quote_a": "...", "page_a": "3"},
        {"summary": "另一条", "detail": "另一详情"},
    ]
    result = normalize_finding_list_strict(payload, dict_no_keys_to_str=False)
    assert result == payload


def test_normalize_finding_list_strict_threshold_value() -> None:
    """阈值边界：恰好 1 条拍平字符串不应触发拒绝"""
    assert FLATTENED_FINDING_REJECT_THRESHOLD == 2
    result = normalize_finding_list_strict(
        ["summary: 单条孤立的拍平 -- 不应触发拒绝"],
        dict_no_keys_to_str=False,
    )
    assert len(result) == 1
    assert result[0]["summary"].startswith("summary:")


def test_llm_dimension_score_rejects_flattened_findings() -> None:
    with pytest.raises(ValidationError):
        LLMDimensionScore(
            score=0.9,
            findings=FLATTENED_LIST_5_FIELDS,
            evidence_level="first",
            certainty=0.9,
        )


def test_llm_compare_result_rejects_flattened_key_findings() -> None:
    with pytest.raises(ValidationError):
        LLMCompareResult(
            overall_risk="high",
            key_findings=FLATTENED_LIST_5_FIELDS,
        )


def test_llm_single_dim_result_rejects_flattened_findings() -> None:
    with pytest.raises(ValidationError):
        LLMSingleDimResult(
            score=0.8,
            findings=FLATTENED_LIST_5_FIELDS,
            evidence_level="second",
            certainty=0.7,
        )


def test_llm_dimension_score_accepts_normal_findings() -> None:
    """正常结构化 findings 仍能通过"""
    model = LLMDimensionScore(
        score=0.9,
        findings=[
            {
                "summary": "发现关键人员重叠",
                "detail": "两份投标文件均出现王博士",
                "quote_a": "王博士，项目经理",
                "page_a": "3",
                "quote_b": "王博士，项目经理",
                "page_b": "5",
            },
        ],
        evidence_level="first",
        certainty=0.9,
    )
    assert len(model.findings) == 1
    assert model.findings[0]["summary"] == "发现关键人员重叠"


# ---------------------------------------------------------------------------
# 3. analyzer 兜底：_recover_flattened_findings
# ---------------------------------------------------------------------------


def test_recover_flattened_findings_merges_5_field_group() -> None:
    """5 个拍平字段合并为 1 条结构化 finding，类型转换正确"""
    findings, recovered = _recover_flattened_findings(FLATTENED_LIST_5_FIELDS)
    assert recovered is True
    assert len(findings) == 1
    f = findings[0]
    assert f["summary"].startswith("发现两份投标文件")
    assert f["detail"].startswith("在文档bid_document2.pdf")
    assert f["quote_a"] == "王博士，项目经理"
    assert f["page_a"] == 3  # 字符串被转为 int
    assert f["quote_b"] == "王博士，项目经理"


def test_recover_flattened_findings_splits_by_repeated_key() -> None:
    """同 key 重复出现 = 下一条 finding 起点"""
    raw = [
        "summary: 第一条",
        "detail: 详细 1",
        "summary: 第二条",
        "detail: 详细 2",
    ]
    findings, recovered = _recover_flattened_findings(raw)
    assert recovered is True
    assert len(findings) == 2
    assert findings[0]["summary"] == "第一条"
    assert findings[1]["summary"] == "第二条"


def test_recover_flattened_findings_passthrough_normal_input() -> None:
    """正常 dict list 行为不变"""
    payload = [
        {"summary": "S", "detail": "D"},
        {"summary": "S2", "detail": "D2"},
    ]
    findings, recovered = _recover_flattened_findings(payload)
    assert recovered is False
    assert findings == payload


def test_recover_flattened_findings_empty_input() -> None:
    findings, recovered = _recover_flattened_findings([])
    assert recovered is False
    assert findings == []


def test_recover_flattened_findings_full_width_colon() -> None:
    """中文全角冒号 '：' 同样支持"""
    raw = [
        "summary：发现一",
        "detail：详细一",
    ]
    findings, recovered = _recover_flattened_findings(raw)
    assert recovered is True
    assert len(findings) == 1
    assert findings[0]["summary"] == "发现一"


def test_recover_flattened_findings_similarity_float_conversion() -> None:
    raw = [
        "summary: 高度相似",
        "detail: 详情",
        "similarity: 0.92",
    ]
    findings, recovered = _recover_flattened_findings(raw)
    assert recovered is True
    assert len(findings) == 1
    assert findings[0]["similarity"] == pytest.approx(0.92)


# ---------------------------------------------------------------------------
# 4. 端到端：_extract_structured_findings 兜底 + quality_flag
# ---------------------------------------------------------------------------


def _build_compare_result_with_flattened_d1() -> dict:
    return {
        "pair_id": "1:2",
        "pair_ref": {
            "pair_id": "1:2",
            "doc_a_id": 1,
            "doc_b_id": 2,
            "doc_a_name": "bid1.pdf",
            "doc_b_name": "bid2.pdf",
        },
        "d1_personnel": {
            "score": 0.95,
            "evidence_level": "first",
            "certainty": 0.95,
            "findings": list(FLATTENED_LIST_5_FIELDS),
        },
    }


def test_extract_structured_findings_recovers_flattened_findings() -> None:
    """_extract_structured_findings 应把拍平 list 合并为 1 条结构化 finding"""
    cr = _build_compare_result_with_flattened_d1()
    out = _extract_structured_findings(cr, doc_a_id=1, doc_b_id=2)

    assert len(out) == 1, f"应输出 1 条合并 finding，实际 {len(out)} 条: {out}"
    f = out[0]
    assert f["dimension"] == "D1"
    assert f["description"].startswith("发现两份投标文件")
    assert f["quote_a"] == "王博士，项目经理"
    assert f["page_a"] == "3"  # _extract 内部把 page_a 标准化为 str

    pair_flags = cr.get("quality_flags") or []
    assert _FLAT_FINDING_RECOVERED_FLAG in pair_flags
    dim_flags = cr["d1_personnel"].get("_quality_flags") or []
    assert _FLAT_FINDING_RECOVERED_FLAG in dim_flags


def test_extract_structured_findings_normal_input_unchanged() -> None:
    """正常输入：不应误标 flat_finding_recovered"""
    cr = {
        "pair_id": "1:2",
        "pair_ref": {
            "doc_a_id": 1,
            "doc_b_id": 2,
            "doc_a_name": "bid1.pdf",
            "doc_b_name": "bid2.pdf",
        },
        "d1_personnel": {
            "score": 0.95,
            "evidence_level": "first",
            "certainty": 0.95,
            "findings": [
                {
                    "summary": "正常的人员重叠 finding",
                    "detail": "详情",
                    "quote_a": "王博士，项目经理",
                    "page_a": "3",
                    "quote_b": "王博士，项目经理",
                    "page_b": "5",
                },
            ],
        },
    }
    out = _extract_structured_findings(cr, doc_a_id=1, doc_b_id=2)
    assert len(out) == 1
    assert _FLAT_FINDING_RECOVERED_FLAG not in (cr.get("quality_flags") or [])
    assert _FLAT_FINDING_RECOVERED_FLAG not in (
        cr["d1_personnel"].get("_quality_flags") or []
    )
