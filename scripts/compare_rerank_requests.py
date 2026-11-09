"""Same-model full-pool score comparison for batch4 versus batch20."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import redis
    from app.config.config import settings
    from app.services.reranker_service import RerankerService
    from app.services.local_admission import RedisCoordinator, request_scope
    from app.services.local_inference import inference_scope
    client = redis.Redis(host="127.0.0.1", port=6380, socket_timeout=.3)
    namespace = "aicshub:test:rerank-requests:{"+uuid.uuid4().hex+"}"
    coord = RedisCoordinator(client, namespace)
    service = RerankerService(local_base="http://127.0.0.1:7997", local_model="BAAI/bge-reranker-large")
    rows = []
    try:
        with patch("app.services.local_admission.get_coordinator", lambda:coord):
            for count in (20, 24):
                for length in (1, 15):
                    documents = [{"id":str(i),"content":(f"合成资料{i}：" + (
                        "质量问题退货需保留包装，商家核实后承担运费。" if i%3==0 else
                        "非质量问题退货不符合上述条件，运费需要另外确认。" if i%3==1 else
                        "产品故障请先断电，随后检查电源和接口。")) * (length + i%4)} for i in range(count)]
                    comparison = []
                    for batch in (4, 20):
                        settings.RERANKER_LOCAL_BATCH_SIZE = batch
                        events = []; began = time.perf_counter()
                        with inference_scope(events=events), request_scope():
                            result = service.rerank_sync("质量问题退货，包装和运费有什么条件？", documents, count)
                        if not result or len(result) != count:
                            raise ValueError("incomplete_rerank")
                        comparison.append({"batch_size":batch, "duration_ms":(time.perf_counter()-began)*1000,
                            "dispatches":sum(e.get("dispatched",False) for e in events),
                            "scores":{r["id"]:r["rerank_score"] for r in result}, "ranking":[r["id"] for r in result]})
                    a,b = comparison
                    delta=max(abs(a["scores"][key]-b["scores"][key]) for key in a["scores"])
                    rows.append({"candidates":count, "length_factor":length, "same_scores":delta==0,
                        "same_ranking":a["ranking"]==b["ranking"], "max_score_delta":delta, "comparison":comparison})
    finally:
        service.close()
        keys = list(client.scan_iter(namespace+"*"))
        if keys:client.delete(*keys)
        client.close()
    report={"paid_calls":0, "passed":len(rows)==4 and all(r["same_scores"] and r["same_ranking"] for r in rows), "rows":rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"rows":[{k:v for k,v in r.items() if k!="comparison"} for r in rows]}))


if __name__=="__main__":main()
