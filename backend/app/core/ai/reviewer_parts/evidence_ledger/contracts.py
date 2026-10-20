"""
职责: 承载项目级可引用事实原子及按分面匹配
不做: 检索、LLM、符合性判断、ORM 写入
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from loguru import logger

from app.core.bid_envelope import BID_PART_LABELS
from app.core.retriever.evidence_coverage import (
    EvidenceFacet,
    _literal_probes,
    facet_hit_prepared,
    normalize_text,
)
from app.core.retriever.material_key import (
    MATCH_STRONG,
    canonical_material_key,
    match_key_in_compact,
    normalize_material_compact,
)

# ---------------------------------------------------------------------------
# PreparedFacet: 分面预规范化，避免热路径重复 normalize
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreparedFacet:
    """分面预规范化缓存。两套规范化不可互换：norm_* 仅去空白，canon_key NFKC+去噪。"""

    key: str
    query: str
    norm_key: str       # normalize_text(key)  — 只去空白，用于尾部探针
    norm_query: str     # normalize_text(query) — 用于 facet_hit_prepared
    probes: tuple[str, ...]  # _literal_probes(key)
    canon_key: str      # canonical_material_key(key) — NFKC+去噪


def prepare_facet(facet: EvidenceFacet) -> PreparedFacet:
    """将 EvidenceFacet 预规范化为 PreparedFacet。"""
    return PreparedFacet(
        key=facet.key,
        query=facet.query,
        norm_key=normalize_text(facet.key),
        norm_query=normalize_text(facet.query),
        probes=_literal_probes(facet.key),
        canon_key=canonical_material_key(facet.key),
    )


def facet_covers_normalized(
    prepared_facet: PreparedFacet,
    norm_text: str,
    norm_sub_query: str,
) -> bool:
    """实现：分面是否覆盖已规范化文本（比 facet_covers_text 快：不重复 normalize）。

 与 facet_covers_text 等价：
 - 先走 facet_hit_prepared（子查询精确匹配 + 字面探针）
 - 再试尾部 4/6 字探针（长键补充）
 """
    sub_queries = {norm_sub_query} if norm_sub_query else set()
    if facet_hit_prepared(prepared_facet.norm_query, prepared_facet.probes, sub_queries, [norm_text]):
        return True
    if norm_text and prepared_facet.norm_key and len(prepared_facet.norm_key) > 4:
        if prepared_facet.norm_key[-4:] in norm_text:
            return True
        if len(prepared_facet.norm_key) > 6 and prepared_facet.norm_key[-6:] in norm_text:
            return True
    return False


def facet_covers_text(
    facet: EvidenceFacet,
    content: str,
    sub_query: str = "",
) -> bool:
    """分面是否覆盖文本：先走既有 facet_hit，长键再试尾部名词探针。委托 facet_covers_normalized。

 只把待判定正文送进判定。facet.key 自身的归一化串是探针的超集,
 混进去会使覆盖判定恒真。
 """
    prepared = prepare_facet(facet)
    norm_text = normalize_text(content)
    norm_sub = normalize_text(sub_query) if sub_query else ""
    return facet_covers_normalized(prepared, norm_text, norm_sub)


_GRADE_LABEL = {"material": "材料级", "mention": "提及级"}
_MAX_MERGED_PAGES = 10
_OVERFLOW_RESERVE = 80
_LEDGER_HEADER = (
    "## 本项目投标文件已核实材料（硬证据）",
    "材料级已核实存在，禁止写未提供/未发现；提及级仅供定位。"
    "模型只判适用性（主体/有效期/等级/专业类别）。如认为不适用，必须点名反驳理由。",
)


def _location_label(atom: "EvidenceAtom") -> str:
    """(卷,页) 展示；整本无卷角色时只保留页码。"""
    page_label = f"第{atom.page}页" if atom.page is not None else "页码未知"
    part = getattr(atom, "bid_part", None)
    if not part:
        return page_label
    volume = BID_PART_LABELS.get(str(part), str(part))
    return f"{volume}{page_label}"


def _format_atom_line(atom: "EvidenceAtom", extra_pages_note: str = "") -> str:
    """渲染单条账本行；摘录截到 80 字，可选附加同 kind 归并页码。"""
    grade_label = _GRADE_LABEL.get(atom.grade, atom.grade)
    excerpt = (atom.excerpt or "").replace("\n", " ").strip()
    if len(excerpt) > 80:
        excerpt = excerpt[:80] + "…"
    line = f"- {atom.kind} / {_location_label(atom)} / {grade_label} / 已核实 / {excerpt}"
    if extra_pages_note:
        return f"{line} / {extra_pages_note}"
    return line


def _merged_pages_note(pages: Sequence[int]) -> str:
    unique = list(dict.fromkeys(pages))
    if not unique:
        return ""
    shown = unique[:_MAX_MERGED_PAGES]
    labels = "、".join(f"第{p}页" for p in shown)
    if len(unique) > _MAX_MERGED_PAGES:
        return f"另见{labels}等{len(unique)}页"
    return f"另见{labels}"


def _kind_sort_key(
    kind: str,
    group: Sequence["EvidenceAtom"],
    facet_keys: set[str],
) -> tuple:
    exact = 0 if kind in facet_keys else 1
    material = 0 if any(atom.grade == "material" for atom in group) else 1
    return (exact, material, kind)


def _select_kind_representatives(
    group: Sequence["EvidenceAtom"],
    per_kind_limit: int,
) -> tuple[list["EvidenceAtom"], list[int]]:
    """材料级、不同页优先取代表原子，其余页码留给归并注记。"""
    limit = max(1, int(per_kind_limit))
    ordered = sorted(
        group,
        key=lambda atom: (
            0 if atom.grade == "material" else 1,
            atom.page is None,
            atom.page if atom.page is not None else 10**9,
            atom.chunk_id if atom.chunk_id is not None else 10**9,
        ),
    )
    reps: list[EvidenceAtom] = []
    seen_pages: set[int | None] = set()
    deferred: list[EvidenceAtom] = []
    for atom in ordered:
        if len(reps) >= limit:
            deferred.append(atom)
            continue
        if atom.page in seen_pages:
            deferred.append(atom)
            continue
        reps.append(atom)
        seen_pages.add(atom.page)
    for atom in deferred:
        if len(reps) >= limit:
            break
        reps.append(atom)
        seen_pages.add(atom.page)
    rep_pages = {rep.page for rep in reps}
    leftover_pages = list(dict.fromkeys(
        atom.page
        for atom in ordered
        if atom not in reps
        and atom.page is not None
        and atom.page not in rep_pages
    ))
    return reps, leftover_pages


def _kind_render_lines(
    group: Sequence["EvidenceAtom"],
    per_kind_limit: int,
) -> tuple[str, list[str]]:
    """每种先一行(页码归并)，额外代表摘录留给预算余量。"""
    reps, leftover_pages = _select_kind_representatives(group, per_kind_limit)
    first = reps[0]
    extra_pages = [
        page
        for page in dict.fromkeys(
            [*(atom.page for atom in reps[1:]), *leftover_pages]
        )
        if page is not None and page != first.page
    ]
    primary = _format_atom_line(first, _merged_pages_note(extra_pages))
    extras = [_format_atom_line(atom) for atom in reps[1:]]
    return primary, extras


@dataclass(frozen=True)
class EvidenceAtom:
    """一条可跨条款引用的事实原子。"""

    kind: str
    grade: str
    page: int | None
    chunk_id: int | None
    excerpt: str
    source_requirement_ids: tuple[int, ...]
    document_id: int | None = None
    bid_part: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "canonical_material_key": self.kind,
            "grade": self.grade,
            "page": self.page,
            "physical_page": self.page,
            "chunk_id": self.chunk_id,
            "excerpt": self.excerpt,
            "source_requirement_ids": list(self.source_requirement_ids),
            "document_id": self.document_id,
            "bid_part": self.bid_part,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "EvidenceAtom":
        ids = raw.get("source_requirement_ids") or ()
        page = raw.get("page", raw.get("physical_page"))
        return cls(
            kind=str(raw.get("kind") or raw.get("canonical_material_key") or ""),
            grade=str(raw.get("grade") or "mention"),
            page=page,
            chunk_id=raw.get("chunk_id"),
            excerpt=str(raw.get("excerpt") or ""),
            source_requirement_ids=tuple(int(i) for i in ids),
            document_id=raw.get("document_id"),
            bid_part=raw.get("bid_part"),
        )


# ---------------------------------------------------------------------------
# PreparedAtom: 原子预规范化，避免热路径重复 normalize
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreparedAtom:
    """原子预规范化缓存。两套规范化不可互换：norm_* 仅去空白，compact_* NFKC+去空白。"""

    atom: EvidenceAtom
    compact_kind: str    # normalize_material_compact(atom.kind)
    compact_excerpt: str # normalize_material_compact(atom.excerpt)
    norm_kind: str       # normalize_text(atom.kind)
    norm_excerpt: str    # normalize_text(atom.excerpt)


def prepare_atom(atom: EvidenceAtom) -> PreparedAtom:
    """将 EvidenceAtom 预规范化为 PreparedAtom。"""
    return PreparedAtom(
        atom=atom,
        compact_kind=normalize_material_compact(atom.kind),
        compact_excerpt=normalize_material_compact(atom.excerpt),
        norm_kind=normalize_text(atom.kind),
        norm_excerpt=normalize_text(atom.excerpt),
    )


def _atom_identity(atom: EvidenceAtom) -> tuple[str, int | None, int | None]:
    return (atom.kind, atom.page, atom.chunk_id)


def _prepared_atom_matches(prepared_atom: PreparedAtom, prepared_facet: PreparedFacet) -> bool:
    """与条款读取账本时的五段判定：键相等、键强匹配、摘录强匹配、分面覆盖（均基于预规范化）。

    五段顺序：
    1) atom.kind 为空 → False
    2) facet.key == atom.kind 原文相等（最快路径）
    3) match_key_in_compact(canon_key, compact_kind) == MATCH_STRONG
    4) match_key_in_compact(canon_key, compact_excerpt) == MATCH_STRONG
    5) facet_covers_normalized(prepared_facet, norm_excerpt, norm_kind)
       sub_query = atom.kind（与今天 facet_covers_text(facet, excerpt, atom.kind) 一致）
    """
    if not prepared_atom.atom.kind:
        return False
    if prepared_facet.key == prepared_atom.atom.kind:
        return True
    if match_key_in_compact(prepared_facet.canon_key, prepared_atom.compact_kind) == MATCH_STRONG:
        return True
    if match_key_in_compact(prepared_facet.canon_key, prepared_atom.compact_excerpt) == MATCH_STRONG:
        return True
    return facet_covers_normalized(prepared_facet, prepared_atom.norm_excerpt, prepared_atom.norm_kind)


def _atom_matches_facet(atom: EvidenceAtom, facet: EvidenceFacet) -> bool:
    """与条款读取账本时的四段判定相同。委托 _prepared_atom_matches（命中规则单源）。"""
    return _prepared_atom_matches(prepare_atom(atom), prepare_facet(facet))


def _scan_atoms_for_facets(
    atoms: Sequence[EvidenceAtom],
    facets: Sequence[EvidenceFacet],
) -> list[EvidenceAtom]:
    selected: list[EvidenceAtom] = []
    seen: set[tuple[str, int | None, int | None]] = set()
    for atom in atoms:
        if not atom.kind:
            continue
        matched = False
        for facet in facets:
            if _atom_matches_facet(atom, facet):
                matched = True
                break
        if not matched:
            continue
        identity = _atom_identity(atom)
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(atom)
    return selected


def _atoms_from_probe_index(
    atoms: Sequence[EvidenceAtom],
    facets: Sequence[EvidenceFacet],
    index: Mapping[tuple[str, str], frozenset[tuple[str, int | None, int | None]]],
) -> list[EvidenceAtom]:
    wanted: set[tuple[str, int | None, int | None]] = set()
    for facet in facets:
        wanted.update(index[(facet.key, facet.query)])
    if not wanted:
        return []
    selected: list[EvidenceAtom] = []
    seen: set[tuple[str, int | None, int | None]] = set()
    for atom in atoms:
        identity = _atom_identity(atom)
        if identity not in wanted or identity in seen:
            continue
        seen.add(identity)
        selected.append(atom)
    return selected


@dataclass(frozen=True)
class EvidenceLedger:
    """项目级证据账本。空账本对所有查询返回空列表。"""

    atoms: tuple[EvidenceAtom, ...] = ()
    probe_facets: tuple[EvidenceFacet, ...] | None = None
    probe_index: tuple[
        tuple[
            tuple[str, str],
            frozenset[tuple[str, int | None, int | None]],
        ],
        ...,
    ] | None = None

    def atoms_for_facets(self, facets: Sequence[EvidenceFacet]) -> list[EvidenceAtom]:
        """选出 kind/摘录命中任一目标分面的原子。"""
        facet_tuple = tuple(facets or ())
        if not facet_tuple or not self.atoms:
            return []
        if self.probe_index is not None:
            index = dict(self.probe_index)
            missing = [
                (f.key, f.query) for f in facet_tuple if (f.key, f.query) not in index
            ]
            if not missing:
                return _atoms_from_probe_index(self.atoms, facet_tuple, index)
            logger.debug("探针索引未覆盖分面: {}", missing)
        return _scan_atoms_for_facets(self.atoms, facet_tuple)

    def material_atoms_for_facets(
        self, facets: Sequence[EvidenceFacet]
    ) -> list[EvidenceAtom]:
        return [atom for atom in self.atoms_for_facets(facets) if atom.grade == "material"]

    @staticmethod
    def to_prompt_block(atoms: Sequence[EvidenceAtom]) -> str:
        if not atoms:
            return ""
        lines = list(_LEDGER_HEADER)
        for atom in atoms:
            lines.append(_format_atom_line(atom))
        return "\n".join(lines)

    @staticmethod
    def to_budgeted_prompt_block(
        atoms: Sequence[EvidenceAtom],
        facets: Sequence[EvidenceFacet],
        *,
        char_budget: int,
        per_kind_limit: int,
    ) -> str:
        """
        职责: 按精确键/材料级优先与预算上限，把注入原子渲染为有界 prompt 账本块
        不做: 原子选取过滤(atoms_for_facets 职责)、裁剪机器可读原子集、检索与判断
        """
        if not atoms:
            return ""
        grouped: dict[str, list[EvidenceAtom]] = defaultdict(list)
        for atom in atoms:
            if atom.kind:
                grouped[atom.kind].append(atom)
        if not grouped:
            return ""

        facet_keys = {facet.key for facet in (facets or ()) if getattr(facet, "key", "")}
        kinds = sorted(
            grouped,
            key=lambda kind: _kind_sort_key(kind, grouped[kind], facet_keys),
        )
        lines = list(_LEDGER_HEADER)
        budget = max(0, int(char_budget))
        prepared: list[tuple[str, list[str], int]] = []
        for kind in kinds:
            group = grouped[kind]
            primary, extras = _kind_render_lines(group, per_kind_limit)
            mention_count = sum(1 for atom in group if atom.grade != "material")
            prepared.append((primary, extras, mention_count))

        omitted_kinds = 0
        omitted_mention = 0
        included_extras: list[str] = []
        for index, (primary, extras, mention_count) in enumerate(prepared):
            remaining_after = len(prepared) - index - 1
            room = budget - _OVERFLOW_RESERVE if remaining_after > 0 else budget
            candidate = "\n".join(lines + [primary])
            already_has_kind = len(lines) > len(_LEDGER_HEADER)
            if len(candidate) > room and already_has_kind:
                rest = prepared[index:]
                omitted_kinds = len(rest)
                omitted_mention = sum(item[2] for item in rest)
                break
            lines.append(primary)
            included_extras.extend(extras)

        overflow_room = budget - (_OVERFLOW_RESERVE if omitted_kinds else 0)
        for extra in included_extras:
            candidate = "\n".join(lines + [extra])
            if len(candidate) > overflow_room:
                break
            lines.append(extra)

        if omitted_kinds:
            lines.append(
                f"- 另有{omitted_kinds}类共{omitted_mention}条提及级原子因篇幅省略"
            )
        return "\n".join(lines)
