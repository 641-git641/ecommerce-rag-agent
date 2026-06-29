"""图片相关 API 路由

从 api/routes.py 抽取，提供图片理解查询、图片入库、以图搜图功能。
所有端点通过 app.handlers_instance 委托到 VisionService / VisionEmbeddingService / RAGService。
"""

import os
import uuid
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse

from .state import app

image_router = APIRouter()


def extract_keywords(description: str) -> str:
    """从视觉描述文本中提取关键属性值

    解析 VisionService 返回的描述文本（格式如 "品类: T恤\n颜色: 白色"），
    提取冒号后的值作为搜索关键词。供 image_routes 和 chat_multimodal 共用。

    Args:
        description: VisionService 返回的商品描述文本

    Returns:
        空格分隔的关键词字符串
    """
    keywords = []
    for line in description.split("\n"):
        line = line.strip()
        if ":" in line:
            val = line.split(":", 1)[1].strip()
            if val:
                keywords.append(val)
    return " ".join(keywords)


@image_router.post("/query")
async def query_by_image(
    file: UploadFile = File(...),
    question: str = Form(""),
):
    """图片理解+RAG查询：上传商品图片，视觉识别后检索推荐"""
    if app.handlers_instance is None:
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

        description = app.handlers_instance.vision_service.describe_product(image_path)

        if not description:
            os.remove(image_path)
            return {"answer": "❌ 图片识别失败，请确认图片清晰且包含商品信息", "status": "error"}

        keywords = extract_keywords(description)

        merged_query = keywords
        if question.strip():
            merged_query = f"{keywords} {question.strip()}"

        result = app.handlers_instance.rag_service.query(merged_query)

        result["vision_description"] = description
        result["merged_query"] = merged_query

        return result

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"answer": f"❌ 图片查询失败: {str(e)}", "status": "error"}


@image_router.post("/index")
async def index_image(
    file: UploadFile = File(...),
    product_id: str = Form(...),
):
    """图片入库：上传商品图片并向量化存入 ChromaDB 图片向量集合"""
    if app.handlers_instance is None:
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

        image_vector = app.handlers_instance.vision_embedding.embed_image(image_path)
        if image_vector is None:
            os.remove(image_path)
            return {"status": "❌ 图片向量化失败"}

        app.handlers_instance.image_vector_store.add_embeddings(
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


@image_router.post("/search")
async def search_by_image(
    file: UploadFile = File(...),
    top_k: int = Form(5),
):
    """以图搜图：上传图片，通过视觉向量检索相似商品图片"""
    if app.handlers_instance is None:
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

        image_vector = app.handlers_instance.vision_embedding.embed_image(image_path)
        if image_vector is None:
            return {"status": "❌ 查询图片向量化失败", "results": []}

        results = app.handlers_instance.image_vector_store.search_by_vector(image_vector, k=top_k)

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
