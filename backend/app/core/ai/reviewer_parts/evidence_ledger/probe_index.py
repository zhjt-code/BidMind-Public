"""
职责: 按分面键与查询把已有原子收成查找表，支持增量扩展
不做: 改写命中规则、删除旧命中、检索、符合性判断
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Sequence

from loguru import logger

from app.core.ai.reviewer_parts.evidence_ledger.contracts import (
    EvidenceAtom,
    EvidenceLedger,
    PreparedFacet,
    _atom_identity,
    prepare_atom,
    prepare_facet,
    _prepared_atom_matches,
)
from app.core.retriever.evidence_coverage import EvidenceFacet


def bind_probe_index(
    ledger: EvidenceLedger,
    facets: Sequence[EvidenceFacet],
) -> EvidenceLedger:
    """用与逐条扫描相同的判定，把原子挂到 (键, 查询) 上。

    先对探针与原子各预规范化一次，再用 _prepared_atom_matches 做 原子×探针，
    避免在循环内重复调用 normalize_text / normalize_material_compact。
    """
    t0 = time.monotonic()
    probes: list[EvidenceFacet] = []
    seen: set[tuple[str, str]] = set()
    for facet in facets:
        if not facet.key:
            continue
        probe_id = (facet.key, facet.query)
        if probe_id in seen:
            continue
        seen.add(probe_id)
        probes.append(facet)

    prepared_probes: list[PreparedFacet] = [prepare_facet(f) for f in probes]
    prepared_atoms = [prepare_atom(a) for a in ledger.atoms if a.kind]

    buckets: dict[tuple[str, str], set[tuple[str, int | None, int | None]]] = {
        (pp.key, pp.query): set() for pp in prepared_probes
    }
    for pa in prepared_atoms:
        identity = _atom_identity(pa.atom)
        for pp in prepared_probes:
            if _prepared_atom_matches(pa, pp):
                buckets[(pp.key, pp.query)].add(identity)

    ms = (time.monotonic() - t0) * 1000
    logger.info("探针索引: probes={} atoms={} ms={:.1f}", len(probes), len(prepared_atoms), ms)

    probe_index = tuple(
        ((pp.key, pp.query), frozenset(buckets[(pp.key, pp.query)]))
        for pp in prepared_probes
    )
    return replace(
        ledger,
        probe_facets=tuple(probes),
        probe_index=probe_index,
    )


def extend_probe_index(
    ledger: EvidenceLedger,
    atoms: Sequence[EvidenceAtom],
) -> EvidenceLedger:
    """仅对传入原子评估全部已有探针并并入既有桶。

    职责: 只对传入原子评估全部已有探针并并入既有桶
    不做: 改写命中规则、删除旧命中、检索

    等价性: 绑定是 (atoms, probes) 的纯函数；增量并入等价于全量重绑，
    因为合并只会让 excerpt 从空变非空、kind 不变，谓词对 excerpt 单调
    （更多文本只增加真值），旧命中保持；扩大探针覆盖只新增索引键。
    """
    if not ledger.probe_facets:
        return ledger
    if ledger.probe_index is None:
        return bind_probe_index(ledger, ledger.probe_facets)

    t0 = time.monotonic()
    prepared_probes: list[PreparedFacet] = [prepare_facet(f) for f in ledger.probe_facets]
    prepared_atoms = [prepare_atom(a) for a in atoms if a.kind]

    # 从旧桶拷贝 set，不删除旧身份（摘录只增不减，旧命中保持）
    old_index = dict(ledger.probe_index)
    buckets: dict[tuple[str, str], set[tuple[str, int | None, int | None]]] = {
        probe_id: set(hits) for probe_id, hits in old_index.items()
    }

    for pa in prepared_atoms:
        identity = _atom_identity(pa.atom)
        for pp in prepared_probes:
            probe_id = (pp.key, pp.query)
            if probe_id not in buckets:
                buckets[probe_id] = set()
            if _prepared_atom_matches(pa, pp):
                buckets[probe_id].add(identity)

    ms = (time.monotonic() - t0) * 1000
    logger.info(
        "探针索引: probes={} atoms={} ms={:.1f}",
        len(ledger.probe_facets),
        len(prepared_atoms),
        ms,
    )

    new_index = tuple((pid, frozenset(hits)) for pid, hits in buckets.items())
    return replace(ledger, probe_index=new_index)
