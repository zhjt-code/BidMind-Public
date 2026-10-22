""" Level 3 合成样本：投标审查补搜非单调且缺分面覆盖导致已有信息误判未提供。

复刻用户反馈中三类可静态复现的失效层（不依赖 DB / LLM）：
1. QHSE"须包含"复合长句未拆分为可检索分面——防护设备设施配置、安全费用使用计划等
 并列项共用一条长查询，语义稀释后跨页证据无法分路召回；
2. 整页 VL 识别文本被复制到同页每张图片，逐图拼接后图片证据 chunk 重复膨胀，
 同页多实体（多本证书）在截断/去重阶段互相挤占；
3. 投标响应已含"投标有效期：开标之日起90日历天"明确事实，审查意见以
 "未明确具体有效期"否认时，一致性闸门因固定否认词表与 20 字下限双双失守直接放行。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.review.claim_response_consistency import (
    ClaimResponseConsistencyGate,
)
from app.core.parser.image_extractor import ExtractedImage, build_natural_evidence_chunk
from app.core.retriever.query_decomposer import QueryDecomposer

# 用户反馈截图中的 QHSE 复合要求（案例4/5 的要求侧原文形态）
_QHSE_DESC = (
    "投标文件须包含完整的QHSE组织机构、HSE作业计划书、施工风险初步评估、"
    "风险识别与削减措施、应急方案、防护设备设施配置及安全费用使用计划等材料"
)


def _make_image(index: int, visual_content: str) -> ExtractedImage:
    img = ExtractedImage(
        image_index=index,
        page_num=611,
        width=595,
        height=842,
        image_format="jpeg",
        xref=100 + index,
        file_path=f"p611_img{index}.jpeg",
    )
    img.visual_content = visual_content
    return img


def test_bug995_qhse_compound_clause_decomposes_into_parallel_facets() -> None:
    """"须包含 A、B、…及 G 等材料"必须拆出并列分面查询。

    修复前实测：整句只产出 1 条长查询，关键词仅"投标文件"，
    防护配置与费用计划两个子面没有独立检索通道（对应反馈案例4/5）。
    """
    queries = QueryDecomposer().decompose(_QHSE_DESC)
    joined = " ".join(queries)

    assert len(queries) >= 4, f"复合并列项未拆分为多路查询: {queries}"
    assert "防护设备设施配置" in joined, f"防护配置子面丢失: {queries}"
    assert "安全费用使用计划" in joined, f"费用计划子面丢失: {queries}"
    assert "应急方案" in joined, f"应急方案子面丢失: {queries}"


def test_bug995_full_page_vl_text_appears_once_in_page_evidence() -> None:
    """同页多图共享的整页 VL 文本在图片证据 chunk 中只允许出现一次。

    修复前：vl_stage 把整页识别结果复制给每张图片，build_natural_evidence_chunk
    又逐图拼接，同一段"安全生产许可证…"出现 N 次（对应反馈案例2/3 的膨胀挤占）。
    """
    full_page_text = "安全生产许可证 证书编号(晋)JZ安许证字[2023]000000 有效期至2027年8月"
    images = [_make_image(i, full_page_text) for i in range(3)]

    evidence = build_natural_evidence_chunk(page_num=611, images=images, context="")

    occurrences = evidence.count("安全生产许可证 证书编号")
    assert occurrences == 1, (
        f"整页VL文本重复拼接{occurrences}次，图片证据chunk膨胀:\n{evidence}"
    )


def test_bug995_explicit_validity_fact_conflict_routes_to_needs_review() -> None:
    """响应含明确"90日历天"事实、结论称"未明确具体有效期"时必须转人工复核。

    修复前双失守：'未明确'不在固定否认词表；响应正文 16 字不足 20 字下限，
    闸门直接 pass（对应反馈案例1）。修复后只转 needs_review，不得自动提升符合度。
    """
    target = SimpleNamespace(
        compliance_level="not",
        comment="投标文件仅承诺有效期内不撤销投标，未明确具体有效期时长，判定不符合。",
        score=0,
        is_compliant=False,
    )
    ctx = GateContext(
        flow="investigate",
        stage="post_llm",
        target=target,
        evidence={
            "has_response": True,
            "response_content": "投标有效期：开标之日起90日历天",
        },
    )
    gate = ClaimResponseConsistencyGate()
    assert gate.applies(ctx) is True

    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "enforce_patch", (
        f"明确事实与否认结论冲突必须触发复核, 实际: {verdict.verdict} ({verdict.reason})"
    )
    patch = verdict.suggested_patch or {}
    assert patch.get("failure_state") == "needs_review"
    # 闸门只负责转复核，不得替 LLM 自动提升符合度
    assert patch.get("compliance_level") in (None, "not"), (
        f"闸门不得自动升级符合度: {patch}"
    )
