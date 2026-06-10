"""Agent 工具集：search, recommend, compare, clarify, combo

每个工具通过 RAGService / LLMService 执行具体操作。
工具不负责决策，只负责执行并返回结果。

子模块：
  cart_client.py  — CartAPIClient（HTTP 客户端，对接 Go 服务）
  cart_tool.py    — CartTool（购物车管理）
  parsers.py      — JSON 解析工具函数
"""

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from .parsers import extract_answer_text, parse_cards_from_answer


# ============================================================
# 工具基类 + 5 个搜索/推荐工具
# ============================================================

class AgentToolBase:
    """工具基类"""

    def __init__(self, rag_service, llm_service):
        self.rag_service = rag_service
        self.llm = llm_service

    def name(self) -> str:
        raise NotImplementedError

    def description(self) -> str:
        raise NotImplementedError

    def execute(self, params: Dict[str, Any], session_id: str = "default") -> Dict[str, Any]:
        raise NotImplementedError


class SearchTool(AgentToolBase):
    def name(self): return "search"

    def description(self):
        return "搜索商品信息，参数: {\"query\":\"搜索关键词\"}"

    def execute(self, params, session_id="default"):
        query = params.get("query", "")
        if not query:
            return {"error": "缺少 query 参数"}

        result = self.rag_service.query(query, structured=True, no_backoff=True)
        raw_answer = result.get("answer", "")

        clean_text = extract_answer_text(raw_answer)
        cards = parse_cards_from_answer(raw_answer)

        return {
            "answer": clean_text,
            "raw_answer": raw_answer,
            "sources": result.get("sources", []),
            "search_time": result.get("search_time", 0),
            "cards": cards,
            "type": "search",
        }


class RecommendTool(AgentToolBase):
    def name(self): return "recommend"

    def description(self):
        return "根据条件推荐最佳商品，参数: {\"criteria\":\"推荐标准\",\"budget\":\"预算(可选)\",\"scenario\":\"场景(可选)\"}"

    def execute(self, params, session_id="default"):
        criteria = params.get("criteria", "")
        budget = params.get("budget", "")
        scenario = params.get("scenario", "")

        prompt_parts = [criteria]
        if budget:
            prompt_parts.append(f"预算{budget}")
        if scenario:
            prompt_parts.append(f"适用于{scenario}")
        query = " ".join(prompt_parts) if any(prompt_parts) else "推荐商品"

        result = self.rag_service.query(query, structured=True, no_backoff=True)
        raw_answer = result.get("answer", "")
        return {
            "answer": extract_answer_text(raw_answer),
            "raw_answer": raw_answer,
            "cards": parse_cards_from_answer(raw_answer),
            "criteria": criteria,
            "budget": budget,
            "scenario": scenario,
            "type": "recommend",
        }


class CompareTool(AgentToolBase):
    def name(self): return "compare"

    def description(self):
        return "对比商品，参数: {\"products\":[\"商品A\",\"商品B\"]}"

    def execute(self, params, session_id="default"):
        products = params.get("products", [])
        if isinstance(products, str):
            products = [p.strip() for p in products.split(",") if p.strip()]
        if len(products) < 2:
            return {"error": "对比至少需要2个商品"}

        p1, p2 = products[0], products[1]

        def _search_one(p):
            return self.rag_service.query(f"{p} 详细信息 规格 价格 品类", structured=True, no_backoff=True, skip_query_expansion=True, skip_generation=True)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(_search_one, p1)
            f2 = executor.submit(_search_one, p2)
            r1 = f1.result()
            r2 = f2.result()

        info1 = extract_answer_text(r1.get("answer", ""))
        info2 = extract_answer_text(r2.get("answer", ""))

        # ── 从原始检索文本中提取 product_id（skip_generation 后 answer 为纯文本，非 JSON）──
        import re as _re

        def _extract_pid_from_context(text: str) -> str:
            """从 RAG 检索上下文中提取第一个 product_id"""
            m = _re.search(r'商品ID[：:]\s*(p_[a-z]+_\d{3})', text)
            return m.group(1) if m else ""

        pid1 = _extract_pid_from_context(r1.get("answer", ""))
        pid2 = _extract_pid_from_context(r2.get("answer", ""))

        cards = []
        if pid1:
            cards.append({"product_id": pid1, "name": p1, "price": 0})
        if pid2:
            cards.append({"product_id": pid2, "name": p2, "price": 0})

        no_info1 = not info1 or len(info1) < 20
        no_info2 = not info2 or len(info2) < 20
        if no_info1 or no_info2:
            answer = json.dumps({
                "answer_text": (
                    f"抱歉，无法对比「{p1}」和「{p2}」"
                    + ("（未找到「{p1}」的相关信息）。" if no_info1 else "。")
                ),
                "recommendations": [],
                "voice_friendly": f"抱歉，无法对比{p1}和{p2}",
            }, ensure_ascii=False)
            return {
                "answer": answer,
                "product1": p1, "product2": p2,
                "cards": cards,
                "type": "compare",
            }

        prompt = f"""你是电商商品对比助手。请基于以下两个商品信息生成结构化对比，输出严格JSON。

## 商品1
{info1[:400]}

## 商品2
{info2[:400]}

输出一个JSON对象（不要markdown代码块包裹）:
{{
  "answer_text": "对比分析结果。聚焦关键差异，用简洁中文，每段不超过3行。格式：先一句话结论，再分点列出核心差异。即使两者定位相近，也要找出一两处细微差别。永远不要拒绝对比。",
  "recommendations": [
    {{"product_id": "", "name": "从商品1信息中提取的真实商品全名", "price": 商品1的价格数字, "reason": "一句话优势"}},
    {{"product_id": "", "name": "从商品2信息中提取的真实商品全名", "price": 商品2的价格数字, "reason": "一句话优势"}}
  ],
  "voice_friendly": "对比摘要，不超过60字"
}}
重要：recommendations 中的 name 和 price 必须来自上面检索到的真实商品信息，不要编造。"""
        answer = self.llm.chat(prompt, temperature=0.3, max_tokens=1024)

        return {
            "answer": answer,
            "product1": p1, "product2": p2,
            "cards": cards,
            "type": "compare",
        }


