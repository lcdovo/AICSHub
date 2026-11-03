"""真实HTTP编码契约+供应商响应替身，零真实模型调用。"""

import httpx
import pytest
from app.services.embedding_service import APIEmbedding


def encoder(response, dim=3):
    embedding = APIEmbedding(
        "https://synthetic.invalid/v1",
        "synthetic-only",
        "model",
        dim,
        max_retries=0,
        batch_size=20,
    )
    calls = []

    def handle(request):
        import json

        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=response(body))

    embedding._client = httpx.Client(transport=httpx.MockTransport(handle))
    return embedding, calls


def test_out_of_order_indices_map_to_original_and_100_use_five_batches():
    def response(body):
        return {
            "data": [
                {"index": i, "embedding": [float(text) + 1, 1.0, 0.0]}
                for i, text in reversed(list(enumerate(body["input"])))
            ]
        }

    embedding, calls = encoder(response)
    try:
        vectors = embedding.encode_batch([str(i) for i in range(100)])
        assert [v[0] for v in vectors] == list(range(1, 101))
        assert len(calls) == 5 and all(len(c["input"]) == 20 for c in calls)
        assert all(c["dimensions"]==3 for c in calls)
    finally:
        embedding.close()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"index": 0, "embedding": [1, 0, 0]}],
        [{"index": 0, "embedding": [1, 0, 0]}, {"index": 0, "embedding": [0, 1, 0]}],
        [{"index": 0, "embedding": []}, {"index": 1, "embedding": [0, 1, 0]}],
        [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [0, 1, 0]}],
        [{"index": True, "embedding": [1, 0, 0]}, {"index": 1, "embedding": [0, 1, 0]}],
        [{"index": 0, "embedding": [0, 0, 0]}, {"index": 1, "embedding": [0, 1, 0]}],
    ],
)
def test_invalid_vectors_never_zero_pad_truncate_or_cache(rows, monkeypatch):
    monkeypatch.setattr("app.services.embedding_service.time.sleep", lambda _: None)
    embedding, _ = encoder(lambda body: {"data": rows})
    try:
        with pytest.raises(RuntimeError):
            embedding.encode_batch(["a", "b"])
        assert embedding._text_cache == {}
    finally:
        embedding.close()
