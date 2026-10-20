"""生成图示正文位置后处理器。

职责: 规范化 data-diagram-id 协议并把缺失图示补插到对应论述位置。
不做: 不编译 IR、不渲染 PNG、不写 Word、不处理素材图片。
"""

from __future__ import annotations

import re
from html import escape
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup, NavigableString, Tag
from loguru import logger

if TYPE_CHECKING:
    from app.core.writer.visuals.materializer import DiagramAsset

_ATTACHMENT_REF_RE = re.compile(
    r"(?:，|,)?\s*(?:详见|参见|见|另见|请见)\s*(?:附件|附图|附表|图示|图件)[^。；;，,]*",
)


def _diagram_figure_html(asset: "DiagramAsset") -> str:
    safe_title = escape(asset.title or "")
    return (
        f'<figure data-diagram-id="{asset.diagram_id}">'
        f"<figcaption>{safe_title}</figcaption></figure>"
    )


def _make_figure_tag(soup: BeautifulSoup, asset: "DiagramAsset") -> Tag:
    fragment = BeautifulSoup(_diagram_figure_html(asset), "html.parser")
    figure = fragment.find("figure")
    if figure is None:
        raise ValueError("failed to build diagram figure")
    return figure


def _clean_attachment_references(html: str) -> str:
    return _ATTACHMENT_REF_RE.sub("", html or "")


def _find_visual_payload(
    asset: "DiagramAsset",
    plan_payload: dict[str, Any],
) -> dict[str, Any]:
    for visual in plan_payload.get("visuals", []) or []:
        if str(visual.get("visual_id", "")) == asset.visual_id:
            return visual
    return {}


def _find_measure_payload(
    visual: dict[str, Any],
    plan_payload: dict[str, Any],
) -> dict[str, Any]:
    measures_by_id = {
        str(measure.get("measure_id", "")): measure
        for measure in plan_payload.get("measures", []) or []
    }
    hint = str(visual.get("placement_hint", "") or "")
    if hint and hint in measures_by_id:
        return measures_by_id[hint]
    for measure_id in visual.get("measure_ids", []) or []:
        measure = measures_by_id.get(str(measure_id))
        if measure is not None:
            return measure
    return {}


def _placement_terms(
    asset: "DiagramAsset",
    visual: dict[str, Any],
    measure: dict[str, Any],
) -> list[str]:
    candidates: list[str] = [
        str(measure.get("action", "") or ""),
        str(visual.get("purpose", "") or ""),
        str(asset.title or ""),
    ]
    candidates.extend(str(item or "") for item in measure.get("steps", []) or [])
    terms: list[str] = []
    for item in candidates:
        item = re.sub(r"\s+", "", item.strip())
        if len(item) >= 2 and item not in terms:
            terms.append(item)
    return terms


def _text_score(text: str, terms: list[str]) -> int:
    compact = re.sub(r"\s+", "", text or "")
    score = 0
    for term in terms:
        if term and term in compact:
            score += 3 if len(term) >= 6 else 1
    return score


def _find_anchor_tag(
    soup: BeautifulSoup,
    asset: "DiagramAsset",
    plan_payload: dict[str, Any],
) -> Tag | None:
    visual = _find_visual_payload(asset, plan_payload)
    measure = _find_measure_payload(visual, plan_payload)
    terms = _placement_terms(asset, visual, measure)
    if not terms:
        return soup.find(["p", "li"])

    best_tag: Tag | None = None
    best_score = 0
    for tag in soup.find_all(["p", "li"]):
        score = _text_score(tag.get_text(" ", strip=True), terms)
        if score > best_score:
            best_tag = tag
            best_score = score
    if best_tag is not None:
        return best_tag
    return soup.find(["p", "li"])


def _normalize_existing_figures(
    soup: BeautifulSoup,
    assets_by_id: dict[str, "DiagramAsset"],
) -> set[str]:
    seen: set[str] = set()
    for figure in list(soup.find_all("figure")):
        diagram_id = str(figure.get("data-diagram-id", "") or "").strip()
        if diagram_id not in assets_by_id:
            continue
        if diagram_id in seen:
            figure.decompose()
            continue
        figure.replace_with(_make_figure_tag(soup, assets_by_id[diagram_id]))
        seen.add(diagram_id)
    return seen


def enforce_diagram_placement(
    html: str,
    assets: list["DiagramAsset"],
    plan_payload: dict[str, Any] | None = None,
) -> str:
    """保证每张生成图在正文中规范、唯一，并尽量贴近其 placement_hint。"""

    if not assets:
        return html
    payload = plan_payload or {}
    cleaned_html = _clean_attachment_references(html or "")
    soup = BeautifulSoup(cleaned_html, "html.parser")
    assets_by_id = {str(asset.diagram_id): asset for asset in assets}
    existing_ids = _normalize_existing_figures(soup, assets_by_id)

    inserted = 0
    for asset in assets:
        diagram_id = str(asset.diagram_id)
        if diagram_id in existing_ids:
            continue
        figure = _make_figure_tag(soup, asset)
        anchor = _find_anchor_tag(soup, asset, payload)
        if anchor is not None:
            anchor.insert_after(NavigableString("\n"))
            anchor.insert_after(figure)
        else:
            soup.append(NavigableString("\n"))
            soup.append(figure)
        inserted += 1

    if inserted:
        logger.info("图示原位补插: count={}", inserted)
    return str(soup)


_FAKE_FIGURE_RE = re.compile(
    r"<figure\s+[^>]*data-diagram-id=['\"]([^'\"]*)['\"][^>]*>.*?</figure>",
    re.DOTALL | re.IGNORECASE,
)
_UNGROUNDED_CLAIM_RES = (
    re.compile(r"如下图所示[：:]?"),
    re.compile(r"见图[0-9一二三四五六七八九十]+"),
    re.compile(r"以甘特图形式提交[^。；;]*[。；;]?"),
    re.compile(r"详细甘特图作为本方案附件一并提交[。；;]?"),
)


def sanitize_ungrounded_diagram_claims(
    html: str,
    assets: list["DiagramAsset"],
) -> str:
    """删除无对应资产的 figure，并在无图时清掉成图口头承诺。

    职责: 保证正文不出现假 diagram-id 或无图的「如下图/附件甘特图」。
    不做: 不插入 PNG、不改 Word。
    """

    allowed_ids = {
        str(asset.diagram_id)
        for asset in assets
        if str(asset.diagram_id).isdigit()
    }

    def _keep_or_drop(match: re.Match[str]) -> str:
        diagram_id = str(match.group(1) or "").strip()
        if diagram_id.isdigit() and diagram_id in allowed_ids:
            return match.group(0)
        return ""

    cleaned = _FAKE_FIGURE_RE.sub(_keep_or_drop, html or "")
    if allowed_ids:
        return cleaned
    cleaned = _clean_attachment_references(cleaned)
    for pattern in _UNGROUNDED_CLAIM_RES:
        cleaned = pattern.sub("", cleaned)
    return cleaned


__all__ = ["enforce_diagram_placement", "sanitize_ungrounded_diagram_claims"]
