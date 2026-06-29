"""话题切换检测模块

从用户查询文本中提取品类+品牌关键词，检测对话话题是否切换。
从 api/routes.py 抽取，供 rag_stream_events 使用。

注意：本模块的品类/品牌集合与 agent/intent.py 存在重叠但职责不同——
本模块用于流式管线中的话题切换检测（影响 chat_history 传递策略），
intent.py 用于意图分类和模糊查询判断。两处各自维护以避免耦合。
"""

# ── 品类词集合 ──
TOPIC_CATEGORIES = {
    "手机", "电脑", "笔记本", "平板", "耳机", "手表", "音箱", "充电器", "充电宝", "键盘", "鼠标", "数据线",
    "跑鞋", "运动鞋", "篮球鞋", "拖鞋", "鞋子", "T恤", "衬衫", "连衣裙", "牛仔裤", "外套", "卫衣", "短裤", "帽子", "墨镜", "背包",
    "洗面奶", "面霜", "防晒霜", "防晒", "精华", "口红", "粉底", "面膜", "眼霜", "卸妆", "洁面", "爽肤水", "乳液", "水乳",
    "护肤品", "化妆品", "美妆", "护肤", "彩妆",
}

# ── 品牌词集合 ──
TOPIC_BRANDS = {
    "nike", "adidas", "华为", "小米", "三星", "苹果", "oppo", "vivo",
    "兰蔻", "雅诗兰黛", "科颜氏", "sk-ii", "资生堂", "理肤泉",
    "优衣库", "李宁", "安踏", "迪卡侬", "波司登",
    "索尼", "bose", "jbl", "漫步者",
    "戴尔", "联想", "华硕", "惠普",
    "花西子", "完美日记", "mac", "ysl", "dior",
    "欧莱雅", "olay", "赫莲娜", "倩碧", "安热沙", "安耐晒",
}


def extract_topic(text: str) -> str:
    """从文本中提取品类+品牌话题关键词

    Args:
        text: 用户查询文本

    Returns:
        品类+品牌关键词拼接的字符串（最多3个词），无匹配时返回空串
    """
    t = text.lower()
    parts = []
    for cat in TOPIC_CATEGORIES:
        if cat in t:
            parts.append(cat)
    for brand in TOPIC_BRANDS:
        if brand in t:
            parts.append(brand)
    return " ".join(parts[:3]) if parts else ""


def detect_topic_switch(question: str, chat_history: list) -> str:
    """检测话题是否切换，返回提示文本（空串=未切换）

    比较当前问题与上一条用户消息的话题关键词，
    若不同则返回话题切换提示，供 LLM prompt 注入使用。

    Args:
        question: 当前用户问题
        chat_history: 对话历史列表

    Returns:
        话题切换提示文本，未切换时返回空串
    """
    cur_topic = extract_topic(question)
    if not cur_topic:
        return ""
    # 找上一条用户消息
    for m in reversed(chat_history):
        if m.get("role") == "user":
            prev_topic = extract_topic(m.get("content", ""))
            if prev_topic and prev_topic != cur_topic:
                return f"[话题已切换：{prev_topic} → {cur_topic}] 请忽略之前的对话历史，仅根据当前问题和知识库内容回答。"
            break
    return ""
