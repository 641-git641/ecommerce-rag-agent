"""意图分类模块：基于关键词规则快速识别用户查询意图

在调用 LLM 之前执行，用于决定是否跳过 ReAct 循环，减少延迟。
"""

import re
from typing import List, Optional


# 查询意图类型
INTENT_SIMPLE = "simple"     # 简单推荐/模糊查询 → 快速路径
INTENT_CART = "cart"         # 购物车操作 → 直接 cart API
INTENT_COMPARE = "compare"   # 对比决策 → 对比路径
INTENT_EXCLUDE = "exclude"   # 反选/排除 → 过滤路径
INTENT_COMBO = "combo"       # 场景化组合 → 组合路径
INTENT_COMPLEX = "complex"   # 复杂/混合意图 → 完整 ReAct


def classify_query(query: str) -> str:
    """基于关键词规则快速分类用户意图

    Args:
        query: 用户查询文本

    Returns:
        意图类型常量
    """
    normalized = query.lower()

    # ── 购物车意图检测（最高优先级，直接操作不绕 ReAct）──
    cart_actions = [
        "加购物车", "加入购物车", "加到购物车",
        "加购", "添加购物车",
        "买这个", "买它", "下单", "结算", "结账",
        "清空购物车", "删除购物车",
        "购物车", "我的购物车", "看看购物车",
        "删除第", "移除第",
        "数量改成", "数量改成", "改成", "改为",
        "确认下单",
    ]
    for p in cart_actions:
        if p in normalized:
            return INTENT_CART

    # ── 对比意图检测 ──
    compare_patterns = [
        "对比", "比较", "哪个更", "哪个好", "哪个适合",
        "vs", "versus", "区别", "差别", "相比", "pk",
        "选哪个", "怎么选", "纠结", "二选一", "哪个性价比",
    ]
    for p in compare_patterns:
        if p in normalized:
            return INTENT_COMPARE

    # ── 反选/排除意图检测 ──
    exclude_patterns = [
        "不要", "不含", "除了", "排除", "去掉", "别推",
        "不要给我", "别给我", "非", "勿", "避开",
        "不能有", "不可以有", "不希望",
    ]
    for p in exclude_patterns:
        if p in normalized:
            return INTENT_EXCLUDE

    # ── 场景化组合意图检测 ──
    combo_keywords = [
        "三亚", "度假", "旅行", "旅游", "出差", "海边", "露营",
        "户外", "野餐", "爬山", "滑雪", "装备", "全套",
        "搭配", "方案", "组合", "套餐", "出行", "去玩",
    ]
    combo_count = sum(1 for kw in combo_keywords if kw in normalized)
    if combo_count >= 2:
        return INTENT_COMBO
    for kw in ("搭配", "方案", "装备", "全套", "组合"):
        if kw in normalized:
            return INTENT_COMBO

    # ── 复杂意图检测：多步骤提问 ──
    complex_indicators = [
        "然后", "接着", "之后", "先", "再",
        "顺便", "同时", "还有", "另外",
    ]
    complex_count = sum(1 for kw in complex_indicators if kw in normalized)
    if complex_count >= 2:
        return INTENT_COMPLEX

    # ── 多意图混合 ──
    has_recommend = "推荐" in normalized or "推荐一下" in normalized
    has_cart = "购物车" in normalized or "加购" in normalized or "下单" in normalized
    if has_recommend and has_cart:
        return INTENT_COMPLEX

    return INTENT_SIMPLE


