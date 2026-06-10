"""API路由模块：定义FastAPI的所有HTTP接口

提供聊天查询、文档上传、图片解析等RESTful接口。
所有接口都通过依赖注入获取RAG服务和文档处理器实例。
新增特性：分块预览调试接口、元数据过滤检索支持、轻量JSON电商关联图谱
"""

import os
import json
import re
import time
import uuid
import asyncio
from fastapi import APIRouter, UploadFile, File, Form, Body
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, AsyncGenerator, List
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty

_executor = ThreadPoolExecutor(max_workers=2)

# 自定义模块导入
from document import DocumentProcessor
from rag_service import RAGService
from knowledge_graph import LightEcommerceGraph
from vision import VisionService
from embeddings import VisionEmbeddingService
from vector_store import VectorStoreService
from speech import AsrService, TtsService
from memory import get_memory


# ── 会话持久化：回写 Go 网关 MySQL ──
_GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080")


def _save_session_message(session_id: str, role: str, content: str, cards: str = "", voice_url: str = ""):
    """异步回写一条消息到 Go 网关的 MySQL 会话存储"""
    def _post():
        try:
            import requests as _req
            payload = {"role": role, "content": content}
            if cards:
                payload["cards"] = cards
            if voice_url:
                payload["voice_url"] = voice_url
            _req.post(
                f"{_GATEWAY_URL}/api/session/{session_id}/message",
                json=payload,
                timeout=5,
            )
        except Exception as e:
            print(f"[Session] 回写消息失败: {e}", flush=True)
    _executor.submit(_post)


# 创建API路由实例
router = APIRouter()

# 聊天查询请求模型
class ChatQueryRequest(BaseModel):
    question: str  # 用户问题
    session_id: str = ""  # 会话ID，默认为空
    filter: Optional[Dict[str, Any]] = None  # 可选，外部传入的强制元数据过滤条件
    no_backoff: bool = False  # 跳过后退提问质量检查（Agent 快速通道使用）


# 图谱操作请求模型
class AddNodeRequest(BaseModel):
    node_id: str
    node_type: str
    properties: Optional[Dict[str, Any]] = None


class AddEdgeRequest(BaseModel):
    from_id: str
    to_id: str
    relation_type: str
    properties: Optional[Dict[str, Any]] = None


# 处理器类，封装所有服务实例
class Handlers:
    def __init__(
        self,
        rag_service: RAGService,
        document_processor: DocumentProcessor,
        ecommerce_graph: LightEcommerceGraph,
        vision_service: VisionService,
        vision_embedding: VisionEmbeddingService,
        image_vector_store: VectorStoreService,
        asr_service: AsrService,
        tts_service: TtsService,
    ):
        self.rag_service = rag_service
        self.document_processor = document_processor
        self.ecommerce_graph = ecommerce_graph
        self.vision_service = vision_service
        self.vision_embedding = vision_embedding
        self.image_vector_store = image_vector_store
        self.asr_service = asr_service
        self.tts_service = tts_service


# 全局处理器实例，初始为None
handlers_instance: Optional[Handlers] = None


# 初始化处理器函数，用于设置全局实例
def init_handlers(
    rag_service: RAGService,
    document_processor: DocumentProcessor,
    ecommerce_graph: LightEcommerceGraph,
    vision_service: VisionService,
    vision_embedding: VisionEmbeddingService,
    image_vector_store: VectorStoreService,
    asr_service: AsrService,
    tts_service: TtsService,
):
    global handlers_instance
    handlers_instance = Handlers(rag_service, document_processor, ecommerce_graph, vision_service, vision_embedding, image_vector_store, asr_service, tts_service)


# 根路由，返回服务状态
@router.get("/")
async def root():
    """健康检查接口，返回服务状态"""
    debug_info = {}
    if handlers_instance and handlers_instance.ecommerce_graph:
        debug_info = handlers_instance.ecommerce_graph.export_debug_info()
    return {
        "status": "✅ Python RAG服务运行正常，ChromaDB向量库已就绪，支持智能元数据过滤检索 + 轻量JSON电商关联图谱",
        "port": 9000,
        "graph_stats": debug_info,
        "endpoints": {
            "chat": "/chat/query",
            "chat_stream": "/chat/stream",
            "multimodal": "/chat/multimodal",
            "upload": "/document/upload",
            "preview_chunks": "/document/preview-chunks",
            "documents_list": "/documents/list",
            "image_query": "/image/query",
            "image_index": "/image/index",
            "image_search": "/image/search",
            "asr": "/asr",
            "voice_playback": "/voice/playback/{filename}",
            "graph_node_add": "/graph/node/add",
            "graph_edge_add": "/graph/edge/add",
            "graph_same_style": "/graph/product/{product_id}/same-style",
            "graph_match": "/graph/product/{product_id}/match",
            "graph_by_brand": "/graph/brand/{brand_id}/products",
            "graph_debug": "/graph/debug",
            "health": "/"
        }
    }


# ── 纯语音识别接口：上传音频，返回文本，不做 RAG 查询 ──
@router.post("/asr")
async def asr_transcribe(voice: UploadFile = File(...)):
    if handlers_instance is None:
        return JSONResponse({"error": "服务未初始化"}, status_code=503)
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        audio_dir = os.path.join(base_dir, "uploads", "audio")
        os.makedirs(audio_dir, exist_ok=True)
        ext = os.path.splitext(voice.filename or "voice.wav")[1] or ".wav"
        filename = f"asr_{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(audio_dir, filename)
        content = await voice.read()
        with open(filepath, "wb") as f:
            f.write(content)
        text = handlers_instance.asr_service.transcribe(filepath)
        return {"text": text or ""}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# 文档上传路由，处理文件上传并生成知识片段，存入向量库 + 自动加入关联图谱
