"""检索融合子模块：去重 + RRF + 元数据加权 + MMR 多样性

从 RAGService 中解耦出来，纯函数或接收显式参数。
"""

import math
import re
from collections import Counter as Ct
from typing import List, Dict, Optional, Set, Tuple, Any


def deduplicate_docs(doc_list: List) -> List:
    """对多路召回的文档进行去重（基于内容前200字符）"""
    seen_contents: Set[str] = set()
    unique_docs = []
    for doc in doc_list:
        content_key = doc.page_content.strip()[:200]
        if content_key not in seen_contents:
            seen_contents.add(content_key)
            unique_docs.append(doc)
    return unique_docs


def reciprocal_rank_fusion(
    per_query_results: List[List],
    top_k: int,
    rrf_k: int = 60,
    weights: Optional[List[float]] = None,
) -> Tuple[List, List[float]]:
    """RAG-Fusion: Reciprocal Rank Fusion 重排算法（支持加权）

    公式: score(doc) = sum(weight * 1 / (rank_i + rrf_k))
    """
    if weights is None:
        weights = [1.0] * len(per_query_results)

    content_key_to_doc = {}
    score_accum: Dict[str, float] = {}

    for query_docs, weight in zip(per_query_results, weights):
        for rank, doc in enumerate(query_docs, start=1):
            content_key = doc.page_content.strip()[:200]

            if content_key not in content_key_to_doc:
                content_key_to_doc[content_key] = doc

            rrf_score = weight / (rank + rrf_k)
            score_accum[content_key] = score_accum.get(content_key, 0.0) + rrf_score

    sorted_items = sorted(score_accum.items(), key=lambda x: x[1], reverse=True)
    result_docs = []
    result_scores = []
    for content_key, score in sorted_items[:top_k]:
        result_docs.append(content_key_to_doc[content_key])
        result_scores.append(round(score, 6))

    return result_docs, result_scores


def metadata_weight_adjust(docs: List, scores: List[float]) -> List[float]:
    """元数据权威加权重排"""
    type_boost = {
        "faq": 1.20, "official_faq": 1.20,
        "basic_info": 1.10, "sku_info": 1.05,
        "review": 0.90, "marketing": 0.85, "system_rule": 1.0,
    }
    adjusted = []
    for doc, score in zip(docs, scores):
        meta = getattr(doc, 'metadata', {}) or {}
        boost = type_boost.get(meta.get('type', ''), 1.0)
        adjusted.append(round(score * boost, 6))
    return adjusted


def mmr_rerank(
    docs: List, scores: List[float],
    top_k: int, mmr_lambda: float = 0.7,
) -> Tuple[List, List[float]]:
    """MMR 多样性重排"""
    if len(docs) <= 1:
        return docs[:top_k], scores[:top_k] if scores else []

    def _text_to_bow(text: str) -> Dict[str, int]:
        chars = [ch for ch in text if '\u4e00' <= ch <= '\u9fff']
        alphas = re.findall(r'[a-z0-9]+', text.lower())
        return Ct(chars + alphas)

    bow_list = [_text_to_bow(doc.page_content) for doc in docs]

    def _cos_sim(bow_a: dict, bow_b: dict) -> float:
        all_keys = set(bow_a) | set(bow_b)
        dot = sum(bow_a.get(k, 0) * bow_b.get(k, 0) for k in all_keys)
        na = math.sqrt(sum(v ** 2 for v in bow_a.values()))
        nb = math.sqrt(sum(v ** 2 for v in bow_b.values()))
        return dot / (na * nb + 1e-9)

    selected: List[int] = []
    remained = list(range(len(docs)))
    for _ in range(min(top_k, len(docs))):
        best_idx = -1
        best_score = -float('inf')
        for j in remained:
            sim_max = max((_cos_sim(bow_list[j], bow_list[s]) for s in selected), default=0.0)
            mmr = mmr_lambda * scores[j] - (1 - mmr_lambda) * sim_max
            if mmr > best_score:
                best_score = mmr
                best_idx = j
        selected.append(best_idx)
        remained.remove(best_idx)
    return [docs[i] for i in selected], [round(scores[i], 6) for i in selected]


def retrieve_flat(
    vector_store, retrieval_k: int,
    all_queries: List[str], detected_filter: Optional[Dict[str, Any]],
    k_multiplier: int = 1,
) -> List:
    """普通合并去重检索（RRF 降级/关闭时使用）

    使用 retrieval_k * 4 * k_multiplier 倍召回，确保品类/预算过滤后仍有产品多样性。
    k_multiplier 用于预算查询时扩大检索窗口（×2），给平价品更多曝光机会。
    """
    all_retrieved_docs = []
    for q in all_queries:
        docs = vector_store.similarity_search(q, k=retrieval_k * 4 * k_multiplier, filter=detected_filter)
        all_retrieved_docs.extend(docs)
    return deduplicate_docs(all_retrieved_docs)
