"""防御测试： 串标证据级归错修复

锁定本次修复的 5 个核心回归点，覆盖三层防御：
 1. Prompt 收紧（D1/D2 evidence_level 限定 first） — 由 schemas + analyzer 强制矫正实现
 2. 写入端按维度强制矫正 _normalize_dim_evidence_levels
 3. _derive_overall_risk_from_dims 弱一级兜底通道
 4. Schema model_validator 按维度强制 evidence_level
 5. _algorithm_compare 通路 D1/D2 evidence_level 维持 "first"（回归保护）
"""

from __future__ import annotations
from tests.synthetic_data import synthetic_text

import pytest

from app.core.collusion.analyzer import (
    _algorithm_compare,
    _consistency_check,
    _derive_overall_risk_from_dims,
    _extract_structured_findings,
    _normalize_dim_evidence_levels,
)
from app.schemas.collusion import (
    DIMENSION_EVIDENCE_LEVEL_CONTRACT,
    EVIDENCE_LEVEL_CORRECTED_FLAG,
    LLMCompareResult,
    LLMEntityResult,
    LLMTextPatternResult,
    LLMBusinessResult,
    coerce_evidence_level_for_dim,
)


# ---------------------------------------------------------------------------
# T1. D1 强一级（score=0.6, cert=0.8, level="first"）→ HIGH
# ---------------------------------------------------------------------------


def test_d1_strong_first_triggers_high():
    compare_result = {
        "d1_personnel": {
            "score": 0.6,
            "certainty": 0.8,
            "evidence_level": "first",
            "findings": [{"summary": "证书编号重叠"}],
        },
        "d2_company": None,
        "d3_text": None,
        "d4_price": None,
        "d5_qualification": None,
        "d6_format": None,
        "d7_abnormal": None,
        "d8_metadata": None,
    }
    result = _derive_overall_risk_from_dims(compare_result)
    assert result["overall_risk"] == "high"
    assert "直接定性证据" in result["risk_explanation"]


# ---------------------------------------------------------------------------
# T2. D1 弱一级（score=0.6, cert=0.6, level="first"）→ MEDIUM（验证 R1 修复）
# ---------------------------------------------------------------------------


def test_d1_weak_first_triggers_medium_not_dropped():
    """ 核心断言：D1 弱命中（cert<0.7）必须降级到 MEDIUM，而不是被静默丢弃。"""
    compare_result = {
        "d1_personnel": {
            "score": 0.6,
            "certainty": 0.6,
            "evidence_level": "first",
            "findings": [{"summary": "同名重叠但证号未交叉验证"}],
        },
        "d2_company": None,
        "d3_text": None,
        "d4_price": None,
        "d5_qualification": None,
        "d6_format": None,
        "d7_abnormal": None,
        "d8_metadata": None,
    }
    result = _derive_overall_risk_from_dims(compare_result)
    assert result["overall_risk"] == "medium"
    assert "弱一级" in result["risk_explanation"]


def test_d1_very_weak_first_triggers_low():
    """D1 score 不足以进入 medium 阈值（>0.3+cert>=0.4）但仍命中 LOW 兜底。"""
    compare_result = {
        "d1_personnel": {
            "score": 0.2,
            "certainty": 0.5,
            "evidence_level": "first",
            "findings": [{"summary": "极弱命中"}],
        },
        "d2_company": None,
        "d3_text": None,
        "d4_price": None,
        "d5_qualification": None,
        "d6_format": None,
        "d7_abnormal": None,
        "d8_metadata": None,
    }
    result = _derive_overall_risk_from_dims(compare_result)
    assert result["overall_risk"] == "low"


def test_d1_below_certainty_min_falls_to_none():
    """certainty < _CERTAINTY_MIN(0.3) 时 D1 不进入弱一级集合，回到 NONE/INCONCLUSIVE 语义。"""
    compare_result = {
        "d1_personnel": {
            "score": 0.6,
            "certainty": 0.2,
            "evidence_level": "first",
            "findings": [],
        },
        "d2_company": None,
        "d3_text": None,
        "d4_price": None,
        "d5_qualification": None,
        "d6_format": None,
        "d7_abnormal": None,
        "d8_metadata": None,
    }
    result = _derive_overall_risk_from_dims(compare_result)
    assert result["overall_risk"] == "none"


# ---------------------------------------------------------------------------
# T3. LLM 错填 D1 evidence_level="third" → 矫正后落库 level=="first"
# ---------------------------------------------------------------------------


