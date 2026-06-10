"""重排序子模块：多层重排序管线编排

从 RAGService 中解耦出来，通过 svc 参数透传访问所需属性。
"""

import json
from typing import List, Dict, Optional, Tuple, Any


def apply_reranking(svc, question: str, docs: List, initial_scores: List[float], detected_filter: Optional[Dict[str, Any]] = None) -> Tuple[List, List[float], Dict[str, Any]]:
    """多层重排序管线

    顺序：元数据加权(无硬过滤时) → Cross-Encoder API精排 → MMR多样性

    通过 svc 访问 RAGService 实例的全部配置属性。
    """
    rerank_log = {
        "metadata_weight_applied": False, "mmr_applied": False,
        "cross_encoder_rerank_applied": False,
    }
    if not docs:
        return docs, initial_scores, rerank_log

    scores = list(initial_scores) if initial_scores else [1.0] * len(docs)
    top_k = svc.retrieval_k

    # 元数据加权：只在无硬过滤时生效
    if svc.enable_metadata_weight and not detected_filter:
        try:
            scores = svc._metadata_weight_adjust(docs, scores)
            combined = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
            docs = [d for d, _ in combined]
            scores = [s for _, s in combined]
            rerank_log["metadata_weight_applied"] = True
        except Exception as e:
            print(f"[元数据加权降级] 失败，跳过: {str(e)}")

    # Cross-Encoder API 精排
    if svc.enable_cross_encoder_rerank and svc.reranker is not None and len(docs) > top_k:
        try:
            docs, scores = svc.reranker.rerank_documents(question, docs, top_k * 2)
            rerank_log["cross_encoder_rerank_applied"] = True
        except Exception as e:
            print(f"[Cross-Encoder重排降级] 失败，跳过: {str(e)}")

    # MMR 多样性重排
    if svc.enable_mmr and len(docs) > 1:
        try:
            docs, scores = svc._mmr_rerank(docs, scores, max(top_k, len(docs)))
            rerank_log["mmr_applied"] = True
        except Exception as e:
            print(f"[MMR降级] 多样性重排失败，跳过: {str(e)}")

    docs = docs[:top_k]
    scores = scores[:top_k] if len(scores) >= len(docs) else scores
    return docs, scores, rerank_log
