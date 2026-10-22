""" 防御测试: 投标审查上传必须以双文件完整性为状态边界。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings

_PDF_BYTES = b"%PDF-1.4\n% bid review upload state guard\n"


def _sync_engine():
    return create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"))


def _create_bid_review_project(client: TestClient, headers: dict) -> int:
    resp = client.post(
        "/api/projects",
        json={"name": f"-{time.time_ns()}", "project_type": "bid_review"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _set_project_status(project_id: int, status_name: str) -> None:
    engine = _sync_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("update projects set status = :status where id = :project_id"),
                {"status": status_name, "project_id": project_id},
            )
    finally:
        engine.dispose()


def _document_count(project_id: int) -> int:
    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("select count(*) from documents where project_id = :project_id"),
                {"project_id": project_id},
            ).scalar_one()
    finally:
        engine.dispose()


def _upload_pdf(
    client: TestClient,
    project_id: int,
    headers: dict,
    doc_type: str,
    filename: str,
    bid_part: str | None = None,
):
    data = {"doc_type": doc_type}
    if bid_part is not None:
        data["bid_part"] = bid_part
    return client.post(
        f"/api/projects/{project_id}/files",
        headers=headers,
        data=data,
        files={"file": (filename, _PDF_BYTES, "application/pdf")},
    )


def test_single_bid_review_file_upload_does_not_mark_project_uploading(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """单份文件落库不能把项目推进到 uploading, 否则会制造半成品运行态。"""
    project_id = _create_bid_review_project(client, auth_headers)

    resp = _upload_pdf(client, project_id, auth_headers, "tender", "tender.pdf")

    assert resp.status_code == 201, resp.text
    project_resp = client.get(f"/api/projects/{project_id}", headers=auth_headers)
    assert project_resp.status_code == 200
    assert project_resp.json()["data"]["status"] == "created"


def test_bid_review_single_document_cannot_start_processing(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """只有 tender 或 bid 任一文件时, 不允许启动文档解析。"""
    project_id = _create_bid_review_project(client, auth_headers)
    upload_resp = _upload_pdf(client, project_id, auth_headers, "tender", "tender.pdf")
    assert upload_resp.status_code == 201, upload_resp.text
    document_id = upload_resp.json()["data"]["id"]

    resp = client.post(
        f"/api/projects/{project_id}/files/{document_id}/process",
        headers=auth_headers,
    )

    assert resp.status_code == 400, resp.text
    assert "同时上传招标文件和投标文件" in resp.text


def test_running_bid_review_project_rejects_missing_document_backfill(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """历史半成品 uploading 项目即使缺 bid, 也不能通过普通上传接口补传。"""
    project_id = _create_bid_review_project(client, auth_headers)
    upload_resp = _upload_pdf(client, project_id, auth_headers, "tender", "tender.pdf")
    assert upload_resp.status_code == 201, upload_resp.text
    _set_project_status(project_id, "UPLOADING")

    resp = _upload_pdf(client, project_id, auth_headers, "bid", "bid.pdf")

    assert resp.status_code == 400, resp.text
    assert "重新开始上传" in resp.text


def test_reset_bid_review_upload_clears_half_uploaded_documents(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """半成品上传态通过显式重置回到 created, 清理已落库的单份文档。"""
    project_id = _create_bid_review_project(client, auth_headers)
    upload_resp = _upload_pdf(client, project_id, auth_headers, "tender", "tender.pdf")
    assert upload_resp.status_code == 201, upload_resp.text
    _set_project_status(project_id, "UPLOADING")
    assert _document_count(project_id) == 1

    resp = client.post(
        f"/api/projects/{project_id}/files/reset-upload",
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "created"
    assert _document_count(project_id) == 0


# ---------------------------------------------------------------------------
# 投标信封分卷(商务标+技术标)防御测试
# 形态: 信封由 1 份整本或 N 份分卷构成, 齐套/冲突/同卷替换按 bid_envelope 契约
# ---------------------------------------------------------------------------


def _create_collusion_project(client: TestClient, headers: dict) -> int:
    resp = client.post(
        "/api/projects",
        json={
            "name": f"envelope-collusion-{time.time_ns()}",
            "project_type": "collusion_check",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def test_split_envelope_upload_accepts_both_volumes(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """商务标+技术标各一份均可落库, 与招标文件共 3 条文档记录。"""
    project_id = _create_bid_review_project(client, auth_headers)
    tender_resp = _upload_pdf(client, project_id, auth_headers, "tender", "tender.pdf")
    assert tender_resp.status_code == 201, tender_resp.text

    commercial_resp = _upload_pdf(
        client, project_id, auth_headers, "bid", "commercial.pdf",
        bid_part="commercial",
    )
    technical_resp = _upload_pdf(
        client, project_id, auth_headers, "bid", "technical.pdf",
        bid_part="technical",
    )

    assert commercial_resp.status_code == 201, commercial_resp.text
    assert technical_resp.status_code == 201, technical_resp.text
    assert commercial_resp.json()["data"]["bid_part"] == "commercial"
    assert technical_resp.json()["data"]["bid_part"] == "technical"
    assert _document_count(project_id) == 3


def test_split_envelope_reupload_replaces_same_volume_only(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """重传技术标只替换技术标, 商务标保留, 总数不增。"""
    project_id = _create_bid_review_project(client, auth_headers)
    assert _upload_pdf(
        client, project_id, auth_headers, "tender", "tender.pdf"
    ).status_code == 201
    assert _upload_pdf(
        client, project_id, auth_headers, "bid", "commercial.pdf",
        bid_part="commercial",
    ).status_code == 201
    first_technical = _upload_pdf(
        client, project_id, auth_headers, "bid", "technical.pdf",
        bid_part="technical",
    )
    assert first_technical.status_code == 201

    second_technical = _upload_pdf(
        client, project_id, auth_headers, "bid", "technical_v2.pdf",
        bid_part="technical",
    )

    assert second_technical.status_code == 201, second_technical.text
    assert _document_count(project_id) == 3
    assert (
        second_technical.json()["data"]["id"]
        != first_technical.json()["data"]["id"]
    )


def test_split_envelope_missing_volume_cannot_start_processing(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """tender+商务标(缺技术标)不齐套, 不允许启动文档解析。"""
    project_id = _create_bid_review_project(client, auth_headers)
    tender_resp = _upload_pdf(client, project_id, auth_headers, "tender", "tender.pdf")
    assert tender_resp.status_code == 201
    commercial_resp = _upload_pdf(
        client, project_id, auth_headers, "bid", "commercial.pdf",
        bid_part="commercial",
    )
    assert commercial_resp.status_code == 201
    document_id = commercial_resp.json()["data"]["id"]

    resp = client.post(
        f"/api/projects/{project_id}/files/{document_id}/process",
        headers=auth_headers,
    )

    assert resp.status_code == 400, resp.text
    assert "同时上传招标文件和投标文件" in resp.text


def test_integral_then_volume_upload_is_mode_conflict(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """已传整本投标后再传分卷 → 模式冲突拒绝。"""
    project_id = _create_bid_review_project(client, auth_headers)
    integral_resp = _upload_pdf(client, project_id, auth_headers, "bid", "bid.pdf")
    assert integral_resp.status_code == 201
    assert integral_resp.json()["data"]["bid_part"] is None

    resp = _upload_pdf(
        client, project_id, auth_headers, "bid", "commercial.pdf",
        bid_part="commercial",
    )

    assert resp.status_code == 400, resp.text
    assert "模式冲突" in resp.text
    assert _document_count(project_id) == 1


def test_volume_then_integral_upload_is_mode_conflict(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """已传分卷后再传整本 → 模式冲突拒绝(同形态反向)。"""
    project_id = _create_bid_review_project(client, auth_headers)
    assert _upload_pdf(
        client, project_id, auth_headers, "bid", "commercial.pdf",
        bid_part="commercial",
    ).status_code == 201

    resp = _upload_pdf(client, project_id, auth_headers, "bid", "bid.pdf")

    assert resp.status_code == 400, resp.text
    assert "模式冲突" in resp.text


def test_collusion_project_rejects_bid_part(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """串标项目多 BID 语义与信封分卷无关, 传 bid_part 必须拒绝。"""
    project_id = _create_collusion_project(client, auth_headers)

    resp = _upload_pdf(
        client, project_id, auth_headers, "bid", "bid1.pdf",
        bid_part="commercial",
    )

    assert resp.status_code == 400, resp.text
    assert "仅投标审查项目" in resp.text


def test_unknown_bid_part_value_rejected(
    client: TestClient,
    auth_headers: dict,
) -> None:
    project_id = _create_bid_review_project(client, auth_headers)

    resp = _upload_pdf(
        client, project_id, auth_headers, "bid", "bid.pdf", bid_part="bogus"
    )

    assert resp.status_code == 400, resp.text
    assert "无效的投标分卷类型" in resp.text
