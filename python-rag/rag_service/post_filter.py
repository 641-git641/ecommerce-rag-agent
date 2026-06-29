"""后置过滤管线子模块：检索后的多阶段内存层过滤 + 预算补偿 + 兜底

从 RAGService / rag_service.py 中解耦，所有函数为纯模块级函数，不依赖 self。

管线顺序：
  1. _resolve_filter_field      — ChromaDB $and 格式透明解析
  2. _post_filter_by_pid         — product_id $in/$nin 内存层二次校验
  3. _post_filter_by_category    — 品类字段二次校验
  4. _post_filter_by_exclusion   — 品牌/产地排除
  5. _get_cheapest_in_category   — 预算补全（品类内最便宜 basic_info）
  6. _post_filter_by_budget      — 预算价格过滤 + 兜底 + Cross-Encoder 补排序

辅助函数：
  _extract_category_hint   — 品类→人类可读提示（用于查询扩展 prompt）
  _strip_budget_terms      — 去除查询中的预算短语
  _build_budget_aware_query— 构造"平价 性价比"查询变体
  _build_budget_hint       — 预算约束提示文本（注入知识上下文）
  _lookup_brand_origin     — 品牌名→产地标签子串匹配
  _get_fallback_prompt_static — 硬编码兜底 Prompt
"""

import re
from typing import List, Dict, Any, Optional

from langchain_core.documents import Document

from .context import safe_float
from .expansion import BRAND_ORIGIN


# ═══════════════════════════════════════════════════════════════
# ChromaDB 过滤格式工具
# ═══════════════════════════════════════════════════════════════

def resolve_filter_field(detected_filter: Optional[Dict[str, Any]], field_name: str) -> Any:
    """从 ChromaDB filter dict 中提取字段值，透明处理两种格式

    格式 1（简单）：{"sub_category": "洁面"} → 直接从顶层取
    格式 2（$and）： {"$and": [{"type": "faq"}, {"sub_category": "洁面"}]} → 遍历 $and 子句查找

    统一处理避免每个后置过滤函数重复解析 $and 结构。
    """
    if not detected_filter or not isinstance(detected_filter, dict):
        return None
    and_clauses = detected_filter.get("$and")
    if and_clauses and isinstance(and_clauses, list):
        for clause in and_clauses:
            if isinstance(clause, dict) and field_name in clause:
                return clause[field_name]
    return detected_filter.get(field_name)


# ═══════════════════════════════════════════════════════════════
# 查询扩展辅助
# ═══════════════════════════════════════════════════════════════

def extract_category_hint(detected_filter: Optional[Dict[str, Any]]) -> str:
    """从品类过滤条件中提取人类可读的约束提示，用于查询扩展 prompt

    例如 {"sub_category": "洁面"} → "请限定在「洁面」品类范围内，只生成洁面相关产品的查询"
    """
    if not detected_filter or not isinstance(detected_filter, dict):
        return ""

    sub_filter = resolve_filter_field(detected_filter, "sub_category")
    cat_filter = resolve_filter_field(detected_filter, "category")

    if sub_filter:
        return f"请限定在「{sub_filter}」品类范围内，只生成该品类相关产品的查询"
    if cat_filter:
        return f"请限定在「{cat_filter}」大类范围内，只生成该大类相关产品的查询"
    return ""


def strip_budget_terms(question: str) -> str:
    """去除查询中的预算相关短语，返回纯净品类查询

    用途：当用户查询含预算词（如"200元以下"）时，embedding 模型可能
    将高价品（如"安热沙 ¥298"）的语义排在低价品（如"巴黎欧莱雅隔离露 ¥170"）
    之前，导致预算过滤后无一命中。追加一条去预算词的查询可确保全品类覆盖。
    """
    stripped = question
    # "200元以下的"、"100块钱以下的"、"500以内的" 等
    stripped = re.sub(r'\d+\s*元?\s*以[下内]的?', '', stripped)
    stripped = re.sub(r'\d+\s*块钱?\s*以[下内]的?', '', stripped)
    stripped = re.sub(r'不超过\s*\d+\s*元?的?', '', stripped)
    stripped = re.sub(r'预算\s*\d+\s*元?的?', '', stripped)
    stripped = re.sub(r'\d+\s*元?\s*以[内下]', '', stripped)
    stripped = re.sub(r'\d+\s*块?\s*以[内下]', '', stripped)
    # 清理多余空格
    stripped = re.sub(r'\s+', '', stripped)
    # 如果去掉预算词后只剩空或单字（无效品类词），回退使用原问题
    if stripped and len(stripped) < 2:
        stripped = question
    return stripped


