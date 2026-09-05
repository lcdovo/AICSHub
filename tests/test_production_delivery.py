from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_delivery_artifacts_are_present_and_documented():
    assert (ROOT / ".github/workflows/ci.yml").exists()
    assert (ROOT / "requirements.lock").exists()
    assert (ROOT / "scripts/ci.ps1").exists()
    assert (ROOT / "docs/API.md").exists()
    assert (ROOT / "docs/DEPLOYMENT.md").exists()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docker compose up" in readme


def test_compose_is_single_full_dependency_stack():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for service in ("mysql:", "redis:", "etcd:", "minio:", "milvus:", "app:"):
        assert service in compose
    assert "profiles:" not in compose
    assert "app-local" not in compose
    assert "app-integration" not in compose
    assert compose.count("healthcheck:") >= 6
    assert "mysql_data:/var/lib/mysql" in compose
    assert "AUTH_DEMO_ACCOUNTS_ENABLED: ${AUTH_DEMO_ACCOUNTS_ENABLED:-false}" in compose
    assert "EMBEDDING_API_KEY" in compose


def test_production_image_drops_root_and_only_installs_runtime_dependencies():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER appuser" in dockerfile
    assert "requirements.lock" in dockerfile
    assert "INSTALL_LOCAL_DEPS" not in dockerfile
