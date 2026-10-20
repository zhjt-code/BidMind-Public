"""任务级只读缓存。

职责: 在单次 composer/task 内缓存按 id 读取的草稿、项目背景与图片元数据。
不做: 不缓存绑定选择结果，不跨项目/跨任务复用，本轮不缓存 chunk 正文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

_MISSING = object()


@dataclass
class ComposeTaskCache:
    """
    职责: 单任务内按 id 缓存只读行并记录负缓存。
    不做: 不替代 confirmed/rejected、祖先继承或 global/orphan 权威策略。
    """

    project_id: int
    chunk_cache_enabled: bool = False
    _drafts: dict[int, Any] = field(default_factory=dict)
    _draft_known: set[int] = field(default_factory=set)
    _project_context: Any = _MISSING
    _image_meta: dict[int, Any] = field(default_factory=dict)
    _image_known: set[int] = field(default_factory=set)
    hits: dict[str, int] = field(default_factory=lambda: {"draft": 0, "project_context": 0, "image": 0, "chunk": 0})
    misses: dict[str, int] = field(default_factory=lambda: {"draft": 0, "project_context": 0, "image": 0, "chunk": 0})

    def _ensure_project(self, project_id: int) -> None:
        if int(project_id) != int(self.project_id):
            raise ValueError("compose cache is project-scoped")

    def get_current_draft(self, node_id: int) -> tuple[bool, Any]:
        if node_id in self._draft_known:
            self.hits["draft"] += 1
            return True, self._drafts.get(node_id)
        self.misses["draft"] += 1
        return False, None

    def set_current_draft(self, node_id: int, draft: Any) -> None:
        self._draft_known.add(node_id)
        self._drafts[node_id] = draft

    def invalidate_current_draft(self, node_id: int) -> None:
        self._draft_known.discard(node_id)
        self._drafts.pop(node_id, None)

    def preload_current_drafts(self, drafts_by_node_id: dict[int, Any]) -> None:
        for node_id, draft in drafts_by_node_id.items():
            self.set_current_draft(int(node_id), draft)

    def get_project_context(self, project_id: int) -> tuple[bool, Any]:
        self._ensure_project(project_id)
        if self._project_context is not _MISSING:
            self.hits["project_context"] += 1
            return True, self._project_context
        self.misses["project_context"] += 1
        return False, None

    def set_project_context(self, project_id: int, clauses: Any) -> None:
        self._ensure_project(project_id)
        self._project_context = clauses

    def get_image_metadata(self, material_id: int) -> tuple[bool, Any]:
        if material_id in self._image_known:
            self.hits["image"] += 1
            return True, self._image_meta.get(material_id)
        self.misses["image"] += 1
        return False, None

    def set_image_metadata(self, material_id: int, images: Any) -> None:
        self._image_known.add(material_id)
        self._image_meta[material_id] = images

    def record_chunk_access(self, *, hit: bool) -> None:
        if self.chunk_cache_enabled and hit:
            self.hits["chunk"] += 1
            return
        self.misses["chunk"] += 1

    def source_fingerprint(self, payload: Any) -> dict[str, Any]:
        return {
            "source_clause_ids": list(getattr(payload, "source_clause_ids", None) or []),
            "source_material_ids": list(getattr(payload, "source_material_ids", None) or []),
            "gate_flags": getattr(payload, "gate_flags", None),
            "planning_mode": getattr(payload, "planning_mode", None),
            "failure_state": getattr(payload, "failure_state", None),
        }
