"""
Embedding 服务 - 文本向量化
仅支持真实 Embedding API；未配置时 encode 抛出 RuntimeError，不再使用伪向量降级。
"""
import asyncio
import math
import logging
import time
import threading
from contextlib import contextmanager
from typing import List, Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)


class BaseEmbedding:
    """Embedding 基类"""

    dim: int = 1024
    name: str = "base"

    def encode(self, text: str) -> List[float]:
        raise NotImplementedError

    def encode_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.encode(t) for t in texts]

    def is_available(self) -> bool:
        return True


class APIEmbedding(BaseEmbedding):
    """
    API 驱动的 Embedding 后端
    支持 DashScope / 智谱 / OpenAI 兼容格式
    通过环境变量配置：EMBEDDING_API_BASE / EMBEDDING_API_KEY / EMBEDDING_MODEL
    """

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        dim: int = 1024,
        timeout: float = 30.0,
        max_retries: int = 2,
        query_instruction: str = "",
        batch_size: int = 20,
    ):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.name = f"api({model})"
        self.timeout = timeout
        self.max_retries = max_retries
        # 兼容 DashScope 等外接 API 的单次输入条数上限（实测 >20 返回 400）；
        # 本地 Ollama 无此限制，可通过 EMBEDDING_BATCH_SIZE 调大以降低往返次数。
        self.batch_size = batch_size
        # 查询侧指令前缀（Qwen3-Embedding 等指令感知模型）：encode_query 使用，
        # 文档入库侧 encode 不加，避免两侧向量空间不一致。
        self.query_instruction = query_instruction
        self._client: Optional[httpx.Client] = None
        self._client_lock = threading.RLock()
        self._client_users = 0
        self._closed = False
        self._text_cache: Dict[str, List[float]] = {}

    def encode_query(self, text: str) -> List[float]:
        """查询侧编码：自动拼查询指令前缀后走 encode（缓存 key 天然区分）。"""
        if self.query_instruction:
            text = f"{self.query_instruction}{text}"
        return self.encode(text)

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            self._get_client()
            return True
        except Exception:
            return False

    def _get_client(self) -> httpx.Client:
        from app.services.local_inference import is_local_endpoint
        with self._client_lock:
            if self._closed:
                raise RuntimeError("embedding_closed")
            if self._client is None or self._client.is_closed:
                self._client = httpx.Client(
                    timeout=httpx.Timeout(self.timeout),
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    trust_env=not is_local_endpoint(self.api_base),
                )
            return self._client

    @contextmanager
    def _borrow_client(self, deadline: float):
        if not self._client_lock.acquire(timeout=max(0, deadline-time.monotonic())):
            raise TimeoutError("embedding_deadline_waiting_for_client")
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError("embedding_deadline_before_client")
            client = self._get_client()
            self._client_users += 1
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

    def encode(self, text: str) -> List[float]:
        cache_key = f"{self.model}:{text}"
        if cache_key in self._text_cache:
            return self._text_cache[cache_key]

        vector = self._call_api([text])[0]
        self._text_cache[cache_key] = vector
        return vector

    def encode_batch(self, texts: List[str]) -> List[List[float]]:
        uncached = []
        uncached_indices = []
        results: List[Optional[List[float]]] = [None] * len(texts)

        for i, text in enumerate(texts):
            cache_key = f"{self.model}:{text}"
            if cache_key in self._text_cache:
                results[i] = self._text_cache[cache_key]
            else:
                uncached.append(text)
                uncached_indices.append(i)

        if uncached:
            batch_vectors = self._call_api(uncached)
            for idx, vec in zip(uncached_indices, batch_vectors):
                results[idx] = vec
                cache_key = f"{self.model}:{texts[idx]}"
                self._text_cache[cache_key] = vec

        if any(r is None for r in results):
            raise RuntimeError("embedding_response_missing")
        return results

    def _call_api(self, texts: List[str]) -> List[List[float]]:
        """调用 Embedding API；大批量按 self.batch_size 分批后合并结果。"""
        vectors: List[List[float]] = []
        for batch in self._iter_batches(texts):
            vectors.extend(self._call_api_once(batch))
        return vectors

    def _iter_batches(self, texts: List[str]) -> List[List[str]]:
        limit = max(1, self.batch_size)
        return [texts[i : i + limit] for i in range(0, len(texts), limit)]

    def _call_api_once(self, texts: List[str]) -> List[List[float]]:
        last_error = None
        from app.services.local_inference import CONTEXT, is_local_endpoint, LocalModelBusy, LocalModelDeadline
        from app.services.local_admission import run_http
        local = is_local_endpoint(self.api_base)
        deadline = min(time.monotonic() + min(self.timeout, 5.0),
                       CONTEXT.get().deadline or float("inf")) if local else None

        for attempt in range(self.max_retries + 1):
            try:
                payload = {
                    "model": self.model,
                    "input": texts,
                    "encoding_format": "float",
                    "dimensions": self.dim,
                }

                if local:
                    def post_local(remaining):
                        expires = min(deadline, time.monotonic()+remaining)
                        with self._borrow_client(expires) as client:
                            remaining_http = expires-time.monotonic()
                            if remaining_http <= 0:
                                raise LocalModelDeadline("embedding_deadline_before_http")
                            result = client.post(f"{self.api_base}/embeddings", json=payload,
                                                 timeout=min(self.timeout, remaining_http))
                            result.raise_for_status()
                            return result
                    response = run_http(
                        post_local, purpose="embedding", deadline=deadline)
                else:
                    with self._borrow_client(time.monotonic()+self.timeout) as client:
                        response = client.post(f"{self.api_base}/embeddings", json=payload)
                response.raise_for_status()
                data = response.json()

                if "data" not in data:
                    raise ValueError("embedding_response_invalid")

                items = data["data"]
                if not isinstance(items,list) or len(items)!=len(texts):
                    raise ValueError("embedding_count_mismatch")
                vectors = [None]*len(texts)
                for item in items:
                    index=item.get("index")
                    if type(index) is not int or not 0<=index<len(texts) or vectors[index] is not None:
                        raise ValueError("embedding_index_invalid")
                    vectors[index]=validate_vector(item.get("embedding"),self.dim)

                if local and time.monotonic() >= deadline:
                    raise LocalModelDeadline("embedding_deadline_after_decode")
                return vectors

            except (LocalModelBusy, LocalModelDeadline):
                raise
            except httpx.TimeoutException as exc:
                if local:
                    raise LocalModelDeadline("local_embedding_http_timeout") from None
                last_error = exc
                time.sleep(1 * (attempt + 1))
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"Embedding API error (attempt {attempt + 1}): HTTP {e.response.status_code}")
                if e.response.status_code >= 500:
                    time.sleep(1 * (attempt + 1))
                else:
                    break
            except Exception as e:
                last_error = e
                logger.warning("Embedding API contract/dependency error (attempt %s)",attempt+1)
                time.sleep(1 * (attempt + 1))

        raise RuntimeError("embedding_dependency_or_contract_error") from None


    def close(self):
        closing = None
        with self._client_lock:
            self._closed = True
            if self._client_users == 0:
                closing, self._client = self._client, None
        if closing is not None and not closing.is_closed:
            closing.close()


