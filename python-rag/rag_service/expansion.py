"""查询扩展子模块：意图过滤 + 品类检测 + HyDE + 子问题拆解 + 多角度扩展

从 RAGService 中解耦出来，所有函数接收显式参数，不依赖 self。
"""

from typing import List, Dict, Optional, Set, Tuple, Any

from .intent_config import INTENT_KEYWORDS, INTENT_FILTER_MAP, INTENT_PRIORITY


# ═══════════════════════════════════════════════════════════════
# 品牌产地映射（用于日系/国货/欧美等产地概念排除）
# ═══════════════════════════════════════════════════════════════

# 品牌名 → 产地标签（小写品牌名 → 产地）
BRAND_ORIGIN: Dict[str, str] = {}
# 日系
for _b in ["安热沙", "珊珂", "资生堂", "芳珂", "日清", "sk-ii", "skii"]:
    BRAND_ORIGIN[_b.lower()] = "日系"
# 国货
for _b in ["花西子", "珀莱雅", "完美日记", "薇诺娜", "方里",
            "华为", "小米", "oppo", "vivo", "联想",
            "安踏", "李宁", "特步",
            "元气森林", "农夫山泉", "三顿半",
            "伊利", "蒙牛", "康师傅", "统一",
            "良品铺子", "三只松鼠", "百草味",
            "东鹏", "东方树叶", "海天", "李锦记", "金典", "纯甄"]:
    BRAND_ORIGIN[_b.lower()] = "国货"
# 欧美（英/法/德/瑞士/加拿大品牌）
for _b in ["巴黎欧莱雅", "理肤泉", "雅诗兰黛", "科颜氏", "玉兰油", "兰蔻",
            "the ordinary", "nike", "耐克", "apple", "苹果",
            "阿迪达斯", "adidas", "雀巢", "可口可乐",
            "始祖鸟", "北面", "the north face", "迈乐", "hoka",
            "osprey", "萨洛蒙", "迪卡侬", "露露乐蒙"]:
    BRAND_ORIGIN[_b.lower()] = "欧美"
# 韩系（AHC 等韩国品牌）
for _b in ["ahc", "雪花秀", "后", "兰芝", "悦诗风吟", "innisfree",
            "伊蒂之屋", "3ce", "dr.jart", "蒂佳婷", "苏秘37"]:
    BRAND_ORIGIN[_b.lower()] = "韩系"

# ═══════════════════════════════════════════════════════════════
# 品类关键词 → ChromaDB metadata 过滤条件映射
# ═══════════════════════════════════════════════════════════════

# 子品类关键词（精确过滤，优先级更高）
SUBCATEGORY_KEYWORDS: Dict[str, str] = {
    # ── 美妆护肤（12 个子品类） ──
    "洗面奶":  "洁面",     "洁面":    "洁面",
    "面霜":    "面霜",
    "防晒霜":  "防晒",     "防晒":    "防晒",
    "精华液":  "精华",     "精华":    "精华",
    "粉底液":  "粉底液",   "粉底":    "粉底液",
    "眉笔":    "眉笔",
    "卸妆油":  "卸妆",     "卸妆":    "卸妆",
    "眼霜":    "眼霜",
    "面膜":    "面膜",
    "化妆水":  "化妆水",
    "蜜粉":    "蜜粉",     "散粉":    "蜜粉",
    "唇釉":    "唇釉",     "口红":    "唇釉",
    # ── 服饰运动（12 个子品类） ──
    "T恤":     "短袖T恤",  "短袖":    "短袖T恤",
    "跑鞋":    "跑步鞋",   "运动鞋":  "跑步鞋",
    "徒步鞋":  "徒步鞋",
    "篮球鞋":  "篮球鞋",
    "背包":    "背包",
    "帽子":    "帽子",
    "运动裤":  "运动长裤", "运动长裤": "运动长裤",
    "卫衣":    "卫衣",
    "户外裤":  "户外裤",
    "瑜伽裤":  "瑜伽裤",
    "速干T恤": "速干T恤",  "速干T":   "速干T恤",
    "短裤":    "运动短裤",
    "跑步":    "跑步鞋",

    # ── 数码电子（4 个子品类） ──
    "笔记本电脑": "笔记本电脑",
    "平板电脑":   "平板电脑",
    "真无线耳机": "真无线耳机",
    "智能手机":   "智能手机",

    # ── 食品饮料（10 个子品类） ──
    "咖啡":    "咖啡",
    "速溶咖啡": "咖啡",
    "茶饮":    "茶饮",
    "牛奶":    "牛奶",
    "酸奶":    "酸奶",
    "功能饮料": "功能饮料",
    "方便食品": "方便食品",
    "坚果":    "坚果/零食",
    "调味品":  "调味品",
    "碳酸饮料": "碳酸饮料",
}

