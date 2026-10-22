"""文件上传接口集成测试

覆盖: MIME 类型校验、PDF 魔数校验、文件大小限制、正常上传。
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def valid_pdf_bytes() -> bytes:
    """最小合法 PDF 文件（仅满足 %PDF 头部校验）"""
    return b"%PDF-1.4 minimal test content for upload validation"


@pytest.fixture()
def non_pdf_bytes() -> bytes:
    return b"This is a plain text file, not a PDF"


class TestUploadValidation:

    def _create_project(self, client: TestClient, headers: dict) -> int:
        resp = client.post(
            "/api/projects",
            json={"name": "上传测试项目"},
            headers=headers,
        )
        return resp.json()["data"]["id"]

    def test_upload_valid_pdf(
        self, client: TestClient, auth_headers, valid_pdf_bytes
    ):
        pid = self._create_project(client, auth_headers)
        resp = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "tender"},
            files={"file": ("tender.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["filename"] == "tender.pdf"

    def test_upload_wrong_extension(
        self, client: TestClient, auth_headers, valid_pdf_bytes
    ):
        pid = self._create_project(client, auth_headers)
        resp = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "tender"},
            files={"file": ("document.txt", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "PDF" in resp.json()["detail"]
        assert "docx" in resp.json()["detail"]

    def test_upload_wrong_mime_type(
        self, client: TestClient, auth_headers, valid_pdf_bytes
    ):
        pid = self._create_project(client, auth_headers)
        resp = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "tender"},
            files={"file": ("tender.pdf", valid_pdf_bytes, "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 415

    def test_upload_invalid_pdf_magic(
        self, client: TestClient, auth_headers, non_pdf_bytes
    ):
        pid = self._create_project(client, auth_headers)
        resp = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "tender"},
            files={"file": ("fake.pdf", non_pdf_bytes, "application/pdf")},
            headers=auth_headers,
        )
        assert resp.status_code == 415

    def test_upload_invalid_doc_type(
        self, client: TestClient, auth_headers, valid_pdf_bytes
    ):
        pid = self._create_project(client, auth_headers)
        resp = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "invalid_type"},
            files={"file": ("tender.pdf", valid_pdf_bytes, "application/pdf")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_upload_without_auth(
        self, client: TestClient, valid_pdf_bytes
    ):
        resp = client.post(
            "/api/projects/1/files",
            data={"doc_type": "tender"},
            files={"file": ("tender.pdf", valid_pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 401

    def test_upload_rejects_file_over_size_limit(
        self, client: TestClient, auth_headers, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("app.api.routers.files._MAX_UPLOAD_SIZE_MB", 1)
        pid = self._create_project(client, auth_headers)
        oversized = b"%PDF-1.4 " + b"x" * (1 * 1024 * 1024)
        resp = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "tender"},
            files={"file": ("tender.pdf", oversized, "application/pdf")},
            headers=auth_headers,
        )
        assert resp.status_code == 413
        assert "最大支持 1MB" in resp.json()["detail"]

    def test_upload_valid_docx(self, client: TestClient, auth_headers):
        pid = self._create_project(client, auth_headers)
        payload = b"PK\x03\x04" + b"minimal-docx-bytes"
        resp = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "tender"},
            files={
                "file": (
                    "tender.docx",
                    payload,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["filename"] == "tender.docx"
        assert resp.json()["data"].get("gate_result") in (None, {})

    def test_upload_valid_doc(self, client: TestClient, auth_headers):
        pid = self._create_project(client, auth_headers)
        payload = bytes.fromhex("D0CF11E0A1B11AE1") + b"minimal-doc-bytes"
        resp = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "bid"},
            files={"file": ("bid.doc", payload, "application/msword")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["filename"] == "bid.doc"

    def test_upload_docx_wrong_magic(self, client: TestClient, auth_headers):
        pid = self._create_project(client, auth_headers)
        resp = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "tender"},
            files={
                "file": (
                    "fake.docx",
                    b"this-is-not-a-zip",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=auth_headers,
        )
        assert resp.status_code == 415

    def test_upload_rejects_docm(self, client: TestClient, auth_headers):
        pid = self._create_project(client, auth_headers)
        resp = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "tender"},
            files={
                "file": (
                    "macro.docm",
                    b"PK\x03\x04macro",
                    "application/vnd.ms-word.document.macroEnabled.12",
                )
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_preview_unconverted_office_returns_409(
        self, client: TestClient, auth_headers
    ):
        pid = self._create_project(client, auth_headers)
        payload = b"PK\x03\x04" + b"minimal-docx-bytes"
        uploaded = client.post(
            f"/api/projects/{pid}/files",
            data={"doc_type": "tender"},
            files={
                "file": (
                    "tender.docx",
                    payload,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=auth_headers,
        )
        doc_id = uploaded.json()["data"]["id"]
        preview = client.get(
            f"/api/projects/{pid}/files/{doc_id}/preview",
            headers=auth_headers,
        )
        assert preview.status_code == 409
        assert "转换" in preview.json()["detail"]