def build_budget_aware_query(
    question: str,
    detected_filter: Optional[Dict[str, Any]],
    exclusion_filter: Optional[Dict[str, Any]],
) -> Optional[str]:
    """构造预算感知查询变体：显式注入平价关键词，帮助语义搜索锚定低价品

    问题：embedding 模型可能将高价品（营销文本丰富）排在低价品之前，
    即使品类匹配，预算过滤后也可能只剩极少数产品。
    解决：追加一条显式包含"平价/性价比/预算内"的查询，拉高低价品语义得分。
    """
    if not exclusion_filter or not isinstance(exclusion_filter, dict):
        return None
    budget_max = exclusion_filter.get("budget_max")
    budget_min = exclusion_filter.get("budget_min")
    if not budget_max and not budget_min:
        return None

    # 提取品类名作为查询锚点
    cat_name = ""
    if detected_filter and isinstance(detected_filter, dict):
        cat_name = resolve_filter_field(detected_filter, "sub_category") or \
                   resolve_filter_field(detected_filter, "category") or ""
    if not cat_name:
        cat_name = strip_budget_terms(question)

    # 构造预算友好关键词
    budget_terms = ["平价", "性价比", "实惠", "学生党"]
    if budget_max:
        budget_terms.append(f"{int(budget_max)}元以内")

    parts = [cat_name] if cat_name else []
    parts.extend(budget_terms)
    return " ".join(parts)


def build_budget_hint(exclusion_filter: Optional[Dict[str, Any]]) -> str:
    """构建预算提示文本，注入到知识上下文最前面

    解决 LLM 看到混合价格文档时因 prompt 过度谨慎而输出"无法回答"的问题。
    明确告知预算约束和兜底策略，引导 LLM 推荐最接近预算的商品。
    """
    if not exclusion_filter or not isinstance(exclusion_filter, dict):
        return ""
    budget_max = exclusion_filter.get("budget_max")
    budget_min = exclusion_filter.get("budget_min")
    if not budget_max and not budget_min:
        return ""
    parts = []
    if budget_max is not None:
        parts.append(f"用户预算上限为{budget_max:g}元")
    if budget_min is not None:
        parts.append(f"用户预算下限为{budget_min:g}元")
    hint = "【预算约束】" + "，".join(parts) + "。"
    hint += "如果知识库中所有商品都超出预算，请推荐价格最低的1-2款，并说明'略超预算但这是最接近的选择'。"
    hint += "如果知识库中有预算内的商品，务必优先推荐预算内的。"
    return hint


# ═══════════════════════════════════════════════════════════════
# 后置过滤管线
# ═══════════════════════════════════════════════════════════════

def post_filter_by_pid(retrieved_docs, detected_filter):
    """后置硬过滤：ChromaDB $in/$nin 在某些版本不可靠，在内存层再过滤一遍

    同时处理 $nin 排除和 $in 限定。
    支持简单格式和 $and 包裹格式（通过 resolve_filter_field 统一解析）。
    """
    if not detected_filter:
        return retrieved_docs

    pid_filter = resolve_filter_field(detected_filter, "product_id")
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


def post_filter_by_category(retrieved_docs, detected_filter):
    """后置品类校验：丢弃 metadata.category/sub_category 与过滤条件不匹配的文档

    防御性设计：ChromaDB 的 metadata 过滤在某些版本中不可靠（与 post_filter_by_pid 同理），
    因此在 Python 内存层进行二次校验，确保不会把洁面乳混入防晒霜的推荐中。

    $and 格式解析由 resolve_filter_field 统一处理。
    """
    if not detected_filter or not isinstance(detected_filter, dict):
        return retrieved_docs

    cat_filter = resolve_filter_field(detected_filter, "category")
    sub_filter = resolve_filter_field(detected_filter, "sub_category")

    if not cat_filter and not sub_filter:
        return retrieved_docs

    filtered = []
    for doc in retrieved_docs:
        meta = getattr(doc, 'metadata', {}) or {}
        doc_cat = meta.get('category', '')
        doc_sub = meta.get('sub_category', '')

        if cat_filter and doc_cat != cat_filter:
            continue
        if sub_filter and doc_sub != sub_filter:
            continue
        filtered.append(doc)

    if len(filtered) < len(retrieved_docs):
        print(f"[后置品类过滤] {len(retrieved_docs)} -> {len(filtered)} docs "
              f"(category={cat_filter or '?'}, sub_category={sub_filter or '?'})", flush=True)

    return filtered


def _lookup_brand_origin(doc_brand_lower: str) -> str:
    """子串匹配查找品牌产地（日系/国货/欧美）"""
    for origin_brand, origin_label in BRAND_ORIGIN.items():
        if origin_brand in doc_brand_lower:
            return origin_label
    return ""


