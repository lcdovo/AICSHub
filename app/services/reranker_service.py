"""
Reranker 服务 - RAG 检索结果重排（模型 reranker）

重排优先级（用户确认的链路）：
1. 外部 rerank API：openai 风格 `POST {base}/rerank`（Cohere/Infinity 兼容），
   或 dashscope 风格 `POST {base}/services/rerank/text-rerank/generation`。
2. 本地 bge-reranker-large：Infinity Docker 部署（Cohere 兼容 `/rerank`），
   请求格式与 openai 风格一致，可用 RERANKER_LOCAL_MODEL 覆盖模型名。

约定：
- 未配置任何 provider 时 is_available() 为 False，不创建客户端、不发请求。
- 单个 provider 失败/超时记 warning 后继续尝试下一个；全部失败返回 None，
  由调用方（KnowledgeBaseService._rerank）回退启发式重排，不阻塞主链路。
- 所有调用为同步 httpx（_search_milvus/_compose_result 均在 to_thread 中执行，
  不会阻塞事件循环）；超时严格，无重试。
"""
import logging
import math
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextvars import ContextVar
from contextvars import copy_context
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

PROVIDER_EXTERNAL = "external"
PROVIDER_LOCAL = "local"

# 单次重排的候选上限：控制外部/本地推理的时延与费用（取 4×top_k 检索池的上界）。
MAX_CANDIDATES = 24
# 单个候选输入重排器的内容长度上限，防止超长片段撑爆输入。
MAX_DOC_CHARS = 2000
# 仅对已验证逐query/document独立打分的本地BGE模型分批，不改变外部供应商请求数。
LOCAL_BATCH_SIZE = 4
LOCAL_BATCH_WORKERS = 2
RERANK_ALGORITHM_VERSION = "local-bge-batch4-parallel2-v1"
_LOCAL_BATCH_POOL = ThreadPoolExecutor(max_workers=LOCAL_BATCH_WORKERS, thread_name_prefix="rag-rerank")
_LOCAL_BATCH_SLOTS = threading.BoundedSemaphore(LOCAL_BATCH_WORKERS)
_RERANK_DEADLINE: ContextVar[float | None] = ContextVar("rerank_deadline", default=None)


class _RerankProvider:
    """单个重排源：拼接请求、解析响应。失败时抛异常，由上层捕获降级。"""

    def __init__(
        self,
        name: str,
        base: str,
        api_key: str,
        model: str,
        style: str,
        timeout: float,
    ):
        self.name = name
        self.base = base.rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.style = style
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None
        self._client_lock = threading.Lock()
        self._client_users = 0
        self._closed = False

    @contextmanager
    def _borrow_client(self, deadline: float):
        """共享线程安全HTTP池；构建/等锁耗时计入同一截止时间。"""
        if not self._client_lock.acquire(timeout=max(0, deadline-time.monotonic())):
            raise TimeoutError("rerank_deadline_waiting_for_client")
        try:
            if self._closed:
                raise RuntimeError("reranker_closed")
            if time.monotonic() >= deadline:
                raise TimeoutError("rerank_deadline_before_client")
            if self._client is None:
                from app.services.local_inference import is_local_endpoint
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                self._client = httpx.Client(
                    timeout=httpx.Timeout(self.timeout), headers=headers,
                    trust_env=not (self.name == PROVIDER_LOCAL and is_local_endpoint(self.base)),
                )
            self._client_users += 1
            client = self._client
        finally:
            self._client_lock.release()
        try:
            yield client
        finally:
            closing = None
            with self._client_lock:
                self._client_users -= 1
                if self._closed and self._client_users == 0:
                    closing, self._client = self._client, None
            if closing is not None:
                closing.close()

    def close(self):
        """拒绝新调用；已发HTTP归还后再释放连接池，不中断在途请求。"""
        closing = None
        with self._client_lock:
            self._closed = True
            if self._client_users == 0:
                closing, self._client = self._client, None
        if closing is not None:
            closing.close()

    def rerank(
        self, query: str, documents: List[str], top_n: int
    ) -> Optional[List[Tuple[int, float]]]:
        """调用重排接口，返回 (index, score) 列表（按分降序）；无结果返回 None。"""
        if self.style == "dashscope":
            url = f"{self.base}/services/rerank/text-rerank/generation"
            payload = {
                "model": self.model,
                "input": {"query": query, "documents": documents},
                "parameters": {"top_n": top_n},
            }
            raw = self._post(url, payload)
            results = (raw or {}).get("output", {}).get("results") or []
        else:
            url = f"{self.base}/rerank"
            payload = {
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "return_documents": False,
            }
            raw = self._post(url, payload)
            # Cohere/Infinity: {"results":[...]}；TEI 原生返回裸列表 [{index,score}]。
            if isinstance(raw, list):
                results = raw
            else:
                results = (raw or {}).get("results") or []

        scored = []
        for item in results:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            if (type(index) is int and 0 <= index < len(documents)
                    and type(score) in (int, float) and math.isfinite(score)):
                scored.append((index, float(score)))
        if not scored:
            return None
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored

    def _post(self, url: str, payload: Dict[str, Any]) -> Any:
        from app.services.local_inference import is_local_endpoint
        from app.services.local_admission import run_http
        if self.name == PROVIDER_LOCAL and is_local_endpoint(self.base):
            return run_http(lambda remaining: self._post_uncoordinated(url, payload, remaining),
                                 purpose="rerank", deadline=_RERANK_DEADLINE.get())
        return self._post_uncoordinated(url, payload)

    def _post_uncoordinated(self, url: str, payload: Dict[str, Any], remaining=None) -> Any:
        began = time.monotonic()
        deadline = min(d for d in (began+self.timeout, _RERANK_DEADLINE.get(),
                                  began+remaining if remaining is not None else None) if d is not None)
        with self._borrow_client(deadline) as client:
            timeout = deadline-time.monotonic()
            if timeout <= 0:
                raise TimeoutError("rerank_deadline_before_http")
            response = client.post(url, json=payload,
                                   timeout=httpx.Timeout(timeout, connect=min(timeout, 3.0)))
            response.raise_for_status()
            result = response.json()
            if time.monotonic() >= deadline:
                raise TimeoutError("rerank_deadline_after_http")
            return result


