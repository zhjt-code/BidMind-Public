""" MaterialInventory 分组索引快路径与 inventory_strong_hits 快路径。

问题形态: 对每个名词全扫全部 MaterialFact 且每次重算 canonical_material_key(noun)。
本案例实体: 安全生产许可证、营业执照、质量管理体系认证证书。
同形态第二例: 噪声组（尾二字「证书」但不命中三个主材料）。
"""

from __future__ import annotations

import os

# 必须在 import app 之前设置环境变量
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-1118-not-a-placeholder-x9z7w")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

import random

from app.core.retriever.material_inventory import (
    MaterialFact,
    MaterialInventory,
    inventory_strong_hits,
    merge_fact_union,
)
from app.core.retriever.material_key import (
    _CONTAINS_STRONG_MIN,
    MATCH_NONE,
    MATCH_STRONG,
    MATCH_WEAK,
    _match_canonical_left,
    canonical_material_key,
    match_key_in_compact,
    match_material_in_text,
    match_material_keys,
    normalize_material_compact,
)

# ---------------------------------------------------------------------------
# 神谕: 旧全扫实现的拷贝（不调用 inventory_strong_hits）
# ---------------------------------------------------------------------------

def _oracle_strong_hits(
    facts: tuple[MaterialFact, ...],
    noun: str,
    *,
    material_grade_only: bool = True,
) -> tuple[MaterialFact, ...]:
    """旧全扫神谕: 逐 fact 调用 match_material_in_text, 按 grade 过滤。"""
    hits: list[MaterialFact] = []
    for fact in facts:
        if match_material_in_text(noun, fact.canonical_material_key) != MATCH_STRONG:
            if match_material_in_text(noun, fact.raw_noun) != MATCH_STRONG:
                continue
        if material_grade_only and fact.grade != "material":
            continue
        hits.append(fact)
    return tuple(hits)


# ---------------------------------------------------------------------------
# 旧窗口扫描神谕（供 match_key_in_compact 优化验证）
# ---------------------------------------------------------------------------

def _old_window_scan(key: str, compact: str) -> str:
    """match_key_in_compact 优化前的慢路径: 窗口内调用 match_material_keys。"""
    if not key or not compact:
        return MATCH_NONE
    if key in compact:
        return MATCH_STRONG if len(key) >= _CONTAINS_STRONG_MIN else MATCH_WEAK
    if len(key) >= 2 and key[-2:] not in compact:
        return MATCH_NONE
    best = MATCH_NONE
    for size in range(min(len(key), 8), 1, -1):
        needle = key[-size:]
        start = 0
        while True:
            pos = compact.find(needle, start)
            if pos < 0:
                break
            window_start = max(0, pos + size - max(len(key), 12))
            window = compact[window_start : pos + size]
            level = match_material_keys(key, window)
            if level == MATCH_STRONG:
                return MATCH_STRONG
            if level == MATCH_WEAK:
                best = MATCH_WEAK
            start = pos + 1
    return best


# ---------------------------------------------------------------------------
# 夹具: 三实体 + 噪声
# ---------------------------------------------------------------------------

_MATERIALS = ["安全生产许可证", "营业执照", "质量管理体系认证证书"]
# 尾二字「证书」高频噪声: 正文含证书但不是三个主材料
_NOISE_NOUNS = ["合格证书", "技术证书", "资质证书", "安全证书", "授权证书"]
_ALL_NOUNS_POOL = _MATERIALS + _NOISE_NOUNS


def _make_fact(rng: random.Random, idx: int) -> MaterialFact:
    raw = rng.choice(_ALL_NOUNS_POOL)
    key = canonical_material_key(raw)
    grade = rng.choice(["material", "material", "mention"])
    return MaterialFact(
        canonical_material_key=key,
        raw_noun=raw,
        grade=grade,
        document_id=rng.randint(1, 5),
        bid_part=rng.choice([None, "商务标", "技术标"]),
        chunk_id=1000 + idx,
        physical_page=rng.randint(1, 200),
        identifier=None,
        subject=None,
        source_channel="lexical",
        excerpt=f"第{idx}条 {raw}",
    )


_RNG = random.Random(1118)
_FACTS_500: tuple[MaterialFact, ...] = tuple(_make_fact(_RNG, i) for i in range(500))

_QUERY_POOL = _MATERIALS + _NOISE_NOUNS + [
    "安全许可证",
    "营业执照副本",
    "认证证书",
    "未知材料甲",
    "冰川冰芯年代",
    "",
    "证",  # 规范化后长度 < 2 的边界输入
]
_NOUNS_60: tuple[str, ...] = tuple(_RNG.choice(_QUERY_POOL) for _ in range(60))


# ---------------------------------------------------------------------------
# 主断言: MaterialInventory.strong_hits 与神谕逐项相等（含顺序）
# ---------------------------------------------------------------------------

def test_strong_hits_equals_oracle_for_all_nouns() -> None:
    inv = MaterialInventory.from_facts(_FACTS_500)
    for noun in _NOUNS_60:
        for grade_only in (True, False):
            expected = _oracle_strong_hits(_FACTS_500, noun, material_grade_only=grade_only)
            got = inv.strong_hits(noun, grade_only)
            assert got == expected, (
                f"strong_hits 与神谕不符: noun={noun!r} material_grade_only={grade_only}\n"
                f"expected={expected}\ngot={got}"
            )


# ---------------------------------------------------------------------------
# inventory_strong_hits 快路径: MaterialInventory 与 tuple 结果相等
# ---------------------------------------------------------------------------

