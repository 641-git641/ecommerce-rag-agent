import sys
import os
import json

# 将项目根目录添加到sys.path，确保相对导入能正常工作
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from config import Settings
from embeddings import TongyiEmbedding, VisionEmbeddingService
from vector_store import VectorStoreService
from llm import LLMService
from document import DocumentProcessor
from rag_service import RAGService, BM25Service
from knowledge_graph import LightEcommerceGraph
from reranker import DashScopeReranker
from vision import VisionService
from speech import AsrService, TtsService
from memory import init_memory
from api import router, init_handlers, handlers_instance
from agent import Agent
from agent.api import init_agent, init_services, create_router as create_agent_router
from agent.cart_client import CartAPIClient

# 全局轻量电商关联图谱单例
ecommerce_graph: LightEcommerceGraph = None


def create_app():
    global ecommerce_graph

    # 加载应用配置
    settings = Settings()

    # 初始化嵌入模型（使用通义千问Qwen3-Embedding）
    embedding_function = TongyiEmbedding(api_key=settings.OPENAI_API_KEY)

    # 初始化向量数据库服务
    vector_store = VectorStoreService(
        collection_name=settings.CHROMA_COLLECTION_NAME,
        embedding_function=embedding_function,
        persist_directory=settings.CHROMA_PERSIST_DIRECTORY,
    )

    # 初始化大语言模型服务
    llm_service = LLMService(
        base_url=settings.OPENAI_BASE_URL,
        api_key=settings.OPENAI_API_KEY,
        model_name=settings.LLM_MODEL_NAME,
    )

    # 初始化文档处理器
    document_processor = DocumentProcessor(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
    )

    # 初始化 BM25 关键词检索服务（从向量库已有文档构建索引）
    bm25_service = BM25Service(k1=1.5, b=0.75)
    try:
        existing_docs = vector_store.list_documents().get("documents", [])
        if existing_docs:
            bm25_service.build_index(existing_docs)
            print(f"BM25 索引已构建，共 {len(existing_docs)} 篇文档")
    except Exception as e:
        print(f"BM25 索引构建失败，混合检索将不启用关键词: {str(e)}")
        bm25_service = None

    # ============================================================
    # Docker 首次启动自动初始化数据（ChromaDB 为空时自动加载）
    # ============================================================
    _docs = vector_store.list_documents().get("documents", [])
    if not _docs:
        print("[Init] ChromaDB 为空，开始自动加载测试数据...")
        _test_data_dir = os.path.join(os.path.dirname(__file__), "..", "docs", "test")
        if os.path.isdir(_test_data_dir):
            for _category in sorted(os.listdir(_test_data_dir)):
                _cat_data = os.path.join(_test_data_dir, _category, "data")
                if not os.path.isdir(_cat_data):
                    continue
                for _fname in sorted(os.listdir(_cat_data)):
                    if not _fname.endswith(".json"):
                        continue
                    _fpath = os.path.join(_cat_data, _fname)
                    try:
                        chunks = document_processor.process_local_file(_fpath)
                        if chunks:
                            vector_store.add_documents(chunks)
                    except Exception as e:
                        print(f"  [Init] 加载失败 {_fname}: {e}")
            _final_docs = vector_store.list_documents().get("documents", [])
            print(f"[Init] 数据加载完成，共 {len(_final_docs)} 条文档片段")
            # 重建 BM25 索引
            try:
                if _final_docs:
                    bm25_service.build_index(_final_docs)
                    print(f"[Init] BM25 索引已重建，共 {len(_final_docs)} 篇文档")
            except Exception as e:
                print(f"[Init] BM25 重建失败: {e}")
        else:
            print("[Init] 警告: docs/test 目录不存在，跳过数据加载")
    else:
        print(f"[Init] ChromaDB 已有 {len(_docs)} 条文档，跳过初始化")

    # 初始化RAG服务（整合向量检索与LLM生成 + Cross-Encoder API精排）
    reranker = DashScopeReranker(
        api_key=settings.OPENAI_API_KEY,
        model_name=settings.RERANKER_MODEL_NAME,
    )

    # 初始化轻量电商关联图谱（纯JSON实现，零依赖图数据库）
    ecommerce_graph = LightEcommerceGraph(graph_file_path="./ecommerce_graph.json")

    # 自动从所有品类数据构建完整图谱（Cloth/Digital/Food/Beauty）
    _test_data_dir = os.path.join(os.path.dirname(__file__), "..", "docs", "test")
    if os.path.isdir(_test_data_dir):
        for _category in os.listdir(_test_data_dir):
            _cat_data = os.path.join(_test_data_dir, _category, "data")
            if not os.path.isdir(_cat_data):
                continue
            for _fname in os.listdir(_cat_data):
                if not _fname.endswith(".json"):
                    continue
                _fpath = os.path.join(_cat_data, _fname)
                try:
                    with open(_fpath, "r", encoding="utf-8") as _f:
                        _item = json.loads(_f.read())
                    _pid = _item.get("product_id", "")
                    _title = _item.get("title", "")
                    _brand = _item.get("brand", "")
                    _category = _item.get("category", "")
                    _price = _item.get("base_price", _item.get("price", 0))
                    if _pid:
                        ecommerce_graph.add_node(
                            f"product:{_pid}", "product",
                            {"title": _title, "product_id": _pid, "price": _price,
                             "brand_name": _brand, "category": _category,
                             "sub_category": _item.get("sub_category", "")}
                        )
                        if _brand:
                            ecommerce_graph.add_node(f"brand:{_brand}", "brand", {"name": _brand})
                            ecommerce_graph.add_edge(f"product:{_pid}", f"brand:{_brand}", "belongs_to_brand")
                        if _category:
                            ecommerce_graph.add_node(f"category:{_category}", "category", {"name": _category})
                            ecommerce_graph.add_edge(f"product:{_pid}", f"category:{_category}", "belongs_to_category")
                except Exception:
                    pass
        print(f"[图谱] 启动时已从 docs/test 全品类数据构建图谱，共 {len(ecommerce_graph.nodes)} 个节点")
        # 自动构建同子分类关联边
        ecommerce_graph.build_same_sub_category_edges()

    # 初始化RAG服务（整合向量检索与LLM生成 + Cross-Encoder API精排 + 图谱一跳展开）
    rag_service = RAGService(
        vector_store=vector_store,
        llm=llm_service,
        retrieval_k=settings.RETRIEVAL_K,
        enable_multi_query=True,
        num_expanded_queries=3,
        enable_rrf=True,
        rrf_k=60,
        bm25_service=bm25_service,
        enable_hybrid=True,
        hybrid_vector_weight=0.6,
        hybrid_bm25_weight=0.4,
        enable_metadata_weight=True,
        enable_mmr=True,
        mmr_lambda=0.7,
        reranker=reranker,
        enable_cross_encoder_rerank=True,
        ecommerce_graph=ecommerce_graph,
        enable_graph_expand=True,
    )

    # 初始化视觉理解服务（图片→文字描述）
    vision_service = VisionService(
        api_key=settings.OPENAI_API_KEY,
        model_name=settings.VISION_MODEL_NAME,
        max_image_size=settings.VISION_MAX_IMAGE_SIZE,
    )

    # 初始化多模态图片向量化服务（图片→向量，DashScope API）
    vision_embedding = VisionEmbeddingService(
        api_key=settings.OPENAI_API_KEY,
        model_name=settings.VISION_EMBEDDING_MODEL_NAME,
        dimensions=settings.VISION_EMBEDDING_DIMENSIONS,
    )

    # 初始化图片向量库（独立collection，以图搜图用）
    image_vector_store = VectorStoreService(
        collection_name=settings.CHROMA_IMAGE_COLLECTION_NAME,
        embedding_function=embedding_function,
        persist_directory=settings.CHROMA_PERSIST_DIRECTORY,
    )

    # 初始化语音识别服务（DashScope fun-asr）
    asr_service = AsrService(
        api_key=settings.OPENAI_API_KEY,
        model_name=settings.ASR_MODEL_NAME,
    )

    # 初始化语音合成服务（DashScope CosyVoice）
    tts_service = TtsService(
        api_key=settings.OPENAI_API_KEY,
        model_name=settings.TTS_MODEL_NAME,
        voice=settings.TTS_VOICE,
        audio_dir=settings.TTS_AUDIO_DIR,
    )

    # 初始化API处理器（注入依赖服务）
    init_handlers(rag_service, document_processor, ecommerce_graph, vision_service, vision_embedding, image_vector_store, asr_service, tts_service)

    # 初始化三层记忆系统（纯文件模式）
    init_memory(
        graph=ecommerce_graph,
    )

    # 初始化 CartAPIClient（对接 Go 网关 MySQL 购物车）
    cart_api_client = CartAPIClient(base_url=settings.GO_SERVER_URL)

    # 初始化 Agentic RAG 智能体
    agent = Agent(rag_service=rag_service, llm_service=llm_service, ecommerce_graph=ecommerce_graph, tts_service=tts_service, cart_api_client=cart_api_client)
    init_agent(agent)
    init_services(rag_service, tts_service, ecommerce_graph, vision_service)
    agent_router = create_agent_router()

    # 创建FastAPI应用实例
    app = FastAPI(title="电商RAG核心服务 - 带轻量关联图谱")

    # 配置跨域中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册API路由
    app.include_router(router)
    # 注册 Agent 路由（/agent/query, /agent/stream）
    app.include_router(agent_router, prefix="/agent", tags=["agent"])


    # 挂载上传目录静态文件（TTS 音频回放）
    uploads_dir = os.path.join(os.path.dirname(__file__), "uploads")
    if os.path.isdir(uploads_dir):
        app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

    return app, settings


# 创建应用实例和配置
app, settings = create_app()


# 主入口：启动Uvicorn服务器
if __name__ == "__main__":
    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)
