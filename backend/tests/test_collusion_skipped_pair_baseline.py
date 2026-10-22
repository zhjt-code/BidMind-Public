"""C1 防御测试：智能筛对跳过的文件对算法基线对比。

职责: 验证 algorithmic.build_skipped_pair_baseline 在 D1/D2/D4/D8 各维度
      命中时返回带 findings 的结构，未命中或提取缺失时返回 None；防止"被
      智能筛对跳过 = 完全不分析"这一类一级证据漏检的回归。
不做: 真实 DB 落库、analyzer 全流程、向量计算。

历史教训映射:
    Golden Rule 4 "宁漏报三级、不错判一级"。原 save_skipped_pairs 把跳过对
    一律标 inconclusive，连零成本的结构化字段比对都不跑。本测试守护算法
    基线最低保障，防止后续重构再次回到"跳过即漏检"的状态。
"""

from __future__ import annotations

from app.core.collusion.analyzer_parts.algorithmic import (
    build_skipped_pair_baseline,
)
from tests.synthetic_data import synthetic_text

# ---------------------------------------------------------------------------
# 1. 提取缺失边界
# ---------------------------------------------------------------------------


def test_baseline_returns_none_when_either_side_empty() -> None:
    """空提取/None 时应返回 None，由调用方维持 inconclusive 占位。"""
    assert build_skipped_pair_baseline({}, {"company_info": {}}) is None
    assert build_skipped_pair_baseline({"company_info": {}}, {}) is None
    assert build_skipped_pair_baseline(None, {"company_info": {}}) is None  # type: ignore[arg-type]


def test_baseline_returns_none_for_completely_unrelated_docs() -> None:
    """两份完全不相关的文件应返回 None（无任一维度命中）。"""
    info_a = {
        "company_info": {
            "company_name": "<ORG_22>",
            "legal_representative": '<PERSON_A>',
            "contact_phone": synthetic_text('010-', '1111', '1111'),
            "unified_social_credit_code": synthetic_text('911100', '00AAAA', 'AAAA1A'),
            "registered_address": "北京市朝阳区A路1号",
        },
        "personnel_list": [{"name": '<PERSON_B>', "role": "项目经理"}],
        "price_info": {"total_price": "1000000"},
        "metadata_info": {"creator_tool": "WPS Office", "author": "user_a"},
    }
    info_b = {
        "company_info": {
            "company_name": "<ORG_5>",
            "legal_representative": "<PERSON_3>",
            "contact_phone": synthetic_text('021-', '2222', '2222'),
            # 不同行政区划前缀，避免被 is_credit_code_related 当作同辖区关联
            "unified_social_credit_code": synthetic_text('923300', '00BBBB', 'BBBB2B'),
            "registered_address": "上海市浦东新区B路2号",
        },
        "personnel_list": [{"name": '<PERSON_E>', "role": "技术负责人"}],
        "price_info": {"total_price": "2500000"},
        "metadata_info": {"creator_tool": "Microsoft Word", "author": "user_b"},
    }
    result = build_skipped_pair_baseline(info_a, info_b, "甲", "乙")
    # 注：WPS / Microsoft Word 命中通用工具白名单会被折扣为弱信号，且双方不同名
    # 故 D8 不会命中
    assert result is None


# ---------------------------------------------------------------------------
# 2. D2 命中（法定代表人相同 → 高风险一级证据）
# ---------------------------------------------------------------------------


def test_baseline_hits_when_legal_representative_identical() -> None:
    info_a = {
        "company_info": {
            "company_name": "<ORG_22>",
            "legal_representative": '<PERSON_A>',
        },
        "personnel_list": [],
        "metadata_info": {},
    }
    info_b = {
        "company_info": {
            "company_name": "<ORG_4>",
            "legal_representative": '<PERSON_A>',
        },
        "personnel_list": [],
        "metadata_info": {},
    }
    result = build_skipped_pair_baseline(info_a, info_b, "甲", "乙")
    assert result is not None
    assert result.get("overall_risk") == "high"
    d2 = result.get("d2_company")
    assert isinstance(d2, dict)
    assert d2.get("score", 0) >= 0.7
    findings_text = " ".join(
        f.get("summary", "") for f in d2.get("findings", [])
    )
    assert "法" in findings_text or "代表人" in findings_text


# ---------------------------------------------------------------------------
# 3. D1 命中（人员证书重叠）
# ---------------------------------------------------------------------------


