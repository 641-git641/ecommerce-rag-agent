"""Agentic RAG 智能体主模块

基于 ReAct (Reasoning + Acting) 模式：
1. 意图分类 → 选择执行路径
2. ReAct 循环：LLM 决策 → 工具执行 → 观察 → 下一轮
3. 简单查询走快速路径，复杂意图走完整 ReAct

多轮对话支持：
- 接入 ConversationMemory 做槽位累积 + 查询增强
- 话题切换检测：问完跑鞋问衣服时自动清除旧品类上下文
- 模糊追问自动补全：上一轮说"跑鞋"，下一句"要轻量的"→ 搜索"跑鞋 轻量的"
"""

import json
import os
import time
import uuid
from typing import Any, Dict, Generator, List, Optional

from llm import LLMService
from rag_service import RAGService
from memory import get_memory

from .intent import classify_query, extract_compare_products
from .planner import decide, MAX_REACT_STEPS
from .tools import SearchTool, RecommendTool, CompareTool, ClarifyTool, ComboTool
from .cart_client import CartAPIClient
from .cart_tool import CartTool
from shared.stream_utils import _enrich_recommendations, _parse_stream_structured

# 实体桥接触发词：用户说这些词时注入图谱同款/搭配信息
ENTITY_BRIDGE_TRIGGERS = [
    "同款", "类似", "相似的", "差不多的", "替代", "替换", "还有吗",
    "还有什么", "还有别的", "有别的吗", "搭配", "配什么", "和什么",
    "再来一个", "其他的", "别的", "另外", "再推荐",
]


