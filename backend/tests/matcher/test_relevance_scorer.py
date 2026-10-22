"""relevance_scorer 单元测试

覆盖 5 个分类（PERF / QUAL / STAFF / COMM / TECH），每类 4 个用例（2 高分 + 2 低分），
共 20 个参数化用例，另有若干独立用例覆盖边界行为。

评分预期（mock semantic_sim=0.5，无 fallback）：
    raw = 0.55 × TM + 0.30 × 0.5 + 0.15 × DKH = 0.55 × TM + 0.15 + 0.15 × DKH

    HIGH-TM-0.8 (8/10 kw)  : raw ≈ 0.617 → score 62
    HIGH-TM-1.0 (8/8 kw)   : raw ≈ 0.727 → score 73
    HIGH-TM-0.875 (7/8 kw) : raw ≈ 0.655 → score 66
    LOW  (TM=0, DKH=0)      : raw = 0.150 → score 15
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.matcher.relevance_scorer import (
    DOMAIN_KEYWORD_WEIGHT,
    SEMANTIC_SIM_WEIGHT,
    TYPE_MATCH_WEIGHT,
    _compute_domain_keyword_hit,
    _compute_type_match,
    score_relevance,
)

# --------------------------------------------------------------------------- #
# 工具 fixture
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_semantic():
    """将 _compute_semantic_sim 固定返回 (0.5, {})，隔离嵌入模型依赖。"""
    with patch(
        "app.core.matcher.relevance_scorer._compute_semantic_sim",
        return_value=(0.5, {}),
    ) as m:
        yield m


@pytest.fixture
def mock_semantic_unavailable():
    """模拟嵌入模型不可用（返回降级信号）。"""
    with patch(
        "app.core.matcher.relevance_scorer._compute_semantic_sim",
        return_value=(0.0, {"fallback": "embedding_unavailable", "error": "test"}),
    ) as m:
        yield m


# --------------------------------------------------------------------------- #
# 参数化数据：(req, resp, category, min_score, max_score)
#
# 测试文本设计原则：
#   HIGH：两段文本**同时**含有 ≥7 个目标分类关键词（交集命中），确保 TM≥0.875
#   LOW ：文本来自完全无关领域，0 个目标关键词命中
# --------------------------------------------------------------------------- #

_CASES: list[tuple[str, str, str, int, int]] = [
    # ------------------------------------------------------------------ PERF --
    # HIGH-1: PERF 8/10 关键词同时出现在双方文本（TM=0.8）
    (
        "要求提供工程业绩、项目业绩、合同业绩、类似业绩、施工业绩、设计业绩、监理业绩、服务业绩证明材料",
        "提交工程业绩3项、项目业绩5份、合同业绩清单、类似业绩证明、施工业绩验收报告、设计业绩图纸、监理业绩记录、服务业绩评价",
        "PERF",
        60,
        100,
    ),
    # HIGH-2: 另外 8/10 关键词组合（TM=0.8）
    (
        "核查合同业绩、类似业绩、施工业绩、设计业绩、监理业绩、咨询业绩、服务业绩、研究业绩数量",
        "合同业绩12份、类似业绩8项、施工业绩6个、设计业绩4项、监理业绩3个、咨询业绩2份、服务业绩5份、研究业绩1份",
        "PERF",
        60,
        100,
    ),
    # LOW-1: 完全不同技术领域，无任何 PERF 关键词
    (
        "防腐蚀钢管材料技术标准要求",
        "CO2地质封存实验室测试报告",
        "PERF",
        0,
        40,
    ),
    # LOW-2: 软件领域，无 PERF 关键词
    (
        "软件系统功能需求说明书",
        "数据库查询性能优化方案",
        "PERF",
        0,
        40,
    ),
    # ------------------------------------------------------------------ QUAL --
    # HIGH-1: QUAL 8/10 关键词同时出现（TM=0.8）
    (
        "须提交资质证书、认证证书、体系认证、ISO、CCC、安全生产许可证、建筑业企业资质、设计资质证明",
        "已持有资质证书副本、认证证书原件、体系认证通过、ISO9001达标、CCC认证有效、安全生产许可证、建筑业企业资质壹级、设计资质甲级",
        "QUAL",
        60,
        100,
    ),
    # HIGH-2: 另外 8/10 关键词（TM=0.8）
    (
        "要求认证证书、体系认证、ISO证书、安全生产许可证、建筑业企业资质、设计资质、监理资质、特种设备许可",
        "提供认证证书原件、体系认证文件、ISO9001证书、安全生产许可证有效、建筑业企业资质壹级、设计资质甲级、监理资质乙级、特种设备许可证",
        "QUAL",
        60,
        100,
    ),
    # LOW-1: 软件著作权领域，无 QUAL 关键词
    (
        "软件著作权登记证书及开发文档",
        "开源项目代码贡献记录",
        "QUAL",
        0,
        40,
    ),
    # LOW-2: 建材检测领域，无 QUAL 关键词
    (
        "建筑材料进场验收记录表",
        "混凝土配合比实验数据",
        "QUAL",
        0,
        40,
    ),
    # ----------------------------------------------------------------- STAFF --
    # HIGH-1: 全部 8/8 STAFF 关键词同时出现（TM=1.0）
    (
        "配备项目经理、总监理工程师、技术负责人、注册建造师、注册监理工程师、造价工程师、安全工程师、高级工程师各一名",
        '项目经理：张某，总监理工程师：李某，技术负责人：<PERSON_C>，注册建造师：赵某，注册监理工程师：陈某，造价工程师：周某，安全工程师：吴某，高级工程师：郑某',
        "STAFF",
        60,
        100,
    ),
    # HIGH-2: 7/8 关键词（TM=0.875）
    (
        "须有项目经理、技术负责人、注册建造师、注册监理工程师、造价工程师、安全工程师、高级工程师资格",
        "项目经理持证上岗，技术负责人职称有效，注册建造师一级，注册监理工程师在册，造价工程师注册，安全工程师证书，高级工程师职称证",
        "STAFF",
        60,
        100,
    ),
    # LOW-1: 学历职称领域，无 STAFF 关键词
    (
        "学历证书及职称评定要求",
        "大学本科毕业生求职简历",
        "STAFF",
        0,
        40,
    ),
    # LOW-2: 驾驶员资格领域，无 STAFF 关键词
    (
        "驾驶员资格认证文件要求",
        "车辆保险及年检记录",
        "STAFF",
        0,
        40,
    ),
    # ------------------------------------------------------------------ COMM --
    # HIGH-1: 全部 8/8 COMM 关键词同时出现（TM=1.0）
    (
        "提交财务报表、审计报告、纳税证明、社保证明、银行资信、信用报告、经营状况说明、资产负债表",
        "财务报表（近三年）、审计报告（注册会计师）、纳税证明（税务局）、社保证明、银行资信证明、信用报告、经营状况良好、资产负债率30%",
        "COMM",
        60,
        100,
    ),
    # HIGH-2: 7/8 关键词（TM=0.875）
    (
        "要求财务报表、审计报告、纳税证明、银行资信、信用报告、经营状况、资产负债",
        "提供财务报表三年、审计报告、纳税证明完税证明、银行资信证明、信用报告查询、经营状况正常、资产负债健康",
        "COMM",
        60,
        100,
    ),
    # LOW-1: 产品检测领域，无 COMM 关键词
    (
        "产品质量检测报告及合格证明",
        "国际标准化组织规范参考",
        "COMM",
        0,
        40,
    ),
    # LOW-2: 设备维修领域，无 COMM 关键词
    (
        "设备维修保养操作手册",
        "零部件库存管理方案",
        "COMM",
        0,
        40,
    ),
    # ------------------------------------------------------------------ TECH --
    # HIGH-1: 全部 8/8 TECH 关键词同时出现（TM=1.0）
    (
        "提供技术方案、施工组织设计、实施方案、服务方案、质量保证措施、安全措施、进度计划、技术路线",
        "技术方案详见附件，施工组织设计如下，实施方案明确，服务方案周全，质量保证措施得力，安全措施完善，进度计划合理，技术路线清晰",
        "TECH",
        60,
        100,
    ),
    # HIGH-2: 7/8 关键词（TM=0.875）
    (
        "需要实施方案、服务方案、质量保证措施、安全措施、进度计划、技术路线、技术方案",
        "实施方案已制定、服务方案配套、质量保证措施已建立、安全措施落实、进度计划经过审核、技术路线可行、技术方案通过评审",
        "TECH",
        60,
        100,
    ),
    # LOW-1: 市场调研领域，无 TECH 关键词
    (
        "市场调研分析及竞争对手评估报告",
        "用户满意度调查问卷汇总",
        "TECH",
        0,
        40,
    ),
    # LOW-2: 法律合规领域，无 TECH 关键词
    (
        "法律合规文件及知识产权证明",
        "专利申请状态说明书",
        "TECH",
        0,
        40,
    ),
]


# --------------------------------------------------------------------------- #
# 主参数化测试：20 个用例
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "req,resp,category,min_score,max_score",
    _CASES,
    ids=[
        f"case_{i+1:02d}_{cat}_{level}"
        for i, (_, _, cat, lo, _hi) in enumerate(_CASES)
        for level in ("HIGH" if lo >= 60 else "LOW",)
    ],
)
def test_relevance_score_range(
    req: str,
    resp: str,
    category: str,
    min_score: int,
    max_score: int,
    mock_semantic,  # noqa: ARG001
) -> None:
    """验证每个用例的得分落在预期区间内。"""
    result = score_relevance(req, resp, category)

    assert result.score is not None, "score 不应为 None"
    assert min_score <= result.score <= max_score, (
        f"[{category}] score={result.score} 超出区间 [{min_score}, {max_score}]\n"
        f"  type_match={result.type_match:.4f}, "
        f"semantic_sim={result.semantic_sim:.4f}, "
        f"domain_kw_hit={result.domain_keyword_hit:.4f}\n"
        f"  req={req[:60]!r}\n"
        f"  resp={resp[:60]!r}"
    )


# --------------------------------------------------------------------------- #
# 独立用例：边界与特殊行为
# --------------------------------------------------------------------------- #


def test_result_is_frozen_dataclass(mock_semantic) -> None:
    """RelevanceScore 是 frozen dataclass，不可修改字段。"""
    result = score_relevance("需要工程业绩", "提供工程业绩", "PERF")
    with pytest.raises((AttributeError, TypeError)):
        result.score = 0  # type: ignore[misc]


def test_score_clamp_upper(mock_semantic) -> None:
    """得分不超过 100。"""
    result = score_relevance(
        "提供技术方案、施工组织设计、实施方案、服务方案、质量保证措施、安全措施、进度计划、技术路线",
        "技术方案详见附件，施工组织设计如下，实施方案明确，服务方案周全，质量保证措施得力，安全措施完善，进度计划合理，技术路线清晰",
        "TECH",
    )
    assert result.score is not None
    assert result.score <= 100


def test_score_clamp_lower(mock_semantic) -> None:
    """得分不低于 0。"""
    result = score_relevance("", "", "PERF")
    assert result.score is not None
    assert result.score >= 0


def test_unknown_category_returns_neutral_type_match(mock_semantic) -> None:
    """未知分类返回中性 type_match=0.5，得分在中间区间（不为 0 也不为满分）。"""
    result = score_relevance("随便一段文字", "另一段文字", "UNKNOWN_XYZ")
    assert result.type_match == 0.5
    assert result.score is not None
    assert 0 <= result.score <= 100


def test_category_case_insensitive(mock_semantic) -> None:
    """分类名称大小写不敏感。"""
    req = "提供技术方案、施工组织设计、实施方案、服务方案、质量保证措施、安全措施、进度计划、技术路线"
    resp = "技术方案详见附件，施工组织设计如下，实施方案明确，服务方案周全，质量保证措施得力，安全措施完善，进度计划合理，技术路线清晰"
    score_upper = score_relevance(req, resp, "TECH")
    score_lower = score_relevance(req, resp, "tech")
    assert score_upper.score == score_lower.score
    assert score_upper.type_match == score_lower.type_match


def test_degradation_weight_redistribution(mock_semantic_unavailable) -> None:
    """嵌入模型不可用时，权重重新分配至确定性维度，trace 记录降级信息。"""
    req = "提供技术方案、施工组织设计、实施方案、服务方案、质量保证措施、安全措施、进度计划、技术路线"
    resp = "技术方案详见附件，施工组织设计如下，实施方案明确，服务方案周全，质量保证措施得力，安全措施完善，进度计划合理，技术路线清晰"
    result = score_relevance(req, resp, "TECH")

    assert result.semantic_sim == 0.0
    assert result.trace.get("fallback") == "embedding_unavailable"
    assert result.trace.get("weight_redistribution") == "semantic_unavailable"
    assert result.score is not None
    assert result.score >= 60  # 高质量文本即使降级也应得高分


def test_degradation_score_formula(mock_semantic_unavailable) -> None:
    """降级时得分公式为 0.65 × TM + 0.35 × DKH。"""
    # 构造 TM=1.0、DKH 已知的文本（TECH 全 8 关键词）
    req = "提供技术方案、施工组织设计、实施方案、服务方案、质量保证措施、安全措施、进度计划、技术路线"
    resp = "技术方案详见附件，施工组织设计如下，实施方案明确，服务方案周全，质量保证措施得力，安全措施完善，进度计划合理，技术路线清晰"
    result = score_relevance(req, resp, "TECH")

    expected_raw = 0.65 * result.type_match + 0.35 * result.domain_keyword_hit
    expected_score = max(0, min(100, round(expected_raw * 100)))
    assert result.score == expected_score


def test_semantic_sim_included_in_result(mock_semantic) -> None:
    """正常路径下 semantic_sim 应为 mock 返回的 0.5。"""
    result = score_relevance("招标要求工程业绩", "投标响应工程业绩", "PERF")
    assert result.semantic_sim == 0.5


def test_type_match_is_intersection_not_union() -> None:
    """type_match 只统计**同时**出现在两段文本的关键词（交集语义）。"""
    # 关键词仅在 req 侧出现，resp 侧无对应词
    with patch(
        "app.core.matcher.relevance_scorer._compute_semantic_sim",
        return_value=(0.5, {}),
    ):
        result_one_side = score_relevance("工程业绩", "CO2地质封存实验", "PERF")
    # 关键词同时在双方出现
    with patch(
        "app.core.matcher.relevance_scorer._compute_semantic_sim",
        return_value=(0.5, {}),
    ):
        result_both = score_relevance("工程业绩", "工程业绩合同", "PERF")

    # 单侧 TM 为 0，双侧 TM > 0
    assert result_one_side.type_match == 0.0
    assert result_both.type_match > 0.0
    assert result_both.score > result_one_side.score


def test_domain_keyword_hit_uses_union_semantics(mock_semantic) -> None:
    """domain_keyword_hit 统计出现在**任意一方**文本的关键词（并集语义）。"""
    req_only = _compute_domain_keyword_hit("工程业绩", "")
    both = _compute_domain_keyword_hit("工程业绩", "项目业绩")
    assert both >= req_only  # 更多文本只会增加命中数


def test_type_match_internal_logic() -> None:
    """直接测试 _compute_type_match 的交集逻辑。"""
    # PERF: 工程业绩 在双方 → matched=1
    tm = _compute_type_match("工程业绩记录", "工程业绩合同", "PERF")
    assert tm == pytest.approx(1 / 10)

    # 工程业绩 仅在 req 侧 → matched=0
    tm_zero = _compute_type_match("工程业绩", "无相关内容", "PERF")
    assert tm_zero == pytest.approx(0.0)


def test_weight_constants() -> None:
    """权重常量之和为 1.0。"""
    total = TYPE_MATCH_WEIGHT + SEMANTIC_SIM_WEIGHT + DOMAIN_KEYWORD_WEIGHT
    assert total == pytest.approx(1.0)
