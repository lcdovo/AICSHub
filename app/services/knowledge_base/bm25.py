"""BM25 关键词检索评分器（自 knowledge_base.py 拆分迁移，检索核心组件）。"""
import re
from collections import Counter
from typing import List


class BM25Scorer:
    """BM25 关键词检索评分器"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._corpus: List[str] = []
        self._doc_count = 0
        self._avg_dl = 0.0
        self._df: dict = {}

    def build_index(self, documents: List[str]):
        self._corpus = documents
        self._doc_count = len(documents)
        if not documents:
            return

        total_len = 0
        self._df = {}

        for doc in documents:
            tokens = self._tokenize(doc)
            total_len += len(tokens)
            seen = set()
            for token in tokens:
                if token not in seen:
                    self._df[token] = self._df.get(token, 0) + 1
                    seen.add(token)

        self._avg_dl = total_len / max(self._doc_count, 1)

    def score(self, query: str, doc_idx: int) -> float:
        if doc_idx >= len(self._corpus) or self._doc_count == 0:
            return 0.0

        query_tokens = self._tokenize(query)
        doc_tokens = self._tokenize(self._corpus[doc_idx])

        if not doc_tokens or not query_tokens:
            return 0.0

        tf = Counter(doc_tokens)
        doc_len = len(doc_tokens)
        score = 0.0

        for qt in set(query_tokens):
            f = tf.get(qt, 0)
            if f == 0:
                continue
            df = self._df.get(qt, 0)
            idf = (self._doc_count - df + 0.5) / (df + 0.5) + 1
            idf = max(0, idf)
            tf_norm = (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * doc_len / max(self._avg_dl, 1)))
            score += idf * tf_norm

        return score

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        text = text.lower().strip()
        tokens = []
        english = re.findall(r'[a-zA-Z0-9]+', text)
        tokens.extend(english)
        chinese = re.findall(r'[\u4e00-\u9fff]', text)
        tokens.extend(chinese)
        for i in range(len(chinese) - 1):
            tokens.append(chinese[i] + chinese[i + 1])
        return tokens