def extract_compare_products(query: str) -> Optional[List[str]]:
    """从对比类查询中提取商品名"""
    normalized = query

    # ── 剥离记忆增强后缀（格式: "原问题？ 品类 品牌"）──
    # 内存模块会在问号后追加品类+品牌词，这部分不是商品名的一部分
    for sep in ('？', '?'):
        pos = normalized.find(sep)
        if pos >= 0:
            after = normalized[pos + 1:].strip()
            # 问号后文本短且不含对比/疑问关键词 → 判定为增强后缀
            if after and len(after) <= 30 and not re.search(r'[？?和与vs对比比较还是]', after):
                normalized = normalized[:pos + 1]
            break

    parts = re.split(r'\s*(?:和|与|vs\.?|versus|还是|对比|比较)\s*', normalized, flags=re.IGNORECASE)

    products = []
    for p in parts:
        p = p.strip()
        # 去除开头引导词
        p = re.sub(r'^(推荐|哪个|哪款|哪一款)', '', p)
        # 去除尾部问句/评价成分（贪婪匹配，处理 "阿迪达斯的跑鞋哪个更好？ 跑鞋 Nike" → "阿迪达斯的跑鞋"）
        p = re.sub(r'(哪个更好|哪个好|哪个更适合|怎么选|如何选|选哪个|哪个更|哪个性价比|哪个值得).*$', '', p)
        # 去除尾部问句/评价成分（仅末尾，处理短查询如 "拍照哪个好"）
        TAIL_PATTERN = r'(好|更好|适合|拍照|怎么样)$'
        for _ in range(2):
            p, n = re.subn(TAIL_PATTERN, '', p)
            if n == 0:
                break
        # 去除尾部问号和多余空格
        p = p.rstrip('？? \t')
        p = p.strip()
        if p:
            products.append(p)
    return products[:2] if len(products) >= 2 else None


def extract_product_category(query: str) -> str:
    """从查询中提取商品品类关键词"""
    categories = [
        "手机", "电脑", "笔记本", "平板", "耳机", "手表", "音箱",
        "跑鞋", "运动鞋", "徒步鞋", "登山鞋", "篮球鞋", "帆布鞋", "板鞋",
        "T恤", "衬衫", "连衣裙", "牛仔裤", "外套", "卫衣", "短裤", "瑜伽裤",
        "背包", "洗面奶", "面霜", "防晒霜", "精华", "口红", "粉底", "面膜",
        "充电器", "充电宝", "键盘", "鼠标", "拖鞋", "帽子", "墨镜",
        "方便面", "牛肉面", "泡面", "零食", "饮料", "牛奶", "饼干", "坚果", "面包",
        "咖啡", "茶", "功能饮料", "酸奶", "酱油", "矿泉水", "可乐", "火腿肠",
    ]
    for c in categories:
        if c in query:
            return c
    return ""


def intent_label(intent: str) -> str:
    """意图中英文标签映射"""
    labels = {
        INTENT_SIMPLE: "简单推荐 → 快速路径 (search → finish)",
        INTENT_CART: "购物车操作 → 直接 cart API",
        INTENT_COMPARE: "对比决策 → 对比路径 (search → compare → finish)",
        INTENT_EXCLUDE: "反选排除 → 过滤路径 (search+filter → finish)",
        INTENT_COMBO: "场景组合 → 组合路径 (combo → finish)",
        INTENT_COMPLEX: "复杂意图 → 完整 ReAct 推理",
    }
    return labels.get(intent, "未知")


