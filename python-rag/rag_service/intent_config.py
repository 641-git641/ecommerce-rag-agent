"""意图配置：电商问题类型关键词映射 + ChromaDB 元数据过滤条件

从 RAGService.__init__ 中解耦，供 expansion.py 的意图检测函数和 Agent 路由共同引用。
"""

from typing import Dict, List, Any, Optional

# 电商问题类型 → 触发关键词（小写）
INTENT_KEYWORDS: Dict[str, List[str]] = {
    "price_sku": ["多少钱", "价格", "价格多少", "sku", "规格", "尺码", "颜色", "尺寸", "库存", "什么码", "码数", "码"],
    "faq": ["售后", "保修", "退换", "退货", "换货", "支持吗", "怎么使用", "如何", "FAQ", "问题", "无理由", "7天", "保修期", "怎么保养", "怎么洗", "洗涤", "机洗"],
    "review": ["评价", "怎么样", "用户说", "大家都说", "好不好用", "口碑"],
    "basic": ["基本信息", "品牌", "分类", "产地", "材质"],
    "marketing": ["卖点", "优势", "为什么好", "特色", "推荐"],
}

# 意图名称 → ChromaDB metadata 过滤条件（None 表示不限内容类型）
INTENT_FILTER_MAP: Dict[str, Optional[Dict[str, Any]]] = {
    "price_sku": None,
    "faq": {"type": {"$in": ["faq", "faq_q"]}},
    "review": {"type": "review"},
    "basic": {"type": "basic_info"},
    "marketing": {"type": "marketing"},
}

# 意图优先级（多个命中时取最高优先级）
INTENT_PRIORITY: List[str] = ["faq", "price_sku", "review", "basic", "marketing"]
