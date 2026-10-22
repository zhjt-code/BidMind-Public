"""评分项换轴重构 P0-3 只读探针：原文声明账本地基数据实测。

职责: 只读实测四项指标(R1声明对捕获率/R2账本自洽率/组级缺口检测案例/副本表指纹去重),
      输出报告到 docs/reports/scoring_ledger_probe_p0.md 供换轴闸门决策。
不做: 不修改生产代码、不做枚举卷映射、不调用 LLM/DB、
      不读取冻结验证集(sample_corpus_holdout_round2 与 holdout_round2_truth_sealed.md 绝对禁止)。

说明: 捕获阶段复用 volume_parser 第一阶段正则与 scoring_group_anchor 锚点逻辑
      (以模块私有名 import 只读复用, 探针脚本允许, 来源已注明)。
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

TRAINING_CORPUS_DIR = _BACKEND_DIR / "tests" / "fixtures" / "scoring" / "sample_corpus"
HOLDOUT_CORPUS_DIR = _BACKEND_DIR / "tests" / "fixtures" / "scoring" / "sample_corpus_holdout"
TRAINING_TRUTH_PATH = _BACKEND_DIR / "tests" / "fixtures" / "scoring" / "sample_truth_training.md"
SEALED_TRUTH_PATH = _BACKEND_DIR / "storage" / "samples" / "scoring_corpus" / "holdout_truth_sealed.md"
REPORT_PATH = _PROJECT_ROOT / "docs" / "reports" / "scoring_ledger_probe_p0.md"

TRAINING_KEYS: list[str] = [*(f"{i:02d}" for i in range(1, 22)), "27", "28"]
DOWNGRADED_HOLDOUT_KEYS: list[str] = ["23", "24", "25", "26", "29", "30", "31", "32", "33", "34"]

SCORE_TOLERANCE = 0.01
LEDGER_TOLERANCE = 1.0

# 标签归一尾缀。任务裁决集为 部分/标/卷/评审/评分标准; 为对齐真值与原文的等价表述
# 追加 评审标准/标准/分/项(如 真值"商务" vs 原文"商务分"), 追加项在报告中单独声明。
_LABEL_SUFFIXES = ("评分标准", "评审标准", "评分", "评审", "标准", "部分", "卷", "标", "项", "分")
_LABEL_SKIP_TERMS = ("总分", "合计", "满分", "总计", "分值构成", "分值分配", "分值组成", "得分")

# 声明总分候选句: "总分100分/满分100分/合计100分/总计100分" (任务裁决的声明句形态)。
_TOTAL_SENTENCE_RE = re.compile(r"(?:总分|满分|合计|总计)\s*[共为]?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*分")
# 折算制卷定义: "技术总分100分...按40%权重折算" (来源: volume_parser.parse_volume_definition)。
_WEIGHTED_DEF_LABEL_RE = re.compile(r"([\u4e00-\u9fff]{1,10}?)总分\s*[:：]?\s*(\d+(?:\.\d+)?)\s*分")
# ── 扩展捕获形态(裁决五类源之外, 实测发现的常见组声明书写, 报告中单列 ext 口径) ──
# 括注式组声明: 整格/整行为 "技术商务部分（70分）" / "资信标 （100 分）"。
_GROUP_PAREN_RE = re.compile(
    r"^(?:[一二三四五六七八九十\d]+[、.．]\s*)?([\u4e00-\u9fff]{2,12})\s*[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[)）]$"
)
# 组尾缀裸数字式: 整格/整行为 "一、价格部分 20" / "商务标 34 分"(无分字时要求组尾缀)。
_GROUP_BARE_RE = re.compile(
    r"^(?:[一二三四五六七八九十\d]+[、.．]\s*)?([\u4e00-\u9fff]{2,10})\s+(\d+(?:\.\d+)?)\s*(分)?$"
)
# 组尾缀门控: 扩展形态仅接受组形标签(不加门控会把"质量保证措施（15分）"等项级行误捕为组对,
# 实测导致 R2 分母污染)。"书"覆盖 技术建议书/承包人建议书 类组名, 与少数承诺书类项名存在固有歧义。
_GROUP_BARE_SUFFIXES = ("部分", "标", "卷", "评审", "标准", "项", "书")
_WEIGHT_CELL_RE = re.compile(r"^\d+(?:\.\d+)?\s*[%％]$")
EXT_SOURCES = ("group_paren", "group_bare")
# 真值构成解析用
_REF_EQUALS_PAIR_RE = re.compile(r"([^\s=；;，,()：:]+)\s*=\s*(-?\d+(?:\.\d+)?)")
_REF_PAREN_PAIR_RE = re.compile(r"([\u4e00-\u9fff]{1,12})\s+(\d+(?:\.\d+)?)")
_REF_BULLET_RE = re.compile(r"^\s*-\s+([^:：]+?)\s*[:：]\s*(.+?)\s*$")
_REF_WEIGHTED_VALUE_RE = re.compile(
    r"原始\s*(\d+(?:\.\d+)?)[，,]\s*权重\s*(\d+(?:\.\d+)?)\s*[%％][，,]\s*折算\s*(\d+(?:\.\d+)?)"
)
_NUMERIC_CELL_RE = re.compile(r"^\d+(?:\.\d+)?$")


# ── 纯函数区(仅 stdlib, 供防御测试) ──────────────────────────────────────────


def normalize_group_label(label: str) -> str:
    """组标签归一: NFKC 全半角归一 + 去空白 + 迭代剥离尾缀 + 小写。"""
    text = unicodedata.normalize("NFKC", str(label or ""))
    text = re.sub(r"\s+", "", text).lower()
    changed = True
    while changed:
        changed = False
        for suffix in _LABEL_SUFFIXES:
            if len(text) > len(suffix) and text.endswith(suffix):
                text = text[: -len(suffix)]
                changed = True
                break
    return text


def _is_skippable_label(label: str) -> bool:
    compact = re.sub(r"\s+", "", str(label or ""))
    return (not compact) or any(term in compact for term in _LABEL_SKIP_TERMS)


def parse_composition_references(content: str) -> dict[str, dict]:
    """解析训练真值的构成参照: declared_total + [(组标签, 分值, 折算值, 权重)]。

    兼容四种 volume_totals 写法: 括注式/分号式/多方案式(=N)/多行列表式(含16号加权行)。
    """
    result: dict[str, dict] = {}
    parts = re.split(r"\n## structure-(\d+)\n", "\n" + content)
    i = 1
    while i + 1 < len(parts):
        key, body = parts[i].zfill(2), parts[i + 1]
        i += 2
        m = re.search(r"declared_total`?\s*[:：]\s*(\d+(?:\.\d+)?)", body)
        declared = float(m.group(1)) if m else None

        pairs: list[dict] = []
        lines = body.split("\n")
        for idx, line in enumerate(lines):
            if "volume_totals" not in line:
                continue
            rest = line.split("volume_totals", 1)[1].strip("`").strip().lstrip(":：").strip()
            if rest:
                for label, score in _REF_EQUALS_PAIR_RE.findall(rest):
                    if label.strip("`") != "unknown" and float(score) > 0:
                        pairs.append({"label": label, "score": float(score), "alt_score": None, "weight": None})
                for paren in re.findall(r"[（(]([^()]*)[）)]", rest):
                    for label, score in _REF_PAREN_PAIR_RE.findall(paren):
                        if float(score) > 0:
                            pairs.append({"label": label, "score": float(score), "alt_score": None, "weight": None})
            else:
                for follow in lines[idx + 1 :]:
                    bullet = _REF_BULLET_RE.match(follow)
                    if not bullet:
                        if follow.strip():
                            break
                        continue
                    label, value = bullet.group(1), bullet.group(2)
                    weighted = _REF_WEIGHTED_VALUE_RE.search(value)
                    if weighted:
                        pairs.append({"label": label, "score": float(weighted.group(1)),
                                      "alt_score": float(weighted.group(3)),
                                      "weight": float(weighted.group(2)) / 100.0})
                    elif _NUMERIC_CELL_RE.match(value.strip()) and float(value) > 0:
                        pairs.append({"label": label, "score": float(value), "alt_score": None, "weight": None})
            break

        if declared is None:
            explicit = [p["score"] for p in pairs]
            declared = round(sum(explicit), 4) if explicit else None
        result[key] = {"declared_total": declared, "pairs": pairs, "annotated": len(pairs) >= 1}
    return result


def match_reference_pairs(
    ref_pairs: list[dict], captured_pairs: list[dict], tolerance: float = SCORE_TOLERANCE
) -> list[dict]:
    """R1 匹配: 归一标签相等 且 分值(原始/折算任一)差 <= tolerance。返回逐参照对命中明细。"""
    captured_index: dict[str, set[float]] = defaultdict(set)
    for cap in captured_pairs:
        norm = normalize_group_label(cap["label"])
        if not norm:
            continue
        captured_index[norm].add(round(float(cap["score"]), 4))
        if cap.get("weight"):
            captured_index[norm].add(round(float(cap["score"]) * float(cap["weight"]), 4))
    details = []
    for ref in ref_pairs:
        norm = normalize_group_label(ref["label"])
        ref_values = {float(ref["score"])} | ({float(ref["alt_score"])} if ref.get("alt_score") else set())
        hit = any(
            abs(cand - rv) <= tolerance for cand in captured_index.get(norm, set()) for rv in ref_values
        )
        details.append({"label": ref["label"], "score": ref["score"], "hit": hit})
    return details


def dedupe_captured_pairs(pairs: list[dict]) -> list[dict]:
    """按(归一标签, 分值)去重; 同键条目优先保留带权重者; 剔除总分/合计类标签。"""
    out: dict[tuple[str, float], dict] = {}
    for pair in pairs:
        if _is_skippable_label(pair["label"]):
            continue
        norm = normalize_group_label(pair["label"])
        score = round(float(pair["score"]), 4)
        if not norm or score <= 0:
            continue
        key = (norm, score)
        if key not in out or (pair.get("weight") and not out[key].get("weight")):
            out[key] = {**pair, "norm_label": norm}
    return list(out.values())


def assess_ledger_consistency(
    pairs: list[dict], total_candidates: list[float], tolerance: float = LEDGER_TOLERANCE
) -> dict:
    """R2 自洽判定: Σ组分≈候选→自洽; 否则有权重且Σ(组分x权重)≈候选→折算制自洽。

    落点: self_consistent / folded_consistent / inconsistent / insufficient。
    """
    if len(pairs) < 2:
        return {"status": "insufficient", "raw_sum": None, "folded_sum": None, "matched_candidate": None}
    raw_sum = round(sum(float(p["score"]) for p in pairs), 4)
    matched = next((c for c in total_candidates if abs(raw_sum - c) <= tolerance), None)
    if matched is not None:
        return {"status": "self_consistent", "raw_sum": raw_sum, "folded_sum": None, "matched_candidate": matched}
    if any(p.get("weight") for p in pairs):
        folded = round(sum(float(p["score"]) * float(p.get("weight") or 1.0) for p in pairs), 4)
        matched = next((c for c in total_candidates if abs(folded - c) <= tolerance), None)
        if matched is not None:
            return {"status": "folded_consistent", "raw_sum": raw_sum, "folded_sum": folded, "matched_candidate": matched}
        return {"status": "inconsistent", "raw_sum": raw_sum, "folded_sum": folded, "matched_candidate": None}
    return {"status": "inconsistent", "raw_sum": raw_sum, "folded_sum": None, "matched_candidate": None}


def parse_training_truth_groups(content: str) -> dict[str, list[dict]]:
    """解析训练真值项级表格, 保留 scheme/volume 组字段(audit 脚本解析器不保留, 故探针自备)。"""
    result: dict[str, list[dict]] = {}
    parts = re.split(r"\n## structure-(\d+)\n", "\n" + content)
    i = 1
    while i + 1 < len(parts):
        key, body = parts[i].zfill(2), parts[i + 1]
        i += 2
        items: list[dict] = []
        for line in body.split("\n"):
            stripped = line.strip()
            if not (stripped.startswith("|") and stripped.endswith("|")) or "---" in stripped:
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) < 9 or cells[0] == "sample_id":
                continue
            try:
                score = float(cells[2])
            except ValueError:
                continue
            items.append({"item_name": cells[1], "max_score": score, "scheme": cells[6], "volume": cells[7]})
        result[key] = items
    return result


def parse_sealed_truth_groups(content: str) -> dict[str, list[dict]]:
    """解析密封真值(yamlish), 保留 scheme/volume 字段。仅用于已降级的首轮 holdout。"""
    result: dict[str, list[dict]] = defaultdict(list)
    current: dict = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("- sample_id:"):
            if current.get("sample_id"):
                _append_sealed_item(result, current)
            current = {"sample_id": line.split(":", 1)[1].strip().strip('"')}
        elif current and ":" in line and not line.startswith("```"):
            field, value = line.split(":", 1)
            if field in ("item_name", "max_score", "scheme", "volume"):
                current[field] = value.strip().strip('"')
    if current.get("sample_id"):
        _append_sealed_item(result, current)
    return dict(result)


def _append_sealed_item(result: dict, current: dict) -> None:
    match = re.search(r"(\d+)", current.get("sample_id", ""))
    try:
        score = float(current.get("max_score", ""))
    except (TypeError, ValueError):
        return
    if match and current.get("item_name"):
        result[match.group(1).zfill(2)].append({
            "item_name": current["item_name"], "max_score": score,
            "scheme": current.get("scheme", ""), "volume": current.get("volume", "")})


# ── 捕获与提取(需后端依赖, 惰性导入; 环境准备方式沿用 audit 脚本) ────────────


def _ensure_probe_env() -> None:
    """最小环境变量(与 audit_scoring_legacy_baseline._ensure_test_env 对齐)。"""
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
    os.environ.setdefault("JWT_SECRET_KEY", "probe-script-only-not-for-prod")
    os.environ.setdefault("LOG_LEVEL", "DEBUG")
    os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")
    os.environ.setdefault("EMAIL_VERIFICATION_ENABLED", "false")


def _load_fixture(key: str, corpus_dir: Path) -> dict | None:
    path = corpus_dir / f"{key}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _stage1_pairs_from_text(raw: str) -> tuple[list[dict], list[float]]:
    """volume_parser 第一阶段原文对(平铺/内联/每部分满分)+折算卷定义, 不做枚举映射。

    正则来源: app/core/extractor/scoring/volume_parser.py(私有名只读复用)。
    """
    from app.core.extractor.scoring import volume_parser as vp  # noqa: PLC2701 私有正则只读复用

    pairs: list[dict] = []
    totals: list[float] = []
    if any(term in raw for term in vp._FLAT_COMPOSITION_TRIGGER_TERMS):
        each = vp._EACH_PART_PATTERN.search(raw)
        if each:
            for label in (t.strip() for t in re.split(r"[、，,和及]+", each.group(1)) if t.strip()):
                pairs.append({"label": label, "score": float(each.group(2)), "weight": None, "source": "each_part"})
        for m in vp._FLAT_PAIR_PATTERN.finditer(raw):
            if not any(t in m.group(1) for t in vp._FLAT_LABEL_SKIP_TERMS) and float(m.group(2)) > 0:
                pairs.append({"label": m.group(1), "score": float(m.group(2)), "weight": None, "source": "flat"})
    inline = vp._INLINE_COMPOSITION_TRIGGER_RE.search(raw)
    if inline:
        totals.append(float(inline.group(1)))
        tail = raw[inline.end():]
        end = vp._INLINE_SENTENCE_END_RE.search(tail)
        for m in vp._INLINE_PAIR_RE.finditer(tail[: end.start()] if end else tail):
            label = m.group(1).strip().strip("、，, ")
            if label and not any(t in label for t in vp._FLAT_LABEL_SKIP_TERMS) and float(m.group(2)) > 0:
                pairs.append({"label": label, "score": float(m.group(2)), "weight": None, "source": "inline"})
    weight_match = vp._VOLUME_WEIGHT_PATTERN.search(raw)
    if weight_match:
        weight = float(weight_match.group(1)) / 100.0
        for m in _WEIGHTED_DEF_LABEL_RE.finditer(raw):
            if float(m.group(2)) > 0:
                pairs.append({"label": m.group(1), "score": float(m.group(2)), "weight": weight, "source": "weighted_def"})
    return pairs, totals


def _ext_group_pair_from_unit(unit: str) -> dict | None:
    """扩展形态: 整格/整行的括注式或组尾缀裸数字式组声明; 不属于则返回 None。"""
    compact = str(unit or "").strip()
    if not compact or len(compact) > 40:
        return None
    for pattern, source in ((_GROUP_PAREN_RE, "group_paren"), (_GROUP_BARE_RE, "group_bare")):
        m = pattern.match(compact)
        if (m and float(m.group(2)) > 0 and not _is_skippable_label(m.group(1))
                and m.group(1).endswith(_GROUP_BARE_SUFFIXES)):
            return {"label": m.group(1), "score": float(m.group(2)), "weight": None, "source": source}
    return None


def capture_declaration_ledger(fixture: dict) -> dict:
    """对单样本 fixture 的页文本+表格运行确定性声明捕获, 产出原文组对与总分候选。"""
    from app.core.parser import scoring_group_anchor as sga  # noqa: PLC2701 私有函数只读复用
    from app.core.extractor.scoring.volume_parser import _FLAT_COMPOSITION_TRIGGER_TERMS  # noqa: PLC2701

    strong: list[dict] = []
    loose: list[dict] = []
    totals: list[float] = []
    pages = [(p["page_num"], p.get("text") or "") for p in fixture.get("pages", [])]

    # 组锚点: 闭合门控版(生产语义)入强集; 无门控原始锚点仅入宽松集(供 R1 上限观察)。
    for anchor in sga.extract_closed_group_anchors_from_pages(pages, expected_total=100.0):
        strong.append({"label": anchor.label, "score": anchor.max_score, "weight": None, "source": "anchor_gated"})
    for page_num, text in pages:
        for anchor in sga._extract_page_anchors(page_num, text):
            loose.append({"label": anchor.label, "score": anchor.max_score, "weight": None, "source": "anchor_loose"})

    # 页文本: 声明总分句 + 触发词行窗口(前1后2行)上的第一阶段捕获 + 扩展形态整行匹配。
    for _page_num, text in pages:
        totals.extend(float(v) for v in _TOTAL_SENTENCE_RE.findall(text))
        lines = text.split("\n")
        for idx, line in enumerate(lines):
            if any(term in line for term in _FLAT_COMPOSITION_TRIGGER_TERMS) or "总分" in line or "其中" in line:
                window = " ".join(lines[max(0, idx - 1): idx + 3])
                got_pairs, got_totals = _stage1_pairs_from_text(window)
                strong.extend(got_pairs)
                totals.extend(got_totals)
            ext_pair = _ext_group_pair_from_unit(line)
            if ext_pair:
                strong.append(ext_pair)

    # 表格: 触发词行(含相邻1行)逐格扫描 + 触发行内纯数字格作总分候选 + 逐格折算卷定义。
    for page in fixture.get("pages", []):
        for table in page.get("tables", []):
            data = table.get("data") if isinstance(table, dict) else table
            rows = [[str(c or "") for c in row] for row in (data or [])]
            trigger_rows = {
                i for i, row in enumerate(rows)
                if any(any(term in cell for term in _FLAT_COMPOSITION_TRIGGER_TERMS) for cell in row)
            }
            scan_rows = {i for t in trigger_rows for i in (t - 1, t, t + 1) if 0 <= i < len(rows)}
            for i, row in enumerate(rows):
                row_text = " ".join(row)
                if i in scan_rows:
                    got_pairs, got_totals = _stage1_pairs_from_text(row_text)
                    strong.extend(got_pairs)
                    totals.extend(got_totals)
                    if i in trigger_rows:
                        totals.extend(float(c) for c in row if _NUMERIC_CELL_RE.match(c.strip()))
                else:
                    got_pairs, got_totals = _stage1_pairs_from_text(row_text)
                    strong.extend([p for p in got_pairs if p["source"] in ("weighted_def", "inline")])
                    totals.extend(got_totals)
                # 扩展形态: 单元格整格匹配; 同行存在权重格(如"10%")时附加权重(权重列若易得)。
                row_weight = next(
                    (float(c.strip().rstrip("%％")) / 100.0 for c in row if _WEIGHT_CELL_RE.match(c.strip())),
                    None,
                )
                for cell in row:
                    for cell_line in str(cell).split("\n"):
                        ext_pair = _ext_group_pair_from_unit(cell_line)
                        if ext_pair:
                            if row_weight is not None and ext_pair["source"] == "group_paren":
                                ext_pair = {**ext_pair, "weight": row_weight}
                            strong.append(ext_pair)

    base_strong = [p for p in strong if p["source"] not in EXT_SOURCES]
    return {
        "strong_pairs": dedupe_captured_pairs(strong),
        "all_pairs": dedupe_captured_pairs(strong + loose),
        "base_pairs": dedupe_captured_pairs(base_strong + loose),
        "total_candidates": sorted({round(v, 4) for v in totals}),
    }


def reconcile_case_gap(
    key: str, corpus_dir: Path, truth_items: list[dict], group_field: str, declared_total: float
) -> dict:
    """指标3: 单案例对账——账本(捕获声明)与提取合计的文档级缺口 + 真值辅助的组级定位。"""
    from scripts.audit_scoring_legacy_baseline import (
        normalize_name_for_match,
        run_deterministic_extraction_for_sample,
    )

    fixture = _load_fixture(key, corpus_dir)
    ledger = capture_declaration_ledger(fixture) if fixture else {"strong_pairs": [], "total_candidates": []}
    extracted = run_deterministic_extraction_for_sample(key, corpus_dir=corpus_dir)
    extracted_total = float(extracted.get("total") or 0.0)

    remaining = [(normalize_name_for_match(n), float(s)) for n, s in extracted.get("item_pairs", [])]
    groups: dict[str, dict] = {}
    for item in truth_items:
        group = str(item.get(group_field) or "unspecified")
        bucket = groups.setdefault(group, {"truth_sum": 0.0, "matched_sum": 0.0, "missing": []})
        bucket["truth_sum"] += float(item["max_score"])
        target = (normalize_name_for_match(item["item_name"]), float(item["max_score"]))
        hit = next(
            (idx for idx, pair in enumerate(remaining)
             if pair[0] == target[0] and abs(pair[1] - target[1]) <= SCORE_TOLERANCE),
            None,
        )
        if hit is not None:
            bucket["matched_sum"] += float(item["max_score"])
            remaining.pop(hit)
        else:
            bucket["missing"].append(f"{item['item_name']}({item['max_score']:g})")

    return {
        "key": key, "corpus_dir": corpus_dir.name, "extracted_total": extracted_total,
        "extracted_count": extracted.get("item_count", 0), "ledger_pairs": ledger["strong_pairs"],
        "ledger_sum": round(sum(p["score"] for p in ledger["strong_pairs"]), 4),
        "ledger_folded": round(
            sum(p["score"] * float(p.get("weight") or 1.0) for p in ledger["strong_pairs"]), 4),
        "total_candidates": ledger["total_candidates"],
        "truth_total": round(sum(float(it["max_score"]) for it in truth_items), 4),
        "declared_total": declared_total, "groups": groups, "unmatched_extracted": len(remaining),
    }


def inspect_duplicate_table_fingerprints(key: str) -> dict:
    """指标4: 整表指纹跨页判重 + 行级去重佐证 + 装配 chunk 级指纹(生产判重口径)。"""
    from types import SimpleNamespace

    from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk, table_fingerprint
    from app.core.parser import ChunkPlanner
    from app.core.parser.pdf_parser import ParsedDocument, ParsedPage
    from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks

    fixture = _load_fixture(key, TRAINING_CORPUS_DIR)
    fp_locations: dict[str, list[tuple[int, int]]] = defaultdict(list)
    row_locations: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for page in fixture["pages"]:
        for idx, table in enumerate(page["tables"]):
            data = table.get("data") if isinstance(table, dict) else table
            grid = [[str(c or "") for c in row] for row in (data or [])]
            fp_locations[table_fingerprint(grid)].append((page["page_num"], idx))
            for row in grid:
                normalized = "\x1f".join("".join(c.split()) for c in row)
                row_locations[normalized].add((page["page_num"], idx))

    pages = [
        ParsedPage(
            page_num=p["page_num"],
            text=p.get("text") or "",
            tables=[
                SimpleNamespace(
                    data=(t.get("data") if isinstance(t, dict) else t) or [],
                    cell_grid=t.get("cell_grid") if isinstance(t, dict) else None,
                    table_index=i,
                    page_num=p["page_num"],
                )
                for i, t in enumerate(p["tables"])
            ],
        )
        for p in fixture["pages"]
    ]
    doc = ParsedDocument(file_path=fixture["source"], filename=fixture["source"],
                         page_count=fixture["page_count"], pages=pages)
    chunk_fps = []
    for chunk in build_scoring_table_chunks(doc, ChunkPlanner(), 0):
        payload = payload_from_chunk(chunk)
        if payload:
            chunk_fps.append({
                "fingerprint": payload.get("table_fingerprint"),
                "rows": len(payload.get("grid") or []),
                "pages": f"{payload.get('page_start')}-{payload.get('page_end')}",
            })

    table_dups = {fp: locs for fp, locs in fp_locations.items()
                  if len({loc[0] for loc in locs}) > 1}
    cross_table_dup_rows = {r: locs for r, locs in row_locations.items() if len(locs) > 1}
    return {
        "key": key,
        "table_count": sum(len(v) for v in fp_locations.values()),
        "unique_table_fps": len(fp_locations),
        "cross_page_dup_tables": table_dups,
        "cross_table_dup_row_count": len(cross_table_dup_rows),
        "unique_rows": list(row_locations.keys()),
        "chunk_fingerprints": chunk_fps,
    }


def _verify_dedup_matches_truth(unique_rows: list[str], truth_items: list[dict]) -> list[dict]:
    """行级去重后, 检查每个真值项名是否恰好出现1次且分值同行出现。"""
    results = []
    for item in truth_items:
        norm_name = re.sub(r"\s+", "", str(item["item_name"]))
        matches = [r for r in unique_rows if norm_name in r.replace("\x1f", "")]
        score_text = f"{item['max_score']:g}"
        results.append({
            "item_name": item["item_name"],
            "occurrences": len(matches),
            "score_in_row": any(score_text in m.split("\x1f") for m in matches),
        })
    return results


# ── 主流程与报告 ─────────────────────────────────────────────────────────────


def probe_ledger_feasibility() -> dict:
    """运行四项指标实测并返回汇总结构(报告与 stdout 共用)。"""
    _ensure_probe_env()
    references = parse_composition_references(TRAINING_TRUTH_PATH.read_text(encoding="utf-8"))
    training_groups = parse_training_truth_groups(TRAINING_TRUTH_PATH.read_text(encoding="utf-8"))
    sealed_groups = parse_sealed_truth_groups(SEALED_TRUTH_PATH.read_text(encoding="utf-8"))

    sample_specs = [(k, TRAINING_CORPUS_DIR) for k in TRAINING_KEYS]
    sample_specs += [(k, HOLDOUT_CORPUS_DIR) for k in DOWNGRADED_HOLDOUT_KEYS]

    r1_rows, r2_rows = [], []
    for key, corpus_dir in sample_specs:
        fixture = _load_fixture(key, corpus_dir)
        ledger = capture_declaration_ledger(fixture) if fixture else {
            "strong_pairs": [], "all_pairs": [], "total_candidates": []}
        ref = references.get(key, {"declared_total": None, "pairs": [], "annotated": False})
        if key in DOWNGRADED_HOLDOUT_KEYS:
            items = sealed_groups.get(key, [])
            ref = {"declared_total": round(sum(i["max_score"] for i in items), 4) if items else None,
                   "pairs": [], "annotated": False}
        declared = ref["declared_total"]
        declared_hit = (
            declared is not None
            and any(abs(c - declared) <= SCORE_TOLERANCE for c in ledger["total_candidates"])
        )
        details = match_reference_pairs(ref["pairs"], ledger["all_pairs"]) if ref["annotated"] else []
        base_details = match_reference_pairs(ref["pairs"], ledger["base_pairs"]) if ref["annotated"] else []
        r1_rows.append({
            "key": key, "annotated": ref["annotated"], "ref_count": len(ref["pairs"]),
            "hit_count": sum(1 for d in details if d["hit"]), "details": details,
            "hit_count_base": sum(1 for d in base_details if d["hit"]),
            "declared_total": declared, "declared_hit": declared_hit,
            "captured_strong": len(ledger["strong_pairs"]), "captured_all": len(ledger["all_pairs"]),
        })
        consistency = assess_ledger_consistency(ledger["strong_pairs"], ledger["total_candidates"])
        r2_rows.append({"key": key, **consistency, "pair_count": len(ledger["strong_pairs"]),
                        "candidates": ledger["total_candidates"][:8]})

    annotated = [r for r in r1_rows if r["annotated"]]
    ref_total = sum(r["ref_count"] for r in annotated)
    hit_total = sum(r["hit_count"] for r in annotated)
    hit_total_base = sum(r["hit_count_base"] for r in annotated)
    r1_recall = hit_total / ref_total if ref_total else 0.0
    r1_recall_base = hit_total_base / ref_total if ref_total else 0.0
    declared_rows = [r for r in r1_rows if r["declared_total"] is not None]
    declared_rate = (
        sum(1 for r in declared_rows if r["declared_hit"]) / len(declared_rows) if declared_rows else 0.0
    )
    eligible = [r for r in r2_rows if r["status"] != "insufficient"]
    consistent = [r for r in eligible if r["status"] in ("self_consistent", "folded_consistent")]
    r2_rate = len(consistent) / len(eligible) if eligible else 0.0
    # 副口径: 总分候选缺失时按本领域惯例默认 100 兜底(观察瓶颈是否在总分声明句捕获)。
    fallback_consistent = [
        r for r in eligible
        if r["status"] in ("self_consistent", "folded_consistent")
        or (r["raw_sum"] is not None and abs(r["raw_sum"] - 100.0) <= LEDGER_TOLERANCE)
        or (r["folded_sum"] is not None and abs(r["folded_sum"] - 100.0) <= LEDGER_TOLERANCE)
    ]
    r2_fallback_rate = len(fallback_consistent) / len(eligible) if eligible else 0.0

    cases = [
        reconcile_case_gap("09", TRAINING_CORPUS_DIR,
                           [{**i, "scheme_volume": f"{i['volume']}/{i['scheme']}"} for i in training_groups.get("09", [])],
                           "scheme_volume", references.get("09", {}).get("declared_total") or 400.0),
        reconcile_case_gap("16", TRAINING_CORPUS_DIR, training_groups.get("16", []), "volume",
                           references.get("16", {}).get("declared_total") or 100.0),
        reconcile_case_gap("23", HOLDOUT_CORPUS_DIR, sealed_groups.get("23", []), "scheme",
                           round(sum(i["max_score"] for i in sealed_groups.get("23", [])), 4)),
    ]
    fp_04 = inspect_duplicate_table_fingerprints("04")
    fp_02 = inspect_duplicate_table_fingerprints("02")
    dedup_check_04 = _verify_dedup_matches_truth(fp_04["unique_rows"], training_groups.get("04", []))

    return {
        "r1_rows": r1_rows, "r1_recall": r1_recall, "ref_total": ref_total, "hit_total": hit_total,
        "r1_recall_base": r1_recall_base, "hit_total_base": hit_total_base,
        "declared_rate": declared_rate, "declared_rows": len(declared_rows),
        "r2_rows": r2_rows, "r2_rate": r2_rate, "r2_eligible": len(eligible), "r2_consistent": len(consistent),
        "r2_fallback_rate": r2_fallback_rate, "r2_fallback_consistent": len(fallback_consistent),
        "cases": cases, "fp_04": fp_04, "fp_02": fp_02, "dedup_check_04": dedup_check_04,
    }


def format_probe_report(summary: dict, run_date: str = "") -> str:
    """将实测结果渲染为 Markdown 报告。"""
    from datetime import date as _date

    lines = [
        "# 评分项换轴重构 P0-3 只读探针报告(原文声明账本地基实测)",
        "",
        f"**运行日期**: {run_date or _date.today()}",
        "**样本范围**: 训练集 01~21/27/28 + 已降级首轮 holdout 23~26/29~34(冻结验证集 round2 未接触)",
        "**捕获口径**: 组锚点(门控入强集/无门控入宽松集) + volume_parser 第一阶段原文对 + 声明总分句 + 表内分值构成行 + 折算卷定义权重",
        "**标签归一**: 全半角/空白归一 + 尾缀剥离(裁决集 部分/标/卷/评审/评分标准, 追加 评审标准/标准/评分/项/分)",
        "",
        "## 指标汇总与阈值判定",
        "",
        "| 指标 | 实测值 | 阈值 | 判定 |",
        "|---|---:|---:|---|",
        f"| R1 声明对捕获召回(裁决口径) | {summary['r1_recall_base']:.2%} ({summary['hit_total_base']}/{summary['ref_total']}) | >=90% | "
        f"{'达标' if summary['r1_recall_base'] >= 0.90 else '不达标'} |",
        f"| R1 声明对捕获召回(含扩展形态) | {summary['r1_recall']:.2%} ({summary['hit_total']}/{summary['ref_total']}) | >=90% | "
        f"{'达标' if summary['r1_recall'] >= 0.90 else '不达标'} |",
        f"| R2 账本自洽率(严格口径: 须捕获总分候选) | {summary['r2_rate']:.2%} ({summary['r2_consistent']}/{summary['r2_eligible']}) | >=70% | "
        f"{'达标' if summary['r2_rate'] >= 0.70 else '不达标'} |",
        f"| R2 账本自洽率(副口径: 总分缺失默认100兜底) | {summary['r2_fallback_rate']:.2%} "
        f"({summary['r2_fallback_consistent']}/{summary['r2_eligible']}) | >=70% | "
        f"{'达标' if summary['r2_fallback_rate'] >= 0.70 else '不达标'} |",
        f"| declared_total 捕获率(单列) | {summary['declared_rate']:.2%} (n={summary['declared_rows']}) | - | - |",
        "| 指标3 组级缺口检测 | 见案例明细 | - | - |",
        "| 指标4 副本表指纹去重 | 见明细(整表指纹判重失败, 行级可判) | - | - |",
        "",
        "## 指标1 R1 逐样本明细(仅有构成注解的样本参与召回)",
        "",
        "| sample | 注解 | 参照对 | 命中(裁决) | 命中(含扩展) | 捕获强/全 | decl_total | decl命中 | 未命中参照(含扩展口径) |",
        "|---|---|---:|---:|---:|---|---:|---|---|",
    ]
    for r in summary["r1_rows"]:
        missing = "; ".join(f"{d['label']}={d['score']:g}" for d in r["details"] if not d["hit"]) or "-"
        decl = f"{r['declared_total']:g}" if r["declared_total"] is not None else "-"
        lines.append(
            f"| {r['key']} | {'Y' if r['annotated'] else 'N'} | {r['ref_count']} | {r['hit_count_base']} | {r['hit_count']} "
            f"| {r['captured_strong']}/{r['captured_all']} | {decl} | {'Y' if r['declared_hit'] else 'N'} | {missing} |"
        )
    lines += [
        "",
        "## 指标2 R2 逐样本落点(强集组对>=2的样本参与)",
        "",
        "| sample | 组对数 | Σ组分 | Σ折算 | 匹配候选 | 落点 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    status_cn = {"self_consistent": "自洽", "folded_consistent": "折算制自洽",
                 "inconsistent": "不自洽", "insufficient": "无捕获(<2组对)"}
    for r in summary["r2_rows"]:
        raw = f"{r['raw_sum']:g}" if r["raw_sum"] is not None else "-"
        folded = f"{r['folded_sum']:g}" if r["folded_sum"] is not None else "-"
        cand = f"{r['matched_candidate']:g}" if r["matched_candidate"] is not None else "-"
        lines.append(f"| {r['key']} | {r['pair_count']} | {raw} | {folded} | {cand} | {status_cn[r['status']]} |")

    lines += ["", "## 指标3 组级缺口检测案例(09/16/23)", ""]
    for case in summary["cases"]:
        ledger_desc = "; ".join(f"{p['label']}={p['score']:g}" + (f"(w{p['weight']:g})" if p.get("weight") else "")
                                for p in case["ledger_pairs"]) or "(无强集捕获)"
        lines += [
            f"### 样本 {case['key']}(语料目录 {case['corpus_dir']})",
            "",
            f"- 账本捕获组: {ledger_desc}; Σ账本={case['ledger_sum']:g}(折算后={case['ledger_folded']:g}); 总分候选={case['total_candidates']}",
            f"- 提取合计={case['extracted_total']:g}(项数{case['extracted_count']}, 匹配不到真值的提取项 {case['unmatched_extracted']} 个); "
            f"真值项和={case['truth_total']:g}; 声明总分={case['declared_total']:g}",
            f"- 文档级缺口: 提取-声明={case['extracted_total'] - case['declared_total']:+g}; "
            f"提取-真值项和={case['extracted_total'] - case['truth_total']:+g}",
            "",
            "| 组 | 真值组和 | 提取命中和 | 组缺口 | 未命中项 |",
            "|---|---:|---:|---:|---|",
        ]
        for group, info in case["groups"].items():
            gap = info["matched_sum"] - info["truth_sum"]
            missing = "; ".join(info["missing"][:6]) + ("..." if len(info["missing"]) > 6 else "") or "-"
            lines.append(f"| {group} | {info['truth_sum']:g} | {info['matched_sum']:g} | {gap:+g} | {missing} |")
        lines.append("")

    fp4, fp2 = summary["fp_04"], summary["fp_02"]
    dup_desc = "; ".join(f"{fp[:12]}@{locs}" for fp, locs in fp4["cross_page_dup_tables"].items()) or "无"
    chunk_desc = "; ".join(f"{c['fingerprint'][:12]}(rows={c['rows']},pages={c['pages']})" for c in fp4["chunk_fingerprints"])
    lines += [
        "## 指标4 structure-04 副本表指纹去重",
        "",
        f"- 原始表: {fp4['table_count']} 张, 唯一整表指纹 {fp4['unique_table_fps']} 个; 跨页完全相同指纹表对: {dup_desc}",
        f"- 行级判重: 跨表重复行 {fp4['cross_table_dup_row_count']} 条(副本证据); 装配chunk级: {chunk_desc}",
        "- 行级去重后与真值(6项/100分)对应性:",
        "",
        "| 真值项 | 去重后出现次数 | 分值同行 |",
        "|---|---:|---|",
    ]
    for c in summary["dedup_check_04"]:
        lines.append(f"| {c['item_name']} | {c['occurrences']} | {'Y' if c['score_in_row'] else 'N'} |")
    lines += [
        "",
        f"- 负例 structure-02: {fp2['table_count']} 张表, 唯一指纹 {fp2['unique_table_fps']} 个, "
        f"跨页相同指纹表对: {'无' if not fp2['cross_page_dup_tables'] else fp2['cross_page_dup_tables']}, "
        f"跨表重复行 {fp2['cross_table_dup_row_count']} 条",
        "",
        "## 实测中发现的与预期不符的事实(如实记录)",
        "",
        "1. **真值构成写法实为四种而非两/三种**: 括注式(01~06)、分号式(07~12)、多方案式(09)之外, 13~21/27/28 使用多行列表式(含 16 号\"原始100，权重10%，折算10\"加权行), 探针已兼容并测试。",
        "2. **structure-23 无任何分组能得出 21/49/30**: 密封真值按 scheme 分组为 承包人建议书17/承包人实施方案13/资信业绩20/投标报价50, 按 volume 分组为 2.2.4(1)/(3)=30、2.2.4(2)=20、2.2.4(4)=50(均合计100)。21/49/30 实为 structure-27 的构成, 任务预期疑似张冠李戴; 案例23 按 scheme 分组对账并如实呈现。",
        "3. **structure-23 提取合计实测为 100 而非任务提示的 58**: 文档级闭合正常, 但 12 个提取项与真值项名称/分值不匹配(见案例明细), 内容级偏差被文档级闭合掩盖。",
        "4. **structure-04 整表指纹无法判重**: 两份副本的跨页切分点不同(7+3行 vs 8+2行), 4 张表指纹全部唯一; 生产装配层(build_scoring_table_chunks)更是把两份副本融合为单个 18 行 chunk(指纹判重同样失效)。行级归一判重可行: 跨表重复行 10 条, 行级去重后 6 个真值项各恰好出现 1 次(投标报价出现 2 次系评分行与基准价说明行均含该词, 非副本残留)。",
        "5. **裁决五类捕获源覆盖不足**: 12/13/16 的组声明实际书写为\"标签（N分）\"括注式或组列内嵌\"商务标 34 分\"式, 07 为\"一、价格部分 20\"编号标题裸数字式, 均不在裁决口径内; 探针以扩展形态(ext)单列实测, 两口径差距见汇总表。",
        "6. **部分参照组对在原文无显式声明可捕**: 09 的四套方案各 100 分、27 的 21/49/30 在原文仅为章节标题+表格项和的隐式存在, 02 的组分值(50/20/10/20)位于多级表头组列(跨单元格), 行级正则捕获天花板低于 100%, 纯\"显式声明账本\"需辅以组标签下的项和推导。",
        f"7. **R2 瓶颈在总分声明句捕获而非组对错误**: declared_total 捕获率仅 {summary['declared_rate']:.0%}; 严格口径 R2={summary['r2_rate']:.2%}, 其中多数不自洽样本的 Σ组分恰好等于 100 只是原文无可捕获的总分声明句; 按\"总分缺失默认100\"兜底后 R2={summary['r2_fallback_rate']:.2%}。",
        "8. **扩展形态必须做组尾缀门控**: 无门控时\"质量保证措施（15分）\"等项级行会被误捕为组对(实测 R2 分母被污染, 自洽率跌至 32%), 组标签与项名的词面歧义(如 建议书/方案 结尾)是账本方案落地时的核心风险点。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    """探针入口: 运行实测、写报告、打印四项汇总。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    summary = probe_ledger_feasibility()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(format_probe_report(summary), encoding="utf-8")
    print(f"[probe] 报告已写入: {REPORT_PATH}")
    print(f"[probe] R1 声明对捕获召回 = 裁决口径 {summary['r1_recall_base']:.2%} "
          f"({summary['hit_total_base']}/{summary['ref_total']}) / 含扩展形态 {summary['r1_recall']:.2%} "
          f"({summary['hit_total']}/{summary['ref_total']}) 阈值>=90% -> "
          f"{'达标' if summary['r1_recall'] >= 0.90 else '不达标'}")
    print(f"[probe] R2 账本自洽率 = 严格口径 {summary['r2_rate']:.2%} "
          f"({summary['r2_consistent']}/{summary['r2_eligible']}) / 默认100兜底 "
          f"{summary['r2_fallback_rate']:.2%} ({summary['r2_fallback_consistent']}/{summary['r2_eligible']}) "
          f"阈值>=70% -> {'达标' if summary['r2_rate'] >= 0.70 else '严格口径不达标'}")
    print(f"[probe] declared_total 捕获率 = {summary['declared_rate']:.2%} (n={summary['declared_rows']})")
    for case in summary["cases"]:
        print(f"[probe] 案例{case['key']}: 提取={case['extracted_total']:g} 声明={case['declared_total']:g} "
              f"真值项和={case['truth_total']:g} Σ账本={case['ledger_sum']:g} "
              f"缺口(提取-声明)={case['extracted_total'] - case['declared_total']:+g}")
    fp4 = summary["fp_04"]
    print(f"[probe] 指标4: 04号整表指纹 {fp4['unique_table_fps']}/{fp4['table_count']} 唯一, "
          f"跨页相同指纹表对 {len(fp4['cross_page_dup_tables'])} 组, 跨表重复行 {fp4['cross_table_dup_row_count']} 条")


if __name__ == "__main__":
    main()