def test_inventory_strong_hits_dispatch_equals_tuple_path() -> None:
    inv = MaterialInventory.from_facts(_FACTS_500)
    facts_tuple = _FACTS_500
    for noun in _NOUNS_60:
        for grade_only in (True, False):
            via_inv = inventory_strong_hits(inv, noun, material_grade_only=grade_only)
            via_tuple = inventory_strong_hits(facts_tuple, noun, material_grade_only=grade_only)
            assert via_inv == via_tuple, (
                f"dispatch 不等: noun={noun!r} grade_only={grade_only}"
            )


# ---------------------------------------------------------------------------
# with_facts: 事实多集 == merge_fact_union((*old, *new)).strong_hits
# ---------------------------------------------------------------------------

def test_with_facts_equals_merge_fact_union() -> None:
    old_facts = _FACTS_500[:250]
    new_facts = _FACTS_500[250:]
    inv_old = MaterialInventory.from_facts(old_facts)
    inv_merged = inv_old.with_facts(new_facts)
    expected_inv = MaterialInventory.from_facts(merge_fact_union((*old_facts, *new_facts)))
    for noun in _NOUNS_60:
        for grade_only in (True, False):
            assert inv_merged.strong_hits(noun, grade_only) == expected_inv.strong_hits(
                noun, grade_only
            ), f"with_facts 不符: noun={noun!r} grade_only={grade_only}"


# ---------------------------------------------------------------------------
# memo: 同一名词第二次查询结果相同（且是相同对象）
# ---------------------------------------------------------------------------

def test_memo_returns_same_object_on_second_call() -> None:
    inv = MaterialInventory.from_facts(_FACTS_500)
    for noun in _MATERIALS:
        first = inv.strong_hits(noun, True)
        second = inv.strong_hits(noun, True)
        assert first is second, f"memo 未命中: noun={noun!r}"
        first_f = inv.strong_hits(noun, False)
        second_f = inv.strong_hits(noun, False)
        assert first_f is second_f, f"memo(False) 未命中: noun={noun!r}"


# ---------------------------------------------------------------------------
# 边界: 空库存、空名词、规范化后长度 < 2
# ---------------------------------------------------------------------------

def test_empty_inventory_returns_empty_tuple() -> None:
    inv = MaterialInventory.from_facts(())
    assert not inv
    assert len(inv) == 0
    assert inv.strong_hits("安全生产许可证") == ()
    assert inv.strong_hits("营业执照", False) == ()


def test_empty_or_short_noun_returns_empty_tuple() -> None:
    inv = MaterialInventory.from_facts(_FACTS_500)
    assert inv.strong_hits("") == ()
    assert inv.strong_hits("证") == ()   # canonical 长度 < 2
    assert inv.strong_hits("   ") == ()  # normalize 后空串


# ---------------------------------------------------------------------------
# match_key_in_compact 优化验证: 与旧窗口扫描神谕相等（两个不同材料名）
# ---------------------------------------------------------------------------

def test_match_key_in_compact_equals_old_window_scan() -> None:
    """_match_canonical_left 替换 match_material_keys 不得改变返回值。"""
    texts = [
        "见安全生产许可证附件",
        "提供安全生产许可证原件及复印件",
        "本次招标须提供有效的安全生产许可",
        "许可证存档备查",
        "营业执照及相关证明材料",
        "出示统一社会信用代码营业执照",
        "提交营业执照副本复印件",
        "今天天气晴朗",
        "无关内容",
        "合格证书存档",
        "质量管理体系认证证书复印件",
    ]
    for noun in ("安全生产许可证", "营业执照"):
        key = canonical_material_key(noun)
        for text in texts:
            compact = normalize_material_compact(text)
            new_result = match_key_in_compact(key, compact)
            old_result = _old_window_scan(key, compact)
            assert new_result == old_result, (
                f"match_key_in_compact 与旧窗口扫描不符: noun={noun!r} text={text!r} "
                f"new={new_result!r} old={old_result!r}"
            )


# ---------------------------------------------------------------------------
# _match_canonical_left 内部函数: 公开 API 调用等价验证
# ---------------------------------------------------------------------------

def test_match_canonical_left_is_equivalent_to_match_material_keys() -> None:
    """_match_canonical_left(canonical(left), right) == match_material_keys(left, right)。"""
    pairs = [
        ("安全生产许可证", "安全生产许可证"),
        ("安全生产许可证", "安全许可证"),
        ("安全生产许可证", "营业执照"),
        ("营业执照", "营业执照副本"),
        ("质量管理体系认证证书", "质量管理认证证书"),
        ("质量管理体系认证证书", "合格证书"),
        ("安全生产许可证", "合格证书"),
    ]
    for left, right in pairs:
        left_key = canonical_material_key(left)
        expected = match_material_keys(left, right)
        got = _match_canonical_left(left_key, right)
        assert got == expected, (
            f"_match_canonical_left 与 match_material_keys 不符: "
            f"left={left!r} right={right!r} expected={expected!r} got={got!r}"
        )


# ---------------------------------------------------------------------------
# MaterialInventory 协议: len / iter / bool
# ---------------------------------------------------------------------------

def test_material_inventory_protocol() -> None:
    inv = MaterialInventory.from_facts(_FACTS_500)
    assert len(inv) == len(_FACTS_500)
    assert list(inv) == list(_FACTS_500)
    assert bool(inv) is True
    assert bool(MaterialInventory.from_facts(())) is False
