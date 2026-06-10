"""RAG服务模块：实现检索增强生成（Retrieval-Augmented Generation）完整链路

该模块负责整合向量检索与大语言模型生成，实现基于知识库的智能问答功能。
支持智能元数据过滤检索 + 多查询扩展（HyDE/子问题拆解/多角度扩展）+ BM25 关键词检索混合 + 多层重排序优化。
核心流程：用户提问 → 意图识别 + 多查询扩展 → 向量+BM25多路并行检索 → 加权RRF融合重排 → 元数据加权(无硬过滤时) → Cross-Encoder API精排 → MMR多样性 → 构建上下文 → 图谱一跳展开 → 调用LLM生成 → 返回带来源标注的回答 + 结构化追踪

子模块划分：
  expansion.py   — 意图过滤 + HyDE + 子问题拆解 + 多角度扩展
  fusion.py      — 去重 + RRF + 元数据加权 + MMR 多样性
  reranking.py   — 多层重排序管线编排
  context.py     — 图谱一跳展开 + 知识上下文构建
  generation.py  — LLM 答案生成 + 质量检测 + 后退提问
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Set, Tuple

from vector_store import VectorStoreService
from llm import LLMService
from prompts import prompt_manager
from .bm25_service import BM25Service
from reranker import DashScopeReranker

# 子模块导入
from .expansion import (
    detect_intent_filter, unified_expand_queries,
    generate_expanded_queries as _gen_expanded_queries,
)
from .fusion import (
    deduplicate_docs, reciprocal_rank_fusion,
    metadata_weight_adjust as _metadata_weight_adjust_fn,
    mmr_rerank as _mmr_rerank_fn, retrieve_flat as _retrieve_flat_fn,
)
from .reranking import apply_reranking
from .context import graph_context_expand as _graph_context_expand_fn, build_context, safe_float
from .generation import (
    simplify_to_keywords, is_low_quality_answer,
    generate_answer as _generate_answer_fn, step_back_answer,
)


class RAGService:
    """RAG服务主类

    负责将用户问题与知识库内容结合，调用大语言模型生成准确的回答。
    支持智能元数据过滤、多查询扩展召回、RAG-Fusion融合重排、
    BM25/向量混合检索、多层重排序优化，返回检索到的知识来源，
    便于溯源和验证回答准确性。

    子模块：expansion / fusion / reranking / context / generation
    """

    def __init__(self, vector_store: VectorStoreService, llm: LLMService, retrieval_k: int = 3, enable_multi_query: bool = True, num_expanded_queries: int = 3, enable_rrf: bool = False, rrf_k: int = 60, bm25_service: Optional[BM25Service] = None, enable_hybrid: bool = False, hybrid_vector_weight: float = 0.6, hybrid_bm25_weight: float = 0.4, enable_metadata_weight: bool = False, enable_mmr: bool = False, mmr_lambda: float = 0.7, reranker: Optional[DashScopeReranker] = None, enable_cross_encoder_rerank: bool = False, ecommerce_graph=None, enable_graph_expand: bool = True):
        """
        初始化RAG服务

        Args:
            vector_store: 向量存储服务实例
            llm: 大语言模型服务实例
            retrieval_k: 单路检索返回文档数量，默认为3
            enable_multi_query: 是否启用多查询扩展，默认True
            num_expanded_queries: 生成扩展查询的数量，默认3
            enable_rrf: 是否启用 RAG-Fusion 重排，默认False
            rrf_k: RRF 算法的平滑常数，默认60
            bm25_service: BM25 关键词检索服务实例
            enable_hybrid: 是否启用 BM25+向量混合检索，默认False
            hybrid_vector_weight: 向量检索权重，默认0.6
            hybrid_bm25_weight: BM25 检索权重，默认0.4
            enable_metadata_weight: 是否启用元数据权威加权重排，默认False
            enable_mmr: 是否启用 MMR 多样性重排，默认False
            mmr_lambda: MMR 相关性权重，默认0.7
            reranker: DashScope Cross-Encoder 重排序实例
            enable_cross_encoder_rerank: 是否启用 Cross-Encoder API 精排
            ecommerce_graph: 轻量电商关联图谱实例
            enable_graph_expand: 是否启用图谱一跳展开
        """
        self.vector_store = vector_store
        self.llm = llm
        self.retrieval_k = retrieval_k
        self.enable_multi_query = enable_multi_query
        self.num_expanded_queries = num_expanded_queries
        self.enable_rrf = enable_rrf
        self.rrf_k = rrf_k
        self.bm25_service = bm25_service
        self.enable_hybrid = enable_hybrid
        self.hybrid_vector_weight = hybrid_vector_weight
        self.hybrid_bm25_weight = hybrid_bm25_weight
        self.enable_metadata_weight = enable_metadata_weight
        self.enable_mmr = enable_mmr
        self.mmr_lambda = mmr_lambda
        self.reranker = reranker
        self.enable_cross_encoder_rerank = enable_cross_encoder_rerank
        self.ecommerce_graph = ecommerce_graph
        self.enable_graph_expand = enable_graph_expand and ecommerce_graph is not None

        # 电商问题类型关键词映射
        self.intent_keywords = {
            "price_sku": ["多少钱", "价格", "价格多少", "sku", "规格", "尺码", "颜色", "尺寸", "库存", "什么码", "码数", "码"],
            "faq": ["售后", "保修", "退换", "退货", "换货", "支持吗", "怎么使用", "如何", "FAQ", "问题", "无理由", "7天", "保修期", "怎么保养", "怎么洗", "洗涤", "机洗"],
            "review": ["评价", "怎么样", "用户说", "大家都说", "好不好用", "口碑"],
            "basic": ["基本信息", "品牌", "分类", "产地", "材质"],
            "marketing": ["卖点", "优势", "为什么好", "特色", "推荐"],
        }

        self.intent_filter_map = {
            "price_sku": None,
            "faq": {"type": "faq"},
            "review": {"type": "review"},
            "basic": {"type": "basic_info"},
            "marketing": {"type": "marketing"},
        }

    # ═══════════════════════════════════════════════════════════
    # 委托方法 — 子模块 thin wrappers
    # ═══════════════════════════════════════════════════════════

    # ── expansion.py ──

    def _detect_intent_filter(self, question: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        return detect_intent_filter(self.intent_keywords, self.intent_filter_map, question)

    def _generate_expanded_queries(self, original_question: str, intent: Optional[str] = None) -> List[str]:
        return _gen_expanded_queries(
            self.llm, self.num_expanded_queries,
            self.intent_keywords, self.intent_filter_map,
            original_question, intent
        )

    # ── fusion.py ──

    def _deduplicate_docs(self, doc_list: List) -> List:
        return deduplicate_docs(doc_list)

    def _reciprocal_rank_fusion(self, per_query_results: List[List], top_k: int, weights: Optional[List[float]] = None) -> Tuple[List, List[float]]:
        return reciprocal_rank_fusion(per_query_results, top_k, self.rrf_k, weights)

    def _retrieve_flat(self, all_queries: List[str], detected_filter: Optional[Dict[str, Any]]) -> List:
        return _retrieve_flat_fn(self.vector_store, self.retrieval_k, all_queries, detected_filter)

    def _metadata_weight_adjust(self, docs: List, scores: List[float]) -> List[float]:
        return _metadata_weight_adjust_fn(docs, scores)

    def _mmr_rerank(self, docs: List, scores: List[float], top_k: int) -> Tuple[List, List[float]]:
        return _mmr_rerank_fn(docs, scores, top_k, self.mmr_lambda)

    # ── reranking.py ──

    def _apply_reranking(self, question: str, docs: List, initial_scores: List[float], detected_filter: Optional[Dict[str, Any]] = None) -> Tuple[List, List[float], Dict[str, Any]]:
        return apply_reranking(self, question, docs, initial_scores, detected_filter)

    # ── context.py ──

    @staticmethod
    def _safe_float(value) -> float:
        return safe_float(value)

    def _graph_context_expand(self, retrieved_docs: list) -> str:
        return _graph_context_expand_fn(self.ecommerce_graph, self.enable_graph_expand, retrieved_docs)

    def _build_context(self, retrieved_docs: list) -> tuple:
        return build_context(retrieved_docs, graph=self.ecommerce_graph)

    # ── generation.py ──

    def _simplify_to_keywords(self, question: str) -> str:
        return simplify_to_keywords(question)

    def _is_low_quality_answer(self, answer: str) -> bool:
        return is_low_quality_answer(answer)

    def _generate_answer(self, question: str, knowledge_context: str, structured: bool = False) -> str:
        return _generate_answer_fn(self.llm, prompt_manager, question, knowledge_context, structured)

    def _single_search(self, query_text: str, md_filter: Optional[Dict[str, Any]], k_multiplier: int = 2) -> List:
        return self.vector_store.similarity_search(query_text, k=self.retrieval_k * k_multiplier, filter=md_filter)

    def _step_back_answer(self, question: str) -> dict:
        return step_back_answer(
            self.llm, self.vector_store, self.retrieval_k,
            prompt_manager, question, self._build_context,
        )

    def _get_fallback_prompt(self, question: str, knowledge_context: str) -> str:
        return _get_fallback_prompt_static(question, knowledge_context)

    # ═══════════════════════════════════════════════════════════
    # 核心查询入口
    # ═══════════════════════════════════════════════════════════

    def query(self, question: str, filter: Optional[Dict[str, Any]] = None, structured: bool = False, no_backoff: bool = False, skip_query_expansion: bool = False, skip_generation: bool = False) -> dict:
        """
        处理用户查询，执行完整的RAG流程
        支持智能元数据过滤 + 多查询扩展多路检索

        Args:
            question: 用户输入的问题
            filter: 可选，外部传入的强制元数据过滤条件，优先级高于自动意图识别
            structured: 是否输出结构化JSON（含推荐商品列表和语音播报文本）
            no_backoff: 跳过回退质量检查（Agent 工具调用时使用）
            skip_query_expansion: 跳过 LLM 查询扩展（Agent 工具调用时使用，减少 LLM 调用次数）
            skip_generation: 跳过重排序和 LLM 生成，直接返回检索上下文（工具调用时使用，工具自有 LLM 做最终组织）

        Returns:
            包含回答、知识来源、耗时统计、扩展查询列表的字典
        """
        start_time = time.time()
        trace_steps: List[Dict] = []

        detected_filter = filter
        detected_intent = None
        if detected_filter is None:
            t0 = time.time()
            detected_intent, detected_filter = self._detect_intent_filter(question)
            trace_steps.append({"step": "intent_filter", "duration_ms": round((time.time() - t0) * 1000)})

        query_expansion_time = 0.0
        all_queries = [question]
        if self.enable_multi_query and not skip_query_expansion:
            exp_start = time.time()
            all_queries = self._generate_expanded_queries(question, detected_intent)
            query_expansion_time = time.time() - exp_start
            trace_steps.append({"step": "query_expansion", "duration_ms": round(query_expansion_time * 1000), "count": len(all_queries)})

        search_start = time.time()
        rrf_fallback_triggered = False
        hybrid_enabled = self.enable_hybrid and self.bm25_service is not None
        if self.enable_rrf and (len(all_queries) > 1 or hybrid_enabled):
            try:
                per_query_results = []
                per_query_weights = []

                def _search_one_query(idx: int, q: str) -> Tuple[int, List, Optional[List]]:
                    vec_docs = self.vector_store.similarity_search(
                        q, k=self.retrieval_k, filter=detected_filter
                    )
                    bm25_docs = None
                    if hybrid_enabled:
                        bm25_docs = self.bm25_service.search(q, k=self.retrieval_k)
                    return idx, vec_docs, bm25_docs

                query_results: Dict[int, Tuple[List, Optional[List]]] = {}
                with ThreadPoolExecutor(max_workers=min(len(all_queries), 4)) as executor:
                    futures = {executor.submit(_search_one_query, i, q): i for i, q in enumerate(all_queries)}
                    for fut in as_completed(futures):
                        try:
                            idx, vec_docs, bm25_docs = fut.result()
                            query_results[idx] = (vec_docs, bm25_docs)
                        except Exception as e:
                            print(f"[检索并行] 查询变体检索失败: {e}")

                for i in range(len(all_queries)):
                    if i not in query_results:
                        continue
                    vec_docs, bm25_docs = query_results[i]
                    per_query_results.append(vec_docs)
                    per_query_weights.append(self.hybrid_vector_weight if hybrid_enabled else 1.0)
                    if hybrid_enabled and bm25_docs is not None:
                        per_query_results.append(bm25_docs)
                        per_query_weights.append(self.hybrid_bm25_weight)

                top_k = self.retrieval_k * len(all_queries) * (2 if hybrid_enabled else 1)
                retrieved_docs, rrf_scores = self._reciprocal_rank_fusion(
                    per_query_results, top_k, weights=per_query_weights
                )
            except Exception as e:
                print(f"[自动降级] RRF 执行失败，回退到普通合并去重模式: {str(e)}")
                rrf_fallback_triggered = True
                retrieved_docs = self._retrieve_flat(all_queries, detected_filter)
                rrf_scores = []
        else:
            retrieved_docs = self._retrieve_flat(all_queries, detected_filter)
            rrf_scores = []
        search_time = time.time() - search_start
        trace_steps.append({"step": "retrieval", "duration_ms": round(search_time * 1000), "docs": len(retrieved_docs), "hybrid": hybrid_enabled, "rrf_fallback": rrf_fallback_triggered})

        # 后置硬过滤（query 路径）
        retrieved_docs = _post_filter_by_pid(retrieved_docs, detected_filter)

        # ── 工具快速通道：跳过重排序 + LLM 生成，直接返回检索上下文 ──
        if skip_generation:
            knowledge_context, sources = self._build_context(retrieved_docs)
            graph_context = self._graph_context_expand(retrieved_docs)
            if graph_context:
                knowledge_context = graph_context + "\n\n" + knowledge_context
            total_time = time.time() - start_time
            print(f"[RAG] skip_generation 快速通道 | total={round(total_time*1000)}ms | docs={len(retrieved_docs)}")
            return {
                "answer": knowledge_context,
                "sources": sources,
                "search_time": round(search_time, 4),
                "query_expansion_time": round(query_expansion_time, 4),
                "generation_time": 0,
                "total_time": round(total_time, 4),
                "retrieved_knowledge_count": len(retrieved_docs),
                "applied_metadata_filter": detected_filter,
                "expanded_queries": all_queries,
                "rrf_enabled": self.enable_rrf,
                "rrf_fallback_triggered": rrf_fallback_triggered,
                "rrf_scores": [],
                "hybrid_enabled": self.enable_hybrid,
                "step_back_round": 0,
                "rerank_log": {"skip_generation": True},
                "trace": trace_steps,
            }

        rerank_start = time.time()
        retrieved_docs, _, rerank_log = self._apply_reranking(question, retrieved_docs, rrf_scores, detected_filter)
        trace_steps.append({"step": "reranking", "duration_ms": round((time.time() - rerank_start) * 1000), **{k: v for k, v in rerank_log.items() if isinstance(v, bool)}})

        knowledge_context, sources = self._build_context(retrieved_docs)

        graph_context = self._graph_context_expand(retrieved_docs)
        if graph_context:
            knowledge_context = graph_context + "\n\n" + knowledge_context

        generation_start = time.time()
        answer = self._generate_answer(question, knowledge_context, structured=structured)
        generation_time = time.time() - generation_start
        trace_steps.append({"step": "generation", "duration_ms": round(generation_time * 1000)})

        step_back_round = 0
        if not no_backoff and self._is_low_quality_answer(answer):
            step_back_result = self._step_back_answer(question)
            step_back_round = step_back_result["step_back_round"]
            if step_back_result["answer"]:
                answer = step_back_result["answer"]
                sources = step_back_result["sources"]

        total_time = time.time() - start_time

        result = {
            "answer": answer,
            "sources": sources,
            "search_time": round(search_time, 4),
            "query_expansion_time": round(query_expansion_time, 4),
            "generation_time": round(generation_time, 4),
            "total_time": round(total_time, 4),
            "retrieved_knowledge_count": len(retrieved_docs),
            "applied_metadata_filter": detected_filter,
            "expanded_queries": all_queries,
            "rrf_enabled": self.enable_rrf,
            "rrf_fallback_triggered": rrf_fallback_triggered,
            "rrf_scores": rrf_scores,
            "hybrid_enabled": self.enable_hybrid,
            "step_back_round": step_back_round,
            "rerank_log": rerank_log,
            "trace": trace_steps,
        }

        return result

    def query_structured(self, question: str, filter: Optional[Dict[str, Any]] = None) -> dict:
        result = self.query(question, filter=filter, structured=True)
        raw_answer = result.get("answer", "")
        structured_result = self._parse_structured_answer(raw_answer)
        structured_result["sources"] = result.get("sources", [])
        structured_result["search_time"] = result.get("search_time", 0)
        structured_result["query_expansion_time"] = result.get("query_expansion_time", 0)
        structured_result["total_time"] = result.get("total_time", 0)
        structured_result["retrieved_knowledge_count"] = result.get("retrieved_knowledge_count", 0)
        structured_result["applied_metadata_filter"] = result.get("applied_metadata_filter")
        return structured_result

    def _parse_structured_answer(self, raw_text: str) -> dict:
        """从 LLM 输出中解析结构化 JSON 回答"""
        if not raw_text:
            return {"answer_text": "", "recommendations": [], "voice_friendly": ""}
        try:
            text = raw_text.strip()
            json_start = text.find("{")
            if json_start == -1:
                return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}
            depth = 0
            json_end = -1
            for i in range(json_start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        json_end = i + 1
                        break
            if json_end == -1:
                return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}
            return json.loads(text[json_start:json_end])
        except (json.JSONDecodeError, ValueError):
            return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}

    def query_stream(
        self, question: str,
        filter: Optional[Dict[str, Any]] = None,
        chat_history: list = None,
        entity_context: str = "",
        summary_context: str = "",
        skip_query_expansion: bool = False,
        topic_switch_hint: str = "",
        user_question_override: str = "",
    ) -> dict:
        """
        流式版本的查询接口（支持多查询扩展 + BM25混合检索 + 记忆注入 + 上下文截断）

        skip_query_expansion: 跳过 LLM 查询扩展（简单查询快速通道）
        topic_switch_hint: 话题切换提示，非空时注入到 knowledge_context 前
        user_question_override: 覆盖 LLM prompt 中的 user_question（检索仍用 question）
        """
        import time as _time
        _t0 = _time.time()
        timing = {}  # 分段耗时诊断 (ms)

        detected_filter = filter
        detected_intent = None
        if detected_filter is None:
            _intent_t0 = _time.time()
            detected_intent, detected_filter = self._detect_intent_filter(question)
            timing["intent_filter_ms"] = round((_time.time() - _intent_t0) * 1000)
        print(f"[RAG] detected_filter={detected_filter} | question={question[:40]}", flush=True)

        all_queries = [question]
        _expand_start = _time.time()
        if self.enable_multi_query and not skip_query_expansion:
            all_queries = self._generate_expanded_queries(question, detected_intent)
        timing["query_expansion_ms"] = round((_time.time() - _expand_start) * 1000)
        timing["expanded_queries_n"] = len(all_queries)
        print(f"[RAG] 查询扩展: {len(all_queries)} queries ({_time.time() - _expand_start:.2f}s)", flush=True)

        rrf_fallback_triggered = False
        hybrid_enabled = self.enable_hybrid and self.bm25_service is not None
        _retrieval_start = _time.time()
        if self.enable_rrf and (len(all_queries) > 1 or hybrid_enabled):
            try:
                per_query_results = []
                per_query_weights = []

                def _search_one_query_stream(idx: int, q: str) -> Tuple[int, List, Optional[List]]:
                    vec_docs = self.vector_store.similarity_search(
                        q, k=self.retrieval_k, filter=detected_filter
                    )
                    bm25_docs = None
                    if hybrid_enabled:
                        bm25_docs = self.bm25_service.search(q, k=self.retrieval_k)
                    return idx, vec_docs, bm25_docs

                query_results: Dict[int, Tuple[List, Optional[List]]] = {}
                with ThreadPoolExecutor(max_workers=min(len(all_queries), 4)) as executor:
                    futures = {executor.submit(_search_one_query_stream, i, q): i for i, q in enumerate(all_queries)}
                    for fut in as_completed(futures):
                        try:
                            idx, vec_docs, bm25_docs = fut.result()
                            query_results[idx] = (vec_docs, bm25_docs)
                        except Exception as e:
                            print(f"[检索并行] 查询变体检索失败: {e}")

                for i in range(len(all_queries)):
                    if i not in query_results:
                        continue
                    vec_docs, bm25_docs = query_results[i]
                    per_query_results.append(vec_docs)
                    per_query_weights.append(self.hybrid_vector_weight if hybrid_enabled else 1.0)
                    if hybrid_enabled and bm25_docs is not None:
                        per_query_results.append(bm25_docs)
                        per_query_weights.append(self.hybrid_bm25_weight)

                top_k = self.retrieval_k * len(all_queries) * (2 if hybrid_enabled else 1)
                retrieved_docs, _ = self._reciprocal_rank_fusion(
                    per_query_results, top_k, weights=per_query_weights
                )
            except Exception as e:
                print(f"[自动降级] RRF 执行失败，回退到普通合并去重模式: {str(e)}")
                rrf_fallback_triggered = True
                retrieved_docs = self._retrieve_flat(all_queries, detected_filter)
        else:
            retrieved_docs = self._retrieve_flat(all_queries, detected_filter)
        timing["retrieval_ms"] = round((_time.time() - _retrieval_start) * 1000)
        timing["retrieved_docs_n"] = len(retrieved_docs)
        print(f"[RAG] 检索+融合: {len(retrieved_docs)} docs ({_time.time() - _expand_start:.2f}s)", flush=True)
        # 后置硬过滤：ChromaDB 的 $in/$nin 不可靠，内存层二次过滤
        retrieved_docs = _post_filter_by_pid(retrieved_docs, detected_filter)
        timing["post_filter_n"] = len(retrieved_docs)
        # 诊断：打印每条检索结果的标题/前60字
        for i, doc in enumerate(retrieved_docs[:6]):
            content = getattr(doc, 'page_content', str(doc))[:80].replace('\n', ' ')
            meta = getattr(doc, 'metadata', {}) or {}
            pid = meta.get('product_id', meta.get('source', ''))
            print(f"[RAG]   检索#{i+1}: pid={pid} | {content}", flush=True)

        _rerank_start = _time.time()
        retrieved_docs, _, _ = self._apply_reranking(question, retrieved_docs, [])
        timing["rerank_ms"] = round((_time.time() - _rerank_start) * 1000)
        print(f"[RAG] 重排序: {len(retrieved_docs)} docs ({_time.time() - _rerank_start:.2f}s)", flush=True)

        _build_start = _time.time()
        knowledge_context, sources = self._build_context(retrieved_docs)

        # ── 记忆注入 ──
        if summary_context and knowledge_context:
            knowledge_context = f"{summary_context}\n\n{knowledge_context}"
        if entity_context and knowledge_context:
            knowledge_context = f"【关联商品（来自上轮推荐）】\n{entity_context}\n\n{knowledge_context}"

        # ── 滑动窗口截断 ──
        KNOWLEDGE_LIMIT = 2800
        if len(knowledge_context) > KNOWLEDGE_LIMIT:
            cut = knowledge_context.rfind("【知识片段", 0, KNOWLEDGE_LIMIT)
            if cut == -1 or cut < KNOWLEDGE_LIMIT // 2:
                cut = knowledge_context.rfind("\n\n", 0, KNOWLEDGE_LIMIT)
            if cut == -1 or cut < KNOWLEDGE_LIMIT // 2:
                cut = KNOWLEDGE_LIMIT
            knowledge_context = knowledge_context[:cut] + "\n\n[上下文窗口已满，更早的信息已截断]"

        print(f"[RAG] knowledge_context 长度: {len(knowledge_context)}  |  前100字: {knowledge_context[:100].replace(chr(10), ' ')}", flush=True)

        _prompt_start = _time.time()
        if not knowledge_context:
            prompt = "根据当前知识库，我无法回答这个问题。知识库当前为空。"
        else:
            # 话题切换：清空历史 + 前置提示
            if topic_switch_hint and chat_history:
                chat_history = []
                knowledge_context = topic_switch_hint + "\n\n" + knowledge_context
            try:
                chat_context = ""
                if chat_history:
                    lines = []
                    for m in chat_history[-6:]:
                        role = "用户" if m.get("role") == "user" else "AI"
                        lines.append(f"{role}：{m.get('content', '')}")
                    chat_context = "\n".join(lines)
                prompt_question = user_question_override if user_question_override else question
                prompt = prompt_manager.render_prompt(
                    "rag_prompt_structured",
                    knowledge_context=knowledge_context,
                    user_question=prompt_question,
                    chat_history=chat_context,
                )
            except Exception:
                prompt = _get_fallback_prompt_static(prompt_question, knowledge_context)

        timing["build_context_ms"] = round((_time.time() - _build_start) * 1000)
        timing["prompt_ms"] = round((_time.time() - _prompt_start) * 1000)
        timing["prompt_chars"] = len(prompt)
        timing["pre_llm_total_ms"] = round((_time.time() - _t0) * 1000)

        print(f"[RAG] query_stream 准备完成: {_time.time() - _t0:.2f}s | prompt={len(prompt)}字符 (开始流式生成)", flush=True)
        return {
            "stream": self.llm.chat_stream(prompt, max_tokens=512),
            "sources": sources,
            "applied_metadata_filter": detected_filter,
            "expanded_queries": all_queries,
            "rrf_enabled": self.enable_rrf,
            "rrf_fallback_triggered": rrf_fallback_triggered,
            "hybrid_enabled": self.enable_hybrid,
            "_timing": timing,
        }


def _post_filter_by_pid(retrieved_docs, detected_filter):
    """后置硬过滤：ChromaDB $in/$nin 在某些版本不可靠，在内存层再过滤一遍

    同时处理 $nin 排除和 $in 限定。
    过滤后如果 docs 为空（如所有候选都被预算排除），返回空列表让 LLM 生成兜底回答。
    """
    if not detected_filter:
        return retrieved_docs

    pid_filter = detected_filter.get("product_id") if isinstance(detected_filter, dict) else None
    if not isinstance(pid_filter, dict):
        return retrieved_docs

    filtered = list(retrieved_docs)

    # $in 限定：只保留在该集合内的文档
    if "$in" in pid_filter:
        allowed_pids = set(pid_filter["$in"])
        before = len(filtered)
        filtered = [d for d in filtered
                    if (getattr(d, 'metadata', {}) or {}).get('product_id', '') in allowed_pids]
        if len(filtered) < before:
            print(f"[RAG] 后置过滤($in): {before} → {len(filtered)} docs (跨品类商品已排除)", flush=True)

    # $nin 排除：移除在该集合内的文档
    if "$nin" in pid_filter:
        exclude_pids = set(pid_filter["$nin"])
        before = len(filtered)
        filtered = [d for d in filtered
                    if (getattr(d, 'metadata', {}) or {}).get('product_id', '') not in exclude_pids]
        if len(filtered) < before:
            print(f"[RAG] 后置过滤($nin): {before} → {len(filtered)} docs", flush=True)

    return filtered


def _get_fallback_prompt_static(question: str, knowledge_context: str) -> str:
    """内置 fallback 提示词模板（静态函数，避免 self 依赖）"""
    return f"""你是一个专业的电商智能导购助手，负责帮助用户了解商品信息、解决购物相关问题。

请严格按照以下规则回答：
1. 必须基于提供的知识库内容进行回答，不要编造信息
2. 如果知识库中没有相关信息，请直接说明
3. 回答要简洁、准确、友好

## 知识库内容
{knowledge_context}

## 用户问题
{question}

请基于以上知识库内容回答用户问题："""