def test_normalize_corrects_d1_third_to_first():
    """验证 R3+R4 修复：LLM 错填 D1 evidence_level=third 也会被强制矫正为 first。"""
    compare_result = {
        "d1_personnel": {
            "score": 0.7,
            "certainty": 0.8,
            "evidence_level": "third",
            "findings": [{"summary": '项目经理<PERSON_C>跨标出现', "quote_a": '<PERSON_C> 项目经理', "quote_b": '<PERSON_C> 项目经理'}],
        },
        "d2_company": {
            "score": 0.0,
            "certainty": 0.0,
            "evidence_level": "second",
            "findings": [],
        },
        "d3_text": None,
        "d4_price": None,
        "d5_qualification": None,
        "d6_format": None,
        "d7_abnormal": None,
        "d8_metadata": None,
    }
    _normalize_dim_evidence_levels(compare_result)
    assert compare_result["d1_personnel"]["evidence_level"] == "first"
    assert compare_result["d2_company"]["evidence_level"] == "first"
    flags_d1 = compare_result["d1_personnel"]["_quality_flags"]
    flags_d2 = compare_result["d2_company"]["_quality_flags"]
    assert EVIDENCE_LEVEL_CORRECTED_FLAG in flags_d1
    assert EVIDENCE_LEVEL_CORRECTED_FLAG in flags_d2


def test_extract_structured_findings_uses_corrected_level():
    """端到端：错填 evidence_level 经 _consistency_check 矫正后，
    finding.level 落库为契约级别。"""
    compare_result = {
        "d1_personnel": {
            "score": 0.7,
            "certainty": 0.8,
            "evidence_level": "third",   # LLM 错填
            "findings": [
                {
                    "summary": '项目经理<PERSON_C>跨标出现',
                    "detail": "doc1 第3页 / doc2 第5页",
                    "quote_a": '<PERSON_C> 项目经理',
                    "page_a": "第3页",
                    "quote_b": '<PERSON_C> 项目经理',
                    "page_b": "第5页",
                }
            ],
        },
        "d2_company": None,
        "d3_text": None,
        "d4_price": None,
        "d5_qualification": None,
        "d6_format": None,
        "d7_abnormal": None,
        "d8_metadata": None,
    }
    # 走完整路径：先经 _consistency_check 矫正，再 _extract_structured_findings
    _consistency_check(compare_result, similarity_score=0.5)
    findings = _extract_structured_findings(compare_result, doc_a_id=1, doc_b_id=2)
    d1_findings = [f for f in findings if f["dimension"] == "D1"]
    assert d1_findings, "D1 finding 必须存在"
    assert all(f["level"] == "first" for f in d1_findings), \
        f"D1 finding.level 必须落库为 'first'，实际：{[f['level'] for f in d1_findings]}"


# ---------------------------------------------------------------------------
# T4. LLM 错填 D3 evidence_level="first" → 矫正为 second（同类风险）
# ---------------------------------------------------------------------------


def test_normalize_corrects_d3_first_to_second():
    """LLM 错填 D3 evidence_level=first 时，矫正为契约允许的 'second'。"""
    compare_result = {
        "d1_personnel": None,
        "d2_company": None,
        "d3_text": {
            "score": 0.5,
            "certainty": 0.6,
            "evidence_level": "first",  # 错填
            "findings": [{"summary": "技术方案大段雷同"}],
        },
        "d4_price": None,
        "d5_qualification": None,
        "d6_format": None,
        "d7_abnormal": None,
        "d8_metadata": None,
    }
    _normalize_dim_evidence_levels(compare_result)
    assert compare_result["d3_text"]["evidence_level"] == "second"
    assert EVIDENCE_LEVEL_CORRECTED_FLAG in compare_result["d3_text"]["_quality_flags"]


def test_normalize_keeps_d3_legal_third():
    """LLM 输出 D3 evidence_level=third（行业通用重叠）属合法值，不应被矫正。"""
    compare_result = {
        "d1_personnel": None,
        "d2_company": None,
        "d3_text": {
            "score": 0.4,
            "certainty": 0.2,
            "evidence_level": "third",
            "findings": [],
        },
        "d4_price": None,
        "d5_qualification": None,
        "d6_format": None,
        "d7_abnormal": None,
        "d8_metadata": None,
    }
    _normalize_dim_evidence_levels(compare_result)
    assert compare_result["d3_text"]["evidence_level"] == "third"
    assert "_quality_flags" not in compare_result["d3_text"]


# ---------------------------------------------------------------------------
# T5. _algorithm_compare 通路 D1/D2 evidence_level 维持 "first"（回归保护）
# ---------------------------------------------------------------------------


