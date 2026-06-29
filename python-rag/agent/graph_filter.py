"""图谱硬过滤 — 预算/品牌/排除条件映射为 product_id 过滤列表

从 api.py 中解耦出来的 _build_graph_filter + 多样性追问排除逻辑。
"""

from typing import List, Optional, Set


def build_graph_filter(query: str, memory_state, ecommerce_graph) -> Optional[dict]:
    """利用知识图谱将预算/品牌/排除条件映射为 product_id 过滤列表

    Returns:
        {"product_id": {"$in": [...]}} 或 None（无过滤条件）
    """
    if ecommerce_graph is None or memory_state is None:
        return None

    budget_max = getattr(memory_state, "budget_max", 0) if memory_state else 0
    brand_pref = getattr(memory_state, "brand", "") if memory_state else ""
    category = getattr(memory_state, "category", "") if memory_state else ""
    excludes = getattr(memory_state, "exclude", []) if memory_state else []

    has_budget = budget_max > 0
    has_brand = bool(brand_pref)
    has_category = bool(category)
    has_exclude = bool(excludes)

    # 没有任何过滤条件 → 不过滤，返回 None
    if not has_budget and not has_brand and not has_category and not has_exclude:
        return None

    candidate_ids: Set[str] = set()
    exclude_ids: Set[str] = set()

    # ── 品类→图谱子品类映射 ──
    CATEGORY_TO_SUB = {
        "跑鞋": ["跑步鞋"],
        "运动鞋": ["跑步鞋"],
        "徒步鞋": ["徒步鞋"],
        "篮球鞋": ["篮球鞋"],
        "洗面奶": ["洁面"], "洁面": ["洁面"],
        "面霜": ["面霜"],
        "防晒霜": ["防晒"], "防晒": ["防晒"],
        "T恤": ["短袖T恤", "速干T恤"], "T恤衫": ["短袖T恤", "速干T恤"],
        "耳机": ["真无线耳机"], "蓝牙耳机": ["真无线耳机"],
        "手机": ["智能手机"],
        "笔记本电脑": ["笔记本电脑"],
        "平板电脑": ["平板电脑"],
        "精华": ["精华"],
        "粉底": ["粉底液"],
        "口红": ["唇釉"],
        "背包": ["背包"],
        "卫衣": ["卫衣"],
        "短裤": ["运动短裤"],
        "瑜伽裤": ["瑜伽裤"],
        "帽子": ["帽子"],
        "面膜": ["面膜"],
        "眼霜": ["眼霜"],
        "卸妆": ["卸妆"],
        "裤": ["运动长裤", "户外裤", "瑜伽裤"],
        "饮料": ["碳酸饮料", "功能饮料", "茶饮"],
        "牛奶": ["牛奶"],
        "坚果": ["坚果/零食"],
        "咖啡": ["咖啡"],
        "茶": ["茶饮"],
        "功能饮料": ["功能饮料"],
        "酸奶": ["酸奶"],
        "方便面": ["方便食品"], "牛肉面": ["方便食品"], "泡面": ["方便食品"],
        "速干T恤": ["速干T恤"],
    }

    for node_id, node in ecommerce_graph.nodes.items():
        if not node_id.startswith("product:"):
            continue
        props = node.get("properties", {})
        pid = props.get("product_id", node_id.replace("product:", ""))
        g_price = float(props.get("price", 0))
        g_brand = props.get("brand_name", "")
        g_name = props.get("title", "")
        g_sub = props.get("sub_category", "")

        if not pid:
            continue

        # 品类过滤
        if has_category:
            allowed_subs = CATEGORY_TO_SUB.get(category, [category])
            if not (g_sub in allowed_subs or category in g_name):
                continue  # 品类不匹配，跳过此商品

        # 预算过滤
        if has_budget:
            if g_price > 0 and g_price <= budget_max:
                candidate_ids.add(pid)
            elif g_price == 0:
                candidate_ids.add(pid)
        else:
            candidate_ids.add(pid)

        # 品牌过滤
        if has_brand:
            if brand_pref.lower() in g_brand.lower() or brand_pref.lower() in g_name.lower():
                pass
            else:
                candidate_ids.discard(pid)

        # 排除过滤
        for exc in excludes:
            if exc in g_name or exc in g_brand:
                exclude_ids.add(pid)

    candidate_ids -= exclude_ids

    # 有候选 → 返回 $in 过滤
    if candidate_ids:
        return {"product_id": {"$in": sorted(candidate_ids)}}

    # 有过滤条件但无候选（如预算太低过滤掉全部） → 返回空 $in 阻止兜底检索
    if has_budget or has_brand or has_category or has_exclude:
        return {"product_id": {"$in": []}}

    return None


