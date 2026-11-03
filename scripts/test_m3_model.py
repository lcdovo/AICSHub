"""仅用户明确授权后运行：最多2个Embedding请求/合成短文本，无重试/聊天模型调用。"""

from pathlib import Path
import sys, json, hashlib, uuid, subprocess, os

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if "--authorized-two-requests" not in sys.argv:
        raise RuntimeError("explicit_user_authorization_required")
    from app.config.config import settings
    from app.services.embedding_service import APIEmbedding
    from app.services.knowledge_base.lifecycle import embedding_identity
    from app.services.knowledge_base.vector_index import VersionedVectorIndex
    from types import SimpleNamespace

    root = Path(__file__).resolve().parents[1]
    state = json.loads(
        (root / "reports/m3-closeout/runtime/state.json").read_text(encoding="utf-8")
    )
    assert state["prefix"].startswith("aicshub-m3-acceptance-")
    for cid in state["containers"].values():
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
    texts = [
        "合成商品A：签收后七天可申请退货，定制品除外。",
        "合成会员B：每月可领取一次合成优惠券。",
    ]
    query = "合成商品A的退货条件是什么？"
    assert all(
        len(text) <= 100
        for text in [*texts, settings.EMBEDDING_QUERY_INSTRUCTION + query]
    )
    backend = APIEmbedding(
        settings.EMBEDDING_API_BASE,
        settings.EMBEDDING_API_KEY,
        settings.EMBEDDING_MODEL,
        dim=settings.EMBEDDING_DIM,
        max_retries=0,
        batch_size=8,
        query_instruction=settings.EMBEDDING_QUERY_INSTRUCTION,
        timeout=20,
    )
    target = root / "reports/m3-closeout/model/embedding.json"
    if target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        if (
            "--remaining-one-request" not in sys.argv
            or previous.get("request_count") != 1
        ):
            raise RuntimeError(
                "authorized_budget_already_used_or_requires_explicit_remaining_mode"
            )
        first = target.with_name("first-failure.json")
        if not first.exists():
            first.write_text(
                json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    else:
        previous = {"request_count": 0, "input_count": 0}
    report = {
        **previous,
        "max_requests": 2,
        "synthetic_only": True,
        "chat_model_calls": 0,
        "status": "RUNNING",
    }
    target.parent.mkdir(parents=True, exist_ok=True)

    def reserve(count):
        if report["request_count"] >= 2:
            raise RuntimeError("authorized_budget_exhausted")
        report["request_count"] += 1
        report["input_count"] += count
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def inspect_response(response):
        if response.status_code >= 400:
            response.read()
            from app.services.llm_governance import SensitiveDataRedactor

            try:
                error = response.json().get("error", {})
                if isinstance(error, dict):
                    report["provider_error_code"] = SensitiveDataRedactor().redact(
                        str(error.get("code", ""))
                    )[:100]
                    report["provider_error_message"] = SensitiveDataRedactor().redact(
                        str(error.get("message", ""))
                    )[:250]
            except Exception:
                pass
            report["http_status"] = response.status_code

    backend._get_client().event_hooks = {"response": [inspect_response]}
    try:
        if previous["request_count"] == 1:
            combined = [*texts, settings.EMBEDDING_QUERY_INSTRUCTION + query]
            reserve(len(combined))
            all_vectors = backend.encode_batch(combined)
            vectors, q = all_vectors[:2], all_vectors[2]
        else:
            reserve(len(texts))
            vectors = backend.encode_batch(texts)
            reserve(1)
            q = backend.encode_query(query)
        service = SimpleNamespace(_embedding=backend, _dim=settings.EMBEDDING_DIM)
        fp, metadata = embedding_identity(service)
        index = VersionedVectorIndex(
            "127.0.0.1",
            int(state["ports"]["milvus"]),
            prefix="aicshub_m3_model_" + uuid.uuid4().hex[:12],
        )
        vid = str(uuid.uuid4())
        chunks = [
            {"id": "model-synthetic-" + str(i), "content": text}
            for i, text in enumerate(texts)
        ]
        index.write(vid, chunks, vectors, fp, settings.EMBEDDING_DIM)
        hits = index.search(q, fp, [c["id"] for c in chunks], 2)
        assert hits and {h["id"] for h in hits} <= {c["id"] for c in chunks}
        # 同维度另一revision落入另一个collection，旧空间不会被检索。
        other = hashlib.sha256((fp + ":different-revision").encode()).hexdigest()
        assert index.collection(other) != index.collection(fp)
        try:
            index.search(q, other, [c["id"] for c in chunks], 2)
        except RuntimeError:
            separated = True
        else:
            separated = False
        assert separated
        report.update(
            status="PASS",
            provider=metadata["provider"],
            model=metadata["model"],
            dim=settings.EMBEDDING_DIM,
            vector_count=len(vectors),
            fingerprint=fp,
            isolated_collection=index.collection(fp),
            same_dim_other_revision_separated=separated,
            returned_ids=[h["id"] for h in hits],
            usage="SDK未回传usage；未推测费用或tokens",
        )
    except Exception as exc:
        report.update(status="FAIL", error_type=type(exc).__name__)
    finally:
        backend.close()
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k in ("status", "request_count", "input_count", "dim", "error_type")
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
