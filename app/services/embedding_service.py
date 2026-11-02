"""
Embedding 服务 - 文本向量化
仅支持真实 Embedding API；未配置时 encode 抛出 RuntimeError，不再使用伪向量降级。
"""
import asyncio
import logging
import time
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
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

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

        return [r if r is not None else [0.0] * self.dim for r in results]

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

        for attempt in range(self.max_retries + 1):
            try:
                client = self._get_client()

                payload = {
                    "model": self.model,
                    "input": texts,
                    "encoding_format": "float",
                }

                response = client.post(
                    f"{self.api_base}/embeddings",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

                if "data" not in data:
                    raise ValueError(f"Unexpected API response: {data}")

                vectors = []
                for item in data["data"]:
                    vector = item.get("embedding", [])
                    if not vector or len(vector) == 0:
                        vector = [0.0] * self.dim
                    elif len(vector) != self.dim:
                        vector = self._pad_or_truncate(vector, self.dim)
                    vectors.append(vector)

                return vectors

            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"Embedding API error (attempt {attempt + 1}): HTTP {e.response.status_code}")
                if e.response.status_code >= 500:
                    time.sleep(1 * (attempt + 1))
                else:
                    break
            except Exception as e:
                last_error = e
                logger.warning(f"Embedding API error (attempt {attempt + 1}): {e}")
                time.sleep(1 * (attempt + 1))

        raise RuntimeError(f"Embedding API error: {last_error}")

    @staticmethod
    def _pad_or_truncate(vector: List[float], target_dim: int) -> List[float]:
        if len(vector) >= target_dim:
            return vector[:target_dim]
        result = vector.copy()
        result.extend([0.0] * (target_dim - len(result)))
        return result

    def close(self):
        if self._client and not self._client.is_closed:
            self._client.close()
            self._client = None


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
            )
            api_embedding.encode("test")
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
