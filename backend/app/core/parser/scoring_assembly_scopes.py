"""模板边界驱动的装配范围切分（Task 5 修订版）。

职责: 把 section/template_boundary 边界求交，返回不跨模板边界的装配范围；
 将 boundary_id 后置注入已产出 chunk 的 payload。
不做: 不拼接评分表、不解析评分项、不访问 DB/LLM。

交集策略（I4 修订）:
- 先 coalesce（合并重叠/邻接区段）；
- 再按模板边界切分；
- 最后按 (page_start, page_end) 去重；
- template_boundary 触发 section 分裂：每个 boundary.page_num 为新 scope 起点；
- lot 变化不自动推进 scheme；volume 标题不等于 scheme 边界。

稳定 ID 规则（C2/I5 单源）:
- boundary_id_for_page 从 scoring_template_boundaries 单源生成
- 格式：tb:{code}（不含页码，C2 稳定性）
"""
from __future__ import annotations

from app.core.parser.scoring_template_boundaries import TemplateBoundary


def _coalesce_sections(sections: list) -> list:
    """合并重叠/邻接的区段（内部辅助，I4）。"""
    from app.core.parser.scoring_section_locator import ScoringSection

    ordered = sorted(sections, key=lambda s: (s.page_start, s.page_end))
    spans: list[ScoringSection] = []
    for sec in ordered:
        if spans and sec.page_start <= spans[-1].page_end + 1:
            prev = spans[-1]
            spans[-1] = ScoringSection(
                title=prev.title,
                page_start=prev.page_start,
                page_end=max(prev.page_end, sec.page_end),
                detection=prev.detection,
            )
        else:
            spans.append(
                ScoringSection(
                    title=getattr(sec, "title", "") or "评分表",
                    page_start=sec.page_start,
                    page_end=sec.page_end,
                    detection=getattr(sec, "detection", "structure"),
                )
            )
    return spans


def split_sections_by_template_boundaries(
    sections: list,
    template_boundaries: list[TemplateBoundary],
) -> list:
    """把 ScoringSection 列表按模板边界分裂（I4 修订：coalesce→split→dedup）。

    执行顺序:
    1. coalesce: 合并重叠/邻接区段，防止切后产出冗余小片段；
    2. split:    在 boundary.page_num 处切断区段；
    3. dedup:    按 (page_start, page_end) 精确去重，消除合并+切分的重复产出。

    切分规则:
    - 若某 boundary.page_num 在 section 范围内（page_start < boundary.page_num <= page_end），
      则在该页前切断，形成 [page_start, boundary.page_num-1] 和 [boundary.page_num, ...] 两段；
    - 切出的片段保留原 section 的 title、detection 属性；
    - 无 boundary 或 boundary 不在范围内 → 原样返回；
    - lot 变化不引入切分。
    """
    if not template_boundaries:
        return list(sections)

    from app.core.parser.scoring_section_locator import ScoringSection

    sorted_boundaries = sorted(template_boundaries, key=lambda b: b.page_num)

    # I4: 先 coalesce 避免重叠导致重复片段
    coalesced = _coalesce_sections(sections)

    result: list[ScoringSection] = []
    for sec in coalesced:
        inner = [b for b in sorted_boundaries if sec.page_start < b.page_num <= sec.page_end]
        if not inner:
            result.append(sec)
            continue

        title = getattr(sec, "title", "") or "评分表"
        detection = getattr(sec, "detection", "structure")
        starts = [sec.page_start] + [b.page_num for b in inner]
        ends = [b.page_num - 1 for b in inner] + [sec.page_end]

        for start, end in zip(starts, ends):
            if start > end:
                continue
            result.append(
                ScoringSection(
                    title=title,
                    page_start=start,
                    page_end=end,
                    detection=detection,
                )
            )

    # I4: 按 (page_start, page_end) 去重（保序）
    seen_ranges: set[tuple[int, int]] = set()
    deduped: list[ScoringSection] = []
    for sec in result:
        key = (sec.page_start, sec.page_end)
        if key not in seen_ranges:
            seen_ranges.add(key)
            deduped.append(sec)
    return deduped


def inject_scheme_boundary_ids(chunks: list, template_boundaries: list) -> None:
    """就地将 scheme_boundary_id 写入已产出 chunk 的载荷（从 scoring_chunk_builder 移入，M2）。

    职责: 后置写入，避免修改 scoring_chunk_sources 回调签名；
          按 chunk 起始页找最近模板边界并写入 payload["scheme_boundary_id"]。
    不做: 不重建 payload、不改变 chunk 其他字段。
    """
    from app.core.extractor.scoring.scoring_grid_payload import (
        PAYLOAD_KEY,
        set_payload_scope_metadata,
    )
    from app.core.parser.scoring_template_boundaries import boundary_id_for_page

    for chunk in chunks:
        container = getattr(chunk, "metadata", None)
        if not isinstance(container, dict):
            continue
        payload = container.get(PAYLOAD_KEY)
        if not isinstance(payload, dict):
            continue
        page_start = payload.get("page_start") or getattr(chunk, "page_num", None)
        if page_start is None:
            continue
        bid = boundary_id_for_page(template_boundaries, int(page_start))
        if bid is not None:
            set_payload_scope_metadata(payload, scheme_boundary_id=bid)


__all__ = [
    "inject_scheme_boundary_ids",
    "split_sections_by_template_boundaries",
]