def test_baseline_hits_when_personnel_certificate_overlap() -> None:
    info_a = {
        "company_info": {"company_name": "甲公司"},
        "personnel_list": [
            {
                "name": "<PERSON_H>",
                "role": "项目经理",
                "certificate_numbers": ["GC-2023-001"],
            },
        ],
        "metadata_info": {},
    }
    info_b = {
        "company_info": {"company_name": "乙公司"},
        "personnel_list": [
            {
                "name": "<PERSON_I>",
                "role": "项目经理",
                "certificate_numbers": ["GC-2023-001"],
            },
        ],
        "metadata_info": {},
    }
    result = build_skipped_pair_baseline(info_a, info_b)
    assert result is not None
    d1 = result.get("d1_personnel")
    assert isinstance(d1, dict)
    assert d1.get("score", 0) >= 0.5
    # 高一级证据→ overall_risk 至少应为 high
    assert result.get("overall_risk") == "high"


# ---------------------------------------------------------------------------
# 4. D4 命中（报价完全相同）
# ---------------------------------------------------------------------------


def test_baseline_hits_when_total_price_identical() -> None:
    info_a = {
        "company_info": {"company_name": "甲"},
        "personnel_list": [],
        "price_info": {"total_price": "5000000"},
        "metadata_info": {},
    }
    info_b = {
        "company_info": {"company_name": "乙"},
        "personnel_list": [],
        "price_info": {"total_price": "5000000"},
        "metadata_info": {},
    }
    result = build_skipped_pair_baseline(info_a, info_b)
    assert result is not None
    d4 = result.get("d4_price")
    assert isinstance(d4, dict)
    assert d4.get("score", 0) >= 0.7


def test_baseline_misses_when_only_meaningless_tail_zeros() -> None:
    """尾数为 00 的整数报价非常常见（百万级整数），不应单独触发 D4。"""
    info_a = {
        "company_info": {"company_name": "甲"},
        "personnel_list": [],
        "price_info": {"total_price": "1000000"},  # 末两位 00
        "metadata_info": {},
    }
    info_b = {
        "company_info": {"company_name": "乙"},
        "personnel_list": [],
        "price_info": {"total_price": "9000000"},  # 末两位 00
        "metadata_info": {},
    }
    result = build_skipped_pair_baseline(info_a, info_b)
    # 比例 1/9 ≈ 0.111，不在 [0.85,1.0) 区间；尾数 00 被排除
    # → 应无任何维度命中
    assert result is None


# ---------------------------------------------------------------------------
# 5. D8 命中（PDF 元数据完全一致且非通用工具）
# ---------------------------------------------------------------------------


def test_baseline_hits_when_pdf_metadata_specific_author_matches() -> None:
    info_a = {
        "company_info": {"company_name": "甲"},
        "personnel_list": [],
        "metadata_info": {
            "creator_tool": "BidMaker Pro 2024",  # 非通用工具
            "author": "zhanghua_company_x",
        },
    }
    info_b = {
        "company_info": {"company_name": "乙"},
        "personnel_list": [],
        "metadata_info": {
            "creator_tool": "BidMaker Pro 2024",
            "author": "zhanghua_company_x",
        },
    }
    result = build_skipped_pair_baseline(info_a, info_b)
    assert result is not None
    d8 = result.get("d8_metadata")
    assert isinstance(d8, dict)
    assert (d8.get("score") or 0) > 0


# ---------------------------------------------------------------------------
# 6. 返回结果 schema 形状保持与 _algorithm_compare 一致
# ---------------------------------------------------------------------------


def test_baseline_result_preserves_dimension_keys() -> None:
    """命中时返回的 dict 应保留 d1_personnel/d2_company/d4_price/d8_metadata
    四个键（即便维度未命中也保留 None/payload 占位，下游 normalize 依赖此约定）。
    """
    info_a = {
        "company_info": {"company_name": "甲", "legal_representative": "同名"},
        "personnel_list": [],
        "metadata_info": {},
    }
    info_b = {
        "company_info": {"company_name": "乙", "legal_representative": "同名"},
        "personnel_list": [],
        "metadata_info": {},
    }
    result = build_skipped_pair_baseline(info_a, info_b)
    assert result is not None
    for key in ("d1_personnel", "d2_company", "d4_price", "d8_metadata"):
        assert key in result
