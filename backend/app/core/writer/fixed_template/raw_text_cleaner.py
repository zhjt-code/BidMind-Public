"""固定模板 raw_text 二次清洗。

职责: 对 LLM 输出的固定模板 raw_text 做 PDF 抽取碎片二次清洗。
不做: 不识别模板、不调用 LLM、不修改字段语义、不识别签章布局。
"""

from __future__ import annotations

import re

from app.core.writer.fixed_template.titles import (
    aliases_for_key,
    infer_fixed_template_key,
    normalize_template_title,
)

_ISOLATED_CURRENCY_PATTERN = re.compile(r"^\s*(?:0\s*[¥￥$]?|[¥￥$])\s*$")
_ISOLATED_NUMBER_PATTERN = re.compile(r"^\s*\d{1,3}\s*$")
_LIST_NUMBER_PATTERN = re.compile(r"^\s*(\d{1,2})\s*$")
_LIST_FOLLOWUP_PATTERN = re.compile(r"^\s*(?:[、。．\.])\s*(.+)$")
_CN_NUMBER_TOKEN = "[一二三四五六七八九十百]"
_FIELD_LABEL_PATTERN = re.compile(r"[:：]\s*$")
_SHORT_FOLLOWUP_MAX = 6


def clean_template_raw_text(raw_text: str, title: str) -> str:
    """对 LLM 输出的 raw_text 做 PDF 碎片二次清洗。

    规则按顺序应用, 每条规则保守处理: 不能确定时保留原文。返回清洗后的文本,
    若清洗后内容为空则回退到原文。
    """
    if not raw_text:
        return raw_text
    lines = [line.rstrip() for line in raw_text.splitlines()]
    lines = _strip_leading_repeat_title(lines, title)
    lines = _merge_list_number_fragments(lines)
    lines = _drop_isolated_noise_lines(lines)
    lines = _merge_short_field_followup(lines)
    cleaned = "\n".join(line for line in lines if line is not None)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        return raw_text
    return cleaned


def _strip_leading_repeat_title(lines: list[str], title: str) -> list[str]:
    """规则 4: 首行命中模板标题 (相等 / 别名 / 子串关系) 时剥离。"""
    norm_title = normalize_template_title(title)
    if not norm_title or not lines:
        return lines
    pattern = re.compile(rf"^\s*{_CN_NUMBER_TOKEN}+\s*[、.\.]\s*(.+?)\s*$")
    first = lines[0].strip()
    match = pattern.match(first)
    candidate = match.group(1) if match else first
    if _matches_template_title(candidate, title, norm_title):
        return lines[1:]
    return lines


def _matches_template_title(candidate: str, title: str, norm_title: str) -> bool:
    """判断 candidate 是否与模板标题指向同一模板。

    命中策略 (按优先级):
      1. 归一相等 -> 命中
      2. candidate 命中 title 推断 template_key 的任一别名 -> 命中
      3. 归一后互为子串且较短一方长度 >= 4 -> 命中
    """
    norm_cand = normalize_template_title(candidate)
    if not norm_cand:
        return False
    if norm_cand == norm_title:
        return True
    inferred_key = infer_fixed_template_key(title)
    if inferred_key:
        for alias in aliases_for_key(inferred_key):
            if normalize_template_title(alias) == norm_cand:
                return True
    short, long = sorted([norm_cand, norm_title], key=len)
    if len(short) >= 4 and short in long:
        return True
    return False


def _merge_list_number_fragments(lines: list[str]) -> list[str]:
    """规则 1: 合并"独立数字行 + 顿号开头行"为同一编号项。

    例如 "1\n、xxx" → "1、xxx"。
    """
    merged: list[str] = []
    skip_next = False
    for idx, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        number_match = _LIST_NUMBER_PATTERN.match(line)
        if number_match and idx + 1 < len(lines):
            followup_match = _LIST_FOLLOWUP_PATTERN.match(lines[idx + 1])
            if followup_match:
                merged.append(f"{number_match.group(1)}、{followup_match.group(1).strip()}")
                skip_next = True
                continue
        merged.append(line)
    return merged


def _drop_isolated_noise_lines(lines: list[str]) -> list[str]:
    """规则 2 + 规则 3: 删除孤立 "0 ¥" / 单字符货币 / 孤立纯数字噪音行。"""
    cleaned: list[str] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        if _ISOLATED_CURRENCY_PATTERN.match(stripped):
            continue
        if _ISOLATED_NUMBER_PATTERN.match(stripped) and not _is_meaningful_number(idx, lines):
            continue
        cleaned.append(line)
    return cleaned


def _is_meaningful_number(idx: int, lines: list[str]) -> bool:
    """判断 idx 处的纯数字行是否构成有意义的编号上下文。"""
    prev_line = lines[idx - 1].strip() if idx > 0 else ""
    next_line = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
    if not next_line:
        return False
    if _LIST_FOLLOWUP_PATTERN.match(next_line):
        return True
    if prev_line.endswith(("。", ":", ":")):
        return True
    return False


def _merge_short_field_followup(lines: list[str]) -> list[str]:
    """规则 5: 当一行以 ":" / ":" 结尾且下一行 <= 6 字时, 合并为 "label: value"。

    保守策略: 仅在下一行不为编号、不为空、字数极短时合并, 避免破坏正文段落。
    """
    merged: list[str] = []
    skip_next = False
    for idx, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        stripped = line.strip()
        if (
            stripped
            and _FIELD_LABEL_PATTERN.search(stripped)
            and idx + 1 < len(lines)
        ):
            next_stripped = lines[idx + 1].strip()
            if (
                next_stripped
                and len(next_stripped) <= _SHORT_FOLLOWUP_MAX
                and not _LIST_NUMBER_PATTERN.match(next_stripped)
                and not _LIST_FOLLOWUP_PATTERN.match(next_stripped)
                and not _ISOLATED_CURRENCY_PATTERN.match(next_stripped)
            ):
                merged.append(f"{stripped} {next_stripped}")
                skip_next = True
                continue
        merged.append(line)
    return merged
