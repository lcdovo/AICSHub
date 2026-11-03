import io
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.security import require_admin, require_user
from app.models.models import User, BackgroundJob
from app.routers.knowledge import router
from tests.test_m3_lifecycle import kb, execute


@pytest.fixture
def client(kb, monkeypatch):
    monkeypatch.setattr("app.routers.knowledge.get_knowledge_base", lambda: kb)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: User(
        id=999, username="synthetic-admin", role="admin"
    )
    app.dependency_overrides[require_user] = lambda: User(
        id=101, username="synthetic-user", role="user"
    )
    with TestClient(app) as client:
        yield client


def test_api_draft_job_publish_failed_retry_and_old_reference(client, kb):
    result = client.post(
        "/api/v1/knowledge/documents/text",
        json={"title": "政策", "content": "ZXKEY policy"},
    ).json()["data"]
    assert result["status"] == "draft" and result["job_id"]
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{result['document_id']}/publish"
        ).status_code
        == 409
    )
    execute(kb, result)
    patched = client.patch(
        "/api/v1/knowledge/documents/" + result["document_id"],
        json={"content": "ZXKEY updated", "expected_version": 1},
    ).json()["data"]
    assert (
        client.patch(
            "/api/v1/knowledge/documents/" + result["document_id"],
            json={"content": "stale", "expected_version": 1},
        ).status_code
        == 409
    )
    kb._embedding_service.fail = True
    execute(kb, patched)
    listed = client.get("/api/v1/knowledge/documents").json()["data"]["documents"][0]
    assert listed["status"] == "failed" and listed["published_version"] == 1
    old = client.get(f"/api/v1/knowledge/documents/{result['document_id']}/versions/1")
    assert old.status_code == 200 and old.json()["data"]["content"] == "ZXKEY policy"


def test_batch_reports_each_failure_and_preserves_markdown_source(
    client, kb, monkeypatch
):
    from app.config.config import settings

    monkeypatch.setattr(settings, "MAX_UPLOAD_FILE_BYTES", 128)
    files = [
        ("files", ("unsupported.exe", b"bad", "application/octet-stream")),
        ("files", ("large.txt", b"x" * 129, "text/plain")),
        ("files", ("valid.md", "## 条款\n例外：不适用。".encode(), "text/markdown")),
    ]
    result = client.post("/api/v1/knowledge/documents/batch-upload", files=files)
    assert result.status_code == 200
    data = result.json()["data"]
    assert data["success"] == 1 and data["failed"] == 2
    good = next(r for r in data["results"] if r["success"])
    assert good["status"] == "draft" and good["job_id"]
    assert kb.document_detail(good["document_id"])["content"].startswith("## 条款")


def test_single_upload_limit_reads_bounded_and_leaves_no_temp_file(
    client, kb, monkeypatch, tmp_path
):
    from app.config.config import settings
    import tempfile

    monkeypatch.setattr(settings, "MAX_UPLOAD_FILE_BYTES", 32)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(upload_dir))
    response = client.post(
        "/api/v1/knowledge/documents/upload",
        files={"file": ("large.txt", b"x" * 33, "text/plain")},
    )
    assert response.status_code == 413 and list(upload_dir.iterdir()) == []
