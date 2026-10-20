""" 探针索引增量扩展等价验证。

问题形态: merge_inventory_atoms 每次全量 bind_probe_index，一次约 160 秒；
 探针只登记有响应条款分面；建账本对每个分面重复 normalize_text(content)，
 且绑定两次。

神谕内联于本文件，不依赖被测实现内部私有函数，以防神谕与被测同源。
"""

from __future__ import annotations

import os

from tests.synthetic_data import synthetic_text

# 必须在 import app 之前设置
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault("JWT_SECRET_KEY", "bidmind-test-jwt-key-not-a-placeholder-1118")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

import random
from dataclasses import dataclass

from app.core.ai.reviewer_parts.evidence_ledger.builder import (
    assemble_project_ledger,
    merge_inventory_atoms,
)
from app.core.ai.reviewer_parts.evidence_ledger.contracts import (
    EvidenceAtom,
    EvidenceLedger,
    _atom_identity,
)
from app.core.ai.reviewer_parts.evidence_ledger.probe_index import (
    bind_probe_index,
    extend_probe_index,
)
from app.core.retriever.evidence_coverage import EvidenceFacet, facet_hit, normalize_text
from app.core.retriever.material_key import (
    MATCH_STRONG,
    match_material_in_text,
)

# ---------------------------------------------------------------------------
# 神谕：内联旧四段判定，不调用 _prepared_atom_matches / facet_covers_text
# ---------------------------------------------------------------------------

def _oracle_facet_covers(facet: EvidenceFacet, content: str, sub_query: str = "") -> bool:
    """旧 facet_covers_text 内联神谕：使用公开 facet_hit，不调用 facet_covers_text 本身。"""
    norm_key = normalize_text(facet.key)
    text = normalize_text(content)
    sub_qs: set[str] = {normalize_text(sub_query)} if sub_query else set()
    # 使用公开 facet_hit（：不得把 facet.key 混入正文）
    if facet_hit(facet, sub_qs, [text]):
        return True
    if text and norm_key and len(norm_key) > 4:
        if norm_key[-4:] in text:
            return True
        if len(norm_key) > 6 and norm_key[-6:] in text:
            return True
    return False


def _oracle_atom_matches(atom: EvidenceAtom, facet: EvidenceFacet) -> bool:
    """旧四段判定神谕（不调用 _prepared_atom_matches）：
    1) 无 kind → False
    2) key == kind 原文相等
    3) match_material_in_text(kind) == STRONG
    4) match_material_in_text(excerpt) == STRONG
    5) 内联 facet_covers（sub_query = atom.kind）
    """
    if not atom.kind:
        return False
    if facet.key == atom.kind:
        return True
    if match_material_in_text(facet.key, atom.kind) == MATCH_STRONG:
        return True
    if match_material_in_text(facet.key, atom.excerpt or "") == MATCH_STRONG:
        return True
    return _oracle_facet_covers(facet, atom.excerpt or "", atom.kind)


def _oracle_bind(
    atoms: list[EvidenceAtom],
    facets: list[EvidenceFacet],
) -> dict[tuple[str, str], frozenset[tuple[str, int | None, int | None]]]:
    """旧嵌套循环绑定神谕（不使用任何被测实现内部函数）。"""
    seen: set[tuple[str, str]] = set()
    deduped: list[EvidenceFacet] = []
    for f in facets:
        if f.key and (f.key, f.query) not in seen:
            seen.add((f.key, f.query))
            deduped.append(f)

    result: dict[tuple[str, str], frozenset[tuple[str, int | None, int | None]]] = {}
    for facet in deduped:
        probe_id = (facet.key, facet.query)
        hits = frozenset(
            _atom_identity(atom) for atom in atoms if _oracle_atom_matches(atom, facet)
        )
        result[probe_id] = hits
    return result


# ---------------------------------------------------------------------------
# 数据生成辅助
# ---------------------------------------------------------------------------

_MATERIAL_NAMES = [
    "安全生产许可证",
    "营业执照",
    "建筑施工资质证书",
    "安全培训证书",
    "税务登记证",
    "组织机构代码证",
    "财务报表",
    "银行资信证明",
    "项目经理职称证书",
    "质量管理体系认证",
]


def _make_facets(count: int) -> list[EvidenceFacet]:
    """生成 count 个 (key, query) 唯一的分面，两种材料名必须都出现。"""
    facets = []
    for i in range(count):
        name = _MATERIAL_NAMES[i % len(_MATERIAL_NAMES)]
        query = f"{name}投标要求第{i}条"
        facets.append(EvidenceFacet(key=name, query=query))
    return facets


