"""scoring chunk 结构化载荷（evidence bundle 最小版, 阶段1.5）。

职责: 评分表 chunk 结构化载荷(网格/lot/表格指纹/页范围)的构建、读取与指纹计算;
      收口"markdown 表格文本 -> 二维网格"的单源实现。
不做: 不解析评分项(scoring_table_parser 职责)、不渲染 markdown、不访问数据库。

定位: 文档处理与要求提取两阶段间的交接契约从"markdown 文本纹协议(渲染->正则回解,
有损往返)"升级为随 chunk 持久化的结构化载荷; 载荷缺失时消费方回退 markdown
解析(双轨兼容存量 chunk), 载荷网格按构造与 markdown 解析结果等价。
"""

from __future__ import annotations

import hashlib
from typing import Any

PAYLOAD_KEY = "scoring_payload"
PAYLOAD_VERSION = 1


def markdown_table_to_grid(markdown: str) -> list[list[str]]:
    """把 markdown 表格行解析为二维数组(跳过页码标注行与 | --- | 分隔行)。"""
    grid: list[list[str]] = []
    for line in (markdown or "").split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        if set(s.replace("|", "").replace("-", "").replace(" ", "")) == set():
            continue  # | --- | 分隔行
        cells = [c.strip() for c in s.strip().strip("|").split("|")]
        grid.append(cells)
    return grid


