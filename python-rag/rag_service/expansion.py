"""查询扩展子模块：意图过滤 + HyDE + 子问题拆解 + 多角度扩展

从 RAGService 中解耦出来，所有函数接收显式参数，不依赖 self。
"""

from typing import List, Dict, Optional, Set, Tuple, Any


def detect_intent_filter(
    intent_keywords: Dict[str, List[str]],
    intent_filter_map: Dict[str, Optional[Dict[str, Any]]],
    question: str,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """轻量快速识别用户问题意图，返回 (意图名称, 元数据过滤条件)"""
    q_lower = question.lower()

    matched_intents = set()
    for intent, keywords in intent_keywords.items():
        for kw in keywords:
            if kw in q_lower:
                matched_intents.add(intent)
                break

    if not matched_intents:
        return None, None

    priority = ["faq", "price_sku", "review", "basic", "marketing"]
    for intent in priority:
        if intent in matched_intents and intent in intent_filter_map:
            return intent, intent_filter_map[intent]

    return None, None


def unified_expand_queries(llm, original_question: str, num_expanded_queries: int = 2) -> List[str]:
    """一次 LLM 调用完成假设文档 + 多角度扩展（省 1 次 API 调用）

    Returns:
        [HyDE, angle1, angle2, ...] 或空列表（失败时）
    """
    count = max(1, num_expanded_queries)
    prompt = f"""你是电商查询扩展助手。根据用户问题，完成两项任务：

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
        resp = llm.chat(prompt, temperature=0.5, max_tokens=256).strip()
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


def break_down_sub_questions(llm, original_question: str) -> List[str]:
    """复杂问题子问题拆解（2-3 个独立短句）"""
    prompt = f"""用户问了一个电商相关的复杂问题，请把这个问题拆分成2-3个独立的简单子查询，
用于分开检索知识库。每行一个，不要任何多余解释、编号、前缀。

复杂问题：{original_question}"""
    resp = llm.chat(prompt, temperature=0.5).strip()
    subs = [q.strip() for q in resp.split("\n") if q.strip()]
    return subs[:3]


def multi_angle_expand_queries(llm, original_question: str, num_expanded_queries: int = 3) -> List[str]:
    """多角度查询扩展：生成不同表述方式的查询"""
    count = max(1, num_expanded_queries - 1)
    prompt = f"""你是一个电商查询扩展助手。根据用户问题，生成 {count} 个不同角度、不同表述方式的电商相关查询，用于增强向量检索的召回率。

要求：
1. 生成的查询要和原始问题语义高度相关
2. 每个查询的表述方式、侧重点都要不一样
3. 直接输出查询列表，每行一个，不要任何多余解释、前缀、编号
4. 输出严格纯文本，不要Markdown格式

用户原始问题：{original_question}"""
    resp = llm.chat(prompt, temperature=0.7).strip()
    expanded = [q.strip() for q in resp.split("\n") if q.strip()]
    return expanded[:num_expanded_queries]


def generate_expanded_queries(
    llm,
    num_expanded_queries: int,
    intent_keywords: Dict[str, List[str]],
    intent_filter_map: Dict[str, Optional[Dict[str, Any]]],
    original_question: str,
    intent: Optional[str] = None,
) -> List[str]:
    """电商查询重写：短问题跳过，长问题做一次合并扩展（HyDE + 多角度）"""
    all_queries = [original_question]

    try:
        # 短问题（≤15字）不做扩展，直接原词检索更精准
        if len(original_question.strip()) <= 15:
            return all_queries

        # 一次调用同时完成 HyDE + 多角度扩展（省 1 次 LLM 调用）
        try:
            expanded = unified_expand_queries(llm, original_question, num_expanded_queries)
            all_queries.extend(expanded[:num_expanded_queries])
        except Exception as e_expand:
            print(f"[查询扩展降级] 合并扩展失败: {str(e_expand)}")

    except Exception as e_all:
        print(f"[查询重写总降级] 整体重写异常，使用原始问题保底: {str(e_all)}")
        return [original_question]

    seen: Set[str] = set()
    unique_queries = []
    for q in all_queries:
        if q and q not in seen and len(q.strip()) >= 2:
            seen.add(q)
            unique_queries.append(q)

    return unique_queries if unique_queries else [original_question]