def post_filter_by_exclusion(retrieved_docs, exclusion_filter: Optional[Dict[str, Any]]):
    """后置排除过滤：根据品牌/产地排除规则丢弃不匹配的文档

    预算过滤已独立为 post_filter_by_budget()，在重排序之后单独执行。
    本函数仅处理品牌排除（"不要XX"）和产地偏好（日系/国货/欧美）。
    """
    if not exclusion_filter or not isinstance(exclusion_filter, dict):
        return retrieved_docs

    brand_excludes = exclusion_filter.get("brand_exclude", [])
    origin_exclude = exclusion_filter.get("origin_exclude")
    origin_prefer = exclusion_filter.get("origin_prefer")

    if not brand_excludes and not origin_exclude and not origin_prefer:
        return retrieved_docs

    filtered = []
    origin_prefer_matched = []  # 国货偏好专用
    for doc in retrieved_docs:
        meta = getattr(doc, 'metadata', {}) or {}

        # 品牌排除过滤
        if brand_excludes:
            doc_brand = (meta.get('brand', '') or '').lower()
            if any(excluded.lower() in doc_brand for excluded in brand_excludes):
                continue

        # 产地概念排除（日系/韩系/欧美）— 子串匹配品牌名
        if origin_exclude:
            doc_brand = (meta.get('brand', '') or '').lower()
            doc_origin = _lookup_brand_origin(doc_brand)
            if doc_origin and any(excl in doc_origin for excl in origin_exclude):
                continue

        # 产地偏好（国货）：优先保留，但不硬排除非国货
        if origin_prefer:
            doc_brand = (meta.get('brand', '') or '').lower()
            doc_origin = _lookup_brand_origin(doc_brand)
            if doc_origin == "国货":
                origin_prefer_matched.append(doc)

        filtered.append(doc)

    # 产地偏好：如果有匹配的国货品牌，只保留国货
    if origin_prefer and origin_prefer_matched:
        filtered = origin_prefer_matched

    if len(filtered) < len(retrieved_docs):
        reason_parts = []
        if brand_excludes:
            reason_parts.append(f"brand_exclude={brand_excludes}")
        if origin_exclude:
            reason_parts.append(f"origin_exclude={origin_exclude}")
        if origin_prefer:
            reason_parts.append(f"origin_prefer={origin_prefer}")
        print(f"[后置排除过滤] {len(retrieved_docs)} -> {len(filtered)} docs ({', '.join(reason_parts)})", flush=True)

    # 兜底：品牌/产地过滤全部清空时，回退保留原文档
    if not filtered and retrieved_docs:
        print(f"[后置排除过滤] 品牌/产地过滤后为空，回退保留原文档作为 LLM 兜底", flush=True)
        return retrieved_docs

    return filtered


# ═══════════════════════════════════════════════════════════════
# 预算补全 + 预算过滤
# ═══════════════════════════════════════════════════════════════

def get_cheapest_in_category(vector_store, detected_filter: Optional[Dict[str, Any]], n: int) -> List:
    """预算补全：从品类中按价格升序取最便宜的 N 篇文档（无视语义相关性）

    语义搜索天然偏好文本丰富的高价品 → 精华液 ¥720 的营销词藻比洁面乳 ¥52 更容易命中。
    此函数用 ChromaDB get(where=...) 直接扫品类全量文档，按 base_price 升序排序取 top-N，
    确保低价品一定有曝光机会。

    只取 basic_info 类型（商品基础信息），避免 FAQ/review 等噪音。
    """
    if not detected_filter or not isinstance(detected_filter, dict):
        return []
    if not hasattr(vector_store, 'db'):
        return []

    try:
        # 构建 ChromaDB where 条件
        where = {}
        cat_filter = resolve_filter_field(detected_filter, "category")
        sub_filter = resolve_filter_field(detected_filter, "sub_category")
        if sub_filter:
            where["sub_category"] = sub_filter
        elif cat_filter:
            where["category"] = cat_filter
        else:
            return []

        # 直接访问底层 ChromaDB collection 获取全量品类文档
        raw = vector_store.db.get(where=where, include=["metadatas", "documents"])
        if not raw or not raw.get("ids"):
            return []

        docs_with_price = []
        for i, doc_id in enumerate(raw["ids"]):
            meta = raw["metadatas"][i] if i < len(raw["metadatas"]) else {}
            doc_text = raw["documents"][i] if i < len(raw["documents"]) else ""
            price = safe_float(meta.get("base_price", 999999))
            # 只取 basic_info（商品基础信息最有用），跳过 FAQ/review
            if str(meta.get("type", "")).lower() != "basic_info":
                continue
            doc = Document(page_content=doc_text, metadata=meta)
            docs_with_price.append((price, doc))

        docs_with_price.sort(key=lambda x: x[0])
        cheapest = [d for _, d in docs_with_price[:n]]
        if cheapest:
            prices = [str(int(safe_float((getattr(d, 'metadata', {}) or {}).get('base_price', 0)))) for d in cheapest]
            print(f"[预算补全] 注入品类最便宜 {len(cheapest)} 篇 basic_info: {prices}", flush=True)
        return cheapest
    except Exception as e:
        print(f"[预算补全] 失败: {e}", flush=True)
        return []


