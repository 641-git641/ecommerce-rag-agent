"""RAG服务模块：实现检索增强生成（Retrieval-Augmented Generation）完整链路

该模块负责整合向量检索与大语言模型生成，实现基于知识库的智能问答功能。
支持智能元数据过滤检索 + 多查询扩展（HyDE/子问题拆解/多角度扩展）+ BM25 关键词检索混合 + 多层重排序优化。
核心流程：用户提问 → 意图识别 + 多查询扩展 → 向量+BM25多路并行检索 → 加权RRF融合重排 → 元数据加权(无硬过滤时) → Cross-Encoder API精排 → MMR多样性 → 构建上下文 → 图谱一跳展开 → 调用LLM生成 → 返回带来源标注的回答 + 结构化追踪

子模块划分：
  intent_config.py — 意图关键词 + 过滤映射配置
  expansion.py    — 意图过滤 + 品类检测 + HyDE + 子问题拆解 + 多角度扩展
  post_filter.py  — 检索后置过滤管线（PID/品类/品牌/预算）+ 兜底
  fusion.py       — 去重 + RRF + 元数据加权 + MMR 多样性
  reranking.py    — 多层重排序管线编排
  context.py      — 图谱一跳展开 + 知识上下文构建
  generation.py   — LLM 答案生成 + 质量检测 + 后退提问
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
    detect_intent_and_category_filter, detect_exclusion_filter,
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
    generate_with_quality_fallback,
)
from .post_filter import (
    extract_category_hint,
    get_cheapest_in_category,
    strip_budget_terms,
    build_budget_aware_query,
    build_budget_hint,
    post_filter_by_pid,
    post_filter_by_category,
    post_filter_by_exclusion,
    post_filter_by_budget,
    get_fallback_prompt_static,
)


class RAGService:
    """RAG服务主类

    负责将用户问题与知识库内容结合，调用大语言模型生成准确的回答。
    支持智能元数据过滤、多查询扩展召回、RAG-Fusion融合重排、
    BM25/向量混合检索、多层重排序优化，返回检索到的知识来源，
    便于溯源和验证回答准确性。

    子模块：intent_config / expansion / post_filter / fusion / reranking / context / generation
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

    # ═══════════════════════════════════════════════════════════
    # 委托方法 — 子模块 thin wrappers
    # ═══════════════════════════════════════════════════════════

    # ── expansion.py ──

    def _detect_intent_filter(self, question: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """意图识别 + 品类检测 + $and 合并，一步完成（委托 expansion.py）"""
        return detect_intent_and_category_filter(question)

    def _generate_expanded_queries(self, original_question: str, category_hint: str = "") -> List[str]:
        return _gen_expanded_queries(
            self.llm, self.num_expanded_queries,
            original_question, category_hint,
        )

    # ── fusion.py ──

    def _deduplicate_docs(self, doc_list: List) -> List:
        return deduplicate_docs(doc_list)

    def _reciprocal_rank_fusion(self, per_query_results: List[List], top_k: int, weights: Optional[List[float]] = None) -> Tuple[List, List[float]]:
        return reciprocal_rank_fusion(per_query_results, top_k, self.rrf_k, weights)

    def _retrieve_flat(self, all_queries: List[str], detected_filter: Optional[Dict[str, Any]], k_multiplier: int = 1) -> List:
        return _retrieve_flat_fn(self.vector_store, self.retrieval_k, all_queries, detected_filter, k_multiplier)

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

    def _generate_answer(self, question: str, knowledge_context: str, structured: bool = False, temperature: float = 0.0) -> str:
        return _generate_answer_fn(self.llm, prompt_manager, question, knowledge_context, structured, temperature=temperature)

    def _step_back_answer(self, question: str, budget_hint: str = "", detected_filter=None) -> dict:
        return step_back_answer(
            self.llm, self.vector_store, self.retrieval_k,
            prompt_manager, question, self._build_context,
            budget_hint=budget_hint,
            detected_filter=detected_filter,
        )

    def _get_fallback_prompt(self, question: str, knowledge_context: str) -> str:
        return get_fallback_prompt_static(question, knowledge_context)

    # ═══════════════════════════════════════════════════════════
    # 共享检索管线（query / query_stream 共用）
    # ═══════════════════════════════════════════════════════════

    def _run_retrieval_pipeline(
        self,
        question: str,
        filter: Optional[Dict[str, Any]] = None,
        skip_query_expansion: bool = False,
        skip_generation: bool = False,
    ) -> dict:
        """共享检索管线：意图 → 扩展 → 搜索 → 过滤 → 重排序 → 上下文构建

        query() 和 query_stream() 的公共部分，消除 ~100 行重复代码。
        同时修复了 query_stream() 遗漏的 _budget_k_multiplier 和预算补全逻辑。

        Returns:
            dict with keys: retrieved_docs, knowledge_context, sources, all_queries,
            detected_filter, exclusion_filter, detected_intent, search_time,
            query_expansion_time, rrf_fallback_triggered, rrf_scores, hybrid_enabled,
            trace_steps, _pre_rerank_pool, _budget_hint, rerank_log, timing
        """
        timing = {}
        trace_steps: List[Dict] = []
        t0 = time.time()

        # ── 管线入口日志 ──
        _q_preview = question[:50] + ("…" if len(question) > 50 else "")
        _flags = []
        if skip_query_expansion: _flags.append("no_expand")
        if skip_generation: _flags.append("no_gen")
        _flag_str = f" flags={','.join(_flags)}" if _flags else ""
        _filter_str = f" filter={filter}" if filter else ""
        print(f"[Pipeline] ▼ retrieval_start | q=\"{_q_preview}\"({len(question)}ch){_flag_str}{_filter_str}", flush=True)

        # ── 第 1 步：意图识别 + 排除检测 ──
        detected_filter = filter
        detected_intent = None
        exclusion_filter = None
        if detected_filter is None:
            _t = time.time()
            detected_intent, detected_filter = self._detect_intent_filter(question)
            exclusion_filter = detect_exclusion_filter(question)
            timing["intent_filter_ms"] = round((time.time() - _t) * 1000)
            trace_steps.append({"step": "intent_filter", "duration_ms": timing["intent_filter_ms"]})

        # ── 第 2 步：预算感知检索窗口 ──
        _has_budget = bool(exclusion_filter and (exclusion_filter.get("budget_max") or exclusion_filter.get("budget_min")))
        _budget_k_multiplier = 2 if _has_budget else 1

        # ── 第 3 步：查询扩展 ──
        query_expansion_time = 0.0
        all_queries = [question]
        if self.enable_multi_query and not skip_query_expansion:
            exp_start = time.time()
            category_hint = extract_category_hint(detected_filter)
            all_queries = self._generate_expanded_queries(question, category_hint)
            query_expansion_time = time.time() - exp_start
            timing["query_expansion_ms"] = round(query_expansion_time * 1000)
            timing["expanded_queries_n"] = len(all_queries)
            trace_steps.append({"step": "query_expansion", "duration_ms": timing["query_expansion_ms"], "count": len(all_queries)})

        # ── 第 4 步：预算探测追加 ──
        if exclusion_filter and (exclusion_filter.get("budget_max") or exclusion_filter.get("budget_min")):
            _budget_free = strip_budget_terms(question)
            if _budget_free and _budget_free != question and _budget_free not in all_queries:
                all_queries.append(_budget_free)

            _budget_aware = build_budget_aware_query(question, detected_filter, exclusion_filter)
            if _budget_aware and _budget_aware not in all_queries:
                all_queries.append(_budget_aware)

        # ── 第 5 步：并行检索 + RRF 融合 ──
        search_start = time.time()
        rrf_fallback_triggered = False
        hybrid_enabled = self.enable_hybrid and self.bm25_service is not None
        if self.enable_rrf and (len(all_queries) > 1 or hybrid_enabled):
            try:
                per_query_results = []
                per_query_weights = []

                def _search_one_query(idx: int, q: str) -> Tuple[int, List, Optional[List]]:
                    vec_docs = self.vector_store.similarity_search(
                        q, k=self.retrieval_k * 4 * _budget_k_multiplier, filter=detected_filter
                    )
                    bm25_docs = None
                    if hybrid_enabled:
                        bm25_docs = self.bm25_service.search(q, k=self.retrieval_k * 4 * _budget_k_multiplier)
                    return idx, vec_docs, bm25_docs

                query_results: Dict[int, Tuple[List, Optional[List]]] = {}
                with ThreadPoolExecutor(max_workers=min(len(all_queries), 4)) as executor:
                    futures = {executor.submit(_search_one_query, i, q): i for i, q in enumerate(all_queries)}
                    for fut in as_completed(futures):
                        try:
                            idx, vec_docs, bm25_docs = fut.result(timeout=60)
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

                top_k = self.retrieval_k * len(all_queries) * (3 if hybrid_enabled else 1) * _budget_k_multiplier
                retrieved_docs, rrf_scores = self._reciprocal_rank_fusion(
                    per_query_results, top_k, weights=per_query_weights
                )
            except Exception as e:
                print(f"[自动降级] RRF 执行失败，回退到普通合并去重模式: {str(e)}")
                rrf_fallback_triggered = True
                retrieved_docs = self._retrieve_flat(all_queries, detected_filter, k_multiplier=_budget_k_multiplier)
                rrf_scores = []
        else:
            retrieved_docs = self._retrieve_flat(all_queries, detected_filter, k_multiplier=_budget_k_multiplier)
            rrf_scores = []
        search_time = time.time() - search_start
        timing["retrieval_ms"] = round(search_time * 1000)
        timing["retrieved_docs_n"] = len(retrieved_docs)
        trace_steps.append({"step": "retrieval", "duration_ms": round(search_time * 1000), "docs": len(retrieved_docs), "hybrid": hybrid_enabled, "rrf_fallback": rrf_fallback_triggered})

        # ── 第 6 步：后置过滤链 ──
        retrieved_docs = post_filter_by_pid(retrieved_docs, detected_filter)
        retrieved_docs = post_filter_by_category(retrieved_docs, detected_filter)
        retrieved_docs = post_filter_by_exclusion(retrieved_docs, exclusion_filter)
        timing["post_filter_n"] = len(retrieved_docs)

        # ── 第 7 步：预算补全（品类内最便宜 N 篇 basic_info 显式注入） ──
        if _has_budget and detected_filter and not skip_generation:
            _cheapest_docs = get_cheapest_in_category(self.vector_store, detected_filter, _budget_k_multiplier * 5)
            if _cheapest_docs:
                retrieved_docs = self._deduplicate_docs(retrieved_docs + _cheapest_docs)

        # ── 第 8 步：重排序 ──
        _pre_rerank_pool = list(retrieved_docs)
        rerank_log = {}

        if not skip_generation:
            rerank_start = time.time()
            retrieved_docs, _, rerank_log = self._apply_reranking(question, retrieved_docs, rrf_scores, detected_filter)
            timing["rerank_ms"] = round((time.time() - rerank_start) * 1000)
            trace_steps.append({"step": "reranking", "duration_ms": timing["rerank_ms"], **{k: v for k, v in rerank_log.items() if isinstance(v, bool)}})

        # ── 第 9 步：预算过滤 ──
        # 必须在 skip_generation 路径中也执行，否则 Compare/Combo 工具的
        # per-product 搜索丢失预算约束（如"对比200元以下跑鞋"的200元水位线）
        retrieved_docs = post_filter_by_budget(
            retrieved_docs, exclusion_filter, fallback_pool=_pre_rerank_pool,
            reranker=self.reranker if self.enable_cross_encoder_rerank else None,
            question=question,
        )

        # ── 第 10 步：上下文构建 ──
        knowledge_context, sources = self._build_context(retrieved_docs)

        graph_context = self._graph_context_expand(retrieved_docs)
        if graph_context:
            knowledge_context = graph_context + "\n\n" + knowledge_context

        _budget_hint = build_budget_hint(exclusion_filter)
        if _budget_hint:
            knowledge_context = _budget_hint + "\n" + knowledge_context

        timing["pre_llm_total_ms"] = round((time.time() - t0) * 1000)

        # ── 管线阶段耗时汇总（一行可 grep）──
        _stage_parts = []
        for _s in trace_steps:
            _name = _s.get("step", "?")
            _ms = _s.get("duration_ms", 0)
            _extra = ""
            if _name == "query_expansion" and "count" in _s:
                _extra = f"x{_s['count']}"
            elif _name == "retrieval" and "docs" in _s:
                _extra = f"→{_s['docs']}d"
            _stage_parts.append(f"{_name}={_ms}ms{_extra}")
        _has_budget_str = " budget" if exclusion_filter and (exclusion_filter.get("budget_max") or exclusion_filter.get("budget_min")) else ""
        print(f"[Pipeline] ▲ retrieval_done | {timing['pre_llm_total_ms']}ms | {' → '.join(_stage_parts)} | ctx={len(knowledge_context)}ch{_has_budget_str}", flush=True)

        return {
            "retrieved_docs": retrieved_docs,
            "knowledge_context": knowledge_context,
            "sources": sources,
            "all_queries": all_queries,
            "detected_filter": detected_filter,
            "exclusion_filter": exclusion_filter,
            "detected_intent": detected_intent,
            "search_time": search_time,
            "query_expansion_time": query_expansion_time,
            "rrf_fallback_triggered": rrf_fallback_triggered,
            "rrf_scores": rrf_scores,
            "hybrid_enabled": hybrid_enabled,
            "trace_steps": trace_steps,
            "_pre_rerank_pool": _pre_rerank_pool,
            "_budget_hint": _budget_hint,
            "rerank_log": rerank_log,
            "timing": timing,
        }

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

        # ── 请求入口 ──
        _q_preview = question[:60] + ("…" if len(question) > 60 else "")
        _flags = []
        if structured: _flags.append("structured")
        if no_backoff: _flags.append("no_backoff")
        if skip_query_expansion: _flags.append("no_expand")
        if skip_generation: _flags.append("no_gen")
        _flag_str = f" flags={','.join(_flags)}" if _flags else ""
        print(f"[Pipeline] ═══ query_start ═══ q=\"{_q_preview}\"({len(question)}ch){_flag_str}", flush=True)

        # ── 共享检索管线 ──
        pipe = self._run_retrieval_pipeline(
            question, filter=filter,
            skip_query_expansion=skip_query_expansion,
            skip_generation=skip_generation,
        )

        # ── 工具快速通道：跳过重排序 + LLM 生成，直接返回检索上下文 ──
        if skip_generation:
            total_time = time.time() - start_time
            print(f"[Pipeline] ═══ query_done(skip_gen) ═══ total={round(total_time*1000)}ms | retrieved={len(pipe['retrieved_docs'])}d ctx={len(pipe['knowledge_context'])}ch expanded={len(pipe['all_queries'])}q", flush=True)
            return {
                "answer": pipe["knowledge_context"],
                "sources": pipe["sources"],
                "search_time": round(pipe["search_time"], 4),
                "query_expansion_time": round(pipe["query_expansion_time"], 4),
                "generation_time": 0,
                "total_time": round(total_time, 4),
                "retrieved_knowledge_count": len(pipe["retrieved_docs"]),
                "applied_metadata_filter": pipe["detected_filter"],
                "expanded_queries": pipe["all_queries"],
                "rrf_enabled": self.enable_rrf,
                "rrf_fallback_triggered": pipe["rrf_fallback_triggered"],
                "rrf_scores": [],
                "hybrid_enabled": pipe["hybrid_enabled"],
                "step_back_round": 0,
                "rerank_log": {"skip_generation": True},
                "trace": pipe["trace_steps"],
            }

        # ── 生成 + 质量检查 + 步退回退（委托 generation.py） ──
        gen_result = generate_with_quality_fallback(
            self.llm, prompt_manager, self.vector_store, self.retrieval_k,
            self._build_context,
            question, pipe["knowledge_context"],
            structured=structured, no_backoff=no_backoff,
            budget_hint=pipe["_budget_hint"], sources=pipe["sources"],
            detected_filter=pipe["detected_filter"],
        )
        answer = gen_result["answer"]
        pipe["sources"] = gen_result["sources"]
        step_back_round = gen_result["step_back_round"]
        generation_time = gen_result["generation_time"]
        pipe["trace_steps"].append({"step": "generation", "duration_ms": round(generation_time * 1000)})

        total_time = time.time() - start_time

        # ── 请求完成汇总（一行可 grep 定位全链路瓶颈）──
        _sb_str = f" step_back={step_back_round}" if step_back_round > 0 else ""
        _stages = pipe.get("trace_steps", [])
        _stage_str = " → ".join(
            f"{s.get('step','?')}={s.get('duration_ms',0)}ms" for s in _stages
        )
        print(f"[Pipeline] ═══ query_done ═══ total={round(total_time*1000)}ms | stages: {_stage_str}{_sb_str} | answer={len(answer)}ch docs={len(pipe['retrieved_docs'])}", flush=True)

        return {
            "answer": answer,
            "sources": pipe["sources"],
            "search_time": round(pipe["search_time"], 4),
            "query_expansion_time": round(pipe["query_expansion_time"], 4),
            "generation_time": round(generation_time, 4),
            "total_time": round(total_time, 4),
            "retrieved_knowledge_count": len(pipe["retrieved_docs"]),
            "applied_metadata_filter": pipe["detected_filter"],
            "expanded_queries": pipe["all_queries"],
            "rrf_enabled": self.enable_rrf,
            "rrf_fallback_triggered": pipe["rrf_fallback_triggered"],
            "rrf_scores": pipe["rrf_scores"],
            "hybrid_enabled": pipe["hybrid_enabled"],
            "step_back_round": step_back_round,
            "rerank_log": pipe["rerank_log"],
            "trace": pipe["trace_steps"],
        }

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

        # ── 请求入口 ──
        _q_preview = question[:60] + ("…" if len(question) > 60 else "")
        _flags = []
        if skip_query_expansion: _flags.append("no_expand")
        if topic_switch_hint: _flags.append("topic_switch")
        if user_question_override: _flags.append("q_override")
        _flag_str = f" flags={','.join(_flags)}" if _flags else ""
        _mem_str = f" has_memory" if (entity_context or summary_context) else ""
        print(f"[Pipeline] ═══ stream_start ═══ q=\"{_q_preview}\"({len(question)}ch){_flag_str}{_mem_str}", flush=True)

        # ── 共享检索管线 ──
        pipe = self._run_retrieval_pipeline(
            question, filter=filter,
            skip_query_expansion=skip_query_expansion,
            skip_generation=False,
        )
        timing = pipe["timing"]
        retrieved_docs = pipe["retrieved_docs"]
        knowledge_context = pipe["knowledge_context"]
        sources = pipe["sources"]

        print(f"[RAG] detected_filter={pipe['detected_filter']} | question={question[:40]}", flush=True)
        print(f"[RAG] 查询扩展: {len(pipe['all_queries'])} queries", flush=True)
        print(f"[RAG] 检索+融合: {len(retrieved_docs)} docs", flush=True)

        # 诊断：打印每条检索结果
        for i, doc in enumerate(retrieved_docs[:6]):
            content = getattr(doc, 'page_content', str(doc))[:80].replace('\n', ' ')
            meta = getattr(doc, 'metadata', {}) or {}
            pid = meta.get('product_id', meta.get('source', ''))
            print(f"[RAG]   检索#{i+1}: pid={pid} | {content}", flush=True)

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

        # ── Prompt 构建 ──
        _prompt_start = _time.time()
        if not knowledge_context:
            prompt = "根据当前知识库，我无法回答这个问题。知识库当前为空。"
        else:
            # 话题切换：清空历史 + 前置提示
            if topic_switch_hint and chat_history:
                chat_history = []
                knowledge_context = topic_switch_hint + "\n\n" + knowledge_context
            prompt_question = user_question_override if user_question_override else question
            try:
                chat_context = ""
                if chat_history:
                    lines = []
                    for m in chat_history[-6:]:
                        role = "用户" if m.get("role") == "user" else "AI"
                        lines.append(f"{role}：{m.get('content', '')}")
                    chat_context = "\n".join(lines)
                prompt = prompt_manager.render_prompt(
                    "rag_prompt_structured",
                    knowledge_context=knowledge_context,
                    user_question=prompt_question,
                    chat_history=chat_context,
                )
            except Exception:
                prompt = get_fallback_prompt_static(prompt_question, knowledge_context)

        timing["build_context_ms"] = round((_time.time() - _prompt_start) * 1000)
        timing["prompt_ms"] = round((_time.time() - _prompt_start) * 1000)
        timing["prompt_chars"] = len(prompt)
        timing["pre_llm_total_ms"] = round((_time.time() - _t0) * 1000)

        _stages = pipe.get("trace_steps", [])
        _stage_str = " → ".join(
            f"{s.get('step','?')}={s.get('duration_ms',0)}ms" for s in _stages
        )
        print(f"[Pipeline] ═══ stream_ready ═══ pre_llm={timing['pre_llm_total_ms']}ms | stages: {_stage_str} | prompt={len(prompt)}ch ctx={len(knowledge_context)}ch", flush=True)
        return {
            "stream": self.llm.chat_stream(prompt, max_tokens=512),
            "sources": sources,
            "applied_metadata_filter": pipe["detected_filter"],
            "expanded_queries": pipe["all_queries"],
            "rrf_enabled": self.enable_rrf,
            "rrf_fallback_triggered": pipe["rrf_fallback_triggered"],
            "hybrid_enabled": pipe["hybrid_enabled"],
            "_timing": timing,
        }
