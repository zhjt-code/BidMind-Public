"""
职责: 文档质检运行的输入指纹(文档级/信封级), 输入或规则一变指纹即变
不做: 不读数据库、不做哈希以外的判定
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from app.core.document_qa.contracts import RULESET_VERSION
from app.core.document_qa.rules.loader import rules_digest


def _ruleset_token() -> str:
    from app.core.config import settings

    version = str(getattr(settings, "DOCUMENT_QA_RULESET_VERSION", "") or RULESET_VERSION)
    return f"{version}:{rules_digest()}"


def document_run_fingerprint(
    *,
    document_id: int,
    file_hash: Optional[str],
    content_hash: Optional[str],
) -> str:
    """文档级指纹: 字节哈希 + 归一化内容哈希 + 规则集。

    file_hash 捕获重新排版(文本不变字节变), content_hash 捕获文本变化;
    存量文档两哈希可能为空, 以 document_id 兜底保证仍可区分。
    """
    raw = "|".join([
        "doc",
        str(document_id),
        file_hash or "",
        content_hash or "",
        _ruleset_token(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def envelope_fingerprint(documents: list[dict[str, Any]]) -> str:
    """信封级指纹: 按 (bid_part, document_id) 稳定排序的全部卷哈希 + 规则集。

    documents 元素: {document_id, bid_part, file_hash, content_hash}
    整本(1卷)与分卷(2卷)走同一计算, 无专用分支。
    """
    ordered = sorted(
        documents,
        key=lambda d: (str(d.get("bid_part") or ""), int(d.get("document_id") or 0)),
    )
    parts = ["env", _ruleset_token()]
    for doc in ordered:
        parts.append("|".join([
            str(doc.get("bid_part") or ""),
            str(doc.get("document_id") or ""),
            str(doc.get("file_hash") or ""),
            str(doc.get("content_hash") or ""),
        ]))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
