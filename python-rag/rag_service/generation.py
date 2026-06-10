"""答案生成子模块：LLM 调用 + 质量检测 + 后退提问 + 查询缓存归一化

从 RAGService 中解耦出来。
"""

from typing import List, Dict, Optional, Any


def normalize_query(question: str) -> str:
    """查询缓存归一化：去停用词、去标点，保留核心语义"""
    discard = {"这个", "那个", "这款", "那款", "请问", "帮我", "我想", "想知道",
                "是什么", "怎么样", "好不好", "能不能", "可以吗", "支持吗",
                "有没有", "多少钱", "吗", "呢", "啊", "吧", "的", "了",
                "推荐", "给我", "一下", "一个", "一款", "最好", "最"}
    chars = []
    for ch in question:
        if '\u4e00' <= ch <= '\u9fff':
            if ch not in discard:
                chars.append(ch)
        elif ch.isalnum():
            chars.append(ch.lower())
    return "".join(chars)


def simplify_to_keywords(question: str) -> str:
    """抽取核心关键词（step-back 时用）"""
    discard = {"这个", "那个", "这款", "那款", "请问", "帮我", "我想", "想知道",
                "是什么", "怎么样", "好不好", "能不能", "可以吗", "支持吗",
                "有没有", "多少钱", "吗", "呢", "啊", "吧", "的", "了"}
    result = []
    for ch in question:
        if '\u4e00' <= ch <= '\u9fff':
            if ch not in discard:
                result.append(ch)
    keywords = " ".join(result)
    return keywords if len(keywords) >= 2 else question


def is_low_quality_answer(answer: str) -> bool:
    """低质量回答判定"""
    if not answer or len(answer.strip()) < 8:
        return True
    low_quality_markers = [
        "无法回答", "我无法", "不清楚", "不知道", "没有相关",
        "知识库为空", "无法提供", "暂时无法", "没有找到",
    ]
    answer_stripped = answer.strip()
    for marker in low_quality_markers:
        if marker in answer_stripped:
            return True
    return False


def generate_answer(llm, prompt_manager, question: str, knowledge_context: str, structured: bool = False) -> str:
    """根据知识上下文调用 LLM 生成回答"""
    if not knowledge_context:
        return "根据当前知识库，我无法回答这个问题。知识库当前为空。"

    try:
        template_name = "rag_prompt_structured" if structured else "rag_prompt"
        prompt = prompt_manager.render_prompt(
            template_name,
            knowledge_context=knowledge_context,
            user_question=question
        )
    except Exception:
        prompt = _get_fallback_prompt(question, knowledge_context)

    return llm.chat(prompt)


def step_back_answer(
    llm, vector_store, retrieval_k,
    prompt_manager,
    question: str,
    build_context_fn,
) -> dict:
    """回答质量不足时，尝试后退提问重搜

    第1轮：纯关键词重搜（去掉停用词）
    第2轮：关闭过滤条件用原始问题重搜
    两轮后仍未改善则放弃。
    """
    step_back_round = 0

    print(f"[后退提问-第1轮] 回答质量不足，尝试用纯关键词重搜...")
    step_back_round = 1
    try:
        simple_q = simplify_to_keywords(question)
        docs = vector_store.similarity_search(simple_q, k=retrieval_k * 3, filter=None)
        ctx, src = build_context_fn(docs)
        if ctx:
            answer = generate_answer(llm, prompt_manager, question, ctx)
            if not is_low_quality_answer(answer):
                return {"answer": answer, "sources": src, "step_back_round": step_back_round}
    except Exception as e:
        print(f"[后退提问-第1轮降级] 关键词重搜失败: {str(e)}")

    print(f"[后退提问-第2轮] 回答仍不足，尝试关闭过滤条件用原始问题重搜...")
    step_back_round = 2
    try:
        docs = vector_store.similarity_search(question, k=retrieval_k * 3, filter=None)
        ctx, src = build_context_fn(docs)
        if ctx:
            answer = generate_answer(llm, prompt_manager, question, ctx)
            if not is_low_quality_answer(answer):
                return {"answer": answer, "sources": src, "step_back_round": step_back_round}
    except Exception as e:
        print(f"[后退提问-第2轮降级] 无过滤重搜失败: {str(e)}")

    print(f"[后退提问] 全部重试轮次结束，未能获得满意回答")
    return {"answer": "", "sources": [], "step_back_round": step_back_round}


def _get_fallback_prompt(question: str, knowledge_context: str) -> str:
    """内置 fallback 提示词模板（外部模板加载失败时使用）"""
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