class EmbeddingService:
    """Embedding 服务管理器

    仅支持真实 Embedding API。未配置或初始化失败时，encode 会抛出
    RuntimeError，避免以无意义的伪向量参与 RAG 检索。
    """

    def __init__(self, dim: int = 1024):
        self._dim = dim
        self._embedding: Optional[BaseEmbedding] = None
        self._backend_name = "unconfigured"
        self._try_init_api_backend()

    async def async_encode(self, text: str) -> List[float]:
        """Run the backward-compatible sync encoder outside the event loop."""
        return await asyncio.to_thread(self.encode, text)

    async def async_encode_batch(self, texts: List[str]) -> List[List[float]]:
        """Async facade for batch embedding generation."""
        return await asyncio.to_thread(self.encode_batch, texts)

    def _try_init_api_backend(self):
        from app.config.config import settings

        api_base = settings.EMBEDDING_API_BASE
        api_key = settings.EMBEDDING_API_KEY
        model = settings.EMBEDDING_MODEL

        if not api_base or not api_key or not model:
            logger.warning("No Embedding API config found (EMBEDDING_API_BASE/KEY/MODEL), RAG 检索不可用")
            return

        try:
            dim = int(settings.EMBEDDING_DIM or self._dim)
            api_embedding = APIEmbedding(
                api_base=api_base,
                api_key=api_key,
                model=model,
                dim=dim,
                query_instruction=settings.EMBEDDING_QUERY_INSTRUCTION or "",
                batch_size=int(settings.EMBEDDING_BATCH_SIZE or 20),
                timeout=settings.EMBEDDING_TIMEOUT_SECONDS,
            )
            # 配置初始化不发付费探测；实际请求才验证服务与向量。
            pass
            self._embedding = api_embedding
            self._backend_name = f"api({model})"
            self._dim = dim
            logger.info(f"Embedding API backend ready: {model} (dim={dim})")
        except Exception as e:
            logger.error(f"Embedding API init failed: {e}; embedding 不可用")
            self._embedding = None
            self._backend_name = "unconfigured"

    def _ensure_backend(self):
        if self._embedding is None:
            raise RuntimeError(
                "Embedding 后端未配置：请设置 EMBEDDING_API_BASE/EMBEDDING_API_KEY/EMBEDDING_MODEL"
            )

    def get_embedding(self) -> Optional[BaseEmbedding]:
        return self._embedding

    @property
    def backend_name(self) -> str:
        return self._backend_name

    @property
    def dim(self) -> int:
        self._ensure_backend()
        return self._embedding.dim

    def encode(self, text: str) -> List[float]:
        self._ensure_backend()
        return self._embedding.encode(text)

    def encode_query(self, text: str) -> List[float]:
        """查询侧编码（带指令前缀，用于检索），文档入库侧请用 encode。"""
        self._ensure_backend()
        return self._embedding.encode_query(text)

    async def async_encode_query(self, text: str) -> List[float]:
        """Async facade for query-side embedding generation."""
        return await asyncio.to_thread(self.encode_query, text)

    def encode_batch(self, texts: List[str]) -> List[List[float]]:
        self._ensure_backend()
        return self._embedding.encode_batch(texts)

    def switch_backend(self, backend: str) -> bool:
        if backend == "api":
            self._try_init_api_backend()
            return self._backend_name.startswith("api")

        logger.warning(f"Unknown embedding backend: {backend}")
        return False

    def health_check(self) -> Dict[str, Any]:
        return {
            "backend": self._backend_name,
            "dim": self._dim if self._embedding is None else self._embedding.dim,
            "available": self._embedding is not None and self._embedding.is_available(),
        }


_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service(dim: int = 1024) -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService(dim=dim)
    return _embedding_service


def reset_embedding_service():
    global _embedding_service
    if _embedding_service and isinstance(_embedding_service._embedding, APIEmbedding):
        _embedding_service._embedding.close()
    _embedding_service = None


def validate_vector(vector,dim):
    if not isinstance(vector,list) or len(vector)!=dim or any(type(v) not in (int,float) or not math.isfinite(v) for v in vector):
        raise ValueError("embedding_dimension_or_value_invalid")
    if not any(v!=0 for v in vector): raise ValueError("embedding_zero_vector")
    return [float(v) for v in vector]
