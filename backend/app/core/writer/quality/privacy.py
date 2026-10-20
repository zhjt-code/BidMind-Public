"""Writer quality privacy guardrails.

职责: 对写标质量导出物做伪名化、敏感字段扫描和原子落盘。
不做: 不执行业务质量评估、不保存或恢复脱敏前原文。
"""

from __future__ import annotations

import hmac
import hashlib
import json
from pathlib import Path
import re
from typing import Any


class PrivacyLeakError(ValueError):
    """Raised when a writer quality export contains unsafe content."""


_FORBIDDEN_KEYS = {
    "project_name",
    "filename",
    "file_name",
    "file_path",
    "raw_text",
    "content",
    "material_content",
    "original_text",
    "document_text",
    "expert_note",
    "summary",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^:*?\"<>|\r\n]+")
_USCC_RE = re.compile(r"\b[0-9A-Z]{18}\b")
_LONG_FREE_TEXT_LIMIT = 240


def build_pseudonym(namespace: str, raw_id: int, secret: bytes) -> str:
    """Build a stable keyed pseudonym for an internal integer ID."""

    if not namespace or not namespace.replace("_", "").isalnum():
        raise ValueError("namespace must be alphanumeric or underscore")
    if not secret:
        raise ValueError("secret must not be empty")
    message = f"{namespace}:{int(raw_id)}".encode("utf-8")
    digest = hmac.new(secret, message, hashlib.sha256).hexdigest()[:16]
    return f"{namespace}_{digest}"


def _looks_like_long_free_text(value: str) -> bool:
    text = value.strip()
    if len(text) <= _LONG_FREE_TEXT_LIMIT:
        return False
    # Fail closed: committed regression assets should contain compact IDs,
    # digests, enums, counts, and ratios. Long strings are private-export only.
    return True


def _scan_string(value: str, path: str) -> None:
    checks = (
        (_EMAIL_RE, "email"),
        (_PHONE_RE, "phone"),
        (_ID_CARD_RE, "id_card"),
        (_WINDOWS_PATH_RE, "windows_path"),
        (_USCC_RE, "unified_social_credit_code"),
    )
    for pattern, reason in checks:
        if pattern.search(value):
            raise PrivacyLeakError(f"{path}: detected {reason}")
    if _looks_like_long_free_text(value):
        raise PrivacyLeakError(f"{path}: detected long free text")


def _scan_payload(payload: Any, path: str) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}" if path else key_text
            if key_text in _FORBIDDEN_KEYS:
                raise PrivacyLeakError(f"{next_path}: forbidden key")
            _scan_payload(value, next_path)
        return
    if isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            _scan_payload(item, f"{path}[{index}]")
        return
    if isinstance(payload, str):
        _scan_string(payload, path or "$")


def validate_private_export(payload: object) -> None:
    """Fail if payload contains raw text, PII, paths, or unsafe free text."""

    _scan_payload(payload, "")


def atomic_write_private_export(path: Path | str, payload: object) -> None:
    """Validate and atomically write a private writer quality export JSON file."""

    target = Path(path)
    validate_private_export(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(target)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


__all__ = [
    "PrivacyLeakError",
    "atomic_write_private_export",
    "build_pseudonym",
    "validate_private_export",
]