# 预算兜底：内容类型优先级（basic_info 最可信，faq 最弱）
_TYPE_PRIORITY = {"basic_info": 5, "marketing": 4, "sku_info": 3, "review": 2, "faq_q": 1, "faq": 0}


def post_filter_by_budget(
    retrieved_docs,
    exclusion_filter: Optional[Dict[str, Any]],
    fallback_pool=None,
    reranker=None,
    question: str = "",
):
    """后置预算过滤：在重排序之后执行，按预算约束保留文档

    关键设计决策：预算过滤必须在 Cross-Encoder 重排序之后执行。
    原因：重排序器是语义模型，不感知价格数字；若在重排序前过滤掉超预算文档后触发兜底
    （回退全部文档），重排序器会按语义相关性排序，淘汰预算友好但文本质量较普通的文档，
    导致最终 top-k 全是超预算产品，LLM 只能输出"无法回答"。

    兜底策略：全部超出预算时，优先从 fallback_pool（重排序前的大池）按价格升序取
    最便宜的 N 篇；若传入 reranker + question，对兜底结果补充一次 Cross-Encoder 重排序，
    避免未重排序文档直接进入 LLM 上下文。
    """
    if not exclusion_filter or not isinstance(exclusion_filter, dict):
        return retrieved_docs

    budget_max = exclusion_filter.get("budget_max")
    budget_min = exclusion_filter.get("budget_min")

    if not budget_max and not budget_min:
        return retrieved_docs

    filtered = []
    for doc in retrieved_docs:
        meta = getattr(doc, 'metadata', {}) or {}
        price = safe_float(meta.get('base_price', 0))

        if budget_max is not None and price > budget_max:
            continue
        if budget_min is not None and price < budget_min:
            continue
        filtered.append(doc)

    reason_parts = []
    if budget_max is not None:
        reason_parts.append(f"budget_max={budget_max:.0f}")
    if budget_min is not None:
        reason_parts.append(f"budget_min={budget_min:.0f}")
    print(f"[后置预算过滤] {len(retrieved_docs)} -> {len(filtered)} docs ({', '.join(reason_parts)})", flush=True)

    # 兜底：全部超出预算时，优先从大池取最便宜的 N 篇
    # 按内容类型优先级排序：basic_info > marketing > sku_info > review > faq_q > faq
    if not filtered:
        pool = fallback_pool if fallback_pool else retrieved_docs
        if pool:
            def _fallback_sort_key(d):
                meta = getattr(d, 'metadata', {}) or {}
                price = safe_float(meta.get('base_price', 999999))
                doc_type = str(meta.get('type', '')).lower()
                type_rank = _TYPE_PRIORITY.get(doc_type, -1)
                return (-type_rank, price)
            sorted_pool = sorted(pool, key=_fallback_sort_key)
            fallback_n = max(3, min(len(sorted_pool) // 2, 5))
            filtered = sorted_pool[:fallback_n]
            cheapest = [
                f"{safe_float((getattr(d, 'metadata', {}) or {}).get('base_price', 0)):.0f}"
                for d in filtered
            ]
            if fallback_pool:
                print(f"[后置预算过滤] 全部超出预算，从大池({len(pool)}篇)兜底保留最便宜的 {fallback_n} 篇: {cheapest}", flush=True)
            else:
                print(f"[后置预算过滤] 全部超出预算，兜底保留最便宜的 {fallback_n} 篇: {cheapest}", flush=True)

            # 对兜底结果补充 Cross-Encoder 重排序（fallback_pool 未经过 reranking）
            if reranker is not None and question and len(filtered) >= 2:
                try:
                    doc_texts = [d.page_content.strip()[:1000] for d in filtered]
                    results = reranker.rerank(question, doc_texts, top_n=len(filtered))
                    ranked = [filtered[item["index"]] for item in results if 0 <= item["index"] < len(filtered)]
                    filtered = ranked
                    print(f"[后置预算过滤] 兜底结果已补充 Cross-Encoder 重排序 ({len(filtered)} docs)", flush=True)
                except Exception as e:
                    print(f"[后置预算过滤] 兜底重排序失败（仍按价格序返回）: {e}", flush=True)

    return filtered


# ═══════════════════════════════════════════════════════════════
# 兜底 Prompt
# ═══════════════════════════════════════════════════════════════

def get_fallback_prompt_static(question: str, knowledge_context: str) -> str:
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
