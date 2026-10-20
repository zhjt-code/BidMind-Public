"""嵌入式分值评分表解析器。

职责: 从无独立满分列的评分表行中解析"评分对象-满分-评分规则"的层级结构。
不做: 不定位评分区段、不拼表、不落库、不调用LLM、不处理投标响应评分。

适用表形态:
- 评审项目单元格自带总分: "技术方案（10分）"
- 父子层级同一行: "技术服务（30分）" + "材料性能（20分）"
- 价格公式表: "商务报价部分" + "基础分30分...最高加10分...40分"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from pydantic import ValidationError

from app.core.extractor.scoring.category_inference import infer_scoring_category
from app.core.extractor.scoring.group_column import detect_group_column
from app.core.extractor.scoring.row_group_attribution import (
    backfill_recovered_group_labels,
)
from app.core.extractor.scoring.scoring_item_naming import strip_scoring_name_decoration
from app.core.extractor.scoring.embedded_score_row_join import (
    join_split_embedded_score_rows,
)
from app.core.extractor.scoring.score_column_semantics import (
    find_embedded_penalties,
    find_embedded_scores,
)
from app.core.extractor.scoring.weighted_volume_table import volume_from_scoring_label
from app.schemas.requirement import ExtractedScoringItem

_SCORE_IN_NAME_RE = re.compile(
    r"(?P<name>.*?)\s*[（(]\s*(?P<score>\d+(?:\.\d+)?)\s*分\s*[）)]"
)
_SCORE_MENTION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*分")
_FORMULA_TOTAL_RE = re.compile(
    r"(?:[MmＭｍ]\s*[=＝]\s*|(?:得分|满分)\s*(?:为|=|＝)?\s*)(\d+(?:\.\d+)?)"
)
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

_RULE_HEADER_KW = ("评审标准", "评分标准", "评审内容", "评分内容")
_EVIDENCE_HEADER_KW = ("评审依据", "对应页码", "是否合格")


@dataclass(frozen=True)
class EmbeddedScoreCandidate:
    """行内评分对象候选。"""

    name: str
    score: float
    col_index: int


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _clean_name(raw: str) -> str:
    name = _norm(raw)
    match = _SCORE_IN_NAME_RE.search(name)
    if match:
        name = _norm(match.group("name"))
    return name.strip("：:;；,，。")


def _score_mentions(text: str) -> list[float]:
    values: list[float] = []
    for raw in _SCORE_MENTION_RE.findall(text or ""):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.append(value)
    return values


def _infer_category(name: str, rule: str) -> tuple[str, str | None]:
    """委托单一权威源推断 (category, basis)（P3-1 收口, 教训）。"""
    return infer_scoring_category(f"{name}{rule}")


def _infer_volume(
    name: str,
    rule: str,
    section_path: str,
    parent_name: str = "",
    fallback_volume: str | None = None,
) -> str:
    """推断评分项卷别。

 层级显式信号优先；父分组是表内结构证据，不得被子项名中的价格词反向覆盖。
 rule 内价格词仅在无表级兜底时才作为弱信号。
 """
    for source in (parent_name, section_path, name):
        strong_volume = volume_from_scoring_label(source, default="")
        if strong_volume:
            return strong_volume
    if fallback_volume:
        return fallback_volume
    if any(k in rule for k in ("报价", "价格", "评标价")):
        return "price"
    return "technical"


def _infer_volume_label(parent_name: str = "") -> str | None:
    """返回用于展示的表内分组原文标签；仅父分组明确时产出。"""
    if not volume_from_scoring_label(parent_name, default=""):
        return None
    label = _clean_name(parent_name)
    return label or None


def _infer_rule_column(data: list[list[str]], excluded: set[int]) -> int | None:
    """无规则表头时, 取"总文本最长且非嵌入分值主列"的列作为规则列。

 根因: 旧实现在识别不到规则表头时把所有列都当规则列, 导致没有任何列被
 扫描括号内嵌分值 → 静默零产出。规则/评审标准列文本最长, 据此推断可留出可扫描列。
 """
    width = max((len(r) for r in data), default=0)
    best_idx: int | None = None
    best_len = 0
    for idx in range(width):
        if idx in excluded:
            continue
        total = sum(len(_norm(_cell_at(row, idx))) for row in data)
        if total > best_len:
            best_len = total
            best_idx = idx
    return best_idx


def _cell_at(row: list[str], idx: int) -> str:
    return str(row[idx] or "") if 0 <= idx < len(row) else ""


def _header_indices(header: list[str], keywords: Iterable[str]) -> list[int]:
    result: list[int] = []
    for idx, cell in enumerate(header):
        h = _norm(cell)
        if any(k in h for k in keywords):
            result.append(idx)
    return result


def _rule_text(row: list[str], rule_indices: list[int]) -> str:
    parts: list[str] = []
    for idx in rule_indices:
        if 0 <= idx < len(row):
            value = str(row[idx] or "").strip()
            if value:
                parts.append(value)
    return " ".join(parts).strip()


def _row_text(row: list[str]) -> str:
    return " ".join(str(c or "") for c in row)


_VOLUME_TOTAL_TERMS = ("分值构成", "分值分配", "分值组成", "总分", "总分值", "合计")


def _is_volume_total_row(name: str, rule: str) -> bool:
    """是否为"分值构成/总分"等卷说明行(非评分子项)。"""
    head = _norm(name)
    return any(term in head for term in _VOLUME_TOTAL_TERMS)


def _price_formula_score(name: str, rule: str) -> float | None:
    """价格公式行通常无对象括号分值, 取规则中可达最高总分作为该项满分。"""
    text = f"{name}{rule}"
    if not any(k in text for k in ("报价", "价格", "评标价", "商务报价")):
        return None
    values = _formula_total_mentions(text)
    if not values:
        return None
    # 公式中会出现 "基础分30分 + 最高加10分 + 40分基础上"; 最高总分为最大值。
    return max(values)


def _formula_total_mentions(text: str) -> list[float]:
    """提取价格公式可达总分候选, 排除扣分步长。"""
    values: list[float] = []
    for match in _FORMULA_TOTAL_RE.finditer(text or ""):
        value = _to_positive_score(match.group(1))
        if value is not None:
            values.append(value)
    for match in _SCORE_MENTION_RE.finditer(text or ""):
        if _is_penalty_step_context(text, match.start()):
            continue
        value = _to_positive_score(match.group(1))
        if value is not None:
            values.append(value)
    return values


def _is_penalty_step_context(text: str, start: int) -> bool:
    prefix = text[max(0, start - 16): start]
    return any(term in prefix for term in ("扣", "每高", "每低", "每小于", "每大于", "减"))


def _to_positive_score(raw: str) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= 200 else None


def _build_item(
    *,
    name: str,
    score: float,
    rule: str,
    section_path: str,
    parent_name: str = "",
    fallback_volume: str | None = None,
) -> ExtractedScoringItem | None:
    clean = strip_scoring_name_decoration(_clean_name(name))
    if not clean or score <= 0:
        return None
    description = " ".join(part for part in (clean, rule.strip()) if part).strip()
    category, category_basis = _infer_category(clean, rule)
    try:
        return ExtractedScoringItem(
            category=category,
            category_basis=category_basis,
            score_item_name=clean[:80],
            description=description,
            score_weight=score,
            volume=_infer_volume(clean, rule, section_path, parent_name, fallback_volume),
            volume_label=_infer_volume_label(parent_name),
            score_rules=[rule] if rule else [],
        )
    except ValidationError:
        return None


def _build_penalty_item_from_row(
    row: list[str],
    scan_skip: set[int],
    rule: str,
    section_path: str,
    parent_name: str,
    fallback_volume: str | None,
) -> ExtractedScoringItem | None:
    """从行内 `对象名（-N-0分）` 纯扣分区间构造 penalty 调整项(score_weight=0)。"""
    for idx, cell in enumerate(row):
        if idx in scan_skip:
            continue
        penalties = find_embedded_penalties(cell)
        if not penalties:
            continue
        name, floor = penalties[0]
        clean = _clean_name(name)
        if not clean:
            continue
        description = " ".join(part for part in (clean, rule.strip()) if part).strip()
        category, category_basis = _infer_category(clean, rule)
        try:
            return ExtractedScoringItem(
                category=category,
                category_basis=category_basis,
                score_item_name=clean[:80],
                description=description or f"{clean}扣分项",
                score_weight=0.0,
                fact_type="penalty",
                volume=_infer_volume(clean, rule, section_path, parent_name, fallback_volume),
                volume_label=_infer_volume_label(parent_name),
                deduction_rules=[rule] if rule else [f"最高扣{floor:g}分"],
            )
        except ValidationError:
            return None
    return None


def parse_embedded_score_table(
    header: list[str],
    data: list[list[str]],
    *,
    section_path: str = "",
    scope_labels: dict[int, str] | None = None,
) -> list[ExtractedScoringItem]:
    """解析无独立满分列但含 `评分对象（N分）/标准分N分` 的评分表。

 scope_labels: 装配层表作用域标签(数据行序), 作组标签最后兜底。
 """
    if not data:
        return []
    data = join_split_embedded_score_rows(data)

    fallback_volume = _table_fallback_volume(header, data)
    allow_range_scores = "方案切分模板" in section_path
    rule_indices = _header_indices(header, _RULE_HEADER_KW)
    evidence_indices = set(_header_indices(header, _EVIDENCE_HEADER_KW))
    if not rule_indices:
        # 表头识别不到规则列时按"最长文本列"推断, 而非把所有列当规则列
        # (后者会让没有任何列被扫描括号分值, 静默返回 0 项)。
        inferred = _infer_rule_column(data, evidence_indices)
        rule_indices = [inferred] if inferred is not None else []
    scan_skip = set(rule_indices) | evidence_indices

    items: list[ExtractedScoringItem] = []
    seen: set[tuple[str, float]] = set()
    last_parent_name = ""
    last_parent_score: float | None = None
    # 分组列(结构+父子闭合)命中时逐行组归属即原文组标签权威来源。
    # `_infer_volume_label` 依赖 `volume_from_scoring_label` 词面判定, 对"设计方案/
    # 施工方案"这类不含表内词面的真实大类恒返回 None, 致账本无标签可对账。
    # 必须在 join_split_embedded_score_rows 之后检测, 否则行号与本循环不对齐。
    structural_detection = detect_group_column(data)
    structural_row_labels = (
        structural_detection.row_labels if structural_detection is not None else {}
    )

    # 分值嵌在项名里的表没有独立分值列, 计分行与项名只有解析完才知道,
    # 故组归属补救放在循环之后按行号回填(见 _backfill_recovered_group_labels)。
    built_rows: list[tuple[int, ExtractedScoringItem]] = []

    for row_idx, row in enumerate(data):
        # 结构标签直接覆盖 volume_label(不经 _clean_name/词面 gate), 与声明侧
        # 组标签严格同源, 保证 normalize_group_label 归一键可配对。
        structural_label = structural_row_labels.get(row_idx)
        rule = _rule_text(row, rule_indices)
        candidates: list[EmbeddedScoreCandidate] = []
        for idx, cell in enumerate(row):
            if idx in scan_skip:
                continue
            for name, score in find_embedded_scores(cell, allow_ranges=allow_range_scores):
                candidates.append(EmbeddedScoreCandidate(name=name, score=score, col_index=idx))

        if candidates:
            # 父子同行时: 浅层(列索引最小)为父项(只提供卷别语义), 深层为实际评分子项。
            selected = max(candidates, key=lambda c: c.col_index)
            if len(candidates) > 1:
                parent = min(candidates, key=lambda c: c.col_index)
                last_parent_name = parent.name
                last_parent_score = parent.score
            elif (
                last_parent_name
                and _clean_name(selected.name) == _clean_name(last_parent_name)
                and selected.score == last_parent_score
            ):
                # 跨页合并单元格使父分组格(如"商务部分评分标准（13分）")再次
                # 单独成行——同名且同分值即分组表头延续, 不是新评分事实, 产出会与子项
                # 重复计分。同名但分值不同的是真实子项(sample06"设计方案(10分)"), 保留。
                continue
            item = _build_item(
                name=selected.name,
                score=selected.score,
                rule=rule,
                section_path=section_path,
                parent_name=last_parent_name,
                fallback_volume=fallback_volume,
            )
        else:
            # 阶段3(样本9诚信评价): 纯扣分区间"对象名（-N-0分）"建模为 penalty 调整项,
            # 不进入基础满分; 禁止静默丢弃或误读为 N 分正项。
            if "分值构成" in _row_text(row):
                continue
            penalty_item = _build_penalty_item_from_row(
                row, scan_skip, rule, section_path, last_parent_name, fallback_volume
            )
            if penalty_item is not None:
                if structural_label:
                    penalty_item.volume_label = structural_label
                key = (penalty_item.score_item_name, -1.0)
                if key not in seen:
                    seen.add(key)
                    items.append(penalty_item)
                continue
            # 没有对象括号分值时, 只对价格/报价公式行按最高总分生成单项; 纯续行不输出。
            name_candidates: list[str] = []
            for idx, cell in enumerate(row):
                if idx in scan_skip:
                    continue
                candidate = _clean_name(str(cell or ""))
                if candidate and _CHINESE_RE.search(candidate):
                    name_candidates.append(candidate)
            name = _select_price_formula_name(name_candidates, rule)
            # 跳过"分值构成/总分/合计"等卷说明行, 否则其规则文本里的"投标报价：30分"
            # 会被价格公式兜底误算成一个满分项(与父子子项重复计分)。
            if _is_volume_total_row(name, rule):
                score = None
            else:
                score = _price_formula_score(name, rule)
            item = (
                _build_item(
                    name=name, score=score, rule=rule,
                    section_path=section_path, parent_name=last_parent_name,
                    fallback_volume=fallback_volume,
                )
                if score is not None
                else None
            )

        if item is None:
            continue
        if structural_label:
            # 与既有 source_pages 富化同一模式: 构造后就地补权威字段, 避免为单一
            # 字段给 _build_item 追加第 7 个入参(复杂度护栏参数上限为 6)。
            item.volume_label = structural_label
        key = (item.score_item_name, float(item.score_weight))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        built_rows.append((row_idx, item))

    if not structural_row_labels:
        # 分组列未命中时按合并单元格/组标题行/项名前缀补齐组标签,
        # 判据与回填语义收口在 row_group_attribution(明细表路径共用同一实现)。
        # header 作为前导行传入: 首个全宽组标题行("价格评分（总分：15分）")
        # 常被表头选择消费, 判据1需要它识别首作用域。
        backfill_recovered_group_labels(data, built_rows, leading_rows=[header])
    if scope_labels:
        # 装配层作用域标签是最弱兜底, 只填仍无标签的项。
        for row_idx, item in built_rows:
            if not getattr(item, "volume_label", None) and scope_labels.get(row_idx):
                item.volume_label = scope_labels[row_idx]
    return items


def _select_price_formula_name(candidates: list[str], rule: str) -> str:
    """价格公式行优先取具体评分对象名, 避免使用`投标报价评分标准`这类列标题。"""
    if not candidates:
        return ""
    if not _price_formula_score("".join(candidates), rule):
        return candidates[0]
    text = f"{''.join(candidates)}{rule}"
    if "有效响应报价" in text:
        return "有效响应报价"
    for candidate in candidates:
        if _clean_name(candidate) == "投标报价":
            return "投标报价"
    if "报价评分" in text:
        return "报价评分"
    if "评标价" in text:
        return "评标价"
    short_candidates = [candidate for candidate in candidates if len(candidate) <= 30]
    if not short_candidates:
        return "报价评分" if "投标报价" in text or "报价" in text else "价格"
    search_space = short_candidates or candidates[:1]
    for candidate in search_space:
        if any(k in candidate for k in ("报价", "价格", "评标价")) and not any(
            marker in candidate for marker in ("评分标准", "评审标准", "部分")
        ):
            return candidate
    return min(search_space, key=len)


def _table_fallback_volume(header: list[str], data: list[list[str]]) -> str | None:
    """从整表文本给嵌入式评分项提供兜底卷别。"""
    text = _norm("|".join(header) + "|" + "|".join(_row_text(row) for row in data))
    business_hits = text.count("商务")
    technical_hits = text.count("技术")
    if business_hits >= 2 and business_hits > technical_hits:
        return "commercial"
    if technical_hits >= 2 and technical_hits > business_hits:
        return "technical"
    return None


def has_embedded_score_signal(header: list[str], data: list[list[str]]) -> bool:
    """是否存在嵌入式评分分值信号；用于避免误判为符合性表后静默跳过。"""
    joined = "\n".join(["|".join(header), *("|".join(row) for row in data)])
    return has_embedded_score_signal_text(joined)


def has_embedded_score_signal_text(text: str) -> bool:
    """文本中是否存在评分表常见分值信号。"""
    return bool(_SCORE_MENTION_RE.search(text or "")) and any(
        k in text for k in ("评分", "评审", "得分", "基础分", "标准分", "满分")
    )
