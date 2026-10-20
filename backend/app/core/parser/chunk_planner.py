"""语义守恒型 chunk planning。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable

from loguru import logger

from app.core.config import settings
from app.core.parser.pdf_parser import ParsedPage
from app.core.parser.table_extractor import ExtractedTable
from app.core.parser.table_linearizer import linearize_table
from app.core.parser.text_chunker import TextChunk, TextChunker


_HEADING_RE = re.compile(
    r"^(第[一二三四五六七八九十百千万0-9]+[章节部分编]|[0-9]+(?:\.[0-9]+){0,3}|[一二三四五六七八九十]+、).{0,80}$"
)
_CLAUSE_RE = re.compile(
    r"^(第[一二三四五六七八九十百千万0-9]+条|[0-9]+(?:\.[0-9]+){1,4}|[一二三四五六七八九十]+、|（[一二三四五六七八九十0-9]+）)"
)


@dataclass(frozen=True)
class ChunkShadowSummary:
    """新旧 chunk 策略影子对比摘要。"""

    old_count: int
    new_count: int
    old_max_chars: int
    new_max_chars: int
    diff_ratio: float

    def to_dict(self) -> dict[str, int | float]:
        return {
            "old_count": self.old_count,
            "new_count": self.new_count,
            "old_max_chars": self.old_max_chars,
            "new_max_chars": self.new_max_chars,
            "diff_ratio": round(self.diff_ratio, 4),
        }


class ChunkPlanner:
    """基于结构和内容类型的轻量 chunk 规划器。"""

    def __init__(
        self,
        max_item_chars: int | None = None,
        paragraph_target_chars: int | None = None,
        paragraph_overlap_chars: int | None = None,
    ) -> None:
        self.max_item_chars = max_item_chars or int(settings.VECTOR_MAX_ITEM_CHARS)
        self.max_item_tokens = int(settings.VECTOR_MAX_ITEM_TOKENS)
        self.paragraph_target_chars = paragraph_target_chars or int(
            settings.VECTOR_PARAGRAPH_TARGET_CHARS
        )
        self.paragraph_overlap_chars = paragraph_overlap_chars or int(
            settings.VECTOR_PARAGRAPH_OVERLAP_CHARS
        )
        self._paragraph_chunker = TextChunker(
            chunk_size=max(self.paragraph_target_chars, 120),
            chunk_overlap=max(min(self.paragraph_overlap_chars, self.paragraph_target_chars // 2), 0),
        )

    def plan_parsed_pages(self, pages: list[ParsedPage]) -> list[TextChunk]:
        """为 ParsedPage 列表生成语义 chunk。"""
        chunks: list[TextChunk] = []
        global_index = 0
        active_section = ""

        for page in pages:
            page_chunks, active_section = self._plan_page(page, active_section, global_index)
            chunks.extend(page_chunks)
            global_index = len(chunks)

        return chunks

    def plan_page_tuples(self, pages: list[tuple[int, str]]) -> list[TextChunk]:
        """为简单页码+文本元组生成 paragraph chunk。"""
        synthetic_pages = [
            ParsedPage(page_num=page_num, text=page_text or "")
            for page_num, page_text in pages
        ]
        return self.plan_parsed_pages(synthetic_pages)

    def build_manual_chunk(
        self,
        *,
        content: str,
        page_num: int,
        chunk_type: str,
        parent_block_id: str,
        section_path: str = "",
        clause_no: str | None = None,
        sub_index: int = 0,
    ) -> TextChunk:
        """为独立证据块等手工内容构造标准 chunk。"""
        return self._make_chunk(
            content=content,
            page_num=page_num,
            section_path=section_path,
            chunk_type=chunk_type,
            parent_block_id=parent_block_id,
            clause_no=clause_no,
            sub_index=sub_index,
        )

    def shadow_compare(self, pages: list[ParsedPage]) -> ChunkShadowSummary:
        """与旧 TextChunker 进行影子对比。"""
        old_pages = [(page.page_num, page.text or "") for page in pages]
        old_chunks = TextChunker().split_with_page_tracking(old_pages)
        new_chunks = self.plan_parsed_pages(pages)
        old_max = max((len(chunk.content) for chunk in old_chunks), default=0)
        new_max = max((len(chunk.content) for chunk in new_chunks), default=0)
        denom = max(len(old_chunks), 1)
        return ChunkShadowSummary(
            old_count=len(old_chunks),
            new_count=len(new_chunks),
            old_max_chars=old_max,
            new_max_chars=new_max,
            diff_ratio=abs(len(new_chunks) - len(old_chunks)) / denom,
        )

    def _plan_page(
        self,
        page: ParsedPage,
        active_section: str,
        start_index: int,
    ) -> tuple[list[TextChunk], str]:
        section = active_section
        chunks: list[TextChunk] = []
        text = (page.text or "").replace("\x00", "").strip()

        blocks = self._split_text_blocks(text)
        for block_no, block in enumerate(blocks):
            stripped = block.strip()
            if not stripped:
                continue
            if self._looks_like_heading(stripped):
                section = stripped[:255]

            block_chunks = self._plan_text_block(
                content=stripped,
                page_num=page.page_num,
                section_path=section,
                block_no=block_no,
            )
            chunks.extend(block_chunks)

        for table in page.tables:
            table_chunks = self._plan_table(
                table=table,
                page_num=page.page_num,
                section_path=section,
            )
            chunks.extend(table_chunks)

        for local_index, chunk in enumerate(chunks, start=start_index):
            chunk.chunk_index = local_index

        return chunks, section

    @staticmethod
    def _split_text_blocks(text: str) -> list[str]:
        if not text:
            return []
        parts = [part.strip() for part in re.split(r"\n{2,}", text) if part and part.strip()]
        return parts or [text]

    @staticmethod
    def _looks_like_heading(text: str) -> bool:
        if len(text) > 80:
            return False
        return bool(_HEADING_RE.match(text.strip()))

    @staticmethod
    def _looks_like_clause(text: str) -> bool:
        return bool(_CLAUSE_RE.match(text.strip()))

    def _plan_text_block(
        self,
        content: str,
        page_num: int,
        section_path: str,
        block_no: int,
    ) -> list[TextChunk]:
        chunk_type = "clause" if self._looks_like_clause(content) else "paragraph"
        base_block_id = self._build_block_id(page_num, block_no, chunk_type, content)
        return self._split_preserving_semantics(
            content=content,
            page_num=page_num,
            section_path=section_path,
            chunk_type=chunk_type,
            parent_block_id=base_block_id,
            clause_no=self._extract_clause_no(content),
        )

    def _plan_table(
        self,
        table: ExtractedTable,
        page_num: int,
        section_path: str,
    ) -> list[TextChunk]:
        """委托 table_linearizer 完成行级线性化, 此处仅负责拼前缀与切分.: 旧实现假设"首行=表头"并按列下标硬配对, 会因合并单元格/标签-值网格
 产生 `姓名=参加工作时间` 类错配; 现改为可靠表头表才走 `表头=值`, 其余退化为
 忠实结构行文本, 由 linearize_table 统一判定.
 """
        chunks: list[TextChunk] = []
        parent_block_id = f"table:{page_num}:{table.table_index}"
        for row in linearize_table(table):
            content = f"表格第{row.row_no}行：{row.content}"
            chunks.extend(
                self._split_preserving_semantics(
                    content=content,
                    page_num=page_num,
                    section_path=section_path,
                    chunk_type="table_row_with_header",
                    parent_block_id=parent_block_id,
                    clause_no=f"table-{table.table_index}-row-{row.row_no}",
                )
            )
        return chunks

    def _split_preserving_semantics(
        self,
        content: str,
        page_num: int,
        section_path: str,
        chunk_type: str,
        parent_block_id: str,
        clause_no: str | None,
    ) -> list[TextChunk]:
        normalized = content.strip()
        if not normalized:
            return []
        if self._within_limit(normalized):
            return [self._make_chunk(normalized, page_num, section_path, chunk_type, parent_block_id, clause_no, 0)]

        if chunk_type == "paragraph":
            raw_chunks = self._paragraph_chunker.split(normalized)
            return [
                self._make_chunk(
                    item.content.strip(),
                    page_num,
                    section_path,
                    chunk_type,
                    parent_block_id,
                    clause_no,
                    idx,
                )
                for idx, item in enumerate(raw_chunks)
                if item.content.strip()
            ]

        parts = [part.strip() for part in re.split(r"(?<=[。；;])", normalized) if part and part.strip()]
        if len(parts) <= 1:
            parts = [normalized[i : i + self.max_item_chars] for i in range(0, len(normalized), self.max_item_chars)]

        merged: list[str] = []
        current = ""
        for part in parts:
            tentative = f"{current}{part}"
            if current and not self._within_limit(tentative):
                merged.append(current.strip())
                current = part
            else:
                current = tentative
        if current.strip():
            merged.append(current.strip())

        return [
            self._make_chunk(
                item,
                page_num,
                section_path,
                chunk_type,
                parent_block_id,
                clause_no,
                idx,
            )
            for idx, item in enumerate(merged)
            if item
        ]

    def _within_limit(self, content: str) -> bool:
        return len(content) <= self.max_item_chars and (len(content) // 2) <= self.max_item_tokens

    @staticmethod
    def _extract_clause_no(text: str) -> str | None:
        match = _CLAUSE_RE.match(text.strip())
        if not match:
            return None
        return match.group(1)

    @staticmethod
    def _build_block_id(page_num: int, block_no: int, chunk_type: str, content: str) -> str:
        digest = hashlib.sha1(content[:200].encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{chunk_type}:{page_num}:{block_no}:{digest}"

    @staticmethod
    def _chunk_fingerprint(
        *,
        content: str,
        page_num: int,
        section_path: str,
        chunk_type: str,
        parent_block_id: str,
        clause_no: str | None,
        sub_index: int,
    ) -> str:
        raw = "|".join(
            [
                settings.VECTOR_EMBEDDING_VERSION,
                chunk_type,
                str(page_num),
                section_path or "",
                parent_block_id or "",
                clause_no or "",
                str(sub_index),
                content,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _make_chunk(
        self,
        content: str,
        page_num: int,
        section_path: str,
        chunk_type: str,
        parent_block_id: str,
        clause_no: str | None,
        sub_index: int,
    ) -> TextChunk:
        clean_content = content.replace("\x00", "").strip()
        fingerprint = self._chunk_fingerprint(
            content=clean_content,
            page_num=page_num,
            section_path=section_path,
            chunk_type=chunk_type,
            parent_block_id=parent_block_id,
            clause_no=clause_no,
            sub_index=sub_index,
        )
        metadata = {
            "page_num": page_num,
            "source": f"page_{page_num}",
            "chunk_type": chunk_type,
            "parent_block_id": parent_block_id,
            "section_path": section_path,
            "clause_no": clause_no,
            "estimated_tokens": max(1, len(clean_content) // 2),
            "chunk_fingerprint": fingerprint,
        }
        return TextChunk(
            chunk_index=0,
            content=clean_content,
            char_count=len(clean_content),
            start_char=0,
            end_char=len(clean_content),
            metadata=metadata,
        )


def summarize_chunk_lengths(chunks: Iterable[TextChunk]) -> dict[str, int]:
    """生成 chunk 长度摘要，便于心跳/日志记录。"""
    lengths = [len(chunk.content) for chunk in chunks]
    if not lengths:
        return {"count": 0, "max_chars": 0, "avg_chars": 0}
    avg = sum(lengths) // len(lengths)
    summary = {"count": len(lengths), "max_chars": max(lengths), "avg_chars": avg}
    logger.debug(f"ChunkPlanner 长度摘要: {summary}")
    return summary
