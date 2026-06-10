"""Agent FastAPI 路由：/agent/query、/agent/stream、/agent/vision

由 Go 网关代理调用。

路由逻辑：
  simple/exclude → 直接走 RAG 的 rag_stream_events()（模糊查询自动升级为 clarify）
  compare/combo/complex → Agent 编排路径
  /agent/vision → 多模态拍照找货
"""

import json
import os
import time
import uuid


from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .intent import classify_query, is_vague_query
from .graph_filter import build_graph_filter
from api.routes import rag_stream_events, _enrich_recommendations, _save_session_message
from memory import get_memory


class AgentQueryRequest(BaseModel):
    query: str                                          # 用户问题
    session_id: str = "default"                         # 会话 ID


class AgentQueryResponse(BaseModel):
    answer: str
    steps: list = []
    used_tools: list = []
    confidence: float = 0.85
    total_time: float = 0


# ── 全局引用，在 main.py 中注入 ──
agent_instance = None
_rag_service = None
_tts_service = None
_ecommerce_graph = None
_vision_service = None


def init_agent(agent):
    """初始化全局 Agent 实例"""
    global agent_instance
    agent_instance = agent


def init_services(rag_service, tts_service, ecommerce_graph, vision_service=None):
    """注册 RAG 管线所需的服务"""
    global _rag_service, _tts_service, _ecommerce_graph, _vision_service
    _rag_service = rag_service
    _tts_service = tts_service
    _ecommerce_graph = ecommerce_graph
    _vision_service = vision_service


# ── Router ────────────────────────────────────────────────────

