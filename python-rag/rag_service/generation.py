"""答案生成子模块：LLM 调用 + 质量检测 + 后退提问 + 查询缓存归一化

从 RAGService 中解耦出来。
"""

from typing import Dict, Optional, Any




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


def generate_answer(llm, prompt_manager, question: str, knowledge_context: str, structured: bool = False, temperature: float = 0.0, purpose: str = "generation") -> str:
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

    return llm.chat(prompt, temperature=temperature, purpose=purpose)


def step_back_answer(
    llm, vector_store, retrieval_k,
    prompt_manager,
    question: str,
    build_context_fn,
    budget_hint: str = "",
    structured: bool = False,
    detected_filter: Optional[Dict[str, Any]] = None,
) -> dict:
    """回答质量不足时，尝试后退提问重搜

    第1轮：纯关键词重搜（保留品类/内容类型过滤，去掉停用词）
    第2轮：原始问题重搜（保留品类/内容类型过滤）
    两轮后仍未改善则放弃。

    之前 filter=None 会丢弃所有意图检测的品类+内容类型过滤，
    导致 step_back 结果跨品类漂移（如国货面霜→推荐玉兰油欧美品牌）。
    detected_filter 保留品类/子品类/内容类型约束，只放宽查询措辞。
    """
    step_back_round = 0

    print(f"[后退提问-第1轮] 回答质量不足，尝试用纯关键词重搜...")
    step_back_round = 1
    try:
        simple_q = simplify_to_keywords(question)
        docs = vector_store.similarity_search(simple_q, k=retrieval_k * 3, filter=detected_filter)
        ctx, src = build_context_fn(docs)
        if ctx:
            if budget_hint:
                ctx = budget_hint + "\n" + ctx
            answer = generate_answer(llm, prompt_manager, question, ctx, structured=structured, purpose="step_back_r1")
            if not is_low_quality_answer(answer):
                return {"answer": answer, "sources": src, "step_back_round": step_back_round}
    except Exception as e:
        print(f"[后退提问-第1轮降级] 关键词重搜失败: {str(e)}")

    print(f"[后退提问-第2轮] 回答仍不足，尝试用原始问题重搜（保留品类过滤）...")
    step_back_round = 2
    try:
        docs = vector_store.similarity_search(question, k=retrieval_k * 3, filter=detected_filter)
        ctx, src = build_context_fn(docs)
        if ctx:
            if budget_hint:
                ctx = budget_hint + "\n" + ctx
            answer = generate_answer(llm, prompt_manager, question, ctx, structured=structured, purpose="step_back_r2")
            if not is_low_quality_answer(answer):
                return {"answer": answer, "sources": src, "step_back_round": step_back_round}
    except Exception as e:
        print(f"[后退提问-第2轮降级] 保留品类重搜失败: {str(e)}")

    print(f"[后退提问] 全部重试轮次结束，未能获得满意回答")
    return {"answer": "", "sources": [], "step_back_round": step_back_round}


def forced_answer_prompt(question: str, knowledge_context: str, budget_hint: str = "") -> str:
    """最终兜底强制回答 Prompt — 当步退回退全部失败时使用

    与常规 prompt 的关键区别：
    1. 明确禁止使用"无法回答""不清楚"等拒绝性短语
    2. 要求基于现有信息给出最有价值的回答，即使信息不完美
    3. 对比类问题引导 LLM 基于已有信息给出倾向性建议
    """
    budget_section = budget_hint + "\n\n" if budget_hint else ""

    # 检测问题类型以提供针对性引导
    is_compare = any(kw in question for kw in ["哪个更好", "哪个更", "对比", "比较", "怎么选", "区别", "差别",
                                                 "谁更好", "选哪个", "选哪款", "哪一款", "哪款更",
                                                 "和", "与", "还是", "vs", "VS", "还是说"])
    compare_guide = ""
    if is_compare:
        compare_guide = ("\n【特殊要求】这是一个对比类问题。即使两个商品的信息不完全对称，"
                        "也请根据现有内容大胆给出倾向性建议。明确指出各商品的优势，"
                        "然后给出你的推荐结论。不要因为信息不完整而拒绝回答。")

    return f"""你是电商智能导购助手。你必须回答用户的问题，绝对不能说"无法回答"、"不清楚"、"不知道"、"没有相关信息"等拒绝性短语。

规则：
1. 基于提供的知识库内容，给出你最好的回答
2. 如果信息有限，请用已有信息尽力帮助用户，列出你所知道的相关信息
3. 如果涉及预算，优先推荐价格最接近预算的商品，即使略超预算也明确说明
4. 回答要简洁准确，不要编造价格和规格
{budget_section}{compare_guide}
## 知识库内容
{knowledge_context}

## 用户问题
{question}

请基于以上知识库内容回答用户问题（禁止说无法回答）："""


def generate_with_quality_fallback(
    llm,
    prompt_manager,
    vector_store,
    retrieval_k: int,
    build_context_fn,
    question: str,
    knowledge_context: str,
    structured: bool = False,
    no_backoff: bool = False,
    budget_hint: str = "",
    sources: list = None,
    detected_filter: Optional[Dict[str, Any]] = None,
) -> dict:
    """生成回答 + 三级回退链：重试 → step_back → forced_answer

    从 RAGService.query() 中解耦，供 query() 和 Agent 路由复用。

    detected_filter: 意图+品类过滤，透传给 step_back_answer 防止跨品类漂移

    Returns:
        {"answer": str, "sources": list, "step_back_round": int, "generation_time": float}
    """
    import time

    if sources is None:
        sources = []

    generation_start = time.time()
    answer = generate_answer(llm, prompt_manager, question, knowledge_context, structured=structured, purpose="generation")

    step_back_round = 0
    if not no_backoff and is_low_quality_answer(answer):
        # 重试生成（temperature=0.2 微调打破确定性锁定）
        retry_answer = generate_answer(
            llm, prompt_manager, question, knowledge_context,
            structured=structured, temperature=0.2, purpose="gen_retry",
        )
        if not is_low_quality_answer(retry_answer):
            answer = retry_answer
            print(f"[生成重试] 首次回答低质量，重试后改善", flush=True)
        else:
            # 重试仍失败，进入完整 step_back 流程
            step_back_result = step_back_answer(
                llm, vector_store, retrieval_k, prompt_manager,
                question, build_context_fn, budget_hint=budget_hint,
                structured=structured, detected_filter=detected_filter,
            )
            step_back_round = step_back_result["step_back_round"]
            if step_back_result["answer"]:
                answer = step_back_result["answer"]
                sources = step_back_result["sources"]
            else:
                # 最终兜底：强制回答
                print(f"[强制回答] 步退回退全部失败，使用强制回答 prompt", flush=True)
                try:
                    forced = forced_answer_prompt(question, knowledge_context, budget_hint)
                    forced_answer = llm.chat(forced, temperature=0.2, max_tokens=512, purpose="gen_forced")
                    if forced_answer and len(forced_answer.strip()) >= 15:
                        answer = forced_answer
                        print(f"[强制回答] 已生成强制性回答 ({len(forced_answer)}字符)", flush=True)
                except Exception as e_final:
                    print(f"[强制回答] 最终兜底生成失败: {e_final}", flush=True)

    generation_time = time.time() - generation_start

    return {
        "answer": answer,
        "sources": sources,
        "step_back_round": step_back_round,
        "generation_time": generation_time,
    }


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
