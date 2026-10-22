"""分块编码文本构造器(P2 上下文块头注入).

职责: 由 TextChunk 内容 + 元数据(section_path / clause_no / chunk_type)
      构造向量化输入文本,使块向量携带章节语境,缓解块独立编码缺上下文问题.
不做: 切块 / 写库 / 质量判定 / 改写 chunk.content 原文.

设计要点:
- 块头(header)固定置于文本最前,保证 CPU 路径 max_seq_length=512 截断时
  最不重要的尾部先被截掉,而非把上下文截掉.
- 块头仅参与"编码输入",不写入数据库 content 字段——存储/引用/提取文本保持干净.
- section_path 已在 ChunkPlanner._make_chunk 中写入 metadata,无需再次推断.
"""

from __future__ import annotations

from app.core.parser.text_chunker import TextChunk

_MAX_HEADER_CHARS = 80
_HEADER_PREFIX = "["
_HEADER_SUFFIX = "] "


def build_embedding_text(chunk: TextChunk) -> str:
    """根据 chunk 元数据为向量化输入拼上下文块头.

    Args:
        chunk: 已规划的 TextChunk(必须含 content,可选含 metadata.section_path / clause_no).

    Returns:
        编码用文本.元数据为空时原样返回 content.
    """
    content = chunk.content if chunk and chunk.content else ""
    metadata = chunk.metadata if chunk and chunk.metadata else {}

    header = _compose_header(metadata)
    if not header:
        return content

    return f"{_HEADER_PREFIX}{header}{_HEADER_SUFFIX}{content}"


def _compose_header(metadata: dict) -> str:
    """从 metadata 拼接简短上下文头.

    优先级: section_path > clause_no.两者都为空时返回空串.
    chunk_type 单独存在意义有限(已隐含在 section_path / clause 编号中),不拼.
    """
    section_path = _normalize(metadata.get("section_path"))
    clause_no = _normalize(metadata.get("clause_no"))

    parts: list[str] = []
    if section_path:
        parts.append(section_path)
    if clause_no and clause_no not in section_path:
        parts.append(clause_no)

    if not parts:
        return ""

    header = " > ".join(parts)
    if len(header) > _MAX_HEADER_CHARS:
        header = header[: _MAX_HEADER_CHARS - 1] + "…"
    return header


def _normalize(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text
