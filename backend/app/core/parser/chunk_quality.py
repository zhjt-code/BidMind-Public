"""分块质量门控(P0 主链路统一质量校验).

职责: 对已规划的 TextChunk 做三态判定与过滤/标记,在向量化前堵住乱码块与无意义块,
 统一覆盖投标/串标/写标素材/知识库等所有走 index_chunks 的向量化流.
不做: 切块 / 向量化 / 改写文本内容(degraded 块仍以原内容编码与入库).

三态判定:
- rejected: 清洗后空 / 有效字符过少 / 纯页码 / 纯符号——丢弃,不入库.
- degraded: compat_ratio 超阈值(同源乱码)——保留并标记,
 理由参见 docs/bug_tracking.md 教训:乱码是系统性问题,
 丢弃会丢整篇证据,应"标记 + 告警 + 等修复",不能默默删数据.
- clean: 通过.

指标: 复用 chunk_text_quality_total{flow,outcome} (metrics.py L187).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from loguru import logger

from app.core.config import settings
from app.core.parser.text_chunker import TextChunk
from app.core.parser.text_normalizer import compat_ratio

Verdict = Literal["clean", "degraded", "rejected"]

_PAGE_NUMBER_RE = re.compile(
    r"^[\s\-—–_·\.]*(?:第\s*)?\d{1,4}(?:\s*页|/\s*\d{1,4})?[\s\-—–_·\.]*$"
)
_PAGE_KEYWORD_RE = re.compile(
    r"^\s*(?:page|第)\s*\d{1,4}\s*(?:/\s*\d{1,4}|页)?\s*$", re.IGNORECASE
)
_VALID_CHAR_RE = re.compile(r"[\u4e00-\u9fff0-9A-Za-z]")
_PUNCT_ONLY_RE = re.compile(
    r"^[\s\.\-—–_·\*=~`\^#@\$%\&\+/\\|<>:;,'\"!\?\(\)\[\]\{\}【】「」、，。；：？！…‧¨\u3000]+$"
)


@dataclass(frozen=True)
class QualityStats:
    """单次 filter_chunks 的统计摘要,便于日志/上层观测."""

    total: int
    clean: int
    degraded: int
    rejected: int

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "clean": self.clean,
            "degraded": self.degraded,
            "rejected": self.rejected,
        }


def assess_chunk(
    content: str,
    *,
    min_valid_chars: int | None = None,
    compat_threshold: float | None = None,
) -> Verdict:
    """对单个 chunk 内容做三态质量判定.

    Args:
        content: chunk 内容(已通过 ChunkPlanner 的 null byte 与 strip 处理).
        min_valid_chars: 有效字符数下限,默认取 settings.CHUNK_MIN_VALID_CHARS.
        compat_threshold: 兼容字符比例上限,默认取 settings.CHUNK_COMPAT_RATIO_THRESHOLD.

    Returns:
        "clean" | "degraded" | "rejected"
    """
    if not content:
        return "rejected"

    stripped = content.strip()
    if not stripped:
        return "rejected"

    min_valid = int(
        min_valid_chars
        if min_valid_chars is not None
        else getattr(settings, "CHUNK_MIN_VALID_CHARS", 10)
    )
    threshold = float(
        compat_threshold
        if compat_threshold is not None
        else getattr(settings, "CHUNK_COMPAT_RATIO_THRESHOLD", 0.02)
    )

    if _is_page_number_only(stripped):
        return "rejected"

    if _is_punct_only(stripped):
        return "rejected"

    # 必须先于 valid_count 检查: 乱码块的标准 CJK 字符数往往为 0,
    # 若先做 valid_count 校验会把乱码块误判为 rejected, 与 教训
    # ("乱码是系统性问题, 应标记保留而非丢弃") 相悖.
    if compat_ratio(stripped) > threshold:
        return "degraded"

    valid_count = len(_VALID_CHAR_RE.findall(stripped))
    if valid_count < min_valid:
        return "rejected"

    return "clean"


def filter_chunks(
    chunks: list[TextChunk],
    *,
    flow: str = "vectorize",
) -> tuple[list[TextChunk], QualityStats]:
    """对 chunks 列表做质量过滤 + 标记.

    - rejected 块从结果列表移除.
    - degraded 块写入 metadata["quality_flag"]="degraded",仍参与编码与入库.
    - clean 块原样保留.

    指标 chunk_text_quality_total{flow,outcome} 按判定结果累加.

    Args:
        chunks: 由 ChunkPlanner 产出的 TextChunk 列表.
        flow: 指标标签,默认 "vectorize"(用于区分 writer/extractor 等其它流).

    Returns:
        (保留的 chunks, QualityStats)
    """
    if not chunks:
        return [], QualityStats(total=0, clean=0, degraded=0, rejected=0)

    metric_counter = _resolve_quality_metric()

    kept: list[TextChunk] = []
    clean_count = 0
    degraded_count = 0
    rejected_count = 0

    for chunk in chunks:
        verdict = assess_chunk(chunk.content)
        if metric_counter is not None:
            try:
                metric_counter.labels(flow=flow, outcome=verdict).inc()
            except Exception:
                pass

        if verdict == "rejected":
            rejected_count += 1
            continue

        if verdict == "degraded":
            degraded_count += 1
            chunk.metadata["quality_flag"] = "degraded"
            kept.append(chunk)
            continue

        clean_count += 1
        kept.append(chunk)

    stats = QualityStats(
        total=len(chunks),
        clean=clean_count,
        degraded=degraded_count,
        rejected=rejected_count,
    )

    if rejected_count or degraded_count:
        logger.info(
            "chunk_quality filter: total={total} clean={clean} "
            "degraded={degraded} rejected={rejected} flow={flow}",
            total=stats.total,
            clean=stats.clean,
            degraded=stats.degraded,
            rejected=stats.rejected,
            flow=flow,
        )

    return kept, stats


def _is_page_number_only(text: str) -> bool:
    if _PAGE_NUMBER_RE.match(text):
        return True
    if _PAGE_KEYWORD_RE.match(text):
        return True
    return False


def _is_punct_only(text: str) -> bool:
    return bool(_PUNCT_ONLY_RE.match(text))


def _resolve_quality_metric():
    try:
        from app.core.observability.metrics import chunk_text_quality_total

        return chunk_text_quality_total
    except Exception:
        return None
