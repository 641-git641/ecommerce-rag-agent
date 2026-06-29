"""轻量关联图谱 API 路由

从 api/routes.py 抽取，提供知识图谱节点的 CRUD 和查询接口。
所有端点通过 app.handlers_instance.ecommerce_graph 委托到 LightEcommerceGraph。
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any

from .state import app


# ── 请求模型 ──

class AddNodeRequest(BaseModel):
    node_id: str
    node_type: str
    properties: Optional[Dict[str, Any]] = None


class AddEdgeRequest(BaseModel):
    from_id: str
    to_id: str
    relation_type: str
    properties: Optional[Dict[str, Any]] = None


# ── 子路由 ──

graph_router = APIRouter()


@graph_router.post("/node/add")
async def graph_add_node(req: AddNodeRequest):
    """向图谱添加实体节点"""
    if app.handlers_instance is None:
        return JSONResponse({"status": "error", "message": "服务未初始化"}, status_code=503)
    app.handlers_instance.ecommerce_graph.add_node(req.node_id, req.node_type, req.properties)
    return {"status": "success", "message": f"节点 {req.node_id} 已成功添加"}


@graph_router.post("/edge/add")
async def graph_add_edge(req: AddEdgeRequest):
    """在图谱中添加关联边"""
    if app.handlers_instance is None:
        return JSONResponse({"status": "error", "message": "服务未初始化"}, status_code=503)
    app.handlers_instance.ecommerce_graph.add_edge(req.from_id, req.to_id, req.relation_type, req.properties)
    return {"status": "success", "message": f"边 {req.from_id} -> {req.to_id} [{req.relation_type}] 已成功添加"}


@graph_router.get("/product/{product_id}/same-style")
async def graph_get_same_style(product_id: str):
    """获取指定商品的同款替代推荐列表"""
    if app.handlers_instance is None:
        return JSONResponse({"status": "error", "message": "服务未初始化"}, status_code=503)
    products = app.handlers_instance.ecommerce_graph.get_same_style_products(f"product:{product_id}")
    return {"status": "success", "products": products}


@graph_router.get("/product/{product_id}/match")
async def graph_get_match_products(product_id: str):
    """获取指定商品的搭配推荐列表"""
    if app.handlers_instance is None:
        return JSONResponse({"status": "error", "message": "服务未初始化"}, status_code=503)
    products = app.handlers_instance.ecommerce_graph.get_match_products(f"product:{product_id}")
    return {"status": "success", "products": products}


@graph_router.get("/brand/{brand_id}/products")
async def graph_get_brand_products(brand_id: str):
    """查询指定品牌下的所有商品"""
    if app.handlers_instance is None:
        return JSONResponse({"status": "error", "message": "服务未初始化"}, status_code=503)
    products = app.handlers_instance.ecommerce_graph.get_products_by_brand(f"brand:{brand_id}")
    return {"status": "success", "products": products}


@graph_router.get("/debug")
async def graph_get_debug():
    """获取图谱调试统计信息"""
    if app.handlers_instance is None:
        return JSONResponse({"status": "error", "message": "服务未初始化"}, status_code=503)
    debug_info = app.handlers_instance.ecommerce_graph.export_debug_info()
    return {"status": "success", "debug_info": debug_info}