# 模糊关键词 → 子品类（需要额外判断的）
FUZZY_CATEGORY_KEYWORDS: Dict[str, str] = {
    "电脑":   "笔记本电脑",
    "笔记本": "笔记本电脑",
    "平板":   "平板电脑",
    "手机":   "智能手机",
    "耳机":   "真无线耳机",
    "蓝牙耳机": "真无线耳机",
    "穿搭":   "服饰运动",
    "数码":   "数码电子",
    "食品":   "食品饮料",
    "零食":   "坚果/零食",
    "饮料":   "碳酸饮料",
    "可乐":   "碳酸饮料",
    "茶":     "茶饮",
    "方便面": "方便食品",
    "泡面":   "方便食品",
}

# 一级类目关键词（只有当没有命中子品类时才使用）
CATEGORY_KEYWORDS: Dict[str, str] = {
    "护肤品": "美妆护肤", "化妆品": "美妆护肤", "美妆": "美妆护肤", "彩妆": "美妆护肤",
    "化妆用品": "美妆护肤",
    "衣服":   "服饰运动", "穿搭":   "服饰运动", "鞋":     "服饰运动",
    "数码":   "数码电子", "电子":   "数码电子",
    "食品":   "食品饮料", "吃的":   "食品饮料", "喝的":   "食品饮料",
}