def test_algorithm_compare_d1_d2_evidence_level_first():
    """低相似度通路兜底：算法对比的 D1/D2 必须保持 evidence_level='first'。"""
    info_a = {
        "personnel_list": [
            {
                "name": '<PERSON_A>',
                "role": "项目经理",
                "id_number": synthetic_text('110101', '199001', '011234'),
                "certificate_numbers": ["GCS-2020-001"],
            }
        ],
        "company_info": {
            "company_name": "<ORG_23>",
            "legal_representative": '<PERSON_B>',
            "unified_social_credit_code": synthetic_text('911101', '05MA00', 'ABCD12'),
            "contact_phone": synthetic_text('010-', '1234', '5678'),
            "registered_address": "北京市朝阳区某街1号",
        },
    }
    info_b = {
        "personnel_list": [
            {
                "name": '<PERSON_A>',
                "role": "技术负责人",
                "id_number": synthetic_text('110101', '199001', '011234'),
                "certificate_numbers": ["GCS-2020-001"],
            }
        ],
        "company_info": {
            "company_name": "<ORG_3>",
            "legal_representative": '<PERSON_B>',
            "unified_social_credit_code": synthetic_text('911101', '06MA00', 'WXYZ34'),
            "contact_phone": synthetic_text('010-', '1234', '5678'),
            "registered_address": "北京市朝阳区某街1号",
        },
    }
    result = _algorithm_compare(info_a, info_b, "公司A", "公司B")
    assert result["d1_personnel"]["evidence_level"] == "first"
    assert result["d2_company"]["evidence_level"] == "first"


# ---------------------------------------------------------------------------
# 额外：契约 helper 单元
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dim_key,current,expected_level,expected_corrected",
    [
        ("d1_personnel", "first", "first", False),
        ("d1_personnel", "second", "first", True),
        ("d1_personnel", "third", "first", True),
        ("d1_personnel", None, "first", True),
        ("d2_company", "second", "first", True),
        ("d3_text", "second", "second", False),
        ("d3_text", "third", "third", False),
        ("d3_text", "first", "second", True),
        ("d3_text", "unknown", "second", True),
        ("d6_format", "third", "third", False),
        ("d6_format", "first", "third", True),
        ("d8_metadata", "second", "third", True),
    ],
)
def test_coerce_evidence_level_contract(
    dim_key, current, expected_level, expected_corrected,
):
    target, corrected = coerce_evidence_level_for_dim(dim_key, current)
    assert target == expected_level
    assert corrected is expected_corrected


# ---------------------------------------------------------------------------
# 额外：Schema model_validator 兜底
# ---------------------------------------------------------------------------


def test_schema_entity_result_enforces_d1_first():
    """LLM 输出 LLMEntityResult 时，model_validator 自动矫正 d1/d2 为 first。"""
    raw = {
        "d1_personnel": {
            "score": 0.7,
            "findings": [{"summary": "人员重叠"}],
            "evidence_level": "third",
            "certainty": 0.8,
        },
        "d2_company": {
            "score": 0.6,
            "findings": [],
            "evidence_level": "second",
            "certainty": 0.7,
        },
        "risk_explanation": "test",
    }
    parsed = LLMEntityResult(**raw)
    assert parsed.d1_personnel.evidence_level == "first"
    assert parsed.d2_company.evidence_level == "first"


def test_schema_text_pattern_result_enforces_d6_third():
    """LLMTextPatternResult: d6/d7 必须强制为 third。"""
    raw = {
        "d3_text": {
            "score": 0.5,
            "findings": [],
            "evidence_level": "second",
            "certainty": 0.5,
        },
        "d6_format": {
            "score": 0.4,
            "findings": [],
            "evidence_level": "first",  # 错填
            "certainty": 0.5,
        },
        "d7_abnormal": {
            "score": 0.3,
            "findings": [],
            "evidence_level": "second",  # 错填
            "certainty": 0.4,
        },
    }
    parsed = LLMTextPatternResult(**raw)
    assert parsed.d3_text.evidence_level == "second"
    assert parsed.d6_format.evidence_level == "third"
    assert parsed.d7_abnormal.evidence_level == "third"


def test_schema_business_result_enforces_d4_d5_contract():
    """LLMBusinessResult: d4/d5 限制在 second/third 内，错填 first 矫正为 second。"""
    raw = {
        "d4_price": {
            "score": 0.7,
            "findings": [],
            "evidence_level": "first",  # 错填
            "certainty": 0.6,
        },
        "d5_qualification": {
            "score": 0.4,
            "findings": [],
            "evidence_level": "third",  # 合法
            "certainty": 0.3,
        },
    }
    parsed = LLMBusinessResult(**raw)
    assert parsed.d4_price.evidence_level == "second"
    assert parsed.d5_qualification.evidence_level == "third"