def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/query")
    async def agent_query(req: AgentQueryRequest):
        """Agent 非流式查询"""
        if agent_instance is None:
            return AgentQueryResponse(answer="Agent 服务未初始化", confidence=0)

        sid = req.session_id
        if not sid or sid == "default":
            sid = str(uuid.uuid4())

        t0 = time.time()
        intent = classify_query(req.query)
        result = agent_instance.process(req.query, sid, pre_classified_intent=intent)
        result["total_time"] = round(time.time() - t0, 4)
        result["session_id"] = sid
        return result

    @router.post("/stream")
    async def agent_stream(req: AgentQueryRequest):
        """Agent SSE 流式查询

        cart → 直接执行购物车操作，不绕 ReAct
        simple/exclude → 先过 Agent 查询增强 → 图谱硬过滤 → RAG 渲染
        模糊查询(simple + is_vague) → 自动升级为 Agent 编排 (走 clarify)
        compare/combo/complex → Agent 编排
        """
        if agent_instance is None or _rag_service is None:
            async def error_stream():
                yield f"data: {json.dumps({'type': 'error', 'content': '服务未初始化'}, ensure_ascii=False)}\n\n"
            return StreamingResponse(error_stream(), media_type="text/event-stream")

        intent = classify_query(req.query)
        sid = req.session_id

        # ── 清洗：剥离 Android 传入的【用户关注的商品】元数据块（非用户自然语言）──
        clean_query = req.query
        attention_start = clean_query.find("【用户关注的商品】")
        if attention_start >= 0:
            clean_query = clean_query[:attention_start].strip()
            # 重新用清洗后的查询做意图分类（清洗后可能从 compare 变 simple）
            intent = classify_query(clean_query) if clean_query != req.query else intent

        # 自动生成会话 ID（空/default → 创建新会话，确保购物车/记忆持续可用）
        if not sid or sid == "default":
            sid = str(uuid.uuid4())
            print(f"[Agent] 自动创建会话: {sid}")

        # ── 购物车快速通道 → 直接执行，不走 ReAct ──
        if intent == "cart":
            async def event_stream():
                yield f"data: {json.dumps({'type': 'waiting', 'content': '正在为您查找相关信息...'}, ensure_ascii=False)}\n\n"
                result = agent_instance.process(req.query, sid, pre_classified_intent=intent)
                answer = result.get("answer", "")
                yield f"data: {json.dumps({'type': 'session', 'sid': sid}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'chunk', 'content': answer}, ensure_ascii=False)}\n\n"
                # 购物车操作结果也尝试传递 cart 状态
                steps = result.get("steps", [])
                cart_data = steps[0].get("raw", {}).get("cart", {}) if steps else {}
                if cart_data:
                    yield f"data: {json.dumps({'type': 'cart_update', 'cart': cart_data}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'timing': {'total': result.get('total_time', 0)}}, ensure_ascii=False)}\n\n"
                # 回写 Go 会话存储
                if sid and sid != "default":
                    _save_session_message(sid, "user", req.query)
                    _save_session_message(sid, "assistant", answer)
            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
            )

        # ── 模糊查询主动反问升级 ──
        # 但如果上一轮是 clarify 应答，则不再升级，直接走搜索
        memory = get_memory()
        was_clarify_recent = False
        clarify_original_query = ""
        last_summary = memory.get_last_summary(sid)
        if last_summary and "[clarify:" in last_summary:
            was_clarify_recent = True
            # 从摘要中提取 clarify 时的原始查询（如 "徒步鞋"），注入到后续应答的搜索上下文中
            import re as _re
            m = _re.search(r"用户说'([^']*)'", last_summary)
            if m:
                clarify_original_query = m.group(1).strip()
            memory.pop_last_summary(sid)

        if intent == "simple" and is_vague_query(clean_query) and not was_clarify_recent:
            print(f"[Agent] 模糊查询升级: '{clean_query[:30]}' → clarify 路径 | sid={sid}")
            async def event_stream():
                answer_parts = []
                for event in agent_instance.process_stream(clean_query, sid, force_clarify=True):
                    yield event
                    try:
                        data = json.loads(event.removeprefix("data: "))
                        if data.get("type") == "chunk":
                            answer_parts.append(data.get("content", ""))
                    except Exception:
                        pass
                if sid and sid != "default":
                    _save_session_message(sid, "user", req.query)
                    _save_session_message(sid, "assistant", "".join(answer_parts))
            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
            )

        if intent in ("simple", "exclude"):
            # ── 查询增强 ──
            enhanced = agent_instance._enrich_query(clean_query, sid)

            # ── clarify 应答：将原始查询品类注入到搜索上下文中 ──
            if was_clarify_recent and clarify_original_query:
                enhanced = f"{clarify_original_query} {enhanced}"
                clean_query = f"用户之前想看「{clarify_original_query}」类商品，现在说：{clean_query}"
                print(f"[Agent] clarify 上下文注入: '{clarify_original_query}' → 搜索增强 | sid={sid}")

            # ── 图谱硬过滤 ──
            memory = get_memory()
            memory_state = memory.get_slot_state(sid)

            # ── 多样性/排除追问：硬排除上一轮商品 + 品牌级硬排除 ──
            diversity_kw = ("另外", "其他", "别的", "不一样", "不同",
                            "换一个", "换一款", "换别的", "还有吗", "还有什么", "还有别的",
                            "再来", "再推", "再推荐", "除了这个", "除了这些", "除了")
            prev_brand = ""
            if any(kw in clean_query for kw in diversity_kw):
                enhanced = f"{enhanced} 不要重复推荐之前出现过的品牌 推荐完全不同的商品"
                prev_brand = getattr(memory_state, "brand", "") if memory_state else ""
                if prev_brand:
                    print(f"[Agent] 多样性追问: 排除品牌 '{prev_brand}' | sid={sid}")
                    if memory_state:
                        memory_state.brand = ""

            graph_filter = build_graph_filter(clean_query, memory_state, _ecommerce_graph)

            # 有过滤条件但无候选商品 → 跳过 RAG，直接返回空结果
            is_empty_filter = (
                isinstance(graph_filter, dict) and isinstance(graph_filter.get("product_id"), dict)
                and "$in" in graph_filter["product_id"] and not graph_filter["product_id"]["$in"]
            )
            if is_empty_filter:
                cat = getattr(memory_state, "category", "") if memory_state else ""
                budget = getattr(memory_state, "budget_max", 0) if memory_state else 0
                if budget > 0 and cat:
                    empty_msg = f"抱歉，当前数据库中没有 {budget} 元以内的{cat}，请尝试放宽预算或更换品类。"
                else:
                    empty_msg = "抱歉，未找到符合您筛选条件的商品。"
                async def empty_stream():
                    yield f"data: {json.dumps({'type': 'session', 'sid': sid}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'chunk', 'content': empty_msg}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'cards', 'cards': []}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'voice', 'url': '', 'text': empty_msg}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'timing': {}}, ensure_ascii=False)}\n\n"
                return StreamingResponse(
                    empty_stream(), media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
                )

            graph_filter = graph_filter or {}

            # 硬排除：把上一轮推荐过的商品ID + 同品牌所有商品从过滤中移除
            if prev_brand:
                exclude_pids = set()
                # a) 从 product_memory 取上轮推荐的 ID
                prev_rounds = memory.get_prev_round_pids(sid)
                if prev_rounds:
                    exclude_pids |= set(prev_rounds)
                    memory.clear_product_memory(sid)

                # b) 从知识图谱查找同品牌的所有商品
                if _ecommerce_graph:
                    brand_lower = prev_brand.lower()
                    for node_id, node in _ecommerce_graph.nodes.items():
                        if not node_id.startswith("product:"):
                            continue
                        g_brand = (node.get("properties", {}).get("brand_name", "") or "").lower()
                        g_title = (node.get("properties", {}).get("title", "") or "").lower()
                        g_pid = node.get("properties", {}).get("product_id", "")
                        if brand_lower in g_brand or brand_lower in g_title:
                            if g_pid:
                                exclude_pids.add(g_pid)

                if exclude_pids:
                    pid_filter = graph_filter.setdefault("product_id", {})
                    if "$in" in pid_filter:
                        before = len(pid_filter["$in"])
                        pid_filter["$in"] = [p for p in pid_filter["$in"] if p not in exclude_pids]
                        after = len(pid_filter["$in"])
                        print(f"[Agent] 多样性追问: 硬排除品牌 '{prev_brand}' 的商品IDs={exclude_pids} (从$in移除, {before}→{after}) | sid={sid}")
                    elif "$nin" in pid_filter:
                        pid_filter["$nin"] = list(set(pid_filter["$nin"] + list(exclude_pids)))
                        print(f"[Agent] 多样性追问: 硬排除品牌 '{prev_brand}' 的商品IDs={exclude_pids} | sid={sid}")
                    else:
                        pid_filter["$nin"] = list(exclude_pids)
                        print(f"[Agent] 多样性追问: 硬排除品牌 '{prev_brand}' 的商品IDs={exclude_pids} | sid={sid}")

            if not graph_filter:
                graph_filter = None

            history: list = []
            return StreamingResponse(
                rag_stream_events(
                    rag_service=_rag_service,
                    question=clean_query,
                    session_id=sid,
                    memory=memory,
                    chat_history=history,
                    filter=graph_filter,
                    tts_service=_tts_service,
                    ecommerce_graph=_ecommerce_graph,
                    enriched_query=enhanced,
                    skip_query_expansion=True,  # simple 不扩展查询
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
            )

        # compare / combo / complex → Agent 编排
        async def event_stream():
            answer_parts = []
            for event in agent_instance.process_stream(clean_query, sid, pre_classified_intent=intent):
                yield event
                # 收集 answer_text 用于回写
                try:
                    data = json.loads(event.removeprefix("data: "))
                    if data.get("type") == "chunk":
                        answer_parts.append(data.get("content", ""))
                except Exception:
                    pass
            # 回写 Go 会话存储
            if sid and sid != "default":
                final_answer = "".join(answer_parts)
                _save_session_message(sid, "user", req.query)
                _save_session_message(sid, "assistant", final_answer)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    @router.post("/vision")
    async def agent_vision(
        image: UploadFile = File(...),
        query: str = Form(""),
        session_id: str = Form("default"),
    ):
        """多模态拍照找货 — 上传图片 + 可选文字描述，返回推荐商品

        SSE 事件序列与 RAG 一致: session → chunk → cards → voice → done
        """
        if _vision_service is None or _rag_service is None:
            async def error_stream():
                yield f"data: {json.dumps({'type': 'error', 'content': '视觉服务未初始化'}, ensure_ascii=False)}\n\n"
            return StreamingResponse(error_stream(), media_type="text/event-stream")

        # 自动生成会话 ID
        if not session_id or session_id == "default":
            session_id = str(uuid.uuid4())
            print(f"[Vision] 自动创建会话: {session_id}")

        # 保存上传图片到临时文件
        upload_dir = "data/uploads"
        os.makedirs(upload_dir, exist_ok=True)
        ext = os.path.splitext(image.filename or "img.jpg")[1] or ".jpg"
        tmp_path = os.path.join(upload_dir, f"vision_{uuid.uuid4().hex}{ext}")
        contents = await image.read()
        with open(tmp_path, "wb") as f:
            f.write(contents)

        memory = get_memory()

        async def event_stream():
            t0 = time.time()
            yield f"data: {json.dumps({'type': 'waiting', 'content': '正在为您查找相关信息...'}, ensure_ascii=False)}\n\n"
            try:
                # 1. 视觉识别 → 描述图片中的商品
                desc = ""
                try:
                    desc = _vision_service.describe_product(tmp_path)
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'content': f'图片识别失败: {str(e)}'}, ensure_ascii=False)}\n\n"
                    return

                if not desc:
                    yield f"data: {json.dumps({'type': 'error', 'content': '未能识别图片中的商品'}, ensure_ascii=False)}\n\n"
                    return

                # 2. 图片描述用于检索/精排，宽松查询给 LLM 生成回答
                print(f"[Vision] 图片描述: {desc}")
                # 提取品类+品牌作为给 LLM 的宽松提示，避免严格属性匹配导致无结果
                import re as _re
                cat_match = _re.search(r'品类[:：]\s*(\S+)', desc)
                brand_match = _re.search(r'品牌[:：]\s*(\S+)', desc)
                parts = []
                if cat_match:
                    parts.append(cat_match.group(1))
                if brand_match:
                    parts.append(brand_match.group(1))
                if parts:
                    base_query = " ".join(parts)
                    relaxed_query = f"{base_query} {query}".strip() if query else base_query
                else:
                    relaxed_query = f"{desc} {query}".strip()
                print(f"[Vision] 检索查询: {desc[:60]} | LLM查询: {relaxed_query}")

                stream_result = _rag_service.query_stream(
                    question=desc,           # 丰富属性用于检索+精排, 提高召回精度
                    user_question_override=relaxed_query  # LLM只看到品类+品牌, 避免拒绝推荐
                )
                stream_gen = stream_result.get("stream")
                if stream_gen is None:
                    yield f"data: {json.dumps({'type': 'error', 'content': '检索失败'}, ensure_ascii=False)}\n\n"
                    return

                full_text = ""
                for chunk in stream_gen:
                    full_text += chunk

                # 解析 + 渲染（与 RAG 一致）
                from api.routes import _parse_stream_structured
                parsed = _parse_stream_structured(full_text)
                answer_text = parsed['answer_text']
                recommendations = parsed["recommendations"]
                voice_text = parsed.get("voice_friendly", "")

                memory.remember(session_id, query or desc, full_text, recommendations)

                yield f"data: {json.dumps({'type': 'session', 'sid': session_id}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'chunk', 'content': answer_text}, ensure_ascii=False)}\n\n"

                if recommendations:
                    enriched_cards = _enrich_recommendations(recommendations, _ecommerce_graph)
                    yield f"data: {json.dumps({'type': 'cards', 'cards': enriched_cards}, ensure_ascii=False)}\n\n"

                tts_url = ""
                # 视觉识别场景不合成语音（图片查询为文本输入）
                yield f"data: {json.dumps({'type': 'voice', 'url': tts_url, 'text': voice_text}, ensure_ascii=False)}\n\n"

                yield f"data: {json.dumps({'type': 'done', 'timing': {'total': round(time.time() - t0, 3)}}, ensure_ascii=False)}\n\n"

            except Exception as e:
                import traceback
                traceback.print_exc()
                yield f"data: {json.dumps({'type': 'error', 'content': f'视觉搜索失败: {str(e)}'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    return router
