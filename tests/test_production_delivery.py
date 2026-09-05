from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_delivery_artifacts_are_present_and_documented():
    assert (ROOT / ".github/workflows/ci.yml").exists()
    assert (ROOT / "requirements.lock").exists()
    assert (ROOT / "scripts/ci.ps1").exists()
    assert (ROOT / "scripts/demo.ps1").exists()
    assert (ROOT / "docs/API.md").exists()
    assert (ROOT / "docs/DEMO.md").exists()
    assert (ROOT / "docs/DEPLOYMENT.md").exists()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "问题" not in readme or "架构边界" in readme
    assert "Recall@K" in readme


def test_compose_has_isolated_profiles_healthchecks_and_persistent_volumes():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "profiles: [local]" in compose
    assert "profiles: [integration]" in compose
    assert "app-local" in compose and "app-integration" in compose
    assert compose.count("healthcheck:") >= 5
    assert "local_data:/app/data" in compose
    assert "AUTH_DEMO_ACCOUNTS_ENABLED: \"false\"" in compose


def test_production_image_drops_root_and_local_profile_adds_sqlite_driver():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER appuser" in dockerfile
    assert "INSTALL_LOCAL_DEPS" in dockerfile
    assert "requirements.lock" in dockerfile