@router.post("/document/upload")
async def upload_document(file: UploadFile = File(...)):
    """文档上传接口

    接收上传的文件，解析内容，切分为知识片段，存入向量库。
    对于JSON商品文件，自动提取商品元数据加入轻量关联图谱。

    Args:
        file: 上传的文件对象

    Returns:
        上传结果状态信息
    """
    if handlers_instance is None:
        return {"status": "❌ 服务未初始化", "error": "handlers_instance is None"}

    try:
        chunks = await handlers_instance.document_processor.process_uploaded_file(file)
        
        # 把知识片段存入向量库
        handlers_instance.rag_service.vector_store.add_documents(chunks)

        # 自动重建 BM25 关键词索引
        if handlers_instance.rag_service.bm25_service is not None:
            try:
                all_docs = handlers_instance.rag_service.vector_store.list_documents().get("documents", [])
                handlers_instance.rag_service.bm25_service.build_index(all_docs)
            except Exception as e:
                print(f"BM25 索引重建失败，混合检索将自动降级: {str(e)}")
        
        # 如果是商品JSON文件，自动构建图谱基础节点
        import json
        import tempfile
        _, suffix = os.path.splitext(file.filename)
        if suffix.lower() == ".json":
            # 重新读取内容解析商品信息
            await file.seek(0)
            content_bytes = await file.read()
            product_data = json.loads(content_bytes.decode("utf-8"))
            
            if isinstance(product_data, list):
                for item in product_data:
                    handlers_instance.ecommerce_graph.build_from_product_json(item)
            else:
                handlers_instance.ecommerce_graph.build_from_product_json(product_data)

        return {
            "status": f"✅ 文档 {file.filename} 已成功解析，共生成 {len(chunks)} 个知识片段存入向量库，基础图谱节点已构建",
            "chunks_count": len(chunks)
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"status": f"❌ 处理失败: {str(e)}", "error": str(e)}


# 聊天查询路由，处理用户问题（支持自动元数据过滤）
@router.post("/chat/query")
async def chat_query(req: ChatQueryRequest):
    """聊天查询接口（支持智能元数据过滤检索）

    接收用户问题，自动识别意图生成元数据过滤条件，通过RAG链路返回回答。
    也支持外部传入强制filter参数自定义检索范围。

    Args:
        req: 包含question、可选session_id和可选filter的请求体

    Returns:
        RAG生成的回答及元数据
    """
    if handlers_instance is None:
        return {
            "answer": "❌ 服务未初始化",
            "sources": [],
            "search_time": 0,
            "total_time": 0,
            "retrieved_knowledge_count": 0,
            "applied_metadata_filter": None,
        }

    try:
        result = handlers_instance.rag_service.query(req.question, filter=req.filter, no_backoff=req.no_backoff)
        return result
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {
            "answer": f"❌ 查询失败: {str(e)}",
            "sources": [],
            "search_time": 0,
            "total_time": 0,
            "retrieved_knowledge_count": 0,
            "applied_metadata_filter": None,
        }


def _parse_stream_structured(raw_text: str) -> dict:
    text = raw_text.strip()
    if not text:
        return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}

    # ── 剥离 markdown 代码块包裹 ──
    import re as _re
    m = _re.match(r'```(?:json)?\s*\n(.*?)\n```', text, _re.DOTALL)
    if m:
        text = m.group(1).strip()

    json_start = text.find('{')
    if json_start == -1:
        return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}
    depth = 0
    json_end = -1
    for i in range(json_start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                json_end = i + 1
                break
    if json_end == -1:
        return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}
    raw_json = text[json_start:json_end]
    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, ValueError):
        # 容错：去除尾部逗号
        try:
            cleaned = _re.sub(r',\s*}', '}', raw_json)
            cleaned = _re.sub(r',\s*]', ']', cleaned)
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}

    try:
        answer_text = parsed.get("answer_text", "")
        voice_friendly = parsed.get("voice_friendly", "")
        recommendations = parsed.get("recommendations", [])
        if not isinstance(recommendations, list):
            recommendations = []
        print(f"[RAG Parse] answer={len(answer_text)}字 voice={len(voice_friendly)}字 recs={len(recommendations)}条", flush=True)
        cleaned_recs = []
        for rec in recommendations[:5]:
            if isinstance(rec, dict):
                cleaned_recs.append({
                    "product_id": str(rec.get("product_id", "")),
                    "name": str(rec.get("name", "")),
                    "price": rec.get("price", 0),
                    "reason": str(rec.get("reason", "")),
                })
        if not answer_text and not cleaned_recs:
            answer_text = voice_friendly or "抱歉，当前知识库中没有找到与您问题匹配的商品信息，请尝试更换关键词或扩大搜索范围。"
        elif not answer_text:
            answer_text = "根据您的需求，以下是为您找到的相关商品："
        return {"answer_text": answer_text, "recommendations": cleaned_recs, "voice_friendly": voice_friendly}
    except json.JSONDecodeError:
        pass
    return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}