class RerankerService:
    """RAG 结果重排器：按 外部 API → 本地 bge-reranker-large 顺序尝试。"""

    def __init__(
        self,
        api_base: str = "",
        api_key: str = "",
        model: str = "",
        style: str = "openai",
        timeout: float = 3.0,
        local_base: str = "",
        local_model: str = "BAAI/bge-reranker-large",
    ):
        self._providers: List[_RerankProvider] = []
        if api_base and model:
            self._providers.append(
                _RerankProvider(
                    name=PROVIDER_EXTERNAL,
                    base=api_base,
                    api_key=api_key,
                    model=model,
                    style=style,
                    timeout=timeout,
                )
            )
        if local_base:
            self._providers.append(
                _RerankProvider(
                    name=PROVIDER_LOCAL,
                    base=local_base,
                    api_key="",
                    model=local_model or "BAAI/bge-reranker-large",
                    style="openai",
                    # 本地推理放宽到 12s：CPU 上重排 10 条长文档实测约 4.3s，
                    # 叠加抖动/冷启动后仍有充足余量，避免真实候选被超时误判失败。
                    timeout=max(timeout, 12.0),
                )
            )
        self.last_used_provider: Optional[str] = None

    def is_available(self) -> bool:
        return bool(self._providers)

    def close(self):
        first_error = None
        for provider in self._providers:
            try:
                provider.close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def rerank_sync(
        self, query: str, candidates: List[Dict[str, Any]], top_k: int, *, deadline: float | None = None
    ) -> Optional[List[Dict[str, Any]]]:
        """按 provider 链重排候选。返回重排后的 top_k（写 rerank_score/final_score）；
        全部失败返回 None，由调用方回退启发式。"""
        if not self.is_available() or not candidates:
            return None
        # 原实现[:24]会默默遗漏尾部相关候选；超支持范围必须整次回退，不能声称完整重排。
        if len(candidates) > MAX_CANDIDATES:
            return None
        deadline = deadline if deadline is not None else time.monotonic() + sum(p.timeout for p in self._providers)

        docs = [
            str(c.get("content") or "")[:MAX_DOC_CHARS]
            for c in candidates[:MAX_CANDIDATES]
        ]
        if not docs:
            return None

        for provider in self._providers:
            if time.monotonic() >= deadline:
                return None
            token = _RERANK_DEADLINE.set(deadline)
            try:
                if provider.name == PROVIDER_LOCAL and provider.model == "BAAI/bge-reranker-large" and len(docs) > LOCAL_BATCH_SIZE:
                    scored = self._local_batches(provider, query, docs, deadline)
                else:
                    scored = provider.rerank(query, docs, top_n=len(docs))
            except Exception as exc:
                from app.services.local_inference import LocalModelBusy, LocalModelDeadline
                if isinstance(exc, (LocalModelBusy, LocalModelDeadline)):
                    raise
                logger.warning("Reranker provider %s 调用失败: %s", provider.name, type(exc).__name__)
                continue
            finally:
                _RERANK_DEADLINE.reset(token)
            if not self._complete_scores(scored, len(docs)) or time.monotonic() >= deadline:
                logger.warning("Reranker provider %s 回执不完整或已超过截止时间", provider.name)
                continue

            pool = candidates[:MAX_CANDIDATES]
            reranked = []
            for index, score in scored:
                if index >= len(pool):
                    continue
                doc = dict(pool[index])
                doc["rerank_score"] = round(score, 4)
                doc["rerank_provider"] = provider.name
                doc["final_score"] = doc["rerank_score"]
                reranked.append(doc)
            reranked = reranked[:top_k]
            if reranked:
                self.last_used_provider = provider.name
                return reranked
        return None

    @staticmethod
    def _complete_scores(scored, count: int) -> bool:
        return (isinstance(scored, list) and len(scored) == count
                and all(isinstance(item, tuple) and len(item) == 2 and type(item[0]) is int
                        and 0 <= item[0] < count and type(item[1]) in (int, float)
                        and math.isfinite(item[1]) for item in scored)
                and len({item[0] for item in scored}) == count)

    @staticmethod
    def _local_batches(provider, query: str, docs: list[str], deadline: float):
        """全池最多2个推理任务，无无界等待队列；超时后不再派发后续批次。"""
        pending = {}
        scored = []
        offset = 0

        def infer(start, texts):
            if time.monotonic() >= deadline:
                raise TimeoutError("rerank_deadline_before_dispatch")
            token = _RERANK_DEADLINE.set(deadline)
            try:
                result = provider.rerank(query, texts, top_n=len(texts))
            finally:
                _RERANK_DEADLINE.reset(token)
            if time.monotonic() >= deadline or not RerankerService._complete_scores(result, len(texts)):
                raise ValueError("late_or_invalid_rerank_batch")
            return [(index + start, value) for index, value in result]

        try:
            while offset < len(docs) or pending:
                while offset < len(docs) and len(pending) < LOCAL_BATCH_WORKERS:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("rerank_deadline")
                    slots = _LOCAL_BATCH_SLOTS
                    if not slots.acquire(blocking=False):
                        # 本轮已有任务先收割；没有任务则只在本轮剩余截止时间内等槽。
                        if pending:
                            break
                        if not slots.acquire(timeout=max(0, deadline - time.monotonic())):
                            raise RuntimeError("rerank_batch_pool_busy")
                    texts = docs[offset:offset + LOCAL_BATCH_SIZE]
                    try:
                        future = _LOCAL_BATCH_POOL.submit(copy_context().run, infer, offset, texts)
                    except BaseException:
                        slots.release()
                        raise
                    future.add_done_callback(lambda _, reserved=slots: reserved.release())
                    pending[future] = offset
                    offset += len(texts)
                finished, _ = wait(pending, timeout=max(0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
                if not finished:
                    raise TimeoutError("rerank_deadline")
                for future in finished:
                    pending.pop(future)
                    scored.extend(future.result())
            return sorted(scored, key=lambda item: (-item[1], item[0]))
        finally:
            for future in pending:
                future.cancel()

    def rerank_with_deadline(self, query, candidates, top_k, *, deadline):
        return self.rerank_sync(query, candidates, top_k, deadline=deadline)


_reranker: Optional[RerankerService] = None
_reranker_lock = threading.Lock()


def get_reranker() -> Optional[RerankerService]:
    """模块级单例。未配置任何 reranker 配置时返回无 provider 的空服务。"""
    global _reranker
    with _reranker_lock:
        if _reranker is None:
            from app.config.config import settings

            _reranker = RerankerService(
                api_base=settings.RERANKER_API_BASE,
                api_key=settings.RERANKER_API_KEY,
                model=settings.RERANKER_MODEL,
                style=settings.RERANKER_API_STYLE,
                timeout=settings.RERANKER_TIMEOUT_SECONDS,
                local_base=settings.RERANKER_LOCAL_BASE,
                local_model=settings.RERANKER_LOCAL_MODEL,
            )
        return _reranker


def reset_reranker() -> None:
    global _reranker
    with _reranker_lock:
        previous, _reranker = _reranker, None
    if previous is not None:
        previous.close()
