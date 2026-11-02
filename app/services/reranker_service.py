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
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

PROVIDER_EXTERNAL = "external"
PROVIDER_LOCAL = "local"

# 单次重排的候选上限：控制外部/本地推理的时延与费用（取 4×top_k 检索池的上界）。
MAX_CANDIDATES = 24
# 单个候选输入重排器的内容长度上限，防止超长片段撑爆输入。
MAX_DOC_CHARS = 2000


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
            if isinstance(index, int) and isinstance(score, (int, float)):
                scored.append((index, float(score)))
        if not scored:
            return None
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored

    def _post(self, url: str, payload: Dict[str, Any]) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        client = httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=min(self.timeout, 3.0)),
            headers=headers,
        )
        try:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        finally:
            client.close()


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

    def rerank_sync(
        self, query: str, candidates: List[Dict[str, Any]], top_k: int
    ) -> Optional[List[Dict[str, Any]]]:
        """按 provider 链重排候选。返回重排后的 top_k（写 rerank_score/final_score）；
        全部失败返回 None，由调用方回退启发式。"""
        if not self.is_available() or not candidates:
            return None

        docs = [
            str(c.get("content") or "")[:MAX_DOC_CHARS]
            for c in candidates[:MAX_CANDIDATES]
        ]
        if not docs:
            return None

        for provider in self._providers:
            try:
                scored = provider.rerank(query, docs, top_n=len(docs))
            except Exception as exc:
                logger.warning("Reranker provider %s 调用失败，尝试下一级: %s", provider.name, exc)
                continue
            if not scored:
                logger.warning("Reranker provider %s 返回空结果，尝试下一级", provider.name)
                continue

            pool = candidates[:MAX_CANDIDATES]
            reranked = []
            for index, score in scored:
                if index >= len(pool):
                    continue
                doc = dict(pool[index])
                doc["rerank_score"] = round(score, 4)
                doc["final_score"] = doc["rerank_score"]
                reranked.append(doc)
            reranked = reranked[:top_k]
            if reranked:
                self.last_used_provider = provider.name
                return reranked
        return None


_reranker: Optional[RerankerService] = None


def get_reranker() -> Optional[RerankerService]:
    """模块级单例。未配置任何 reranker 配置时返回无 provider 的空服务。"""
    global _reranker
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
    _reranker = None