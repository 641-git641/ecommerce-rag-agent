import math
import re
from collections import Counter
from typing import List, Optional

try:
    import jieba
    _JIEBA_AVAILABLE = True
except ImportError:
    jieba = None  # type: ignore
    _JIEBA_AVAILABLE = False


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
        """\u4e2d\u6587\u5206\u8bcd + \u82f1\u6587/\u6570\u5b57\u63d0\u53d6

        \u4f18\u5148\u4f7f\u7528 jieba \u5206\u8bcd\uff08\u8bcd\u7ea7\u522b\u5339\u914d\uff0cBM25 IDF/TF \u6709\u610f\u4e49\uff09\uff1b
        jieba \u4e0d\u53ef\u7528\u65f6\u56de\u9000\u5230\u9010\u5b57\u5206\u8bcd\uff08\u5b57\u7b26\u7ea7\u522b\uff0c\u4ecd\u6709\u57fa\u672c\u68c0\u7d22\u80fd\u529b\uff09\u3002
        """
        if _JIEBA_AVAILABLE:
            # jieba \u7cbe\u786e\u6a21\u5f0f\u5206\u8bcd\uff0c\u8fc7\u6ee4\u7a7a\u767d\u548c\u5355\u5b57\u7b26\u6807\u70b9
            tokens = [w.strip() for w in jieba.cut(text) if w.strip() and len(w.strip()) >= 1]
            # \u8865\u5145\uff1a\u63d0\u53d6\u82f1\u6587/\u6570\u5b57 token\uff08jieba \u53ef\u80fd\u628a "iPhone15" \u62c6\u5f00\uff09
            alpha_tokens = re.findall(r'[a-z0-9]+', text.lower())
            for t in alpha_tokens:
                if t not in tokens:
                    tokens.append(t)
            return tokens

        # \u56de\u9000\uff1a\u9010\u5b57\u5206\u8bcd\uff08\u4e2d\u6587\u5355\u5b57 + \u82f1\u6587/\u6570\u5b57 token\uff09
        tokens = []
        for ch in text:
            if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
                tokens.append(ch)

        alpha_tokens = re.findall(r'[a-z0-9]+', text.lower())
        tokens.extend(alpha_tokens)

        return tokens
