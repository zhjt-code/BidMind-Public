"""评分组锚点识别。

职责: 在评分区段内识别"组名+（满分|总分）:N分"形态的组级满分声明，
 产出带页码的组锚点序列，并做集合级分值闭环验证。
不做: 不解析表格评分行、不产评分项、不写DB、不调用LLM。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.core.extractor.scoring.column_role_inference import volume_from_group_text

_GROUP_SCORE_RE = re.compile(
    # 存量标题字符接受集合保持不变；Markdown `|` 在 _clean_label 协议边界剥离，
    # 禁止为修载体噪声收窄历史锚点召回面。
    r"(?P<label>[\u4e00-\u9fffA-Za-z0-9()、/\\-·\s]{0,40}?)"
    r"(?:满分|总分)\s*[:：]?\s*(?P<score>\d+(?:\.\d+)?)\s*分"
)
_HEADING_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\s*)?(?P<label>[\u4e00-\u9fffA-Za-z0-9()、/\\-·\s]{2,40})\s*$")
_NUMBER_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)*\s*")
_LABEL_NOISE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class GroupAnchor:
    """评分组锚点。"""

    page_num: int
    label: str
    max_score: float
    volume: str


def extract_closed_group_anchors_from_pages(
    pages: Iterable[tuple[int, str]],
    *,
    expected_total: float = 100.0,
    tolerance: float = 1.0,
) -> list[GroupAnchor]:
    """从页文本中提取组锚点；仅当组满分合计闭合到期望总分时返回。"""
    anchors: list[GroupAnchor] = []
    for page_num, text in pages:
        anchors.extend(_extract_page_anchors(page_num, text))
    deduped = _dedupe_anchors(anchors)
    if len(deduped) < 2:
        return []
    total = round(sum(anchor.max_score for anchor in deduped), 4)
    if abs(total - expected_total) > tolerance:
        return []
    return deduped


def build_group_composition_sentence(anchors: Iterable[GroupAnchor]) -> str | None:
    """把组锚点转换为既有 volume_parser 可消费的分值构成声明句。"""
    pairs: list[str] = []
    for anchor in anchors:
        label = _composition_label(anchor.label)
        if not label:
            continue
        score_text = _format_score(anchor.max_score)
        pairs.append(f"{label}：{score_text}分")
    if len(pairs) < 2:
        return None
    return "分值构成：" + "；".join(pairs)


def _extract_page_anchors(page_num: int, text: str) -> list[GroupAnchor]:
    anchors: list[GroupAnchor] = []
    previous_heading = ""
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for match in _GROUP_SCORE_RE.finditer(line):
            label = _clean_label(match.group("label")) or previous_heading
            if line.startswith("|"):
                label = label.lstrip("|").strip()
            if not label:
                continue
            try:
                score = float(match.group("score"))
            except (TypeError, ValueError):
                continue
            if score <= 0:
                continue
            anchors.append(
                GroupAnchor(
                    page_num=page_num,
                    label=label,
                    max_score=score,
                    volume=volume_from_group_text(label, "other"),
                )
            )
        heading = _clean_heading(line)
        if heading:
            previous_heading = heading
    return anchors


def _dedupe_anchors(anchors: list[GroupAnchor]) -> list[GroupAnchor]:
    seen: set[tuple[int, str, float]] = set()
    out: list[GroupAnchor] = []
    for anchor in anchors:
        key = (anchor.page_num, anchor.label, anchor.max_score)
        if key in seen:
            continue
        seen.add(key)
        out.append(anchor)
    return out


def _clean_heading(line: str) -> str:
    if _GROUP_SCORE_RE.search(line):
        return ""
    match = _HEADING_RE.match(line)
    if not match:
        return ""
    return _clean_label(match.group("label"))


def _clean_label(raw: str) -> str:
    label = _NUMBER_PREFIX_RE.sub("", str(raw or "")).strip(" ：:;；，,。")
    label = _LABEL_NOISE_RE.sub(" ", label).strip()
    # "价格评分(总分:15 分)" 的标签捕获组含开括号, 残留尾随"("会使
    # 归一键与提取项侧失配(组行恒报提取0)且展示层出现"价格评分（"。
    # 只剥首尾**不成对**的括号残片, 成对括注(如"详细评审(商务)")不受影响。
    label = label.rstrip("（( ").lstrip("）) ").strip()
    if len(label) < 2:
        return ""
    return label


def _composition_label(label: str) -> str:
    return (
        str(label or "")
        .replace("(", "")
        .replace(")", "")
        .replace("（", "")
        .replace("）", "")
        .replace(" ", "")
        .strip("：:;；，,。")
    )


def _format_score(score: float) -> str:
    value = float(score)
    if value.is_integer():
        return str(int(value))
    return str(round(value, 4)).rstrip("0").rstrip(".")


__all__ = [
    "GroupAnchor",
    "build_group_composition_sentence",
    "extract_closed_group_anchors_from_pages",
]
