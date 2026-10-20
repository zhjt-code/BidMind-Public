from tests.synthetic_data import synthetic_text
from pathlib import Path

import pytest


def test_build_pseudonym_is_stable_and_keyed() -> None:
    from app.core.writer.quality.privacy import build_pseudonym

    first = build_pseudonym("project", 123, b"a" * 32)
    second = build_pseudonym("project", 123, b"a" * 32)
    other_secret = build_pseudonym("project", 123, b"b" * 32)
    other_namespace = build_pseudonym("node", 123, b"a" * 32)

    assert first == second
    assert first.startswith("project_")
    assert first != other_secret
    assert first != other_namespace


@pytest.mark.parametrize(
    "payload",
    [
        {"project_name": "真实项目"},
        {"sections": [{"content": "完整正文不允许进入仓库资产"}]},
        {"contact": synthetic_text('138', '0013', '8000')},
        {"email": "user@example.com"},
        {"id_card": synthetic_text('110105', '194912', '31002X')},
        {"path": r"H:\\BidMind AI Lite\\uploads\\secret.pdf"},
        {"summary": "这是一段非常长的自由文本" * 20},
    ],
)
def test_validate_private_export_rejects_sensitive_payloads(payload: object) -> None:
    from app.core.writer.quality.privacy import PrivacyLeakError, validate_private_export

    with pytest.raises(PrivacyLeakError):
        validate_private_export(payload)


def test_atomic_write_private_export_leaves_no_file_on_privacy_failure(tmp_path: Path) -> None:
    from app.core.writer.quality.privacy import PrivacyLeakError, atomic_write_private_export

    target = tmp_path / "bad.json"

    with pytest.raises(PrivacyLeakError):
        atomic_write_private_export(target, {"filename": "secret.docx"})

    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))