_chat_sessions: Dict[str, list] = {}

# ── 话题切换检测：品类词 + 品牌词 ──
_TOPIC_CATEGORIES = {
    "手机", "电脑", "笔记本", "平板", "耳机", "手表", "音箱", "充电器", "充电宝", "键盘", "鼠标", "数据线",
    "跑鞋", "运动鞋", "篮球鞋", "拖鞋", "鞋子", "T恤", "衬衫", "连衣裙", "牛仔裤", "外套", "卫衣", "短裤", "帽子", "墨镜", "背包",
    "洗面奶", "面霜", "防晒霜", "防晒", "精华", "口红", "粉底", "面膜", "眼霜", "卸妆", "洁面", "爽肤水", "乳液", "水乳",
    "护肤品", "化妆品", "美妆", "护肤", "彩妆",
}
_TOPIC_BRANDS = {
    "nike", "adidas", "华为", "小米", "三星", "苹果", "oppo", "vivo",
    "兰蔻", "雅诗兰黛", "科颜氏", "sk-ii", "资生堂", "理肤泉",
    "优衣库", "李宁", "安踏", "迪卡侬", "波司登",
    "索尼", "bose", "jbl", "漫步者",
    "戴尔", "联想", "华硕", "惠普",
    "花西子", "完美日记", "mac", "ysl", "dior",
    "欧莱雅", "olay", "赫莲娜", "倩碧", "安热沙", "安耐晒",
}

def _extract_topic(text: str) -> str:
    """从文本中提取品类+品牌话题关键词"""
    t = text.lower()
    parts = []
    for cat in _TOPIC_CATEGORIES:
        if cat in t:
            parts.append(cat)
    for brand in _TOPIC_BRANDS:
        if brand in t:
            parts.append(brand)
    return " ".join(parts[:3]) if parts else ""

def _detect_topic_switch(question: str, chat_history: list) -> str:
    """检测话题是否切换，返回提示文本（空串=未切换）"""
    cur_topic = _extract_topic(question)
    if not cur_topic:
        return ""
    # 找上一条用户消息
    for m in reversed(chat_history):
        if m.get("role") == "user":
            prev_topic = _extract_topic(m.get("content", ""))
            if prev_topic and prev_topic != cur_topic:
                return f"[话题已切换：{prev_topic} → {cur_topic}] 请忽略之前的对话历史，仅根据当前问题和知识库内容回答。"
            break
    return ""


