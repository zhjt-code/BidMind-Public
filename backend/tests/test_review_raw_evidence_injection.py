"""R1 防御测试：审查 Prompt 注入投标原文佐证。

职责: 验证 evidence_prefetch._format_raw_evidence_excerpts 的排序/截断/降级
      行为，并验证 pair_review.build_review_prompt 能把 raw_evidence_excerpts
      正确拼接到 response_content 段；确认 settings 关闭开关有效。
不做: 真实 DB 查询、真实 LLM 调用、Gate Pipeline。

历史教训映射:
    审查 LLM 仅消费提取摘要、不回看原文 chunk，导致提取误差 100% 传导为审查
    误判。本测试覆盖三类回归点：
    (a) role/similarity 排序是否稳定；
    (b) 字符截断是否生效；
    (c) 开关关闭时是否不注入任何文本（回滚通道）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.ai.reviewer_parts.evidence_prefetch import (
    _format_raw_evidence_excerpts,
)
from app.core.ai.reviewer_parts.pair_review import build_review_prompt
from app.core.config import settings
from app.core.review_contracts import ReviewEvidenceBundle


# ---------------------------------------------------------------------------
# 1. _format_raw_evidence_excerpts 单元测试
# ---------------------------------------------------------------------------


def test_format_raw_evidence_returns_empty_when_setting_zero(monkeypatch) -> None:
    """REVIEW_RAW_EVIDENCE_CHUNKS=0 时关闭注入，返回空串。"""
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 0)
    rows = [{"role": "primary", "page": 1, "similarity": 0.9, "content": "abc"}]
    assert _format_raw_evidence_excerpts(rows) == ""


def test_format_raw_evidence_returns_empty_when_no_rows(monkeypatch) -> None:
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 3)
    assert _format_raw_evidence_excerpts([]) == ""


def test_format_raw_evidence_orders_primary_before_deep(monkeypatch) -> None:
    """role 优先级: primary > deep；同 role 按 similarity 降序。"""
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 3)
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHARS", 500)
    rows = [
        {"role": "deep", "page": 20, "similarity": 0.30, "content": "DEEP_B"},
        {"role": "primary", "page": 5, "similarity": 0.55, "content": "PRIMARY_A"},
    ]
    text = _format_raw_evidence_excerpts(rows)
    assert "PRIMARY_A" in text
    assert "DEEP_B" in text
    assert text.index("PRIMARY_A") < text.index("DEEP_B")


def test_format_raw_evidence_drops_low_info_toc_keeps_scan_summary(monkeypatch) -> None:
    """目录/点线页不得注入审查原文；扫描件摘要同形态仍保留。"""
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 3)
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHARS", 500)
    rows = [
        {
            "role": "toc",
            "page": 2,
            "similarity": 0.99,
            "content": "目 录 一、投标函 ........................ 1",
        },
        {
            "role": "primary",
            "page": 59,
            "similarity": 0.55,
            "content": "[投标证明材料] 第59页提供了合同扫描件：甲方某某公司",
        },
    ]
    text = _format_raw_evidence_excerpts(rows)
    assert "扫描件" in text
    assert "甲方某某公司" in text
    assert "投标函" not in text
    assert "目 录" not in text


def test_format_raw_evidence_respects_top_n_limit(monkeypatch) -> None:
    """rows 多于 top_n 时按排序裁剪。"""
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 2)
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHARS", 500)
    rows = [
        {"role": "primary", "page": 1, "similarity": 0.9, "content": "AAA"},
        {"role": "primary", "page": 2, "similarity": 0.7, "content": "BBB"},
        {"role": "primary", "page": 3, "similarity": 0.5, "content": "CCC"},
    ]
    text = _format_raw_evidence_excerpts(rows)
    assert "AAA" in text and "BBB" in text
    assert "CCC" not in text, "top_n=2 时第三段不应注入"


def test_format_raw_evidence_truncates_long_content(monkeypatch) -> None:
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 1)
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHARS", 20)
    long_text = "我方郑重承诺" * 50  # 远超 20 字符
    rows = [{"role": "primary", "page": 1, "similarity": 0.9, "content": long_text}]
    text = _format_raw_evidence_excerpts(rows)
    assert "…" in text, "超长内容应附省略号"
    # 截断后单段长度（去掉前缀和省略号）不应超过上限
    assert long_text not in text


def test_format_raw_evidence_contains_judgement_rule(monkeypatch) -> None:
    """注入段必须包含"以原文为准"的审查判读规则。"""
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 3)
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHARS", 500)
    rows = [{"role": "primary", "page": 1, "similarity": 0.9, "content": "X"}]
    text = _format_raw_evidence_excerpts(rows)
    assert "以原文为准" in text
    assert "投标原文佐证" in text


def test_format_raw_evidence_includes_section_path(monkeypatch) -> None:
    """表格命中必须展示中文章节锚点（章节=），便于LLM和人工复核定位。

 设计意图（evidence_prefetch.py:427）：生产代码已将章节标签从英文 section=
 改为中文 章节=，与整体中文标注风格统一。本测试同时断言英文旧标签不再出现，
 防止回潮（与 同源：生产改中文文案，测试仍断言英文）。
 """
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 3)
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHARS", 500)
    rows = [
        {
            "role": "deep",
            "page": 472,
            "similarity": 0.62,
            "section": "正在施工的和新承接的项目情况表",
            "content": "表格第2行：项目名称=<ORG_ENERGY_FIELD_1>项目；状态=正在施工",
        }
    ]

    text = _format_raw_evidence_excerpts(rows)

    # 锁定中文标签语义：章节= 必须出现，且附带完整章节名
    assert "章节=正在施工的和新承接的项目情况表" in text, (
        f"生产已将标签改为中文'章节='，但未在输出中找到。实际输出：{text!r}"
    )
    # 防回潮断言：英文旧标签 section= 不得重现（防止生产代码回退到英文标注）
    assert "section=" not in text, (
        f"英文旧标签'section='不应出现（防回潮），实际输出：{text!r}"
    )
    assert "第472页" in text, f"页码标签必须出现，实际输出：{text!r}"


def test_format_raw_evidence_skips_blank_content(monkeypatch) -> None:
    """空内容行不应渲染（避免出现裸 role 标签）。"""
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 3)
    rows = [
        {"role": "primary", "page": 1, "similarity": 0.9, "content": "   "},
        {"role": "primary", "page": 2, "similarity": 0.8, "content": "real"},
    ]
    text = _format_raw_evidence_excerpts(rows)
    assert text.count("▶") == 1


# ---------------------------------------------------------------------------
# 1b. 分面保底选择（工作包 2C）
# ---------------------------------------------------------------------------


def _facet_rows() -> list[dict]:
    """分面A（资质证书）3条高分 primary + 分面B（项目业绩）1条低分 deep。"""
    return [
        {"role": "primary", "page": 1, "similarity": 0.95,
         "content": "资质证书CERT_A1", "matched_sub_query": "投标人 资质证书", "chunk_id": 1},
        {"role": "primary", "page": 2, "similarity": 0.93,
         "content": "资质证书CERT_A2", "matched_sub_query": "投标人 资质证书", "chunk_id": 2},
        {"role": "primary", "page": 3, "similarity": 0.91,
         "content": "资质证书CERT_A3", "matched_sub_query": "投标人 资质证书", "chunk_id": 3},
        {"role": "deep", "page": 9, "similarity": 0.40,
         "content": "项目业绩PERF_B1", "matched_sub_query": "投标人 项目业绩", "chunk_id": 4},
    ]


def test_format_raw_evidence_facet_guarantee_keeps_minority_facet(monkeypatch) -> None:
    """传入分面时, 强势分面的高分命中不得挤掉其他分面的唯一证据。"""
    from app.core.retriever.evidence_coverage import EvidenceFacet

    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 3)
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHARS", 500)
    facets = (
        EvidenceFacet(key="资质证书", query="投标人 资质证书"),
        EvidenceFacet(key="项目业绩", query="投标人 项目业绩"),
    )
    text = _format_raw_evidence_excerpts(_facet_rows(), facets=facets)
    assert "PERF_B1" in text, "分面保底: 项目业绩分面唯一证据必须入选"
    assert "CERT_A1" in text, "强势分面最佳命中仍应入选"
    assert "CERT_A2" in text, "剩余额度按全局 (role_rank, -similarity) 补齐"
    assert "CERT_A3" not in text, "top_n=3 时第4条不应注入"


def test_format_raw_evidence_without_facets_keeps_legacy_truncation(monkeypatch) -> None:
    """不传 facets 时保持旧排序截断（对照: PERF_B1 被强势分面挤出）。"""
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 3)
    monkeypatch.setattr(settings, "REVIEW_RAW_EVIDENCE_CHARS", 500)
    text = _format_raw_evidence_excerpts(_facet_rows())
    assert "CERT_A1" in text and "CERT_A2" in text and "CERT_A3" in text
    assert "PERF_B1" not in text, "无分面时必须保持旧 (role_rank, -similarity) 行为"


# ---------------------------------------------------------------------------
# 2. build_review_prompt 集成（Prompt 是否真注入）
# ---------------------------------------------------------------------------


def _make_pair() -> MagicMock:
    """构造最小可用的 MatchedPair（不依赖 ORM）。"""
    requirement = SimpleNamespace(
        id=42,
        description="投标人须具有建筑工程一级资质",
    )
    pair = MagicMock()
    pair.requirement_id = 42
    pair.category = "qualification"
    pair.requirement = requirement
    pair.is_mandatory = True
    pair.response_id = 7
    pair.has_response = True
    return pair


def _make_bundle(raw: str = "") -> ReviewEvidenceBundle:
    return ReviewEvidenceBundle(
        response_content="投标人承诺持有建筑工程一级资质证书",
        source_pages=[12],
        completeness="full",
        missing_elements=[],
        linked_to=None,
        evidence_type="certificate",
        evidence_anchor="cert#1",
        relevance_score=85,
        relevance_note="高度相关",
        image_evidence="",
        cross_category_evidence="",
        quality_flags=[],
        raw_evidence_excerpts=raw,
    )


def test_build_review_prompt_injects_raw_evidence_when_present() -> None:
    """raw_evidence_excerpts 非空时必须出现在最终 Prompt 中。"""
    raw_segment = "【投标原文佐证（来自检索命中的 chunk）】\n▶ [role=primary·第5页] 资质证书编号 GC2024-001"
    prompt = build_review_prompt(
        self=MagicMock(),  # orchestrator 引用未在该路径使用
        pair=_make_pair(),
        evidence_bundle=_make_bundle(raw=raw_segment),
    )
    assert "投标原文佐证" in prompt
    assert "GC2024-001" in prompt


def test_build_review_prompt_omits_raw_evidence_when_empty() -> None:
    """raw_evidence_excerpts 为空时 Prompt 必须保持原状（不出现佐证标题）。"""
    prompt = build_review_prompt(
        self=MagicMock(),
        pair=_make_pair(),
        evidence_bundle=_make_bundle(raw=""),
    )
    assert "投标原文佐证" not in prompt


def test_build_review_prompt_raw_evidence_appears_after_response_content() -> None:
    """原文佐证应紧跟 response_content（在结构上属于"投标响应"段的延伸）。"""
    raw_segment = "【投标原文佐证】\n▶ 资质编号 XYZ"
    prompt = build_review_prompt(
        self=MagicMock(),
        pair=_make_pair(),
        evidence_bundle=_make_bundle(raw=raw_segment),
    )
    assert prompt.index("投标人承诺") < prompt.index("投标原文佐证")


# ---------------------------------------------------------------------------
# 3. ReviewEvidenceBundle 默认值向后兼容
# ---------------------------------------------------------------------------


def test_bundle_raw_evidence_default_is_empty_string() -> None:
    """既有构造点未传 raw_evidence_excerpts 时不应破坏 dataclass。"""
    bundle = ReviewEvidenceBundle(
        response_content="x",
        source_pages=[],
        completeness=None,
        missing_elements=[],
        linked_to=None,
        evidence_type=None,
        evidence_anchor=None,
        relevance_score=None,
        relevance_note=None,
        image_evidence="",
        cross_category_evidence="",
        quality_flags=[],
    )
    assert bundle.raw_evidence_excerpts == ""