def is_vague_query(query: str) -> bool:
    """检测是否为信息不足的模糊查询，需要主动反问

    条件：无品牌、无品类、无属性、无预算、无排除关键词，
    且问题很短（<15字），或仅为"推荐/推荐一下/有什么好的"等泛泛之词。

    注意：包含"另外/其他/别的/换一个"等多样性追问关键词的查询不视为模糊，
    因为这是对上一轮推荐的自然追问，应结合对话上下文处理。
    """
    normalized = query.lower()

    # ── 多样性追问检测：用户想要不同的/另外的推荐，不视为模糊查询 ──
    diversity_followup = [
        "另外", "其他", "别的", "不一样", "不同",
        "换一个", "换一款", "换种", "换别的",
        "还有吗", "还有什么", "还有别的", "还有没有",
        "再来", "再推", "再推荐",
    ]
    for kw in diversity_followup:
        if kw in query:
            return False

    # 品类关键词
    categories = {
        "手机", "电脑", "笔记本", "平板", "耳机", "手表", "音箱",
        "跑鞋", "运动鞋", "徒步鞋", "登山鞋", "篮球鞋", "帆布鞋", "板鞋",
        "T恤", "衬衫", "连衣裙", "牛仔裤", "外套", "卫衣", "短裤", "瑜伽裤",
        "背包", "洗面奶", "面霜", "防晒霜", "精华", "口红", "粉底", "面膜",
        "充电器", "充电宝", "键盘", "鼠标", "拖鞋", "帽子", "墨镜",
        "防晒", "洗面", "水乳", "乳液", "爽肤水",
        "护肤品", "化妆品", "美妆", "护肤", "彩妆", "个护", "衣服", "鞋子",
        "方便面", "牛肉面", "泡面", "零食", "饮料", "牛奶", "饼干", "坚果", "面包",
        "咖啡", "茶", "功能饮料", "酸奶", "酱油", "矿泉水", "可乐", "火腿肠",
    }
    brands = {
        "Nike", "nike", "耐克", "Adidas", "adidas", "阿迪达斯",
        "华为", "小米", "三星", "苹果", "OPPO", "vivo",
        "兰蔻", "雅诗兰黛", "科颜氏", "SK-II", "资生堂", "理肤泉",
        "优衣库", "李宁", "安踏", "迪卡侬", "波司登", "海澜之家",
        "索尼", "Bose", "JBL", "漫步者", "韶音", "Beats",
        "戴尔", "联想", "华硕", "惠普", "ThinkPad",
        "花西子", "完美日记", "MAC", "YSL", "Dior",
        "安热沙", "安耐晒", "百草味", "三只松鼠", "良品铺子",
        "欧莱雅", "奥莱雅", "olay", "赫莲娜", "倩碧", "悦木之源",
        "HOKA", "萨洛蒙", "SALOMON", "迈乐", "Merrell",
        "露露乐蒙", "Lululemon", "始祖鸟", "Arc'teryx", "北面", "The North Face",
        "Osprey", "特步", "鸿星尔克", "361", "匹克", "回力",
        "康师傅", "统一", "白象", "今麦郎", "旺旺", "良品", "日清",
        "雀巢", "三顿半", "东方树叶", "元气森林", "东鹏", "红牛",
        "金典", "纯甄", "海天", "农夫山泉", "可口可乐", "蒙牛", "伊利", "李锦记",
    }
    attrs = {
        "轻量", "轻薄", "透气", "防水", "防风", "保暖",
        "保湿", "控油", "美白", "抗皱", "修复", "舒缓",
        "降噪", "高刷", "长续航", "快充", "折叠", "便携",
        "油皮", "干皮", "混合皮", "敏感肌", "平价", "高端",
        "原味", "香辣", "麻辣", "红烧", "酸辣", "藤椒",
        "桶装", "袋装", "大容量",
    }
    budget_patterns = [
        r"\d+元", r"预算", r"以内", r"以下", r"左右", r"便宜", r"贵", r"性价比",
    ]

    has_category = any(c in query for c in categories)
    has_brand = any(b in normalized for b in brands)
    has_attr = any(a in query for a in attrs)
    has_budget = any(re.search(p, query) for p in budget_patterns)
    has_exclude = any(kw in query for kw in ("不要", "不含", "除了", "排除"))

    # 有任意明确信号 → 不模糊
    if has_category or has_brand or has_attr or has_budget or has_exclude:
        return False

    # 无任何信号 + 短查询 → 模糊
    if len(query) <= 15:
        return True

    # 有特定模式也视为模糊
    vague_patterns = [
        r"^推荐", r"^有什么", r"^帮我推荐", r"^给我推荐", r"^推荐一下",
        r"^有什么好", r"^有没有好", r"^哪个好",
    ]
    for p in vague_patterns:
        if re.match(p, query):
            return True

    return False