async def rag_stream_events(
    rag_service: "RAGService",
    question: str,
    session_id: str,
    memory,
    chat_history: list,
    filter: Optional[Dict[str, Any]] = None,
    tts_service=None,
    ecommerce_graph=None,
    enriched_query: str = "",
    skip_query_expansion: bool = False,
    enable_tts: bool = False,
) -> AsyncGenerator[str, None]:
    """核心 RAG 流式事件生成器，被 /chat/stream 和 /agent/stream 共用。

    enriched_query: 如果外部已做查询增强（含话题切换检测/槽位累积/实体桥接），
                    传入此参数可跳过内部 enrichment 步骤。
    skip_query_expansion: 简单查询跳过 LLM 扩展（3 个并行调用），直接检索。
    enable_tts: 是否启用语音合成（默认关闭，仅语音输入场景开启）
    """
    t0 = time.time()
    search_q = question if question and len(question.strip()) >= 2 else (enriched_query or question)

    # 短问题自动跳过查询扩展，省 LLM 调用
    if not skip_query_expansion and len(search_q.strip()) <= 15:
        skip_query_expansion = True
        print(f"[RAG] 短问题自动跳过查询扩展: '{search_q[:30]}'", flush=True)

    # 话题切换检测
    topic_hint = _detect_topic_switch(question, chat_history)
    if topic_hint:
        print(f"[RAG] {topic_hint}", flush=True)

    loop = asyncio.get_running_loop()

    # ── 流水线并行：后台启动检索，同时向前端推 waiting 逐字动画 ──
    retrieval_future = loop.run_in_executor(
        _executor,
        lambda: rag_service.query_stream(
            search_q,
            filter=filter,
            chat_history=chat_history[-6:],
            skip_query_expansion=skip_query_expansion,
            topic_switch_hint=topic_hint,
        ),
    )

    # 逐字推送 waiting 消息，直到检索完成
    waiting_text = "正在为您查找相关信息..."
    for i, ch in enumerate(waiting_text):
        yield f"data: {json.dumps({'type': 'waiting', 'content': ch, 'index': i}, ensure_ascii=False)}\n\n"
        if retrieval_future.done():
            break
        await asyncio.sleep(0.03)  # ~30ms/字，整句话约0.3s推完

    try:
        stream_result = await retrieval_future

        stream_gen = stream_result.get("stream")
        if stream_gen is None:
            yield f"data: {json.dumps({'type': 'error', 'content': '流式生成器为空'}, ensure_ascii=False)}\n\n"
            return

        # 清除 waiting，准备输出答案
        yield f"data: {json.dumps({'type': 'clear_waiting'}, ensure_ascii=False)}\n\n"

        # ── P0: 真正流式 ──
        # 字符级状态机：扫描 "answer_text":"  → 逐字推 chunk → 遇到闭合 " 停
        chunk_queue: Queue = Queue()
        _MARKER = 'answer_text'
        t_llm_start = time.time()

        def _json_depth_closed(text: str) -> bool:
            """检查 text 中第一个 JSON 对象的 {} 是否完整闭合"""
            start = text.find('{')
            if start == -1:
                return False
            depth = 0
            for ch in text[start:]:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return True
            return False

        def _stream_worker():
            full_text = ""
            state = 0          # 0=找key,1=收集key,2=等冒号,3=等值引号,4=在值中
            key_buf = ""
            val_buf = ""
            esc = False
            try:
                for token in stream_gen:
                    full_text += token
                    for ch in token:
                        if state == 4:  # 在 answer_text 值内部
                            if esc:
                                val_buf += {'n': '\n', 't': '\t', '"': '"', '\\': '\\'}.get(ch, ch)
                                esc = False
                            elif ch == '\\':
                                esc = True
                            elif ch == '"':
                                # answer_text 值结束
                                state = 0
                                if val_buf:
                                    chunk_queue.put(("chunk", val_buf))
                            else:
                                val_buf += ch
                                chunk_queue.put(("chunk", val_buf))
                            continue

                        if state == 0:  # 找下一个 '"'
                            if ch == '"':
                                state = 1
                                key_buf = ""
                            continue

                        if state == 1:  # 收集 key
                            if ch == '"':
                                if key_buf == _MARKER:
                                    state = 2
                                else:
                                    state = 0
                            else:
                                key_buf += ch
                            continue

                        if state == 2:  # 等冒号
                            if ch == ':':
                                state = 3
                            elif ch not in (' ', '\n', '\r', '\t'):
                                state = 0
                            continue

                        if state == 3:  # 等值的起始引号
                            if ch == '"':
                                state = 4
                                val_buf = ""
                                esc = False
                            elif ch not in (' ', '\n', '\r', '\t'):
                                state = 0
                            continue

                    # JSON 完整闭合 → done
                    if _json_depth_closed(full_text):
                        chunk_queue.put(("done", full_text))
                        return

                # 流结束
                chunk_queue.put(("done", full_text))
            except Exception as e:
                chunk_queue.put(("error", str(e)))

        loop.run_in_executor(_executor, _stream_worker)

        # 异步读队列，yield chunk 事件
        full_text = ""
        answer_text = ""
        while True:
            def _dequeue():
                try:
                    return chunk_queue.get(timeout=0.05)
                except Empty:
                    return None
            item = await loop.run_in_executor(_executor, _dequeue)
            if item is None:
                await asyncio.sleep(0.01)
                continue
            kind, payload = item

            if kind == "chunk":
                answer_text = payload
                yield f"data: {json.dumps({'type': 'chunk', 'content': payload}, ensure_ascii=False)}\n\n"
            elif kind == "done":
                full_text = payload
                break
            elif kind == "error":
                yield f"data: {json.dumps({'type': 'error', 'content': f'流式解析失败: {payload}'}, ensure_ascii=False)}\n\n"
                return

        llm_time = time.time() - t_llm_start
        rag_time = time.time() - t0
        # 提取 RAG 内部分段耗时
        rag_breakdown = stream_result.get("_timing", {}) if stream_result else {}
        if rag_breakdown:
            _b = rag_breakdown
            print(f"[RAG ⏱] 分段耗时: intent={_b.get('intent_filter_ms','?')}ms expand={_b.get('query_expansion_ms','?')}ms"
                  f" retrieval={_b.get('retrieval_ms','?')}ms({_b.get('retrieved_docs_n','?')}docs)"
                  f" rerank={_b.get('rerank_ms','?')}ms build={_b.get('build_context_ms','?')}ms"
                  f" prompt={_b.get('prompt_ms','?')}ms({_b.get('prompt_chars','?')}字)"
                  f" pre_llm={_b.get('pre_llm_total_ms','?')}ms", flush=True)
        print(f"[RAG] 检索+生成耗时: {rag_time:.2f}s | LLM流耗时: {llm_time:.2f}s | answer_text: {len(answer_text)}字 | full_text: {len(full_text)}字", flush=True)

        # 解析 JSON 获取 recommendations + voice_friendly
        parsed = _parse_stream_structured(full_text)
        voice_friendly = parsed.get("voice_friendly", "")
        recommendations = parsed.get("recommendations", [])
        if not isinstance(recommendations, list):
            recommendations = []

        # 如果流式没提取到 answer_text，用 JSON 解析结果
        if not answer_text:
            answer_text = parsed.get("answer_text", full_text)

        if not answer_text or answer_text.strip().startswith("{") and len(answer_text.strip()) < 100:
            answer_text = "抱歉，当前知识库中没有找到与您问题匹配的商品信息，请尝试更换关键词或扩大搜索范围。"

        chat_history.append({"role": "user", "content": question})
        chat_history.append({"role": "assistant", "content": answer_text[:500]})
        if len(chat_history) > 12:
            chat_history[:] = chat_history[-12:]

        memory.remember(session_id, question, full_text, recommendations)
        stored_pids = [r.get("product_id", "") for r in (recommendations or []) if r.get("product_id")]
        if stored_pids:
            print(f"[Memory] RAG 记住推荐: session={session_id[:16]} pids={stored_pids}")
        else:
            # LLM 产出的 recommendations 中 product_id 经常为空，
            # 尝试用图谱补全再记一次，避免后续 cart 操作找不到商品
            enriched_for_memory = _enrich_recommendations(recommendations, ecommerce_graph) if ecommerce_graph else recommendations
            enriched_pids = [r.get("product_id", "") for r in enriched_for_memory if r.get("product_id")]
            if enriched_pids:
                memory.remember(session_id, question, full_text, enriched_for_memory)
                print(f"[Memory] RAG 记住推荐(图谱补全): session={session_id[:16]} pids={enriched_pids}")

        yield f"data: {json.dumps({'type': 'session', 'sid': session_id}, ensure_ascii=False)}\n\n"

        enriched_recs: list = []
        if recommendations:
            enriched_recs = _enrich_recommendations(recommendations, ecommerce_graph)
            print(f"[RAG] 推送 cards: {len(enriched_recs)} 条", flush=True)
        else:
            print(f"[RAG] 无 recommendations: full_text 长度={len(full_text)} 首200字={full_text[:200]}", flush=True)
        yield f"data: {json.dumps({'type': 'cards', 'cards': enriched_recs}, ensure_ascii=False)}\n\n"

        tts_url = ""
        if enable_tts and voice_friendly and tts_service is not None:
            try:
                def _do_tts():
                    return tts_service.synthesize(
                        voice_friendly, output_filename=f"tts_{uuid.uuid4().hex}.mp3"
                    )
                audio_path = await loop.run_in_executor(_executor, _do_tts)
                if audio_path:
                    tts_url = f"/voice/playback/{os.path.basename(audio_path)}"
                else:
                    print("[TTS] 合成返回空路径", flush=True)
            except Exception as e:
                print(f"[TTS] 合成失败: {e}", flush=True)

        yield f"data: {json.dumps({'type': 'voice', 'url': tts_url, 'text': voice_friendly}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'timing': {'rag_time': round(rag_time, 3), 'total': round(time.time() - t0, 3), 'breakdown': rag_breakdown}}, ensure_ascii=False)}\n\n"

        # ── 回写 Go 网关 MySQL，持久化会话消息 ──
        if session_id and session_id != "default":
            cards_json = json.dumps(enriched_recs, ensure_ascii=False) if enriched_recs else ""
            _save_session_message(session_id, "user", question)
            _save_session_message(session_id, "assistant", answer_text, cards=cards_json, voice_url=tts_url)

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'type': 'error', 'content': f'流式查询失败: {str(e)}'}, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(req: ChatQueryRequest):
    if handlers_instance is None:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'content': '服务未初始化'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    session_id = req.session_id or uuid.uuid4().hex[:12]
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = []
    history = _chat_sessions[session_id]

    return StreamingResponse(
        rag_stream_events(
            rag_service=handlers_instance.rag_service,
            question=req.question,
            session_id=session_id,
            memory=get_memory(),
            chat_history=history,
            filter=req.filter,
            tts_service=handlers_instance.tts_service,
            ecommerce_graph=handlers_instance.ecommerce_graph,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# 图片理解+RAG查询路由
@router.post("/image/query")
async def query_by_image(
    file: UploadFile = File(...),
    question: str = Form(""),
):
    if handlers_instance is None:
        return {"answer": "❌ 服务未初始化", "status": "error"}

    try:
        upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "images")
        os.makedirs(upload_dir, exist_ok=True)

        ext = os.path.splitext(file.filename or "image.jpg")[1] or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        image_path = os.path.join(upload_dir, filename)

        content = await file.read()
        with open(image_path, "wb") as f:
            f.write(content)

        description = handlers_instance.vision_service.describe_product(image_path)

        if not description:
            os.remove(image_path)
            return {"answer": "❌ 图片识别失败，请确认图片清晰且包含商品信息", "status": "error"}

        keywords = _extract_keywords(description)

        merged_query = keywords
        if question.strip():
            merged_query = f"{keywords} {question.strip()}"

        result = handlers_instance.rag_service.query(merged_query)

        result["vision_description"] = description
        result["merged_query"] = merged_query

        return result

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"answer": f"❌ 图片查询失败: {str(e)}", "status": "error"}


