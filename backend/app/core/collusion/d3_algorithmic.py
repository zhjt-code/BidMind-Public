"""S.5.3 · D3 文本相似度纯算法实现。

作为 LLM D3 判定的独立对照通道，advisory 起步。产出结构与 LLM D3 完全兼容
（`{score, findings, evidence_level, certainty, source: "algorithmic"}`），
后续若一致率 >= 85% 可升为主判（留给 P3+ 评估）。

算法组合：
1. SimHash（简化版，基于 Python stdlib hashlib）：段落级"超近似"判重，按 Hamming 距离 < 3 判为雷同
2. LCS：用 difflib.SequenceMatcher 计算最长公共子串比例
3. TF-IDF 余弦相似度（简化版）：词频重合率

三者加权（SimHash 0.4 / LCS 0.3 / TF-IDF 0.3）得到 d3_score。仅用 stdlib + rapidfuzz，无新依赖。
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# SimHash 简化版
# ---------------------------------------------------------------------------

# 分别匹配：单个中文字符 / 连续英文字母 / 连续数字。混合场景自然切分。
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+|[0-9]+")


def _tokenize(text: str) -> list[str]:
    """轻量分词：中文按字 + 英文/数字按连续段。不引入 jieba 等依赖。"""
    if not text:
        return []
    return _TOKEN_RE.findall(text)


def simhash(text: str, bits: int = 64) -> int:
    """简化版 SimHash（非 MinHash；足够用于"段落级超近似"判定）。"""
    tokens = _tokenize(text)
    if not tokens:
        return 0
    weights = Counter(tokens)
    vec = [0] * bits
    for token, w in weights.items():
        h = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
        for i in range(bits):
            if (h >> i) & 1:
                vec[i] += w
            else:
                vec[i] -= w
    out = 0
    for i in range(bits):
        if vec[i] > 0:
            out |= (1 << i)
    return out


def hamming_distance(a: int, b: int) -> int:
    """两个整数汉明距离（二进制差异位数）。"""
    return bin(a ^ b).count("1")


def simhash_similarity(text_a: str, text_b: str) -> float:
    """按 64-bit SimHash 汉明距离转换为 [0,1] 相似度。"""
    ha = simhash(text_a, bits=64)
    hb = simhash(text_b, bits=64)
    dist = hamming_distance(ha, hb)
    # 距离 0 → 1.0；距离 ≥ 32（一半位差）→ 0.0；中间线性
    return max(0.0, 1.0 - dist / 32.0)


# ---------------------------------------------------------------------------
# LCS（用 difflib.SequenceMatcher 近似）
# ---------------------------------------------------------------------------

def lcs_ratio(text_a: str, text_b: str) -> float:
    """最长公共子串长度 / max(len_a, len_b) 的 [0,1] 近似。"""
    if not text_a or not text_b:
        return 0.0
    # quick_ratio 比 ratio 快 3-10 倍但略不精确；用在采集场景够用
    return SequenceMatcher(None, text_a, text_b).ratio()


# ---------------------------------------------------------------------------
# TF-IDF 余弦相似度（简化版）
# ---------------------------------------------------------------------------

def tfidf_cosine(text_a: str, text_b: str) -> float:
    """简化的 TF-IDF 余弦：两文档场景下 IDF 退化为"只有出现在某一侧的词 IDF=0 的近似"，
    等价于词频余弦。足够用于两两比较。"""
    ta = _tokenize(text_a)
    tb = _tokenize(text_b)
    if not ta or not tb:
        return 0.0
    ca = Counter(ta)
    cb = Counter(tb)
    common = set(ca.keys()) & set(cb.keys())
    if not common:
        return 0.0
    dot = sum(ca[w] * cb[w] for w in common)
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# 综合 D3 算法判定
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class D3AlgorithmicResult:
    """与 LLM D3 输出结构一致的算法判定结果。"""
    score: float
    findings: list[str] = field(default_factory=list)
    evidence_level: str = "second"
    certainty: float = 0.0
    source: str = "algorithmic"
    breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "findings": list(self.findings),
            "evidence_level": self.evidence_level,
            "certainty": round(self.certainty, 4),
            "source": self.source,
            "breakdown": {k: round(v, 4) for k, v in self.breakdown.items()},
        }


_WEIGHT_SIMHASH = 0.4
_WEIGHT_LCS = 0.3
_WEIGHT_TFIDF = 0.3


def compute_d3_algorithmic(
    chunks_a: Iterable[str],
    chunks_b: Iterable[str],
    whitelist_keywords: list[str] | None = None,
    high_threshold: float = 0.7,
) -> D3AlgorithmicResult:
    """计算两组文本 chunk 的 D3 文本相似度（纯算法）。

    Args:
        chunks_a / chunks_b: 两侧文档的 chunk 文本列表
        whitelist_keywords: 若 chunk 主要命中的词全在白名单（行业通用章节 / 法规等），
            该对 chunk 不计入相似度（减少误报）
        high_threshold: 综合分数高于此值即在 findings 中记录"疑似雷同"

    Returns:
        D3AlgorithmicResult
    """
    la = [c for c in (chunks_a or []) if c and str(c).strip()]
    lb = [c for c in (chunks_b or []) if c and str(c).strip()]
    if not la or not lb:
        return D3AlgorithmicResult(
            score=0.0, findings=[], evidence_level="second",
            certainty=0.0,
            breakdown={"simhash": 0.0, "lcs": 0.0, "tfidf": 0.0, "pairs": 0},
        )

    # 白名单按"字符集合"展开：中文关键词在 _tokenize 中按字切，因此白名单
    # 也要按字展开才能正确做 issubset 判定。英文关键词保持整词。
    wl_chars: set[str] = set()
    for kw in (whitelist_keywords or []):
        kw_str = str(kw)
        # 对每个关键词按 tokenize 规则拆分后加入白名单
        wl_chars.update(_tokenize(kw_str))

    # best-match 语义：对每个 chunk_a，在 chunk_b 中找最匹配的 chunk 取 max；
    # 最后对 N 个 chunk_a 的 best-match 分数取均值。这样"两侧相同 chunk 列表"
    # 不会被非对角线的跨 chunk 低分稀释，更符合"段落级雷同检测"语义。
    per_a_sh: list[float] = []
    per_a_lc: list[float] = []
    per_a_tf: list[float] = []
    hi_pairs: list[tuple[int, int, float]] = []

    la_cut = la[:20]
    lb_cut = lb[:20]
    total_pairs = 0

    for i, ca in enumerate(la_cut):
        best_sh = 0.0
        best_lc = 0.0
        best_tf = 0.0
        best_j = -1
        best_combined = 0.0
        has_any = False
        for j, cb in enumerate(lb_cut):
            # 白名单扣除：两侧 chunk 的 token 全部落在白名单字符集内时跳过
            if wl_chars:
                tokens_a = set(_tokenize(ca))
                tokens_b = set(_tokenize(cb))
                if tokens_a and tokens_a.issubset(wl_chars) and tokens_b and tokens_b.issubset(wl_chars):
                    continue

            sh = simhash_similarity(ca, cb)
            lc = lcs_ratio(ca, cb)
            tf = tfidf_cosine(ca, cb)
            total_pairs += 1
            has_any = True

            combined = _WEIGHT_SIMHASH * sh + _WEIGHT_LCS * lc + _WEIGHT_TFIDF * tf
            if combined > best_combined:
                best_combined = combined
                best_sh, best_lc, best_tf = sh, lc, tf
                best_j = j

        if has_any:
            per_a_sh.append(best_sh)
            per_a_lc.append(best_lc)
            per_a_tf.append(best_tf)
            if best_combined >= high_threshold:
                hi_pairs.append((i, best_j, best_combined))

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    mean_sh = _mean(per_a_sh)
    mean_lc = _mean(per_a_lc)
    mean_tf = _mean(per_a_tf)
    d3_score = _WEIGHT_SIMHASH * mean_sh + _WEIGHT_LCS * mean_lc + _WEIGHT_TFIDF * mean_tf

    findings: list[str] = []
    for i, j, c in sorted(hi_pairs, key=lambda x: -x[2])[:5]:
        findings.append(
            f"chunk_a[{i}] 与 chunk_b[{j}] 综合相似度 {c:.2%}（SimHash/LCS/TF-IDF 综合）"
        )

    # certainty: 综合分数的"拐点扩展"，中段更敏感
    certainty = min(0.95, 0.5 + abs(d3_score - 0.5))

    return D3AlgorithmicResult(
        score=d3_score,
        findings=findings,
        evidence_level="second",
        certainty=certainty,
        breakdown={
            "simhash": mean_sh,
            "lcs": mean_lc,
            "tfidf": mean_tf,
            "pairs": float(total_pairs),
            "hi_pairs": float(len(hi_pairs)),
        },
    )
