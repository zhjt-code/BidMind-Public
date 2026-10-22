"""
职责: 将用户可见文案中的文档身份编号替换为上传文件名。
不做: 不映射 pair 序号「对1」、不清洗英文枚举、不写库、不做风险判定。
"""

from __future__ import annotations

import re

_GUARD = r"(?=[^0-9年月日份%]|$)"
_TWO_ID_RE = re.compile(
    rf"(文件对|投标文件|文件|文档)\s*(\d+)\s*[与和、,，]\s*(\d+){_GUARD}"
)
_LABELED_ID_RE = re.compile(rf"(文件对|投标文件|文件|文档)\s*(\d+){_GUARD}")
_DOC_ID_PARAM_RE = re.compile(
    r"(?:doc_id|document_id)\s*[=:：]\s*(\d+)(?![0-9])",
    re.IGNORECASE,
)
_CONJ_ID_RE = re.compile(rf"([与和、，,])\s*(\d+){_GUARD}")
_DUI_ID_RE = re.compile(rf"(?<!文件)对(\d+){_GUARD}")


def replace_document_identity_tokens(
    text: str,
    doc_id_to_name: dict[int, str],
    *,
    pair_ordinal_count: int = 0,
) -> str:
    """把文件对/文档/doc_id/与 后的已知文档编号替换为上传文件名。

    ``对N`` 当 N 落在 1..pair_ordinal_count 时视为 pair 序号，留给展示层处理。
    """
    if not text or not doc_id_to_name:
        return text or ""

    def _name(raw: str) -> str | None:
        try:
            return doc_id_to_name.get(int(raw))
        except (TypeError, ValueError):
            return None

    def _two_id(match: re.Match[str]) -> str:
        prefix, left, right = match.group(1), match.group(2), match.group(3)
        name_a, name_b = _name(left), _name(right)
        if name_a and name_b:
            return f"{prefix}{name_a}与{name_b}"
        return match.group(0)

    def _labeled(match: re.Match[str]) -> str:
        name = _name(match.group(2))
        return f"{match.group(1)}{name}" if name else match.group(0)

    def _param(match: re.Match[str]) -> str:
        return _name(match.group(1)) or match.group(0)

    def _conj(match: re.Match[str]) -> str:
        name = _name(match.group(2))
        return f"{match.group(1)}{name}" if name else match.group(0)

    def _dui(match: re.Match[str]) -> str:
        raw = match.group(1)
        try:
            number = int(raw)
        except (TypeError, ValueError):
            return match.group(0)
        if 1 <= number <= pair_ordinal_count:
            return match.group(0)
        return _name(raw) or match.group(0)

    result = _TWO_ID_RE.sub(_two_id, text)
    result = _LABELED_ID_RE.sub(_labeled, result)
    result = _DOC_ID_PARAM_RE.sub(_param, result)
    result = _CONJ_ID_RE.sub(_conj, result)
    result = _DUI_ID_RE.sub(_dui, result)
    return result
