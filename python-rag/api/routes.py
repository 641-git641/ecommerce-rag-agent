"""API路由模块：定义FastAPI的所有HTTP接口

提供聊天查询、文档上传、图片解析等RESTful接口。
所有接口都通过依赖注入获取RAG服务和文档处理器实例。
新增特性：分块预览调试接口、元数据过滤检索支持、轻量JSON电商关联图谱

子模块：
  state.py           — 共享全局状态（app.handlers_instance, _chat_sessions）
  topic_detection.py — 话题切换检测
  graph_routes.py    — 知识图谱 CRUD 端点
  image_routes.py    — 图片理解查询 / 图片入库 / 以图搜图
  file_routes.py     — 文件服务（商品图片/语音播报） / 文档管理
  streaming.py       — RAG 流式事件生成器（rag_stream_events）
"""

import os
import json
import time
import uuid
from fastapi import APIRouter, UploadFile, File, Form, Body
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any

# 自定义模块导入
from document import DocumentProcessor
from rag_service import RAGService
from knowledge_graph import LightEcommerceGraph
from vision import VisionService
from embeddings import VisionEmbeddingService
from vector_store import VectorStoreService
from speech import AsrService, TtsService
from memory import get_memory
from shared.stream_utils import _enrich_recommendations

# 子模块导入（从 routes.py 拆出）
from .state import app, _chat_sessions
from .graph_routes import graph_router
from .image_routes import image_router, extract_keywords
from .file_routes import file_router
from .streaming import rag_stream_events


# 创建API路由实例
router = APIRouter()

# 挂载子路由
router.include_router(graph_router, prefix="/graph")
router.include_router(image_router, prefix="/image")
router.include_router(file_router)

# 聊天查询请求模型
class ChatQueryRequest(BaseModel):
    question: str  # 用户问题
    session_id: str = ""  # 会话ID，默认为空
    filter: Optional[Dict[str, Any]] = None  # 可选，外部传入的强制元数据过滤条件
    no_backoff: bool = False  # 跳过后退提问质量检查（Agent 快速通道使用）


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
    import api.state as _state
    _state.app.handlers_instance = Handlers(rag_service, document_processor, ecommerce_graph, vision_service, vision_embedding, image_vector_store, asr_service, tts_service)


# 根路由，返回服务状态
@router.get("/")
async def root():
    """健康检查接口，返回服务状态"""
    debug_info = {}
    if app.handlers_instance and app.handlers_instance.ecommerce_graph:
        debug_info = app.handlers_instance.ecommerce_graph.export_debug_info()
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
    if app.handlers_instance is None:
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
        text = app.handlers_instance.asr_service.transcribe(filepath)
        return {"text": text or ""}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
    if app.handlers_instance is None:
        return {
            "answer": "❌ 服务未初始化",
            "sources": [],
            "search_time": 0,
            "total_time": 0,
            "retrieved_knowledge_count": 0,
            "applied_metadata_filter": None,
        }

    try:
        result = app.handlers_instance.rag_service.query(req.question, filter=req.filter, no_backoff=req.no_backoff)
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


@router.post("/chat/stream")
async def chat_stream(req: ChatQueryRequest):
    if app.handlers_instance is None:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'content': '服务未初始化'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    session_id = req.session_id or uuid.uuid4().hex[:12]
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = []
    history = _chat_sessions[session_id]

    return StreamingResponse(
        rag_stream_events(
            rag_service=app.handlers_instance.rag_service,
            question=req.question,
            session_id=session_id,
            memory=get_memory(),
            chat_history=history,
            filter=req.filter,
            tts_service=app.handlers_instance.tts_service,
            ecommerce_graph=app.handlers_instance.ecommerce_graph,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# 统一多模态入口（文字/图片/语音）
@router.post("/chat/multimodal")
async def chat_multimodal(
    text: str = Form(""),
    image: UploadFile = File(None),
    voice: UploadFile = File(None),
):
    if app.handlers_instance is None:
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
        transcript = app.handlers_instance.asr_service.transcribe(voice_path)
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
            image_description = app.handlers_instance.vision_service.describe_product(img_path)
        except Exception:
            image_description = ""
        if image_description:
            keywords = extract_keywords(image_description)
            if merged_query:
                merged_query = f"{keywords} {merged_query}"
            else:
                merged_query = keywords

        try:
            image_vector = app.handlers_instance.vision_embedding.embed_image(img_path)
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
    result = app.handlers_instance.rag_service.query_structured(merged_query)
    rag_time = time.time() - rag_start

    answer_text = result.get("answer_text", "")
    recommendations = result.get("recommendations", [])
    voice_friendly = result.get("voice_friendly", answer_text[:80])

    if image_vector is not None and app.handlers_instance.image_vector_store is not None:
        t0 = time.time()
        try:
            similar_images = app.handlers_instance.image_vector_store.search_by_vector(image_vector, k=3)
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

    enriched_recs = _enrich_recommendations(recommendations, app.handlers_instance.ecommerce_graph)

    tts_url = ""
    if voice is not None and voice_friendly and app.handlers_instance.tts_service is not None:
        t0 = time.time()
        try:
            output_filename = f"tts_{uuid.uuid4().hex}.mp3"
            audio_path = app.handlers_instance.tts_service.synthesize(voice_friendly, output_filename=output_filename)
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
