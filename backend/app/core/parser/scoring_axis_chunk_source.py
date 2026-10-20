"""转置最终分值轴声明 chunk 来源（Task 4B）。

职责: 在主装配前扫描 ParsedDocument.pages[].tables，识别转置权重汇总表，
 产生声明型 scoring_table chunk，预占物理表键，并构造 PriceFormulaContext。
不做: 不产评分明细项、不运行完整装配、不修改 Task5 模板边界逻辑、不调用 LLM/DB。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from app.core.extractor.scoring.scoring_grid_payload import (
    PAYLOAD_KEY,
    build_scoring_payload,
    table_fingerprint,
)
from app.core.parser.lot_boundary_locator import lot_for_page

if TYPE_CHECKING:
    from app.core.parser.chunk_planner import ChunkPlanner
    from app.core.parser.pdf_parser import ParsedDocument
    from app.core.parser.text_chunker import TextChunk


@dataclass(frozen=True)
class PriceFormulaContext:
    """从转置最终分值轴构造的价格公式上下文（typed，禁止裸 dict）。

    职责: 将轴解析结果以类型安全方式传递给价格公式识别器。
    不做: 不包含原始 grid 数据、不与具体 recognizer 耦合。
    """

    price_score: float
    lot_label: str | None = None  # None 表示全局单一 scope


@dataclass
class AxisScanResult:
    """转置轴预扫描结果。

    职责: 统一返回声明 chunk、预占表键和价格上下文三类产出。
    不做: 不承载完整表装配逻辑。
    """

    chunks: list = field(default_factory=list)  # list[TextChunk]
    claimed_keys: set = field(default_factory=set)  # set[tuple[int, int]]
    price_contexts: list[PriceFormulaContext] = field(default_factory=list)
    conflict: bool = False  # 多轴 price 期望冲突，不广播价格分


def scan_transposed_axis_chunks(
    parsed_doc: "ParsedDocument",
    planner: "ChunkPlanner",
    start_index: int,
    lots: list | None = None,
) -> AxisScanResult:
    """扫描全部页表格，识别转置最终分值轴，产生声明型 chunk 并预占物理表键。

 Task4B review I1: 必须在 lots 解析完成后调用，以便精确绑定 lot。
 - lots 非空时：按 lot_for_page 绑定 lot_label；axis 页不属于任何 lot → 不生成 price_context。
 - lots 为空或 None 时：单一 scope，lot_label=None。

 去重 (M2): (lot_label, price_score) 相同的 price_context 稳定去重。
 冲突检测: 同一 lot_label 下多个不同 price 期望 → conflict=True，清空 price_contexts。
 """
    from app.core.extractor.scoring.transposed_score_axis import (
        parse_transposed_final_score_axis,
    )

    effective_lots = lots or []
    result = AxisScanResult()
    seen_fingerprints: set[str] = set()
    seen_ctx_keys: set[tuple] = set()  # M2: (lot_label, price_score) 去重键
    idx = start_index

    for page in parsed_doc.pages:
        tables = getattr(page, "tables", None) or []
        for table_idx, table in enumerate(tables):
            grid: list[list[str]] | None = getattr(table, "data", None)
            if not grid:
                continue

            axis = parse_transposed_final_score_axis(grid)
            if axis is None:
                continue

            fp = table_fingerprint(grid)
            if fp in seen_fingerprints:
                logger.debug(
                    "[转置轴] 跳过重复指纹: page={} table_idx={}", page.page_num, table_idx
                )
                continue
            seen_fingerprints.add(fp)

            result.claimed_keys.add((page.page_num, table_idx))

            markdown = _render_axis_declaration_markdown(grid, page.page_num)
            chunk = planner.build_manual_chunk(
                content=markdown,
                page_num=page.page_num,
                chunk_type="scoring_table",
                parent_block_id=f"scoring-axis-declaration:{page.page_num}-{table_idx}",
                section_path="权重表综合得分汇总（转置轴声明）",
            )
            chunk.chunk_index = idx
            idx += 1
            try:
                chunk.metadata["is_scoring_table"] = True
                chunk.metadata["page_end"] = page.page_num
                chunk.metadata["recovered_from"] = "transposed_final_axis"
                chunk.metadata["axis_declaration"] = True
            except Exception:
                pass

            payload = build_scoring_payload(
                markdown,
                page_start=page.page_num,
                page_end=page.page_num,
                source="transposed_final_axis",
            )
            if payload is not None:
                try:
                    chunk.metadata[PAYLOAD_KEY] = payload
                except Exception:
                    pass

            result.chunks.append(chunk)

            price_expected = axis.expected_by_volume.get("price")
            if price_expected is not None and price_expected > 0:
                # I1: 按 lot_for_page 精确绑定 lot_label
                if effective_lots:
                    lot_label = lot_for_page(effective_lots, page.page_num)
                    if lot_label is None:
                        logger.warning(
                            "[转置轴] 轴所在页不属于任何标段，跳过 price_context: page={}",
                            page.page_num,
                        )
                    else:
                        ctx_key = (lot_label, float(price_expected))
                        if ctx_key not in seen_ctx_keys:  # M2: 去重
                            seen_ctx_keys.add(ctx_key)
                            result.price_contexts.append(
                                PriceFormulaContext(price_score=float(price_expected), lot_label=lot_label)
                            )
                else:
                    # 单一 scope：lot_label=None
                    ctx_key = (None, float(price_expected))
                    if ctx_key not in seen_ctx_keys:  # M2: 去重
                        seen_ctx_keys.add(ctx_key)
                        result.price_contexts.append(
                            PriceFormulaContext(price_score=float(price_expected))
                        )

            logger.info(
                "[转置轴] 发现声明轴: page={} commercial={} technical={} price={}",
                page.page_num,
                axis.expected_by_volume.get("commercial"),
                axis.expected_by_volume.get("technical"),
                price_expected,
            )

    # 冲突检测: 同一 lot_label 下多个不同 price 期望 → conflict=True
    lot_score_map: dict[object, set[float]] = {}
    for ctx in result.price_contexts:
        lot_score_map.setdefault(ctx.lot_label, set()).add(ctx.price_score)

    for lot_key, scores in lot_score_map.items():
        if len(scores) > 1:
            result.conflict = True
            result.price_contexts = []
            logger.warning(
                "[转置轴] 同标段 price 期望冲突，禁止广播价格分: lot={} scores={}",
                lot_key,
                sorted(scores),
            )
            break

    return result


def _render_axis_declaration_markdown(grid: list[list[str]], page_num: int) -> str:
    """将转置轴原始网格渲染为 parse_scoring_table 可提取 volume_defs 的 markdown。

    parse_scoring_table 对此 markdown 走 _parse_weight_summary_table 路径，
    由 try_parse_transposed_axis_volume_defs 提取 volume_defs，不产评分 item。
    """
    if not grid:
        return ""

    col_count = max((len(row) for row in grid), default=1)
    sep = "| " + " | ".join(["---"] * col_count) + " |"

    lines: list[str] = [f"[评分表 第{page_num}-{page_num}页]（转置最终分值轴声明）"]
    # 首行作为 markdown 表头行
    first_row = list(grid[0]) + [""] * (col_count - len(grid[0]))
    lines.append("| " + " | ".join(str(c or "") for c in first_row) + " |")
    lines.append(sep)
    for row in grid[1:]:
        padded = list(row) + [""] * (col_count - len(row))
        lines.append("| " + " | ".join(str(c or "") for c in padded) + " |")

    return "\n".join(lines)


__all__ = ["PriceFormulaContext", "AxisScanResult", "scan_transposed_axis_chunks"]