def table_fingerprint(grid: list[list[str]]) -> str:
    """归一化网格指纹(逻辑表副本检测, 阶段4影子对比的判定源)。"""
    normalized = "\x1e".join(
        "\x1f".join("".join(str(cell or "").split()) for cell in row) for row in grid
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def build_scoring_payload(
    markdown: str,
    *,
    lot: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    source: str = "assembled",
    row_pages: list[int | None] | None = None,
) -> dict[str, Any] | None:
    """从最终 markdown 构建结构化载荷; 无有效表格行返回 None。

 网格取自最终 markdown 的确定性解析, 与消费方回退路径(同一实现)按构造等价,
 保证双轨影子一致; 后续阶段可在载荷内增补 bbox/scheme 等字段(version 升位)。
 模板边界 scheme_boundary_id 和 scheme 字段通过 set_payload_scope_metadata 后置写入。
 """
    grid = markdown_table_to_grid(markdown)
    if not grid:
        return None
    payload: dict[str, Any] = {
        "version": PAYLOAD_VERSION,
        "grid": grid,
        "lot": lot,
        "page_start": page_start,
        "page_end": page_end,
        "source": source,
        "table_fingerprint": table_fingerprint(grid),
    }
    if row_pages is not None and len(row_pages) == len(grid):
        payload["row_pages"] = [page if isinstance(page, int) and page >= 1 else None for page in row_pages]
    return payload


def set_payload_scope_metadata(
    payload: dict[str, Any],
    *,
    scheme_boundary_id: str | None = None,
    scheme: str | None = None,
) -> None:
    """后置写入装配范围元信息到已有载荷（Task 5 M1/I8）。

 职责: 把 scheme_boundary_id（模板边界标识）和 scheme（方案归属）后置注入，
 避免 build_scoring_payload 参数超过 6 个；各调用方可按需写入两者之一或全部。
 不做: 不重建载荷、不修改 grid/fingerprint 等核心字段。
 """
    if not isinstance(payload, dict):
        return
    if scheme_boundary_id is not None:
        payload["scheme_boundary_id"] = scheme_boundary_id
    if scheme is not None:
        payload["scheme"] = scheme


def write_chunk_scope_metadata(
    chunk: Any,
    *,
    scheme_boundary_id: str | None = None,
    scheme: str | None = None,
) -> None:
    """将 scheme 元信息写入 chunk 的持久化载荷（D8 — ORM + TextChunk 双路）。

    职责: 对 ORM Chunk（chunk.chunk_metadata）做浅拷贝后重新赋值以触发 SQLAlchemy 脏标记；
          对内存 TextChunk（chunk.metadata dict）等价更新同一 payload。
          Task6 通过 payload_from_chunk 读取 payload.scheme。
    不做: 不重建 payload 结构、不修改 grid/fingerprint 等核心字段、不访问 DB/LLM。
    """
    # 路径 1: ORM Chunk（chunk.chunk_metadata 是 mapped column）
    orm_meta = getattr(chunk, "chunk_metadata", None)
    if isinstance(orm_meta, dict):
        payload = orm_meta.get(PAYLOAD_KEY)
        if isinstance(payload, dict):
            set_payload_scope_metadata(payload, scheme_boundary_id=scheme_boundary_id, scheme=scheme)
            # 浅拷贝后重新赋值，确保 SQLAlchemy 侦测到对象替换（脏标记）
            chunk.chunk_metadata = {**orm_meta}
        return

    # 路径 2: 内存 TextChunk（chunk.metadata 是普通 dict）
    mem_meta = getattr(chunk, "metadata", None)
    if isinstance(mem_meta, dict):
        payload = mem_meta.get(PAYLOAD_KEY)
        if isinstance(payload, dict):
            set_payload_scope_metadata(payload, scheme_boundary_id=scheme_boundary_id, scheme=scheme)


def write_parse_diagnostics(chunk: Any, diagnostics: list[str]) -> None:
    """向 chunk scoring_payload 追加 parse_diagnostics（partial rejection）。

 职责: 将结构化诊断串追加到持久化载荷的 parse_diagnostics 列表；
 不记录完整评分行内容，仅追加无正文诊断串。
 不做: 不修改 grid/fingerprint/scheme 等核心字段、不访问 DB/LLM。
 支持 ORM Chunk (chunk_metadata) 和内存 TextChunk (metadata) 两种容器。
 """
    if not diagnostics:
        return
    # ORM Chunk path
    orm_meta = getattr(chunk, "chunk_metadata", None)
    if isinstance(orm_meta, dict):
        payload = orm_meta.get(PAYLOAD_KEY)
        if isinstance(payload, dict):
            existing = payload.get("parse_diagnostics") or []
            payload["parse_diagnostics"] = list(existing) + list(diagnostics)
            chunk.chunk_metadata = {**orm_meta}
        return
    # Memory TextChunk path
    mem_meta = getattr(chunk, "metadata", None)
    if isinstance(mem_meta, dict):
        payload = mem_meta.get(PAYLOAD_KEY)
        if isinstance(payload, dict):
            existing = payload.get("parse_diagnostics") or []
            payload["parse_diagnostics"] = list(existing) + list(diagnostics)


def payload_from_chunk(chunk: Any) -> dict[str, Any] | None:
    """从 DB Chunk(chunk_metadata 列)或内存 TextChunk(metadata dict)读取载荷。"""
    for container in (
        getattr(chunk, "chunk_metadata", None),
        getattr(chunk, "metadata", None),
    ):
        if isinstance(container, dict):
            payload = container.get(PAYLOAD_KEY)
            if isinstance(payload, dict) and payload.get("grid"):
                return payload
    return None


def override_column_roles(
    roles: dict[str, int | None], column_roles: dict | None
) -> dict[str, int | None]:
    """用装配器已验证列角色覆盖解析器词面绑定; 载荷缺失或非法保持原判。

 装配器 ColumnRoles(score/rule/name/group) → 解析器角色键(score/rule/name+
 content/category); 无有效分值角色的载荷整组放弃覆盖(不可信)。
 """
    if not isinstance(column_roles, dict):
        return roles

    def _idx(key: str) -> int | None:
        value = column_roles.get(key)
        return value if isinstance(value, int) and value >= 0 else None

    score = _idx("score")
    if score is None:
        return roles
    merged = dict(roles)
    merged["score"] = score
    rule = _idx("rule")
    name = _idx("name")
    group = _idx("group")
    if rule is not None:
        merged["rule"] = rule
    if name is not None:
        merged["name"] = name
        merged["content"] = name
    if group is not None:
        merged["category"] = group
    return merged


def grid_from_payload(payload: dict[str, Any] | None) -> list[list[str]] | None:
    """从载荷取网格(防御非法结构); 不可用返回 None(消费方回退 markdown)。"""
    if not isinstance(payload, dict):
        return None
    grid = payload.get("grid")
    if not isinstance(grid, list) or not grid:
        return None
    out: list[list[str]] = []
    for row in grid:
        if not isinstance(row, list):
            return None
        out.append([str(cell or "") for cell in row])
    return out


__all__ = [
    "PAYLOAD_KEY",
    "PAYLOAD_VERSION",
    "build_scoring_payload",
    "grid_from_payload",
    "markdown_table_to_grid",
    "override_column_roles",
    "payload_from_chunk",
    "set_payload_scope_metadata",
    "table_fingerprint",
    "write_chunk_scope_metadata",
    "write_parse_diagnostics",
]
