"""验证已部署的本地Ollama Embedding与Infinity Reranker；只使用合成输入和新Milvus collection。"""

from pathlib import Path
from types import SimpleNamespace
import hashlib, json, subprocess, uuid, sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.embedding_service import APIEmbedding
from app.services.knowledge_base.lifecycle import embedding_identity
from app.services.knowledge_base.vector_index import VersionedVectorIndex
from app.services.reranker_service import RerankerService


def main():
    root = Path(__file__).resolve().parents[1]
    runtime = root / "reports/m3-closeout/runtime"
    state = json.loads((runtime / "state.json").read_text(encoding="utf-8"))
    resources = {}
    for kind in ("mysql", "redis", "etcd", "minio", "milvus"):
        cid = state["containers"][kind]
        label = subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "aicshub.test.owner"}}',
                cid,
            ],
            text=True,
        ).strip()
        assert label == "m3-closeout"
        resources[kind] = cid
    with httpx.Client(timeout=5, trust_env=False) as client:
        tags = client.get("http://127.0.0.1:8012/api/tags")
        tags.raise_for_status()
        names = {item.get("name") for item in tags.json().get("models", [])}
        assert "qwen3-embedding:0.6b" in names
        local_models = client.get("http://127.0.0.1:7997/models")
        local_models.raise_for_status()
        assert any(
            item.get("id") == "BAAI/bge-reranker-large"
            for item in local_models.json().get("data", [])
        )
    texts = [
        "合成商品签收后七天内可申请退货，定制商品除外。",
        "合成会员每月可领取一次合成优惠券。",
    ]
    query = "合成定制商品能否无理由退货？"
    embedding = APIEmbedding(
        "http://127.0.0.1:8012/v1",
        "local-only",
        "qwen3-embedding:0.6b",
        dim=1024,
        max_retries=0,
        timeout=60,
    )
    local_index = json.loads(
        (runtime / "local-model-index.json").read_text(encoding="utf-8")
    )
    label = subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "aicshub.test.owner"}}',
            local_index["container"],
        ],
        text=True,
    ).strip()
    if label != "m3-closeout":
        raise RuntimeError("Refuse unowned local-model index resource")
    bindings = json.loads(
        subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Ports}}",
                local_index["container"],
            ],
            text=True,
        )
    )
    if not any(
        item["HostIp"] == "127.0.0.1"
        and int(item["HostPort"]) == int(local_index["port"])
        for item in bindings.get("19530/tcp", [])
    ):
        raise RuntimeError("Local-model index endpoint does not match owned resource")
    resources["local_model_milvus"] = local_index["container"]
    report = {"synthetic_only": True, "external_model_calls": 0, "resources": resources}
    try:
        vectors = embedding.encode_batch(texts)
        query_vector = embedding.encode_query(query)
        fingerprint, metadata = embedding_identity(
            SimpleNamespace(
                _embedding=embedding, _dim=embedding.dim, backend_name=embedding.name
            )
        )
        index = VersionedVectorIndex(
            "127.0.0.1",
            int(local_index["port"]),
            prefix="m3_local_verified_" + uuid.uuid4().hex[:10],
        )
        chunks = [
            {"id": "local-synthetic-" + str(i), "content": text}
            for i, text in enumerate(texts)
        ]
        index.write(
            "local-model-synthetic-v1", chunks, vectors, fingerprint, embedding.dim
        )
        hits = index.search(
            query_vector, fingerprint, [item["id"] for item in chunks], 2
        )
        other_revision = hashlib.sha256(
            (fingerprint + ":different-revision").encode()
        ).hexdigest()
        assert index.collection(other_revision) != index.collection(fingerprint)
        try:
            index.search(
                query_vector, other_revision, [item["id"] for item in chunks], 2
            )
        except RuntimeError:
            separate = True
        else:
            separate = False
        assert separate
        reranker = RerankerService(
            local_base="http://127.0.0.1:7997",
            local_model="BAAI/bge-reranker-large",
            timeout=30,
        )
        candidates = [
            {"id": item["id"], "content": item["content"], "title": "合成知识"}
            for item in chunks
        ]
        ranked = reranker.rerank_sync(query, candidates, 2)
        assert len(vectors) == 2 and all(
            len(vector) == 1024 for vector in [*vectors, query_vector]
        )
        assert hits and {item["id"] for item in hits} <= {item["id"] for item in chunks}
        assert ranked and len(ranked) == 2 and reranker.last_used_provider == "local"
        report.update(
            status="PASS",
            embedding_provider=metadata["provider"],
            embedding_model=metadata["model"],
            embedding_dimensions=1024,
            embedding_vectors=2,
            index_collection=index.collection(fingerprint),
            dense_hits=hits,
            same_dimension_revision_isolated=separate,
            reranker_model="BAAI/bge-reranker-large",
            reranker_provider=reranker.last_used_provider,
            reranked_ids=[item["id"] for item in ranked],
            rerank_scores=[item["rerank_score"] for item in ranked],
            chat_model_calls=0,
        )
    except Exception as exc:
        report.update(status="FAIL", error_type=type(exc).__name__)
    finally:
        embedding.close()
        target = runtime.parent / "model/local-model-integration.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in (
                    "status",
                    "embedding_dimensions",
                    "embedding_vectors",
                    "reranker_provider",
                    "same_dimension_revision_isolated",
                    "error_type",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
