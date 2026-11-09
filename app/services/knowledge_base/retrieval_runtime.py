"""按SQL发布epoch复用不可变快照和词法索引；不以TTL代替发布检查。"""
from __future__ import annotations

import threading
from .bm25 import BM25Scorer
from .retrieval_text import retrieval_text


class RetrievalRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._epoch: int | None = None
        self._chunks: list[dict] = []
        self._bm25: BM25Scorer | None = None
        self.snapshot_builds = 0
        self.lexical_builds = 0

    def snapshot(self, repository) -> tuple[int, list[dict], bool]:
        with self._lock:
            # 每个请求都读SQL epoch；新发布、取消发布和删除后不能命中旧快照。
            epoch = repository.current_epoch()
            if self._epoch == epoch:
                return epoch, self._chunks, True
            epoch, chunks = repository.snapshot()
            self._chunks = [{**c, "_retrieval_text": retrieval_text(c)} for c in chunks]
            self._epoch, self._bm25 = epoch, None
            self.snapshot_builds += 1
            return epoch, self._chunks, False

    def lexical(self, epoch: int, chunks: list[dict]) -> tuple[BM25Scorer, bool]:
        with self._lock:
            if self._epoch == epoch and self._bm25 is not None:
                return self._bm25, True
            scorer = BM25Scorer()
            scorer.build_index([c["_retrieval_text"] for c in chunks])
            self.lexical_builds += 1
            # 旧请求不能覆盖并发新发布后建立的索引。
            if self._epoch == epoch:
                self._bm25 = scorer
            return scorer, False