def detect_category_filter(question: str) -> Optional[Dict[str, Any]]:
    """从用户查询中检测品类关键词，生成 ChromaDB metadata 过滤条件

    策略（按优先级）：
    1. 精确子品类匹配（如 "洗面奶" → sub_category:"洁面"）—— 最精确
    2. 模糊关键词匹配（如 "手机" → sub_category:"智能手机"）—— 次精确
    3. 一级类目兜底（如 "护肤品" → category:"美妆护肤"）—— 最宽泛

    Returns:
        ChromaDB metadata filter dict，如 {"sub_category": "洁面"} 或 {"category": "美妆护肤"}
        未检测到品类时返回 None
    """
    q = question

    # Step 1: 精确子品类关键词（按长度降序，最长优先 — 避免"跑步"劫持"功能饮料"）
    for kw, sub_cat in sorted(SUBCATEGORY_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if kw in q:
            return {"sub_category": sub_cat}

    # Step 2: 模糊关键词 → 子品类（同样最长优先）
    for kw, sub_cat in sorted(FUZZY_CATEGORY_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if kw in q:
            return {"sub_category": sub_cat}

    # Step 3: 一级类目兜底
    for kw, cat in sorted(CATEGORY_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if kw in q:
            return {"category": cat}

    return None


def detect_exclusion_filter(question: str) -> Optional[Dict[str, Any]]:
    """从用户查询中检测排除/约束意图（预算限制、品牌排除）

    支持的模式：
      - 预算上限："100元以下"、"不超过200"、"预算500"、"500以内"、"500元以下"
      - 预算下限："100元以上"
      - 品牌排除："不要XX"、"除了XX"、"不要XX品牌"、"排除XX"

    Returns:
        {"budget_max": 100.0, "brand_exclude": ["Nike"]} 或 None
    """
    import re
    result: Dict[str, Any] = {}

    # ── 预算检测 ──
    # 上限："XX元以下"、"XX元以内"、"不超过XX元"、"预算XX元"、"XX以内"
    budget_max_patterns = [
        r'(\d+)\s*元?\s*以[下内]',
        r'不超过\s*(\d+)\s*元?',
        r'预算\s*(\d+)\s*元?',
        r'(\d+)\s*块?\s*以[下内]',
    ]
    for pat in budget_max_patterns:
        m = re.search(pat, question)
        if m:
            result["budget_max"] = float(m.group(1))
            break

    # 下限："XX元以上"
    budget_min_match = re.search(r'(\d+)\s*元?\s*以[上外]', question)
    if budget_min_match:
        result["budget_min"] = float(budget_min_match.group(1))

    # ── 品牌排除检测 ──
    # 策略：英文/数字品牌用 [A-Za-z0-9\-]+ 精准捕获（遇到中文字符自动截断）；
    # 中文品牌从 BRAND_ORIGIN 已知品牌列表反向匹配（中文无空格，无法仅靠正则截断）。
    brand_exclude_patterns = [
        r'不要\s*([A-Za-z0-9\-]+)',
        r'除了\s*([A-Za-z0-9\-]+)',
        r'排除\s*([A-Za-z0-9\-]+)',
        r'不看\s*([A-Za-z0-9\-]+)',
    ]
    brand_excludes = []
    for pat in brand_exclude_patterns:
        for m in re.finditer(pat, question):
            brand = m.group(1).strip()
            if brand and len(brand) >= 2:
                brand_excludes.append(brand)

    # 中文品牌兜底：扫描已知品牌列表，检查是否出现在排除上下文中
    for origin_brand in BRAND_ORIGIN:
        if re.search(rf'(?:不要|除了|排除|不看)\s*{re.escape(origin_brand)}', question):
            if origin_brand not in brand_excludes:
                brand_excludes.append(origin_brand)

    if brand_excludes:
        result["brand_exclude"] = brand_excludes

    # ── 产地概念检测（日系/韩系/欧美/美系/国货） ──
    # 排除："不要日系"、"不要日系的"、"排除日系品牌"
    # 美系 → 欧美：当前品牌数据中美系品牌均标记为"欧美"，统一翻译
    origin_exclude_match = re.search(r'(?:不要|排除|不看)\s*(日系|韩系|欧美|美系)', question)
    if origin_exclude_match:
        origin_label = origin_exclude_match.group(1)
        if origin_label == "美系":
            origin_label = "欧美"  # 美系品牌在 BRAND_ORIGIN 中统一归入欧美
        result["origin_exclude"] = [origin_label]

    # 偏好："国货"、"国产"（作为 origin_prefer，后置过滤时优先保留国产品牌）
    origin_prefer_match = re.search(r'(国货|国产|国产品牌)', question)
    if origin_prefer_match:
        result["origin_prefer"] = "国货"

    return result if result else None


def detect_intent_filter(
    intent_keywords: Dict[str, List[str]] = None,
    intent_filter_map: Dict[str, Optional[Dict[str, Any]]] = None,
    question: str = "",
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """轻量快速识别用户问题意图，返回 (意图名称, 元数据过滤条件)

    intent_keywords / intent_filter_map 可省略，默认使用 intent_config 中的配置。
    """
    if intent_keywords is None:
        intent_keywords = INTENT_KEYWORDS
    if intent_filter_map is None:
        intent_filter_map = INTENT_FILTER_MAP

    q_lower = question.lower()

    matched_intents = set()
    for intent, keywords in intent_keywords.items():
        for kw in keywords:
            if kw in q_lower:
                matched_intents.add(intent)
                break

    if not matched_intents:
        return None, None

    for intent in INTENT_PRIORITY:
        if intent in matched_intents and intent in intent_filter_map:
            return intent, intent_filter_map[intent]

    return None, None


def detect_intent_and_category_filter(question: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """意图识别 + 品类检测 + $and 合并，一步完成

    替代 RAGService._detect_intent_filter 中的两层调用 + 手动合并逻辑。
    当同时命中内容类型过滤和品类过滤时，使用 ChromaDB $and 语法组合。

    Returns:
        (intent_name, merged_chromadb_filter)
    """
    intent, type_filter = detect_intent_filter(question=question)
    cat_filter = detect_category_filter(question)

    if type_filter and cat_filter:
        merged = {"$and": [type_filter, cat_filter]}
    elif type_filter:
        merged = type_filter
    elif cat_filter:
        merged = cat_filter
    else:
        merged = None

    return intent, merged


def unified_expand_queries(llm, original_question: str, num_expanded_queries: int = 2, category_hint: str = "") -> List[str]:
    """一次 LLM 调用完成假设文档 + 多角度扩展（省 1 次 API 调用）

    Args:
        category_hint: 品类约束提示，如 "请限定在「洁面」品类范围内"

    Returns:
        [HyDE, angle1, angle2, ...] 或空列表（失败时）
    """
    count = max(1, num_expanded_queries)
    category_instruction = f"\n重要约束：{category_hint}" if category_hint else ""
    prompt = f"""你是电商查询扩展助手。根据用户问题，完成两项任务：{category_instruction}

任务1：写一段简短的产品回答片段（30-50字），模拟一个真实导购的回答
任务2：生成 {count} 个不同表述角度的电商检索查询

输出格式（严格）：
[HYPE]
<回答片段>

[ANGLE]
<查询1>
<查询2>
...

不要任何额外的解释、前言或Markdown。

用户问题：{original_question}"""
    try:
        resp = llm.chat(prompt, temperature=0.5, max_tokens=256, purpose="query_expansion").strip()
    except Exception:
        return []

    hyde_text = ""
    angles: List[str] = []
    section = ""
    for line in resp.split("\n"):
        line = line.strip()
        if line.upper().startswith("[HYPE]"):
            section = "hyde"
            continue
        elif line.upper().startswith("[ANGLE]"):
            section = "angle"
            continue
        if section == "hyde" and line and not line.startswith("["):
            hyde_text += line + " "
        elif section == "angle" and line and not line.startswith("["):
            angles.append(line)

    hyde_text = hyde_text.strip()
    results = []
    if len(hyde_text) > 10:
        results.append(hyde_text)
    results.extend(angles[:num_expanded_queries])
    return results


def generate_expanded_queries(
    llm,
    num_expanded_queries: int,
    original_question: str,
    category_hint: str = "",
) -> List[str]:
    """电商查询重写：短问题跳过，长问题做一次合并扩展（HyDE + 多角度）"""
    all_queries = [original_question]

    # 仅跳过空查询或单字（如纯标点/"?"）的扩展
    # 原阈值 15 字过于保守，导致"跑鞋"(2字)、"推荐个T恤"(5字)等短查询
    # 检索精度不足。HyDE 扩展能给短关键词补充商品描述，提升检索命中率。
    if len(original_question.strip()) <= 1:
        return all_queries

    # 一次调用同时完成 HyDE + 多角度扩展（省 1 次 LLM 调用）
    # unified_expand_queries 内部已 try/except 所有异常，始终返回列表，无需外层再包
    expanded = unified_expand_queries(llm, original_question, num_expanded_queries, category_hint)
    all_queries.extend(expanded[:num_expanded_queries])

    seen: Set[str] = set()
    unique_queries = []
    for q in all_queries:
        if q and q not in seen and len(q.strip()) >= 2:
            seen.add(q)
            unique_queries.append(q)

    return unique_queries if unique_queries else [original_question]
