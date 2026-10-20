"""反向定性陈述识别器单元测试"""

from app.core.collusion.finding_polarity import (
    assess_finding_polarity,
    is_negative_finding,
)


# ── 真阳性：必须命中 ─────────────────────────────────────────────────────────

def test_negative_explicit_no_collusion_pattern() -> None:
    """问题图中典型反向陈述 -- 必须命中"""
    text = (
        "两份投标文件的技术方案核心内容不同：文件A侧重地应力场分布模型测试平台和"
        "三维岩石力学测试平台，文件B侧重抽气集输用新型低碳低铬系耐蚀管线钢的开发。"
        "技术方案无实质性雷同。"
    )
    is_neg, pattern = is_negative_finding(text)
    assert is_neg is True
    assert pattern is not None


def test_negative_whitelist_template_pattern() -> None:
    """问题图中典型反向陈述 -- 已命中白名单不构成串标证据"""
    text = (
        "高相似度段落主要集中在投标函、保证金缴纳证明等通用格式章节，"
        "属于招标文件模板或行业惯例内容，已命中白名单，不构成串标证据。"
    )
    is_neg, pattern = is_negative_finding(text)
    assert is_neg is True
    assert pattern is not None


def test_negative_no_substantial_match() -> None:
    text = "两份文件无实质性雷同"
    is_neg, _ = is_negative_finding(text)
    assert is_neg is True


def test_negative_template_phrase() -> None:
    text = "上述内容属于招标文件模板，所有投标人均需遵守。"
    is_neg, _ = is_negative_finding(text)
    assert is_neg is True


def test_negative_industry_practice() -> None:
    text = "评估认为相似源于行业惯例，certainty 应当较低。"
    is_neg, _ = is_negative_finding(text)
    assert is_neg is True


def test_negative_undiscovered_pattern() -> None:
    text = "未发现关键人员重叠，未发现可疑串标线索。"
    is_neg, _ = is_negative_finding(text)
    assert is_neg is True


def test_negative_does_not_constitute() -> None:
    text = "该段落不构成串通投标证据，仅作参考。"
    is_neg, _ = is_negative_finding(text)
    assert is_neg is True


def test_negative_whitelist_coverage() -> None:
    text = "高相似段落已被白名单覆盖。"
    is_neg, _ = is_negative_finding(text)
    assert is_neg is True


# ── 真阴性：不应命中 ─────────────────────────────────────────────────────────

def test_positive_personnel_overlap_should_not_be_negative() -> None:
    text = '项目经理<PERSON_A>同时出现在两份投标文件中，身份证号末四位一致。'
    is_neg, _ = is_negative_finding(text)
    assert is_neg is False


def test_positive_company_relation_should_not_be_negative() -> None:
    text = "两家投标公司的法定代表人均为<PERSON_3>，疑似关联企业。"
    is_neg, _ = is_negative_finding(text)
    assert is_neg is False


def test_positive_text_similarity_should_not_be_negative() -> None:
    text = "技术方案中『冷却水循环系统设计』段落原文一致，含相同错别字『冷确水』。"
    is_neg, _ = is_negative_finding(text)
    assert is_neg is False


def test_positive_pricing_pattern_should_not_be_negative() -> None:
    text = "三家投标的总价呈等差分布：100万、105万、110万，差值精确一致。"
    is_neg, _ = is_negative_finding(text)
    assert is_neg is False


def test_positive_metadata_cluster_should_not_be_negative() -> None:
    text = "PDF元数据 author 字段均为『<PERSON_D>』，且创建时间相差 3 分钟。"
    is_neg, _ = is_negative_finding(text)
    assert is_neg is False


# ── 边界情况 ────────────────────────────────────────────────────────────────

def test_empty_text_not_negative() -> None:
    is_neg, pattern = is_negative_finding("")
    assert is_neg is False
    assert pattern is None
    is_neg, _ = is_negative_finding(None)
    assert is_neg is False


def test_assess_finding_polarity_dict_input() -> None:
    finding = {
        "summary": "两份投标文件的技术方案核心内容不同",
        "detail": "文件A 侧重 X，文件B 侧重 Y",
    }
    is_neg, pattern = assess_finding_polarity(finding)
    assert is_neg is True
    assert pattern is not None


def test_assess_finding_polarity_non_dict() -> None:
    is_neg, pattern = assess_finding_polarity("not a dict")  # type: ignore
    assert is_neg is False
    assert pattern is None
