"""文件服务 API 路由

从 api/routes.py 抽取，提供商品图片文件服务、语音播报文件服务、文档管理。
所有端点通过 app.handlers_instance 委托到对应服务。
"""

import os
import uuid
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse

from .state import app

file_router = APIRouter()

# ── 商品图片目录缓存 ──
_product_image_dirs_cache = None


def _get_product_image_dirs():
    """扫描 docs/test/ 下所有品类的 images 目录，结果缓存"""
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


# ── 商品图片文件服务 ──

@file_router.get("/product-images/{filename:path}")
async def product_image(filename: str):
    """提供商品图片文件（自动搜索 Cloth/Digital/Food/Beauty 等所有品类目录）"""
    for img_dir in _get_product_image_dirs():
        file_path = os.path.join(img_dir, filename)
        if os.path.exists(file_path):
            return FileResponse(file_path, media_type="image/jpeg")
    return JSONResponse({"error": "图片不存在"}, status_code=404)


# ── 语音播报文件服务 ──

@file_router.get("/voice/playback/{filename}")
async def voice_playback(filename: str):
    """提供 TTS 生成的音频文件"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    audio_dir = os.path.join(base_dir, "uploads", "audio")
    file_path = os.path.join(audio_dir, filename)
    if not os.path.exists(file_path):
        return JSONResponse({"error": "音频文件不存在"}, status_code=404)
    return FileResponse(file_path, media_type="audio/mpeg")


# ── 文档管理 ──

@file_router.get("/documents/list")
async def list_documents():
    """列出已上传至向量库的文档"""
    if app.handlers_instance is None:
        return {"documents": [], "total": 0, "message": "服务未初始化"}

    try:
        result_dict = app.handlers_instance.rag_service.vector_store.list_documents()
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


@file_router.post("/document/upload")
async def upload_document(file: UploadFile = File(...)):
    """文档上传接口

    接收上传的文件，解析内容，切分为知识片段，存入向量库。
    对于JSON商品文件，自动提取商品元数据加入轻量关联图谱。
    """
    if app.handlers_instance is None:
        return {"status": "❌ 服务未初始化", "error": "app.handlers_instance is None"}

    try:
        chunks = await app.handlers_instance.document_processor.process_uploaded_file(file)

        # 把知识片段存入向量库
        app.handlers_instance.rag_service.vector_store.add_documents(chunks)

        # 自动重建 BM25 关键词索引
        if app.handlers_instance.rag_service.bm25_service is not None:
            try:
                all_docs = app.handlers_instance.rag_service.vector_store.list_documents().get("documents", [])
                app.handlers_instance.rag_service.bm25_service.build_index(all_docs)
            except Exception as e:
                print(f"BM25 索引重建失败，混合检索将自动降级: {str(e)}")

        # 如果是商品JSON文件，自动构建图谱基础节点
        import json
        _, suffix = os.path.splitext(file.filename)
        if suffix.lower() == ".json":
            # 重新读取内容解析商品信息
            await file.seek(0)
            content_bytes = await file.read()
            product_data = json.loads(content_bytes.decode("utf-8"))

            if isinstance(product_data, list):
                for item in product_data:
                    app.handlers_instance.ecommerce_graph.build_from_product_json(item)
            else:
                app.handlers_instance.ecommerce_graph.build_from_product_json(product_data)

        return {
            "status": f"✅ 文档 {file.filename} 已成功解析，共生成 {len(chunks)} 个知识片段存入向量库，基础图谱节点已构建",
            "chunks_count": len(chunks)
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"status": f"❌ 处理失败: {str(e)}", "error": str(e)}


@file_router.post("/document/preview-chunks")
async def preview_chunks(file: UploadFile = File(...)):
    """分块预览调试接口 — 仅展示切分结果，不存入向量库

    用于检查分块策略是否符合预期，优化chunk_size等参数。
    """
    if app.handlers_instance is None:
        return {"status": "❌ 服务未初始化", "error": "app.handlers_instance is None"}

    try:
        chunks = await app.handlers_instance.document_processor.process_uploaded_file(file)

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


@file_router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    """删除指定 ID 的文档"""
    if app.handlers_instance is None:
        return {"status": "error", "message": "服务未初始化"}

    try:
        app.handlers_instance.rag_service.vector_store.delete_document([doc_id])
        return {"status": "success", "message": f"文档 {doc_id} 已删除"}
    except Exception as e:
        return {"status": "error", "message": f"删除失败: {str(e)}"}