def _make_atoms(rng: random.Random, count: int) -> list[EvidenceAtom]:
    """用给定 RNG 生成 count 个原子，kind 和 excerpt 均来自 _MATERIAL_NAMES。"""
    atoms = []
    for i in range(count):
        kind = _MATERIAL_NAMES[rng.randint(0, len(_MATERIAL_NAMES) - 1)]
        excerpt_mat = _MATERIAL_NAMES[rng.randint(0, len(_MATERIAL_NAMES) - 1)]
        excerpt = f"提供{excerpt_mat}原件，编号{rng.randint(1000, 9999)}"
        atoms.append(EvidenceAtom(
            kind=kind,
            grade="material" if rng.random() > 0.3 else "mention",
            page=rng.randint(1, 200),
            chunk_id=i + 1,
            excerpt=excerpt,
            source_requirement_ids=(1,),
        ))
    return atoms


@dataclass
class _FakeFact:
    """模拟库存事实（不依赖 DB 模型）。"""
    canonical_material_key: str
    physical_page: int | None
    chunk_id: int | None
    document_id: int | None
    grade: str
    excerpt: str
    bid_part: str | None = None


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

def test_bind_probe_index_oracle_400x50() -> None:
    """400 原子 × 50 探针：bind_probe_index 每个 probe_id 的 frozenset 与神谕相等。"""
    rng = random.Random(1118)
    facets = _make_facets(50)
    atoms = _make_atoms(rng, 400)

    # 两种材料必须都出现
    assert any(f.key == "安全生产许可证" for f in facets)
    assert any(f.key == "营业执照" for f in facets)

    oracle = _oracle_bind(atoms, facets)

    ledger = EvidenceLedger(atoms=tuple(atoms))
    result = bind_probe_index(ledger, facets)

    assert result.probe_index is not None
    actual = dict(result.probe_index)

    for probe_id, expected_hits in oracle.items():
        assert actual.get(probe_id) == expected_hits, (
            f"probe_id={probe_id} 实际={actual.get(probe_id)} 期望={expected_hits}"
        )
    # 同形态第二例：确认营业执照探针的命中集正确
    biz_probe = next(pid for pid in actual if pid[0] == "营业执照")
    assert actual[biz_probe] == oracle[biz_probe]


def test_extend_probe_index_incremental() -> None:
    """增量：已绑定账本 + 新 facts（新身份/空 excerpt 变非空/仅 grade 升级）
    → merge_inventory_atoms 的 probe_index ≡ 全量 bind_probe_index。"""
    rng = random.Random(1118)
    facets = _make_facets(10)
    atoms = _make_atoms(rng, 30)

    # 初始绑定
    initial_ledger = bind_probe_index(EvidenceLedger(atoms=tuple(atoms)), facets)

    # 构造三类 facts
    facts: list[_FakeFact] = [
        # 1. 新身份（新 kind/page/chunk）
        _FakeFact(
            canonical_material_key="安全生产许可证",
            physical_page=999,
            chunk_id=10001,
            document_id=1,
            grade="material",
            excerpt="新加入安全生产许可证扫描件",
        ),
        # 2. 已有身份，excerpt 从空变非空
        _FakeFact(
            canonical_material_key=atoms[0].kind,
            physical_page=atoms[0].page,
            chunk_id=atoms[0].chunk_id,
            document_id=1,
            grade="mention",
            excerpt="",  # 原始 excerpt 不为空 → existing.excerpt or excerpt → 不变
        ),
        # 3. 已有身份，grade 升级（excerpt 不变，不算受影响）
        _FakeFact(
            canonical_material_key=atoms[1].kind,
            physical_page=atoms[1].page,
            chunk_id=atoms[1].chunk_id,
            document_id=1,
            grade="material",
            excerpt=atoms[1].excerpt,  # 摘录不变
        ),
    ]

    merged_ledger = merge_inventory_atoms(initial_ledger, facts)

    # 全量重绑作为基准
    full = bind_probe_index(EvidenceLedger(atoms=merged_ledger.atoms), facets)

    assert merged_ledger.probe_index is not None
    assert dict(merged_ledger.probe_index) == dict(full.probe_index), (
        "增量绑定结果与全量绑定不一致"
    )