def _extract_keywords(description: str) -> str:
    keywords = []
    for line in description.split("\n"):
        line = line.strip()
        if ":" in line:
            val = line.split(":", 1)[1].strip()
            if val:
                keywords.append(val)
    return " ".join(keywords)


# 图片入库路由（以图搜图索引端）
@router.post("/image/index")
async def index_image(
    file: UploadFile = File(...),
    product_id: str = Form(...),
):
    if handlers_instance is None:
        return {"status": "❌ 服务未初始化"}

    try:
        upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "images")
        os.makedirs(upload_dir, exist_ok=True)

        ext = os.path.splitext(file.filename or "image.jpg")[1] or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        image_path = os.path.join(upload_dir, filename)

        content = await file.read()
        with open(image_path, "wb") as f:
            f.write(content)

        image_vector = handlers_instance.vision_embedding.embed_image(image_path)
        if image_vector is None:
            os.remove(image_path)
            return {"status": "❌ 图片向量化失败"}

        handlers_instance.image_vector_store.add_embeddings(
            texts=[f"product_image:{product_id}"],
            embeddings=[image_vector],
            metadatas=[{"product_id": product_id, "image_path": image_path, "filename": file.filename}],
            ids=[uuid.uuid4().hex],
        )

        return {
            "status": "✅ 商品图片已索引入库",
            "product_id": product_id,
            "image_path": image_path,
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"status": f"❌ 入库失败: {str(e)}"}


