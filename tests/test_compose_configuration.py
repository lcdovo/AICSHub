import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_compose_passes_every_settings_field_to_api_and_worker():
    config = ast.parse((ROOT / "app/config/config.py").read_text(encoding="utf-8-sig"))
    fields = {
        node.target.id
        for cls in config.body
        if isinstance(cls, ast.ClassDef) and cls.name == "Settings"
        for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
    start = compose.index("    environment: &app-environment")
    end = compose.index("\n    ports:", start)
    app_environment = compose[start:end]
    mapped = set(re.findall(r"^      ([A-Z][A-Z0-9_]+):", app_environment, re.MULTILINE))

    assert not fields - mapped


def test_compose_and_settings_share_the_default_rerank_candidate_budget():
    settings_source = (ROOT / "app/config/config.py").read_text(encoding="utf-8-sig")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
    example = (ROOT / ".env.example").read_text(encoding="utf-8-sig")

    assert 'RAG_RERANK_K: int = Field(default=20)' in settings_source
    assert 'RAG_RERANK_K: ${RAG_RERANK_K:-20}' in compose
    assert 'RAG_RERANK_K=20' in example


def test_example_keeps_conservative_judge_and_understanding_defaults():
    example = (ROOT / ".env.example").read_text(encoding="utf-8-sig")

    assert "LLM_JUDGE_CONTRACT_VERSION=v1" in example
    assert "LLM_JUDGE_V2_OUTPUT_PROTOCOL=function_calling" in example
    assert "REQUEST_UNDERSTANDING_MODE=rules" in example
    assert "REQUEST_UNDERSTANDING_PERCENTAGE=0" in example
    assert "RERANKER_LOCAL_BATCH_SIZE=4" in example