def test_assemble_single_bind() -> None:
    """单次末绑：assemble_project_ledger(..., probe_facets=全部分面) 的索引
    ≡ 对合并后原子做一次全量绑定。"""
    facets = _make_facets(8)
    rows = [
        {
            "bid_response_id": 1,
            "chunk_id": i + 1,
            "page": i + 10,
            "document_id": 9,
            "matched_sub_query": facets[i % len(facets)].query,
            "content": f"项目须提供{facets[i % len(facets)].key}，编号{i}",
            "chunk_type": "text",
        }
        for i in range(20)
    ]
    facets_by_response = {1: tuple(facets)}
    req_by_resp = {1: 100}

    # 传入 probe_facets → collect 不绑 → merge → 末尾单次绑
    assembled = assemble_project_ledger(
        rows, facets_by_response, req_by_resp, facts=[], probe_facets=facets
    )

    # 全量重绑基准
    full = bind_probe_index(EvidenceLedger(atoms=assembled.atoms), facets)

    assert assembled.probe_index is not None
    assert dict(assembled.probe_index) == dict(full.probe_index)


def test_no_response_pair_in_probe_index() -> None:
    """无响应条款的 pair 分面出现在 probe_index，atoms_for_facets 与回退扫描结果一致。"""
    # 有响应的分面
    resp_facets = (EvidenceFacet(key="营业执照", query="投标人须提供营业执照"),)
    # 无响应的分面（只进探针，不进 facets_by_response）
    no_resp_facets = (EvidenceFacet(key="安全生产许可证", query="投标人须持有安全生产许可证"),)
    all_probe_facets = resp_facets + no_resp_facets

    rows = [
        {
            "bid_response_id": 1,
            "chunk_id": 1,
            "page": 10,
            "document_id": 9,
            "matched_sub_query": "",
            "content": synthetic_text('营业执照 ', '统一社会信用代码 ', '91110101M', 'A0001AA1X'),
            "chunk_type": "image_evidence",
        },
        {
            "bid_response_id": 1,
            "chunk_id": 2,
            "page": 11,
            "document_id": 9,
            "matched_sub_query": "",
            "content": "安全生产许可证 编号(京)JZ安许证字0001",
            "chunk_type": "image_evidence",
        },
    ]
    facets_by_response = {1: resp_facets}
    req_by_resp = {1: 200}

    assembled = assemble_project_ledger(
        rows, facets_by_response, req_by_resp, facts=[], probe_facets=list(all_probe_facets)
    )

    assert assembled.probe_index is not None
    index = dict(assembled.probe_index)

    # 无响应条款的分面必须出现在 probe_index
    no_resp_probe_id = (no_resp_facets[0].key, no_resp_facets[0].query)
    assert no_resp_probe_id in index, "无响应条款分面未出现在 probe_index"

    # atoms_for_facets 用索引与不用索引（回退扫描）结果一致
    ledger_no_index = EvidenceLedger(atoms=assembled.atoms)
    result_indexed = assembled.atoms_for_facets(list(all_probe_facets))
    result_scan = ledger_no_index.atoms_for_facets(list(all_probe_facets))
    assert set(_atom_identity(a) for a in result_indexed) == set(
        _atom_identity(a) for a in result_scan
    ), "索引路径与回退扫描结果不一致"


def test_extend_empty_ledger() -> None:
    """空账本、空探针 extend 原样返回。"""
    empty = EvidenceLedger()
    result = extend_probe_index(empty, [])
    assert result is empty  # 原样返回


def test_extend_no_probe_facets() -> None:
    """无 probe_facets 的账本 extend 原样返回。"""
    ledger = EvidenceLedger(atoms=(
        EvidenceAtom(kind="营业执照", grade="material", page=1, chunk_id=1,
                     excerpt="营业执照", source_requirement_ids=(1,)),
    ))
    result = extend_probe_index(ledger, list(ledger.atoms))
    assert result is ledger


def test_extend_with_null_index_falls_back_to_full_bind() -> None:
    """probe_facets 非空但 probe_index 为 None → 退回全量 bind_probe_index。"""
    facets = [EvidenceFacet(key="营业执照", query="投标人须提供营业执照")]
    atoms = [EvidenceAtom(kind="营业执照", grade="material", page=1, chunk_id=1,
                          excerpt="营业执照扫描件", source_requirement_ids=(1,))]
    # 手动构造 probe_facets 不为空但 probe_index=None 的账本
    from dataclasses import replace
    ledger = replace(
        EvidenceLedger(atoms=tuple(atoms)),
        probe_facets=tuple(facets),
        probe_index=None,
    )
    result = extend_probe_index(ledger, atoms)
    assert result.probe_index is not None
    # 结果等价于全量绑定
    full = bind_probe_index(EvidenceLedger(atoms=tuple(atoms)), facets)
    assert dict(result.probe_index) == dict(full.probe_index)
