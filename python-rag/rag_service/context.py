"""上下文构建子模块：图谱一跳展开 + 知识上下文构建

从 RAGService 中解耦出来。
"""

from typing import List, Set, Any


def safe_float(value: Any) -> float:
    """安全浮点转换"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("¥", "").replace("元", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


def graph_context_expand(ecommerce_graph, enable_graph_expand: bool, retrieved_docs: List) -> str:
    """图谱一跳展开：将检索命中商品的同品类竞品信息注入上下文

    纯内存字典遍历，延迟 <1ms。
    """
    if not enable_graph_expand or not ecommerce_graph:
        return ""

    seen_products: Set[str] = set()
    for doc in retrieved_docs:
        meta = getattr(doc, 'metadata', {}) or {}
        pid = meta.get('product_id', '')
        if pid and pid not in seen_products:
            seen_products.add(pid)

    if not seen_products:
        return ""

    graph_lines: List[str] = []
    reported_related: Set[str] = set()

    for pid in seen_products:
        related = ecommerce_graph.get_same_sub_category_products(pid, limit=2)
        if not related:
            continue

        node_key = f"product:{pid}"
        my_title = pid
        if node_key in ecommerce_graph.nodes:
            my_title = ecommerce_graph.nodes[node_key].get("properties", {}).get("title", pid)

        for node in related:
            props = node.get("properties", {})
            rid = props.get("product_id", "")
            if not rid or rid in seen_products or rid in reported_related:
                continue
            reported_related.add(rid)

            rname = props.get("title", rid)
            rbrand = props.get("brand_name", "")
            rprice = props.get("price", "")
            price_str = f"¥{rprice}" if rprice else ""
            meta_list = [x for x in [rbrand, price_str] if x]
            graph_lines.append(f"- {rname}（{' / '.join(meta_list)}）")

    if graph_lines:
        return "【同品类竞品参考】\n" + "\n".join(graph_lines)
    return ""


def build_context(retrieved_docs: List, graph=None) -> tuple:
    """构建检索上下文和来源列表

    对于 type="faq" 的文档，优先读取 metadata.faq_answer 作为 LLM 上下文。
    对于缺失价格/product_id 的片段，从 metadata 注入，确保 LLM 能输出完整信息。
    graph: 可选，电商关联图谱实例，用于 metadata 无 base_price 时回退查找
    """
    if not retrieved_docs:
        return "", []

    # 收集所有文档的 product_id，查询图谱补全价格
    pid_price_map: dict = {}
    for doc in retrieved_docs:
        meta = getattr(doc, 'metadata', {}) or {}
        pid = meta.get('product_id', '')
        bp = safe_float(meta.get('base_price', 0) or 0)
        if pid and bp > 0 and pid not in pid_price_map:
            pid_price_map[pid] = bp

        # 回退：从图谱查找价格（处理 metadata 无 base_price 的场景）
        if pid and pid not in pid_price_map and graph is not None:
            node_key = f"product:{pid}"
            if node_key in graph.nodes:
                gprice = safe_float(
                    graph.nodes[node_key].get("properties", {}).get("price", 0) or 0
                )
                if gprice > 0:
                    pid_price_map[pid] = gprice

    knowledge_parts = []
    sources = []

    for i, doc in enumerate(retrieved_docs):
        meta = getattr(doc, 'metadata', {}) or {}
        if meta.get('type') in ('faq', 'faq_q') and meta.get('faq_answer'):
            content = meta['faq_answer'].strip()
        else:
            content = doc.page_content.strip()

        # 注入缺失的价格和 product_id
        if content:
            pid = meta.get('product_id', '')
            has_price = '价格' in content or '¥' in content or 'price' in content.lower()
            if not has_price and pid and pid in pid_price_map:
                content = content + f"\n价格: ¥{pid_price_map[pid]}"
            if pid and '商品ID' not in content and 'product_id' not in content.lower():
                content = content + f"\n商品ID: {pid}"

            knowledge_parts.append(f"【知识片段{i + 1}】\n{content}")
            source_info = {
                "content": content,
                "source": meta.get('source', f"知识片段{i + 1}"),
                "product_id": pid,
                "type": meta.get('type', ''),
            }
            sources.append(source_info)

    return "\n\n".join(knowledge_parts), sources
