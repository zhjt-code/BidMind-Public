"""
职责: 校验行业画像六段契约是否齐全，并在缺失时构造一次续写。
不做: 不调用 LLM、不写库、不改展示、不做下游选段。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from app.core.writer.industry_profile_view import (
    REQUIRED_PROFILE_SECTIONS,
    parse_industry_profile,
)
from app.core.writer.prompts.industry_profile import PROFILE_SECTION_HEADINGS

_HEADING_BY_KEY: Final[dict[str, str]] = dict(
    zip(REQUIRED_PROFILE_SECTIONS, PROFILE_SECTION_HEADINGS, strict=True)
)


@dataclass(frozen=True)
class ProfileCompleteness:
    """六段契约校验结果。"""

    parsed: bool
    present: tuple[str, ...]
    missing: tuple[str, ...]
    incomplete_tail: tuple[str, ...]
    reason: str

    @property
    def complete(self) -> bool:
        return self.parsed and not self.missing and not self.incomplete_tail

    @property
    def present_count(self) -> int:
        return len(self.present)


def assess_profile_completeness(content: str | None) -> ProfileCompleteness:
    """判断画像是否具备可入库的六段正文。"""
    view = parse_industry_profile(content)
    if not view.parsed:
        return ProfileCompleteness(
            parsed=False,
            present=(),
            missing=REQUIRED_PROFILE_SECTIONS,
            incomplete_tail=(),
            reason="unparsed",
        )

    present = tuple(
        key for key in REQUIRED_PROFILE_SECTIONS if view.section(key)
    )
    missing = tuple(
        key for key in REQUIRED_PROFILE_SECTIONS if key not in present
    )
    incomplete_tail = tuple(
        key
        for key in present
        if not _section_has_body(view.section(key))
    )
    if missing:
        reason = "missing_sections"
    elif incomplete_tail:
        reason = "incomplete_tail"
    else:
        reason = "ok"
    return ProfileCompleteness(
        parsed=True,
        present=present,
        missing=missing,
        incomplete_tail=incomplete_tail,
        reason=reason,
    )


def build_completion_user_message(
    partial: str,
    assessment: ProfileCompleteness,
) -> str:
    """构造一次续写/重写的 user 消息。"""
    needed = tuple(
        dict.fromkeys((*assessment.missing, *assessment.incomplete_tail))
    )
    heading_lines = "\n".join(
        _HEADING_BY_KEY[key] for key in needed if key in _HEADING_BY_KEY
    )
    if assessment.parsed and assessment.present and needed:
        action = (
            "下面是未完成的行业画像草稿。请从第一个缺失或未写完的标题起，"
            "只补齐缺失各节，或输出从该标题到第 6 节的完整续写。"
        )
    else:
        action = (
            "下面草稿未遵守六段契约（例如写成项目背景/市场分析长文）。"
            "请整篇重写为六段写作指南，不要续写该长文。"
        )
    return (
        f"{action}\n\n"
        f"必须出现的标题：\n{heading_lines or chr(10).join(PROFILE_SECTION_HEADINGS)}\n\n"
        f"## 当前草稿\n{partial.strip() or '（空）'}"
    )


def merge_profile_texts(initial: str, continuation: str) -> str:
    """在续写与原稿之间选择节更齐全的文本。"""
    first = (initial or "").strip()
    second = (continuation or "").strip()
    if not second:
        return first
    if not first:
        return second

    first_assess = assess_profile_completeness(first)
    second_assess = assess_profile_completeness(second)
    if second_assess.complete:
        return second
    if second_assess.present_count > first_assess.present_count:
        return second

    merged = f"{first.rstrip()}\n\n{second}"
    merged_assess = assess_profile_completeness(merged)
    if merged_assess.present_count >= max(
        first_assess.present_count, second_assess.present_count
    ):
        return merged
    return first if first_assess.present_count >= second_assess.present_count else second


async def fulfill_industry_profile_text(
    initial_text: str,
    *,
    sanitize: Callable[[str], str],
    complete: Callable[[str], Awaitable[str]],
) -> tuple[str, dict[str, str]]:
    """已完整则原样返回；否则续写一次，仍不齐则带 incomplete 元数据入库。"""
    cleaned = sanitize(initial_text)
    first = assess_profile_completeness(cleaned)
    if first.complete:
        return cleaned, {}

    try:
        continuation = await complete(build_completion_user_message(cleaned, first))
    except Exception:
        return cleaned, {
            "profile_confidence": "incomplete",
            "source": "llm_completion",
            "reason": "completion_failed",
        }

    merged = sanitize(merge_profile_texts(cleaned, continuation))
    second = assess_profile_completeness(merged)
    if second.complete:
        return merged, {
            "profile_confidence": "repaired",
            "source": "llm_completion",
            "reason": first.reason,
        }
    winner = merged if second.present_count >= first.present_count else cleaned
    winner_assess = assess_profile_completeness(winner)
    missing = ",".join((*winner_assess.missing, *winner_assess.incomplete_tail))
    return winner, {
        "profile_confidence": "incomplete",
        "source": "llm_completion",
        "reason": winner_assess.reason,
        "missing_sections": missing,
    }


def _section_has_body(section_text: str) -> bool:
    """标题行之外是否还有正文，避免「四、」处空标题算完成。"""
    lines = (section_text or "").splitlines()
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    return bool(body)
