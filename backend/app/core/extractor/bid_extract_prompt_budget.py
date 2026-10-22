"""投标响应提取 Prompt 预算护栏 (token 补强)

职责: 提取送模可见集的分面保底排序 + 发送前硬上限适配(与审查侧同口径)
不做: 检索、上下文拼接(仍由 BidExtractor._build_budgeted_context 承担)、
 LLM 调用、重试编排、落库、符合性判断

背景: 审查侧已有"组包总帽 -> 发送前硬预检 -> 本地收缩 -> 仍超转待判定"闭环
(/ review_input_gate), 提取侧此前只有拼接期字符预算, 超限完全依赖
云端 400 兜底, 对超时/排队类过载无效; 且首轮可见集按融合分全局排序, 多分面
条款的弱势分面代表(如授权委托书)会被强势分面(营业执照)挤出 top_k 主槽。本模块把两个缺口收敛为纯函数, 由 BidExtractor 调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional, Sequence

from app.core.ai.input_budget import prompt_hard_cap_violation
from app.core.retriever.evidence_coverage import (
    EvidenceFacet,
    facet_hit,
    normalize_text,
)

if TYPE_CHECKING:
    from app.core.retriever.semantic_retriever import SearchResult

# ExtractionFailedSignal.reason: 收缩到底仍超硬上限, 走"[提取失败待复核]"合同
OVERSIZE_REASON = "input_oversize"

# (reduction_round) -> (system_message, user_message) 渲染回调
RenderPrompt = Callable[[int], tuple[str, str]]


def order_results_facet_first(
    results: Sequence["SearchResult"],
    facets: Sequence[EvidenceFacet],
) -> list["SearchResult"]:
    """每分面首条命中提前到队首, 其余保持原有相关性顺序(稳定)。

    与 select_facet_balanced_results 的差异: 不按 similarity 重排——首轮结果
    是 RRF 融合+重排产物, similarity 跨通道不可比(词法/结构命中为 0), 按其
    重排会把词法命中挤到队尾。代表与剩余条目都保持原相对顺序。
    少于 2 个分面时不存在挤占问题, 原样返回, 保护单分面条款既有行为。
    """
    facet_tuple = tuple(facets or ())
    pool = list(results or ())
    if len(facet_tuple) < 2 or not pool:
        return pool

    representative_indices: set[int] = set()
    for facet in facet_tuple:
        for idx, result in enumerate(pool):
            if idx in representative_indices:
                continue
            sub_query = {
                normalize_text(getattr(result, "matched_sub_query", "") or "")
            }
            sub_query.discard("")
            content = [normalize_text(getattr(result, "content", "") or "")]
            if facet_hit(facet, sub_query, content):
                representative_indices.add(idx)
                break

    if not representative_indices:
        return pool
    representatives = [pool[i] for i in sorted(representative_indices)]
    rest = [r for i, r in enumerate(pool) if i not in representative_indices]
    return representatives + rest


def fit_extract_reduction_round(
    render: RenderPrompt,
    max_rounds: int,
) -> Optional[int]:
    """返回首个通过硬上限预检的收缩轮次; 全部超限返回 None。

    round 0 即业务层原始预算(首次不裁), 与审查侧
    review_input_gate.fit_prompt_within_hard_cap 同口径: 禁止先发送注定
    超限的请求再等 provider 400/超时。调用方对 None 应落
    ExtractionFailedSignal(reason=OVERSIZE_REASON), 不发起 LLM 调用。
    """
    for reduction_round in range(max(0, max_rounds) + 1):
        system_message, user_message = render(reduction_round)
        if prompt_hard_cap_violation(system_message, user_message) is None:
            return reduction_round
    return None
