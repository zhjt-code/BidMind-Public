from __future__ import annotations

from types import SimpleNamespace

from app.models.document import DocumentType
from app.models.vector_job_state import VectorJobStatus


def _doc(tmp_path, *, doc_id: int, filename: str = "bid.pdf", parsed: int = 1):
    path = tmp_path / filename
    path.write_bytes(b"%PDF-1.4\n%%EOF")
    return SimpleNamespace(
        id=doc_id,
        filename=filename,
        file_path=str(path),
        file_size=path.stat().st_size,
        doc_type=DocumentType.BID,
        parsed=parsed,
    )


def test_readiness_blocks_parsed_document_without_embeddings(tmp_path) -> None:
    from app.services.collusion_readiness_service import build_collusion_readiness_report

    doc = _doc(tmp_path, doc_id=101)
    report = build_collusion_readiness_report(
        documents=[doc],
        chunk_counts={101: 3},
        embedding_counts={101: 0},
        vector_statuses={101: VectorJobStatus.COMPLETED},
    )

    assert report.ready is False
    item = report.blocking_documents[0]
    assert item.document_id == 101
    assert item.retry_action == "reprocess"
    assert "向量" in item.blocking_reason


def test_readiness_blocks_missing_source_file_with_reupload_action(tmp_path) -> None:
    from app.services.collusion_readiness_service import build_collusion_readiness_report

    missing_path = tmp_path / "missing.pdf"
    doc = SimpleNamespace(
        id=102,
        filename="missing.pdf",
        file_path=str(missing_path),
        file_size=128,
        doc_type=DocumentType.BID,
        parsed=1,
    )

    report = build_collusion_readiness_report(
        documents=[doc],
        chunk_counts={102: 5},
        embedding_counts={102: 5},
        vector_statuses={102: VectorJobStatus.COMPLETED},
    )

    assert report.ready is False
    item = report.blocking_documents[0]
    assert item.retry_action == "reupload"
    assert "不存在" in item.blocking_reason


def test_readiness_accepts_existing_parsed_document_with_embeddings(tmp_path) -> None:
    from app.services.collusion_readiness_service import build_collusion_readiness_report

    doc = _doc(tmp_path, doc_id=103)
    report = build_collusion_readiness_report(
        documents=[doc],
        chunk_counts={103: 5},
        embedding_counts={103: 5},
        vector_statuses={103: VectorJobStatus.COMPLETED},
    )

    assert report.ready is True
    assert report.blocking_documents == []