# 以图搜图路由
@router.post("/image/search")
async def search_by_image(
    file: UploadFile = File(...),
    top_k: int = Form(5),
):
    if handlers_instance is None:
        return {"status": "❌ 服务未初始化", "results": []}

    try:
        upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "images")
        os.makedirs(upload_dir, exist_ok=True)

        ext = os.path.splitext(file.filename or "image.jpg")[1] or ".jpg"
        filename = f"search_{uuid.uuid4().hex}{ext}"
        image_path = os.path.join(upload_dir, filename)

        content = await file.read()
        with open(image_path, "wb") as f:
            f.write(content)

        image_vector = handlers_instance.vision_embedding.embed_image(image_path)
        if image_vector is None:
            return {"status": "❌ 查询图片向量化失败", "results": []}

        results = handlers_instance.image_vector_store.search_by_vector(image_vector, k=top_k)

        items = []
        for doc in results:
            meta = getattr(doc, "metadata", {}) or {}
            items.append({
                "product_id": meta.get("product_id", ""),
                "image_path": meta.get("image_path", ""),
                "filename": meta.get("filename", ""),
            })

        return {
            "status": "ok",
            "results": items,
            "total": len(items),
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"status": f"❌ 以图搜图失败: {str(e)}", "results": []}


# 统一多模态入口（文字/图片/语音）
@router.post("/chat/multimodal")
async def chat_multimodal(
    text: str = Form(""),
    image: UploadFile = File(None),
    voice: UploadFile = File(None),
):
    if handlers_instance is None:
        return {
            "answer": {"text": "❌ 服务未初始化", "voice_url": ""},
            "recommendations": [],
            "modalities_used": [],
        }

    rag_start = None
    asr_time = 0.0
    vision_time = 0.0
    image_search_time = 0.0
    tts_time = 0.0
    modalities_used = []
    image_vector = None
    merged_query = ""
    image_description = ""

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    upload_img_dir = os.path.join(base_dir, "uploads", "images")
    upload_audio_dir = os.path.join(base_dir, "uploads", "audio")
    os.makedirs(upload_img_dir, exist_ok=True)
    os.makedirs(upload_audio_dir, exist_ok=True)

    if voice is not None:
        modalities_used.append("voice")
        t0 = time.time()
        voice_ext = os.path.splitext(voice.filename or "voice.wav")[1] or ".wav"
        voice_filename = f"voice_{uuid.uuid4().hex}{voice_ext}"
        voice_path = os.path.join(upload_audio_dir, voice_filename)
        voice_content = await voice.read()
        with open(voice_path, "wb") as f:
            f.write(voice_content)
        transcript = handlers_instance.asr_service.transcribe(voice_path)
        asr_time = time.time() - t0
        if transcript:
            if merged_query:
                merged_query = f"{merged_query} {transcript}"
            else:
                merged_query = transcript
        else:
            merged_query = merged_query or text

    if image is not None:
        modalities_used.append("image")
        t0 = time.time()
        img_ext = os.path.splitext(image.filename or "image.jpg")[1] or ".jpg"
        img_filename = f"multi_{uuid.uuid4().hex}{img_ext}"
        img_path = os.path.join(upload_img_dir, img_filename)
        img_content = await image.read()
        with open(img_path, "wb") as f:
            f.write(img_content)

        try:
            image_description = handlers_instance.vision_service.describe_product(img_path)
        except Exception:
            image_description = ""
        if image_description:
            keywords = _extract_keywords(image_description)
            if merged_query:
                merged_query = f"{keywords} {merged_query}"
            else:
                merged_query = keywords

        try:
            image_vector = handlers_instance.vision_embedding.embed_image(img_path)
        except Exception:
            image_vector = None
        vision_time = time.time() - t0

    if not merged_query and text:
        merged_query = text
    if not merged_query:
        return {
            "answer": {"text": "请提供文字问题、图片或语音", "voice_url": ""},
            "recommendations": [],
            "modalities_used": modalities_used,
        }

    if text and merged_query != text:
        merged_query = f"{merged_query} {text}"

    if "text" not in modalities_used and text:
        modalities_used.insert(0, "text")

    rag_start = time.time()
    result = handlers_instance.rag_service.query_structured(merged_query)
    rag_time = time.time() - rag_start

    answer_text = result.get("answer_text", "")
    recommendations = result.get("recommendations", [])
    voice_friendly = result.get("voice_friendly", answer_text[:80])

    if image_vector is not None and handlers_instance.image_vector_store is not None:
        t0 = time.time()
        try:
            similar_images = handlers_instance.image_vector_store.search_by_vector(image_vector, k=3)
            existing_pids = {r.get("product_id", "") for r in recommendations}
            for doc in similar_images:
                meta = getattr(doc, "metadata", {}) or {}
                pid = meta.get("product_id", "")
                if pid and pid not in existing_pids:
                    existing_pids.add(pid)
                    recommendations.append({
                        "product_id": pid,
                        "name": meta.get("filename", ""),
                        "price": 0,
                        "reason": "视觉相似",
                        "match_type": "visual",
                        "image_path": meta.get("image_path", ""),
                    })
            if len(recommendations) > 5:
                recommendations = recommendations[:5]
        except Exception:
            pass
        image_search_time = time.time() - t0

    enriched_recs = _enrich_recommendations(recommendations, handlers_instance.ecommerce_graph)

    tts_url = ""
    if voice is not None and voice_friendly and handlers_instance.tts_service is not None:
        t0 = time.time()
        try:
            output_filename = f"tts_{uuid.uuid4().hex}.mp3"
            audio_path = handlers_instance.tts_service.synthesize(voice_friendly, output_filename=output_filename)
            if audio_path:
                tts_url = f"/voice/playback/{os.path.basename(audio_path)}"
        except Exception:
            pass
        tts_time = time.time() - t0

    total_time = asr_time + vision_time + rag_time + image_search_time + tts_time

    return {
        "answer": {
            "text": answer_text,
            "voice_url": tts_url,
            "voice_text": voice_friendly,
        },
        "recommendations": enriched_recs,
        "sources": result.get("sources", []),
        "vision_description": image_description,
        "merged_query": merged_query,
        "timing": {
            "asr_time": round(asr_time, 3),
            "vision_time": round(vision_time, 3),
            "rag_time": round(rag_time, 3),
            "image_search_time": round(image_search_time, 3),
            "tts_time": round(tts_time, 3),
            "total_time": round(total_time, 3),
        },
        "modalities_used": modalities_used,
    }