class Agent:
    """Agentic RAG 智能体（含多轮对话记忆）

    Args:
        rag_service: RAGService 实例
        llm_service: LLMService 实例
    """

    def __init__(self, rag_service: RAGService, llm_service: LLMService, ecommerce_graph=None, tts_service=None, cart_api_client: CartAPIClient = None):
        self.rag_service = rag_service
        self.llm = llm_service
        self.ecommerce_graph = ecommerce_graph
        self.tts_service = tts_service
        self.cart_api_client = cart_api_client
        self._tools: Dict[str, Any] = {}
        self._register_tools()

    def _register_tools(self):
        tools = [
            SearchTool(self.rag_service, self.llm),
            RecommendTool(self.rag_service, self.llm),
            CompareTool(self.rag_service, self.llm),
            ClarifyTool(self.rag_service, self.llm),
            ComboTool(self.rag_service, self.llm),
            CartTool(self.rag_service, self.llm, self.cart_api_client),
        ]
        for t in tools:
            self._tools[t.name()] = t

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any],
                      session_id: str) -> Dict[str, Any]:
        if tool_name not in self._tools:
            return {"success": False, "error": f"工具 '{tool_name}' 不存在", "output": ""}
        try:
            result = self._tools[tool_name].execute(tool_args, session_id)
            answer = result.get("answer", str(result))
            return {"success": True, "output": answer, "raw": result}
        except Exception as e:
            return {"success": False, "error": str(e), "output": f"工具执行失败: {e}"}

    # ── 多轮查询增强 ──────────────────────────────────────────

    def _enrich_query(self, query: str, session_id: str) -> str:
        """单会话内多轮查询增强：槽位累积 + 实体桥接"""
        memory = get_memory()

        # 槽位累积增强（含内置的话题切换检测和清除）
        enriched = memory.build_enriched_query(session_id, query)

        # 实体桥接增强：用户追问"同款/搭配/类似的"时，
        # 从知识图谱中找上轮推荐商品的同款/搭配，注入到查询中
        is_entity_followup = any(t in query for t in ENTITY_BRIDGE_TRIGGERS)
        if is_entity_followup:
            entity_ctx = memory.get_entity_context(session_id)
            if entity_ctx:
                enriched = f"【关联上下文】\n{entity_ctx}\n\n[用户需求] {enriched}"
                print(f"[Agent] 实体桥接注入: {len(entity_ctx)} 字符 | session={session_id}")

        if enriched != query:
            print(f"[Agent] 查询增强: '{query[:30]}' → '{enriched[:80]}' | session={session_id}")
        return enriched

    def _remember(self, session_id: str, query: str, answer: str, steps: List[Dict[str, Any]] = None):
        """一轮对话结束后更新记忆

        优先从 answer 的 JSON 中解析 recommendations，
        解析失败时从 steps 的 raw.cards 中提取商品ID。
        最后用图谱补全缺失的 product_id。
        """
        try:
            memory = get_memory()
            recs = _parse_stream_structured(answer).get("recommendations", [])
            if not recs and steps:
                # 从工具步骤的 raw.cards 中提取推荐商品（纯文本 answer 不含 JSON 时走这里）
                for step in steps:
                    for card in step.get("raw", {}).get("cards", []):
                        if isinstance(card, dict):
                            recs.append({
                                "product_id": str(card.get("product_id", "")),
                                "name": str(card.get("name", "")),
                                "price": card.get("price", 0),
                            })
                if recs:
                    print(f"[Agent] 记忆从raw.cards提取到 {len(recs)} 个商品 | session={session_id}")

            # 用知识图谱补全 product_id（LLM 产出常为空）
            if recs and self.ecommerce_graph:
                recs = _enrich_recommendations(recs, self.ecommerce_graph)
                enriched_count = sum(1 for r in recs if r.get("product_id"))
                if enriched_count > 0:
                    print(f"[Agent] 记忆图谱补全: {enriched_count}/{len(recs)} 个商品有 product_id | session={session_id}")

            memory.remember(session_id, query, answer, recs)
        except Exception as e:
            print(f"[Agent] 记忆更新失败: {e}")

    def _force_clarify(self, query: str, session_id: str, start_time: float) -> Dict[str, Any]:
        """模糊查询直接反问 — 绕过意图分类和 ReAct 循环

        在返回反问前，更新记忆：标记本轮是 clarify 模式，
        后续用户应答时自动合并上下文（如 "奥莱雅" + "护肤品" → 一起搜索）。
        """
        from .intent import extract_product_category

        memory = get_memory()
        memory_state = memory.get_slot_state(session_id)
        category = getattr(memory_state, "category", "") if memory_state else ""
        if not category:
            category = extract_product_category(query)

        # 记住本次 clarify 的查询内容，供下一轮合并
        memory.extract_slots(session_id, query)
        # 标记为 clarify 等待应答
        memory.append_summary(session_id, f"[clarify: 用户说'{query}'，已反问澄清]")

        prompt = f"""你是电商导购助手，当前用户提问信息严重不足，你必须反问用户来澄清需求。
你绝对不能推荐任何具体商品！你的唯一任务是提问。

用户说: {query}
已经知道的品类: {category or '无'}

请自然地问1-3个关键问题帮助用户明确需求。如果没有已知品类，第一个问题必须是问用户想看什么品类。
涉及的维度：品类、预算范围、品牌偏好、功能属性偏好、适用场景。

输出要求：
- 只输出反问文本，以问号结尾
- 不要推荐商品
- 不要提到商品名
- 不要输出解释、抱歉或前言
- 简洁友好，1-3句话"""

        try:
            answer = self.llm.chat(prompt, temperature=0.3, purpose="agent_clarify")
        except Exception as e:
            print(f"[Agent] _force_clarify LLM 降级: {e}")
            answer = f"请问您想看什么品类的商品呢？方便告诉我您的预算和偏好吗？"

        return {
            "answer": answer,
            "steps": [{
                "tool": "clarify",
                "description": "信息不足，反问用户澄清需求",
                "result": answer[:100],
                "raw": {},
            }],
            "used_tools": ["clarify"],
            "confidence": 0.9,
            "total_time": round(time.time() - start_time, 3),
            "_was_clarify": True,
        }

    # ── 购物车快速通道 ──────────────────────────────────────

    def _process_cart(self, query: str, session_id: str, start_time: float) -> Dict[str, Any]:
        """购物车操作直接走 CartTool，绕过 ReAct 和 LLM 决策

        支持的操作：
        - "加购物车" / "第二个加入购物车" → add（从上一轮推荐取商品信息）
        - "看看购物车" → view
        - "删除第二个" → remove
        - "把数量改成2" → update_qty
        - "清空购物车" → clear
        - "去结算"/"下单" → order_preview
        - "确认下单 地址：xxx" → order_confirm
        """
        cart_tool = self._tools.get("cart")
        if cart_tool is None:
            return {"answer": "购物车功能暂未启用", "steps": [], "used_tools": [], "confidence": 0}

        # ── 判断 action ──
        action = "view"
        product_ref = ""
        quantity = 1

        if any(kw in query for kw in ("加购物车", "加入购物车", "加到购物车", "加购", "买这个", "买它")):
            action = "add"
            # 提取序号/"这个"引用
            product_ref = self._extract_cart_product_ref(query)
        elif any(kw in query for kw in ("删除", "移除")):
            action = "remove"
            product_ref = self._extract_cart_product_ref(query)
        elif any(kw in query for kw in ("数量改成", "改成", "改为", "数量改为")):
            action = "update_qty"
            product_ref = self._extract_cart_product_ref(query)
            # 提取数量
            import re
            m = re.search(r'(\d+)', query)
            if m:
                quantity = int(m.group(1))
        elif any(kw in query for kw in ("清空",)):
            action = "clear"
        elif any(kw in query for kw in ("下单", "结算", "结账")):
            action = "order_preview"
        elif any(kw in query for kw in ("确认下单",)):
            action = "order_confirm"

        # ── 构建 tool_args ──
        tool_args = {"action": action}

        if action in ("add", "remove", "update_qty"):
            if product_ref:
                self._inject_product_from_memory(tool_args, session_id, product_ref)
            elif action == "add":
                # 没有序号引用（"第二个"），默认取上轮推荐的第 1 个
                self._inject_product_from_memory(tool_args, session_id, "1")
                # 内存无记录 → 尝试从 query 中【用户关注的商品】块解析
                if not tool_args.get("product"):
                    parsed = self._parse_attention_block(query)
                    if parsed:
                        # 从知识图谱查找 product_id
                        pid = self._find_product_id_by_name(parsed["name"], parsed["price"])
                        tool_args["product"] = {
                            "name": parsed["name"],
                            "product_id": pid,
                            "price": parsed["price"],
                        }
                        print(f"[Agent] cart 从关注块解析: name={parsed['name']} price={parsed['price']} pid={pid}")
            else:
                # remove/update_qty 无引用 → 默认取上轮推荐第 1 个
                self._inject_product_from_memory(tool_args, session_id, "1")
                if not tool_args.get("product"):
                    tool_args["product"] = query
            if action == "update_qty":
                tool_args["quantity"] = quantity

        if action == "order_confirm":
            tool_args["product"] = query  # LLM 会在 tool 里解析地址

        # ── 如果是 add 但没有从内存找到商品 → 反问用户 ──
        if action == "add" and not tool_args.get("product"):
            memory = get_memory()
            state = memory.get_slot_state(session_id)
            if state and state.category:
                return {
                    "answer": f"您想把哪个「{state.category}」加入购物车呢？请先告诉我您的具体需求，我帮您推荐几款再选择加购。",
                    "steps": [],
                    "used_tools": [],
                    "confidence": 0.9,
                    "total_time": round(time.time() - start_time, 3),
                }
            return {
                "answer": "请问您想把什么商品加入购物车呢？可以先告诉我您想看的品类和需求，我帮您推荐后再加购。",
                "steps": [],
                "used_tools": [],
                "confidence": 0.9,
                "total_time": round(time.time() - start_time, 3),
            }

        # ── 执行 cart 工具 ──
        try:
            result = cart_tool.execute(tool_args, session_id)
        except Exception as e:
            print(f"[Agent] cart 工具执行失败: {e}")
            return {"answer": f"购物车操作失败: {e}", "steps": [], "used_tools": [], "confidence": 0}

        answer = result.get("answer", "购物车已更新")
        return {
            "answer": answer,
            "steps": [{"tool": "cart", "description": f"购物车 {action}", "result": answer[:200], "raw": result}],
            "used_tools": ["cart"],
            "confidence": 0.95,
            "total_time": round(time.time() - start_time, 3),
        }

    def _extract_cart_product_ref(self, query: str) -> str:
        """从 query 中提取商品引用（序号或名称）"""
        chinese_nums = {
            "第一个": "1", "第二个": "2", "第三个": "3", "第四个": "4", "第五个": "5",
            "第一": "1", "第二": "2", "第三": "3", "第四": "4", "第五": "5",
        }
        for label, num in chinese_nums.items():
            if label in query:
                return num
        if "这个" in query:
            return "1"  # "这个" → 第一个推荐
        return ""

    def _inject_product_from_memory(self, tool_args: dict, session_id: str, ref: str):
        """从会话记忆中找到上一轮推荐的第 N 个商品，注入到 tool_args"""
        memory = get_memory()
        last_pids = memory.get_prev_round_pids(session_id)
        print(f"[Agent] cart 内存查询: session={session_id[:16]} pids={len(last_pids)} ref={ref}")
        if not last_pids:
            return

        try:
            idx = int(ref) - 1  # 1-based → 0-based
        except ValueError:
            return

        if idx < 0 or idx >= len(last_pids):
            return

        pid = last_pids[idx]
        # 从知识图谱获取商品名称和价格
        node_key = f"product:{pid}" if not pid.startswith("product:") else pid

        if self.ecommerce_graph and node_key in self.ecommerce_graph.nodes:
            props = self.ecommerce_graph.nodes[node_key].get("properties", {})
            tool_args["product"] = {
                "name": props.get("title", pid),
                "product_id": pid,
                "price": float(props.get("price", 0) or 0),
            }
            print(f"[Agent] cart 从记忆找到: idx={idx + 1}, product={props.get('title', pid)}")

    def _parse_attention_block(self, query: str) -> Optional[Dict[str, Any]]:
        """从 query 中的【用户关注的商品】块解析商品信息

        格式:
            【用户关注的商品】
            - 商品名 (¥价格) - 推荐理由

        Returns:
            {"name": str, "price": float} 或 None
        """
        marker = "【用户关注的商品】"
        idx = query.find(marker)
        if idx < 0:
            return None
        block = query[idx + len(marker):].strip()
        # 取第一行
        line = block.split("\n")[0].strip()
        if line.startswith("-"):
            line = line[1:].strip()
        # 解析: 商品名 (¥价格) - 推荐理由
        import re as _re
        m = _re.match(r"(.+?)\([¥￥](\d+\.?\d*)\)", line)
        if m:
            return {"name": m.group(1).strip(), "price": float(m.group(2))}
        # 备选：无 price 格式
        return {"name": line, "price": 0.0}

    def _find_product_id_by_name(self, name: str, price: float = 0.0) -> str:
        """从知识图谱中按商品名+价格匹配查找 product_id"""
        if not self.ecommerce_graph or not name:
            return ""
        name_lower = name.lower()
        best_pid = ""
        best_score = 0
        for node_id, node in self.ecommerce_graph.nodes.items():
            if not node_id.startswith("product:"):
                continue
            props = node.get("properties", {})
            g_name = (props.get("title", "") or "").lower()
            g_price = float(props.get("price", 0) or 0)
            g_pid = props.get("product_id", "")
            # 名称分数：子串匹配越长越好
            if name_lower in g_name or g_name in name_lower:
                score = min(len(name_lower), len(g_name))
                # 价格匹配加分
                if price > 0 and g_price > 0 and abs(g_price - price) < 0.5:
                    score += 100
                if score > best_score:
                    best_score = score
                    best_pid = g_pid
        return best_pid

    # ── 主处理入口 ────────────────────────────────────────────

    def process(self, query: str, session_id: str = "default", force_clarify: bool = False, pre_classified_intent: str = "") -> Dict[str, Any]:
        """处理用户查询（含多轮上下文增强）

        Args:
            query: 用户查询文本
            session_id: 会话 ID（用于多轮上下文检索）
            pre_classified_intent: 外部已分类的意图，非空时跳过内部 classify_query

        Returns:
            {"answer": "...", "steps": [...], "used_tools": [...], "confidence": float}
        """
        start_time = time.time()

        if force_clarify:
            return self._force_clarify(query, session_id, start_time)

        # 1. 多轮查询增强（槽位累积/话题切换/模糊追问补全）
        enriched_query = self._enrich_query(query, session_id)

        # 2. 意图分类（优先使用外部传入的预分类结果）
        intent = pre_classified_intent if pre_classified_intent else classify_query(enriched_query)
        print(f"[Agent] 意图: {intent} | session={session_id} | raw='{query[:40]}' enriched='{enriched_query[:40]}'")

        steps: List[Dict[str, Any]] = []
        used_tools: set = set()

        # 3. 按意图走不同路径
        if intent == "cart":
            result = self._process_cart(enriched_query, session_id, start_time)
        elif intent == "compare":
            result = self._process_compare(enriched_query, session_id, steps, used_tools, start_time, intent=intent)
        elif intent == "combo":
            result = self._process_combo(enriched_query, session_id, steps, used_tools, start_time)
        elif intent in ("simple", "exclude"):
            result = self._process_fast(enriched_query, session_id, steps, used_tools, start_time)
        else:
            result = self._process_react(enriched_query, session_id, steps, used_tools, start_time, intent=intent)

        # 4. 更新多轮记忆
        self._remember(session_id, query, result.get("answer", ""), result.get("steps", []))

        # 5. 附加可观测性信息
        result["intent"] = intent
        result["path"] = intent if intent in ("cart", "compare", "combo", "simple", "exclude") else "react"
        result["llm_tokens"] = self.llm.last_tokens.copy() if self.llm.last_tokens else {}
        result["total_time"] = round(time.time() - start_time, 4)

        return result

    def process_stream(self, query: str, session_id: str = "default", force_clarify: bool = False, pre_classified_intent: str = "", enable_tts: bool = False) -> Generator:
        """Agent 编排流式输出（仅 compare/combo/complex 路径）

        SSE 事件与 RAG rag_stream_events 完全一致:
            session → chunk(answer_text) → cards(enriched) → voice → done

        simple/exclude 不经过此方法，由 api.py 直接委托给 RAG 管线。
        force_clarify=True 时跳过意图分类，直接反问用户。
        """
        t0 = time.time()
        yield f"data: {json.dumps({'type': 'waiting', 'content': '正在为您查找相关信息...'}, ensure_ascii=False)}\n\n"

        result = self.process(query, session_id, force_clarify=force_clarify, pre_classified_intent=pre_classified_intent)

        raw_answer = result.get("answer", "")
        steps = result.get("steps", [])

        # 解析工具产出的结构化 JSON，提取纯文本 answer_text（与 RAG 一致）
        parsed = _parse_stream_structured(raw_answer)
        answer_text = parsed["answer_text"] or raw_answer
        voice_text = parsed.get("voice_friendly", "")
        stripped_recommendations = parsed.get("recommendations", [])

        yield f"data: {json.dumps({'type': 'session', 'sid': session_id}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'chunk', 'content': answer_text}, ensure_ascii=False)}\n\n"

        # 优先取工具 raw.cards 中的卡片（含 RAG 原始 product_id）；否则用 parsed 中的
        all_cards: List[Dict] = []
        for step in steps:
            for card in step.get("raw", {}).get("cards", []):
                all_cards.append(card)
        if not all_cards:
            all_cards = stripped_recommendations

        if all_cards:
            enriched_cards = _enrich_recommendations(all_cards, self.ecommerce_graph)
            yield f"data: {json.dumps({'type': 'cards', 'cards': enriched_cards}, ensure_ascii=False)}\n\n"

        tts_url = ""
        if enable_tts and self.tts_service is not None and voice_text:
            try:
                audio_path = self.tts_service.synthesize(
                    voice_text, output_filename=f"tts_{uuid.uuid4().hex}.mp3"
                )
                if audio_path:
                    tts_url = f"/voice/playback/{os.path.basename(audio_path)}"
                else:
                    print("[Agent TTS] 合成返回空路径", flush=True)
            except Exception as e:
                print(f"[Agent TTS] 合成失败: {e}", flush=True)
        yield f"data: {json.dumps({'type': 'voice', 'url': tts_url, 'text': voice_text}, ensure_ascii=False)}\n\n"

        rag_time = time.time() - t0
        agent_breakdown = {
            "intent": result.get("intent", ""),
            "path": result.get("path", ""),
            "steps_n": len(steps),
            "used_tools": list(result.get("used_tools", set())),
        }
        yield f"data: {json.dumps({'type': 'done', 'timing': {'rag_time': round(rag_time, 3), 'total': round(time.time() - t0, 3), 'breakdown': agent_breakdown}}, ensure_ascii=False)}\n\n"

    # ── 路径实现 ──────────────────────────────────────────────

    def _process_fast(self, query, session_id, steps, used_tools, start_time):
        print(f"[Agent] 快速路径: search → finish")

        step = self._execute_tool("search", {"query": query}, session_id)
        steps.append({"tool": "search", "success": step["success"], "output_preview": step["output"][:200], "raw": step.get("raw", {})})
        if step["success"]:
            used_tools.add("search")

        answer = step["output"] if step["success"] else "抱歉，暂时无法处理您的请求，请稍后重试。"
        confidence = 0.85 if step["success"] else 0.3

        duration = time.time() - start_time
        print(f"[Agent] 快速路径完成 | duration={duration:.2f}s | confidence={confidence}")

        return {
            "answer": answer,
            "steps": steps,
            "used_tools": sorted(used_tools),
            "confidence": confidence,
        }

    def _process_compare(self, query, session_id, steps, used_tools, start_time, intent=""):
        print("[Agent] 对比路径: search → compare → finish")
        products = extract_compare_products(query)

        if not products or len(products) < 2:
            # 无法从查询中提取产品名 → 先搜索候选商品
            print("[Agent] 对比路径: 未提取到产品名，先搜索候选商品...")
            step = self._execute_tool("search", {"query": query}, session_id)
            steps.append({"tool": "search", "success": step["success"],
                          "output_preview": step["output"][:200], "raw": step.get("raw", {})})
            if step["success"]:
                used_tools.add("search")
                cards = step.get("raw", {}).get("cards", [])
                products = [c["name"] for c in cards[:2] if c.get("name")]
                print(f"[Agent] 对比路径: 从搜索结果提取候选商品: {products}")

            if not products or len(products) < 2:
                # 仍不够，fallback 到 ReAct
                return self._process_react(query, session_id, steps, used_tools, start_time, intent=intent)

        # 提取品类上下文，帮助 CompareTool 限定搜索范围（如 "跑鞋" + "Nike"）
        from .intent import extract_product_category
        category = extract_product_category(query)
        step = self._execute_tool("compare", {"products": products, "category": category}, session_id)
        steps.append({"tool": "compare", "success": step["success"],
                      "output_preview": step["output"][:200], "raw": step.get("raw", {})})
        if step["success"]:
            used_tools.add("compare")

        answer = step["output"]
        duration = time.time() - start_time
        print(f"[Agent] 对比路径完成 | duration={duration:.2f}s")

        return {
            "answer": answer,
            "steps": steps,
            "used_tools": sorted(used_tools),
            "confidence": 0.88,
        }

    def _process_combo(self, query, session_id, steps, used_tools, start_time):
        print("[Agent] 组合路径: combo → finish")

        step = self._execute_tool("combo", {"scenario": query}, session_id)
        steps.append({"tool": "combo", "success": step["success"], "output_preview": step["output"][:200], "raw": step.get("raw", {})})
        if step["success"]:
            used_tools.add("combo")

        answer = step["output"] if step["success"] else "抱歉，暂时无法提供组合方案。"
        duration = time.time() - start_time
        print(f"[Agent] 组合路径完成 | duration={duration:.2f}s")

        return {
            "answer": answer,
            "steps": steps,
            "used_tools": sorted(used_tools),
            "confidence": 0.85,
        }

    def _reflect_on_result(self, query: str, action: str, step_result: dict, history: list) -> str:
        """ReAct 反思：工具执行后评估结果是否能回答用户问题

        与 decorate (决策) 分离，反思只判断"这个结果够不够用"，
        不负责选择下一步行动（交给下一轮 decorate 处理）。

        Returns:
            反思标注字符串，嵌入历史供下一轮决策参考。空字符串表示结果充分。
        """
        output = step_result.get("output", "")[:300]
        if not output or len(output) < 10:
            return "结果为空，应换策略"

        prompt = f"""评估这个工具执行结果是否能回答用户问题。只需回答 YES 或 NO。

用户问题：{query}
使用的工具：{action}
工具返回结果：{output}

这个结果是否能回答用户问题？YES 还是 NO？"""
        try:
            raw = self.llm.chat(prompt, temperature=0.0, max_tokens=64, purpose="react_reflect")
            if raw.strip().upper().startswith("NO"):
                return f"结果不足以回答用户问题，应尝试其他工具或策略"
        except Exception as e:
            print(f"[ReAct反思] LLM评估失败: {e}")
        return ""

    def _process_react(self, query, session_id, steps, used_tools, start_time, intent=""):
        print(f"[Agent] ReAct 路径: 最多 {MAX_REACT_STEPS} 轮")

        history: List[Dict[str, Any]] = []
        final_answer = ""

        for step_idx in range(MAX_REACT_STEPS):
            decision = decide(self.llm, query, history, intent=intent)
            if decision is None:
                break

            action = decision.get("action", "finish")
            tool_args = decision.get("tool_args", {})
            reason = decision.get("reason", "")

            print(f"[Agent] ReAct 第{step_idx + 1}轮: action={action} reason={reason}")

            if action == "finish":
                final_answer = decision.get("answer", "")
                break

            step_result = self._execute_tool(action, tool_args, session_id)
            step_success = step_result["success"]
            steps.append({
                "tool": action,
                "success": step_success,
                "output_preview": step_result["output"][:200],
                "raw": step_result.get("raw", {}),
            })
            if step_success:
                used_tools.add(action)

            # ── ReAct 反思：工具执行后评估结果是否足以回答用户 ──
            reflection_note = ""
            if step_idx < MAX_REACT_STEPS - 1 and not step_result.get("is_clarifying"):
                reflection_note = self._reflect_on_result(query, action, step_result, history)

            history.append({
                "action": action,
                "input": str(tool_args),
                "output": step_result["output"][:500],
                "reflection": reflection_note,
            })

        if not final_answer:
            final_answer = "抱歉，我暂时无法完成您的请求，请换个方式描述您的需求。"

        duration = time.time() - start_time
        print(f"[Agent] ReAct 完成 | duration={duration:.2f}s | steps={len(steps)} | tools={used_tools}")

        return {
            "answer": final_answer,
            "steps": steps,
            "used_tools": sorted(used_tools),
            "confidence": 0.9 if len(steps) > 0 and final_answer else 0.3,
        }
