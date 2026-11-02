"""容器内细粒度耗时插桩：RAG 检索各阶段 + response_generation 各段。"""
import asyncio
import json
import sys
import time
from pathlib import Path

import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.services import knowledge_base as kb_mod
from app.services.embedding_service import APIEmbedding
from app.services.user_memory import UserMemoryService

# ---------- response_generation 内部打点 ----------
resp_timings: dict = {}

_orig_profile = UserMemoryService.get_user_profile
async def profile_spy(self, user_id, query="", session_id=""):
    t0 = time.perf_counter()
    r = await _orig_profile(self, user_id, query=query, session_id=session_id)
    resp_timings["user_memory_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return r
UserMemoryService.get_user_profile = profile_spy

_orig_gen = AgentGraph._generate_response_with_llm
async def gen_spy(self, state):
    t0 = time.perf_counter()
    r = await _orig_gen(self, state)
    resp_timings["llm_generate_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return r
AgentGraph._generate_response_with_llm = gen_spy

_orig_judge = None
from app.agent.validation_llm import LLMRAGJudge  # noqa: E402
if hasattr(LLMRAGJudge, "judge"):
    _orig_judge = LLMRAGJudge.judge
    async def judge_spy(self, **kwargs):
        t0 = time.perf_counter()
        r = await _orig_judge(self, **kwargs)
        resp_timings["independent_judge_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return r
    LLMRAGJudge.judge = judge_spy

# ---------- RAG 检索阶段插桩 ----------
timings = {"retrieval": []}
_orig_encode = kb_mod.KnowledgeBaseService._encode_query_embedding
_orig_vector = kb_mod.KnowledgeBaseService._retrieve_vector
_orig_bm25 = kb_mod.KnowledgeBaseService._retrieve_bm25
_orig_merge = kb_mod.KnowledgeBaseService._merge_results
_orig_rerank = kb_mod.KnowledgeBaseService._rerank
_orig_reorder_dispatch = kb_mod.KnowledgeBaseService._heuristic_rescore

def wrap(name):
    def deco(fn):
        def inner(*args, **kwargs):
            t0 = time.perf_counter()
            r = fn(*args, **kwargs)
            timings["retrieval"].append({"stage": name, "ms": round((time.perf_counter() - t0) * 1000, 1)})
            return r
        return inner
    return deco

kb_mod.KnowledgeBaseService._encode_query_embedding = wrap("1.embedding")(_orig_encode)
kb_mod.KnowledgeBaseService._retrieve_vector = wrap("2.vector_milvus")(_orig_vector)
kb_mod.KnowledgeBaseService._retrieve_bm25 = wrap("3.bm25")(_orig_bm25)
kb_mod.KnowledgeBaseService._merge_results = wrap("4.rrf_merge")(_orig_merge)
# 重排分发（模型 reranker：外部→本地，失败回退启发式）
kb_mod.KnowledgeBaseService._rerank = wrap("5.rerank")(_orig_rerank)
# 启发式重排仅在模型不可用/失败时被调用，作为回退路径单独计时
kb_mod.KnowledgeBaseService._heuristic_rescore = wrap("5.heuristic_rescore.fallback")(_orig_reorder_dispatch)

# embedding 调用内部（跑在 to_thread，无妨）
_orig_call = APIEmbedding._call_api
def emb_spy(self, texts):
    t0 = time.perf_counter()
    r = _orig_call(self, texts)
    timings["retrieval"].append({"stage": f"api_batch({len(texts)})", "ms": round((time.perf_counter() - t0) * 1000, 1)})
    return r
APIEmbedding._call_api = emb_spy


async def main():
    g = AgentGraph()
    queries = ["优惠券怎么领取", "智能手表充不进去电怎么办", "发货一般要几天"]
    for i, q in enumerate(queries):
        timings["retrieval"].clear()
        resp_timings.clear()
        state = AgentState(session_id=f"inst-{i}", user_id=1, user_message=q)
        t0 = time.perf_counter()
        r = await g.run(state)
        total = round((time.perf_counter() - t0) * 1000)
        # 节点级 trace
        node_ms = {t.get("node"): round(t.get("duration_ms") or 0) for t in r.trace}
        print(f"\n### query: {q} (total {total}ms)")
        for node in ("start", "intent_recognition", "route_decision", "query_rewrite", "rag_retrieval", "response_generation"):
            if node in node_ms:
                print(f"   node {node}: {node_ms[node]}ms")
        print("   retrieval stages:")
        for t in timings["retrieval"]:
            print(f"      {t['stage']}: {t['ms']}ms")
        print("   response_generation inner:")
        for k, v in resp_timings.items():
            print(f"      {k}: {v}ms")


asyncio.run(main())