def _enrich_recommendations(recommendations: list, graph) -> list:
    if graph is None:
        return recommendations
    # 建立映射：名称→pid、名称→价格、pid→属性
    name_to_pid: Dict[str, str] = {}
    name_to_price: Dict[str, float] = {}
    pid_to_props: Dict[str, dict] = {}
    for nid, node in graph.nodes.items():
        props = node.get("properties", {})
        title = str(props.get("title", "")).strip()
        pid_val = str(props.get("product_id", "")).strip()
        graph_price = float(props.get("price", 0) or 0)
        if title:
            if pid_val:
                name_to_pid[title] = pid_val
            if graph_price > 0:
                name_to_price[title] = graph_price
        if pid_val:
            pid_to_props[pid_val] = props

    def _match_name(target: str, candidates: dict) -> Optional[str]:
        """多级模糊匹配：精确→长前缀→短前缀→包含"""
        target = target.strip()
        if not target:
            return None
        if target in candidates:
            return candidates[target]
        # 取 token 匹配（忽略空格、品牌后缀等）
        tokens = re.split(r"[\s\-/]+", target)
        for gname, val in candidates.items():
            gtokens = re.split(r"[\s\-/]+", gname)
            if len(tokens) >= 2 and len(gtokens) >= 2:
                # 如果前2个 token 一致就算匹配
                if tokens[0] == gtokens[0] and tokens[1] == gtokens[1]:
                    return val
        # 前缀匹配（6→4 chars）
        for n in (6, 5, 4):
            for gname, val in candidates.items():
                if len(target) >= n and len(gname) >= n:
                    if target[:n] in gname:
                        return val
        return None

    enriched = []
    for rec in recommendations:
        item = dict(rec)
        pid = str(item.get("product_id", "")).strip()
        name = str(item.get("name", "")).strip()

        # 1. 用名称匹配 product_id（pid 为空或不合法时尝试）
        pid_valid = pid and re.match(r'^p_[a-z]+_\d{3}$', pid) and pid in pid_to_props
        if not pid_valid and name:
            matched_pid = _match_name(name, name_to_pid)
            if matched_pid:
                pid = matched_pid
                item["product_id"] = pid
                pid_valid = True

        # 2. 从图谱补属性
        if pid_valid:
            props = pid_to_props[pid]
            if not item.get("name"):
                item["name"] = props.get("title", "")
            gprice = float(props.get("price", 0) or 0)
            if gprice > 0 and float(item.get("price", 0) or 0) == 0:
                item["price"] = gprice

        # 3. 用名称匹配价格
        if float(item.get("price", 0) or 0) == 0 and name:
            matched = _match_name(name, name_to_price)
            if matched:
                item["price"] = matched

        # 4. 生成图片 URL（仅当 product_id 格式合法: p_xxx_nnn）
        if not item.get("image_url"):
            pid = str(item.get("product_id", "")).strip()
            if pid and re.match(r'^p_[a-z]+_\d{3}$', pid):
                item["image_url"] = f"/product-images/{pid}_live.jpg"
            elif pid:
                print(f"[WARN] _enrich_recommendations: 拒绝非法 product_id='{pid}' → 不生成 image_url", flush=True)

        # 5. 价格缺省
        if item.get("price") is None:
            item["price"] = 0

        enriched.append(item)

    # 去重
    seen_names = set()
    unique = []
    for item in enriched:
        nm = str(item.get("name", "")).strip()
        if nm and nm not in seen_names:
            seen_names.add(nm)
            unique.append(item)
        elif not nm:
            unique.append(item)
    return unique[:5]


# 商品图片文件服务（自动搜索 Cloth/Digital/Food/Beauty 等所有品类目录）
_product_image_dirs_cache = None


def _get_product_image_dirs():
    global _product_image_dirs_cache
    if _product_image_dirs_cache is not None:
        return _product_image_dirs_cache
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    test_data = os.path.join(base_dir, "..", "docs", "test")
    dirs = []
    if os.path.isdir(test_data):
        for category in os.listdir(test_data):
            img_dir = os.path.join(test_data, category, "images")
            if os.path.isdir(img_dir):
                dirs.append(img_dir)
    _product_image_dirs_cache = dirs
    return dirs


