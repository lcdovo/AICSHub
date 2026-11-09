"""可复用的BM25倒排索引；分词保留中文、型号和标点边界。"""
from collections import Counter
import math
import re
from typing import List

BM25_VERSION = "bm25-log-idf-boundary-v2"


class BM25Scorer:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.build_index([])

    def build_index(self, documents: List[str]) -> None:
        self._corpus = list(documents)
        self._doc_count = len(documents)
        self._terms = [Counter(self._tokenize(doc)) for doc in documents]
        self._lengths = [sum(tf.values()) for tf in self._terms]
        self._avg_dl = sum(self._lengths) / max(self._doc_count, 1)
        self._postings: dict[str, list[int]] = {}
        for index, tf in enumerate(self._terms):
            for token in tf:
                self._postings.setdefault(token, []).append(index)
        self._df = {token: len(indices) for token, indices in self._postings.items()}

    def _score_terms(self, tokens: set[str], index: int) -> float:
        tf = self._terms[index]
        norm = self.k1 * (1 - self.b + self.b * self._lengths[index] / (self._avg_dl or 1))
        score = 0.0
        for token in tokens:
            frequency = tf.get(token, 0)
            if frequency:
                df = self._df[token]
                idf = math.log1p((self._doc_count - df + 0.5) / (df + 0.5))
                score += idf * frequency * (self.k1 + 1) / (frequency + norm)
        return score

    def score(self, query: str, doc_idx: int) -> float:
        if not 0 <= doc_idx < self._doc_count:
            return 0.0
        return self._score_terms(set(self._tokenize(query)), doc_idx)

    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        tokens = set(self._tokenize(query))
        candidates = {index for token in tokens for index in self._postings.get(token, [])}
        ranked = [(index, self._score_terms(tokens, index)) for index in candidates]
        return sorted(ranked, key=lambda value: (-value[1], value[0]))[:limit]

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        text = text.lower()
        tokens = []
        for match in re.finditer(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*|[\u4e00-\u9fff]+", text):
            word = match.group()
            if re.fullmatch(r"[\u4e00-\u9fff]+", word):
                tokens.extend(word)
                tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
            else:
                tokens.append(word)
                if re.search(r"[-_.]", word):
                    tokens.extend(re.split(r"[-_.]", word))
        return tokens