class ClarifyTool(AgentToolBase):
    def name(self): return "clarify"

    def description(self):
        return "信息不足时反问用户，参数: {\"query\":\"用户原问题\"}"

    def execute(self, params, session_id="default"):
        from .intent import extract_product_category

        query = params.get("query", "")

        prompt = f"""你是电商导购助手。当前用户提问信息不足，需要反问以获取关键决策维度。

用户说: {query}

你需要自然地问1-3个关键问题，帮助用户明确需求。涉及维度：预算、品牌偏好、功能/属性偏好、适用场景。
直接输出反问文本，不要前缀。保持友好、简洁的中文风格。"""

        answer = self.llm.chat(prompt, temperature=0.3)
        return {
            "answer": answer,
            "is_clarifying": True,
            "category": extract_product_category(query),
            "type": "clarify",
        }


class ComboTool(AgentToolBase):
    def name(self): return "combo"

    def description(self):
        return "跨品类组合推荐，参数: {\"scenario\":\"场景描述\"}"

    def execute(self, params, session_id="default"):
        scenario = params.get("scenario", "")

        mapping = {
            "度假": ["防晒霜", "速干T恤", "背包", "徒步鞋", "面霜"],
            "旅行": ["背包", "功能饮料", "徒步鞋", "防晒霜", "速干T恤"],
            "爬山": ["徒步鞋", "背包", "功能饮料", "防晒霜", "速干T恤"],
            "户外": ["防晒霜", "徒步鞋", "背包", "速干T恤", "运动长裤"],
            "办公": ["笔记本电脑", "真无线耳机", "智能手机", "平板电脑"],
            "运动": ["徒步鞋", "速干T恤", "运动长裤", "真无线耳机"],
        }
        categories = []
        for key, cats in mapping.items():
            if key in scenario:
                categories = cats
                break
        if not categories:
            categories = ["智能手机", "真无线耳机", "笔记本电脑"]

        # ── 并行检索所有品类（原来串行是超时根因）──
        import time as _time
        import re as _re
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _extract_pid(text: str) -> str:
            """从 RAG 检索上下文中提取第一个 product_id"""
            m = _re.search(r'商品ID[：:]\s*(p_[a-z]+_\d{3})', text)
            return m.group(1) if m else ""

        def _search_category(cat: str):
            t0 = _time.time()
            r = self.rag_service.query(
                f"为'{scenario}'场景推荐{cat}，列出商品名、价格、推荐理由",
                structured=True, no_backoff=True, skip_query_expansion=True, skip_generation=True,
            )
            raw_answer = r.get("answer", "")
            text = extract_answer_text(raw_answer)
            pid = _extract_pid(raw_answer)
            elapsed = round((_time.time() - t0) * 1000)
            print(f"[ComboTool] 并行检索 '{cat}' 完成 | {elapsed}ms | pid={pid}")
            return cat, text, pid

        category_results = {}
        all_cards = []
        max_workers = min(len(categories), 5)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_search_category, cat): cat for cat in categories}
            for future in as_completed(futures):
                try:
                    cat, text, pid = future.result()
                    category_results[cat] = text
                    if pid:
                        all_cards.append({"product_id": pid, "name": cat, "price": 0})
                except Exception as e:
                    cat = futures[future]
                    print(f"[ComboTool] 并行检索 '{cat}' 失败: {e}")
                    category_results[cat] = ""

        prompt = f"""你是电商导购助手。用户正在准备: {scenario}

以下是各品类搜索结果，请组织为跨品类组合推荐，输出严格JSON。

"""
        for cat, answer in category_results.items():
            prompt += f"## {cat}\n{answer[:200]}\n\n"

        prompt += f"""输出一个JSON对象（不要markdown代码块包裹）:
{{
  "answer_text": "组合推荐方案。先一句话总结场景适用性。然后每个品类单独一行，格式：品类名 — 商品名 — 核心卖点。不要用 --- 拼接，每行一个品类。总字数控制在200字内。\\n\\n示例格式：\\n户外徒步组合，兼顾防护与轻便。\\n防晒霜 — 安热沙金瓶 — 高倍防水\\n帽子 — Osprey渔夫帽 — 遮阳透气",
  "recommendations": [
    {{"product_id": "", "name": "从搜索结果中提取的真实商品全名", "price": 0, "reason": "8字内卖点"}}
  ],
  "voice_friendly": "组合摘要，不超过60字"
}}
重要：recommendations 中的 name 必须来自上面各品类搜索结果中的真实商品名，不要编造或使用简称。每个品类至少推荐一个商品。"""
        final_answer = self.llm.chat(prompt, temperature=0.3, max_tokens=1024)

        return {
            "answer": final_answer,
            "scenario": scenario,
            "categories": categories,
            "search_data": category_results,
            "cards": all_cards,
            "type": "combo",
        }