@router.get("/product-images/{filename:path}")
async def product_image(filename: str):
    for img_dir in _get_product_image_dirs():
        file_path = os.path.join(img_dir, filename)
        if os.path.exists(file_path):
            return FileResponse(file_path, media_type="image/jpeg")
    return JSONResponse({"error": "图片不存在"}, status_code=404)


# 语音播报音频文件服务
@router.get("/voice/playback/{filename}")
async def voice_playback(filename: str):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    audio_dir = os.path.join(base_dir, "uploads", "audio")
    file_path = os.path.join(audio_dir, filename)
    if not os.path.exists(file_path):
        return JSONResponse({"error": "音频文件不存在"}, status_code=404)
    return FileResponse(file_path, media_type="audio/mpeg")


# 文档列表路由
@router.get("/documents/list")
async def list_documents():
    """文档列表接口

    返回已上传的文档列表。

    Returns:
        文档列表
    """
    if handlers_instance is None:
        return {"documents": [], "total": 0, "message": "服务未初始化"}

    try:
        result_dict = handlers_instance.rag_service.vector_store.list_documents()
        doc_list = result_dict.get("documents", [])
        
        documents = []
        for doc in doc_list:
            documents.append({
                "id": doc.metadata.get("id", ""),
                "content": doc.page_content[:100],
                "metadata": doc.metadata
            })
        return {
            "documents": documents,
            "total": len(documents),
            "message": "文档列表获取成功"
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"documents": [], "total": 0, "message": f"获取失败: {str(e)}"}


# 分块预览调试接口（不上传入库，仅查看切分结果）
@router.post("/document/preview-chunks")
async def preview_chunks(file: UploadFile = File(...)):
    """分块预览调试接口 - 仅展示切分结果，不存入向量库
    
    用于检查分块策略是否符合预期，优化chunk_size等参数。
    Args:
        file: 上传的文件对象
        
    Returns:
        切分后的完整chunk列表，包含内容和元数据
    """
    if handlers_instance is None:
        return {"status": "❌ 服务未初始化", "error": "handlers_instance is None"}

    try:
        chunks = await handlers_instance.document_processor.process_uploaded_file(file)
        
        chunks_result = []
        for idx, chunk in enumerate(chunks):
            chunks_result.append({
                "chunk_index": idx,
                "content": chunk.page_content,
                "content_length": len(chunk.page_content),
                "metadata": chunk.metadata
            })
        
        return {
            "status": f"✅ 文档 {file.filename} 切分预览成功，共生成 {len(chunks)} 个知识片段",
            "file_name": file.filename,
            "total_chunks": len(chunks),
            "chunks": chunks_result
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"status": f"❌ 处理失败: {str(e)}", "error": str(e)}


# 文档删除路由
@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    """文档删除接口

    删除指定ID的文档。

    Args:
        doc_id: 要删除的文档ID

    Returns:
        删除结果
    """
    if handlers_instance is None:
        return {"status": "error", "message": "服务未初始化"}

    try:
        handlers_instance.rag_service.vector_store.delete_document([doc_id])
        return {"status": "success", "message": f"文档 {doc_id} 已删除"}
    except Exception as e:
        return {"status": "error", "message": f"删除失败: {str(e)}"}


# ===================== 轻量关联图谱接口 =====================

@router.post("/graph/node/add")
async def graph_add_node(req: AddNodeRequest):
    """向图谱添加实体节点"""
    if handlers_instance is None:
        return {"status": "error", "message": "服务未初始化"}
    handlers_instance.ecommerce_graph.add_node(req.node_id, req.node_type, req.properties)
    return {"status": "success", "message": f"节点 {req.node_id} 已成功添加"}


@router.post("/graph/edge/add")
async def graph_add_edge(req: AddEdgeRequest):
    """在图谱中添加关联边"""
    if handlers_instance is None:
        return {"status": "error", "message": "服务未初始化"}
    handlers_instance.ecommerce_graph.add_edge(req.from_id, req.to_id, req.relation_type, req.properties)
    return {"status": "success", "message": f"边 {req.from_id} -> {req.to_id} [{req.relation_type}] 已成功添加"}


@router.get("/graph/product/{product_id}/same-style")
async def graph_get_same_style(product_id: str):
    """获取指定商品的同款替代推荐列表"""
    if handlers_instance is None:
        return {"status": "error", "message": "服务未初始化"}
    products = handlers_instance.ecommerce_graph.get_same_style_products(f"product:{product_id}")
    return {"status": "success", "products": products}


@router.get("/graph/product/{product_id}/match")
async def graph_get_match_products(product_id: str):
    """获取指定商品的搭配推荐列表"""
    if handlers_instance is None:
        return {"status": "error", "message": "服务未初始化"}
    products = handlers_instance.ecommerce_graph.get_match_products(f"product:{product_id}")
    return {"status": "success", "products": products}


@router.get("/graph/brand/{brand_id}/products")
async def graph_get_brand_products(brand_id: str):
    """查询指定品牌下的所有商品"""
    if handlers_instance is None:
        return {"status": "error", "message": "服务未初始化"}
    products = handlers_instance.ecommerce_graph.get_products_by_brand(f"brand:{brand_id}")
    return {"status": "success", "products": products}


@router.get("/graph/debug")
async def graph_get_debug():
    """获取图谱调试统计信息"""
    if handlers_instance is None:
        return {"status": "error", "message": "服务未初始化"}
    debug_info = handlers_instance.ecommerce_graph.export_debug_info()
    return {"status": "success", "debug_info": debug_info}
