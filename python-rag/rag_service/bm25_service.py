import math
import re
from collections import Counter
from typing import List, Optional


class BM25Service:

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents = []
        self.corpus: List[List[str]] = []
        self.avgdl: float = 0.0
        self.idf: dict = {}
        self.doc_freqs: Counter = Counter()
        self.N: int = 0

    def build_index(self, documents) -> None:
        self.documents = list(documents)
        self.corpus = [self._tokenize(doc.page_content) for doc in self.documents]
        self.N = len(self.corpus)

        if self.N == 0:
            return

        doc_lengths = [len(tokens) for tokens in self.corpus]
        self.avgdl = sum(doc_lengths) / self.N

        self.doc_freqs = Counter()
        for tokens in self.corpus:
            self.doc_freqs.update(set(tokens))

        self.idf = {}
        for term, freq in self.doc_freqs.items():
            self.idf[term] = math.log((self.N - freq + 0.5) / (freq + 0.5) + 1.0)

    def search(self, query: str, k: int = 3) -> list:
        if not self.corpus:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores = self._compute_scores(query_tokens)
        if not scores:
            return []

        scores.sort(key=lambda x: x[0], reverse=True)
        return [self.documents[idx] for _, idx in scores[:k]]

    def score_documents(self, query: str, docs: list) -> list:
        if not self.corpus or not docs:
            return [(doc, 0.0) for doc in docs]

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return [(doc, 0.0) for doc in docs]

        idx_to_score = {idx: score for score, idx in self._compute_scores(query_tokens)}

        return [(doc, idx_to_score.get(self._find_corpus_idx(doc), 0.0)) for doc in docs]

    def _compute_scores(self, query_tokens: list) -> list:
        scores = []
        for idx, doc_tokens in enumerate(self.corpus):
            doc_len = len(doc_tokens)
            score = 0.0
            term_counts = Counter(doc_tokens)

            for token in set(query_tokens):
                if token in self.idf:
                    tf = term_counts.get(token, 0)
                    if tf == 0:
                        continue
                    numerator = self.idf[token] * tf * (self.k1 + 1)
                    denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
                    score += numerator / denominator

            if score > 0:
                scores.append((score, idx))

        return scores

    def _find_corpus_idx(self, doc) -> int:
        doc_key = doc.page_content.strip()[:200]
        for i, d in enumerate(self.documents):
            if d.page_content.strip()[:200] == doc_key:
                return i
        return -1

    def list_indexed_documents(self) -> list:
        return self.documents

    def _tokenize(self, text: str) -> List[str]:
        tokens = []

        for ch in text:
            if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
                tokens.append(ch)

        alpha_tokens = re.findall(r'[a-z0-9]+', text.lower())
        tokens.extend(alpha_tokens)

        return tokens
