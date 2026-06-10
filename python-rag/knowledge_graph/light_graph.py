"""轻量JSON电商关联图谱模块

完全基于纯JSON文件实现，零依赖图数据库，专为电商场景设计。
核心能力：商品关联查询、品牌查询、分类查询、同款替代推荐、搭配推荐。
"""

import os
import json
from typing import List, Dict, Any, Optional


class LightEcommerceGraph:
    """轻量电商关联图谱类
    
    数据结构完全基于字典实现，持久化到本地JSON文件，开箱即用。
    """

    def __init__(self, graph_file_path: str = "./ecommerce_graph.json"):
        """
        初始化轻量电商关联图谱

        Args:
            graph_file_path: 图谱JSON文件的持久化路径
        """
        self.graph_file_path = graph_file_path
        
        # 初始化空图结构
        self.nodes: Dict[str, Dict[str, Any]] = {}  # key: node_id, value: node详情
        self.edges: List[Dict[str, Any]] = []  # 所有边的列表
        
        # 从文件加载已有图谱（如果存在）
        self._load_graph()

    def _load_graph(self):
        """从本地JSON文件加载图谱"""
        if os.path.exists(self.graph_file_path):
            try:
                with open(self.graph_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.nodes = data.get("nodes", {})
                    self.edges = data.get("edges", [])
            except Exception as e:
                print(f"⚠️ 图谱文件加载失败，初始化空图谱: {str(e)}")
                self.nodes = {}
                self.edges = []

    def _save_graph(self):
        """把当前图谱状态持久化到本地JSON文件"""
        try:
            os.makedirs(os.path.dirname(self.graph_file_path), exist_ok=True)
            with open(self.graph_file_path, "w", encoding="utf-8") as f:
                json.dump({
                    "nodes": self.nodes,
                    "edges": self.edges
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ 图谱持久化失败: {str(e)}")

    def add_node(self, node_id: str, node_type: str, properties: Optional[Dict[str, Any]] = None):
        """
        添加一个实体节点到图谱

        Args:
            node_id: 节点唯一ID
            node_type: 节点类型（product/brand/category）
            properties: 节点属性字典
        """
        self.nodes[node_id] = {
            "id": node_id,
            "type": node_type,
            "properties": properties or {}
        }
        self._save_graph()

    def add_edge(self, from_id: str, to_id: str, relation_type: str, properties: Optional[Dict[str, Any]] = None):
        """
        在两个节点之间添加一条关联边

        Args:
            from_id: 起始节点ID
            to_id: 目标节点ID
            relation_type: 关系类型（belongs_to_brand / belongs_to_category / same_product / match_product）
            properties: 边的属性字典
        """
        for edge in self.edges:
            if edge["from"] == from_id and edge["to"] == to_id and edge["relation"] == relation_type:
                return
        self.edges.append({
            "from": from_id,
            "to": to_id,
            "relation": relation_type,
            "properties": properties or {}
        })
        self._save_graph()

    def get_related_products(self, product_id: str, relation_type: str) -> List[Dict[str, Any]]:
        """
        查询指定商品的所有关联商品（指定关系类型）

        Args:
            product_id: 中心商品ID
            relation_type: 要查询的关系类型

        Returns:
            关联节点列表
        """
        result = []
        for edge in self.edges:
            if edge["from"] == product_id and edge["relation"] == relation_type:
                target_id = edge["to"]
                if target_id in self.nodes:
                    result.append(self.nodes[target_id])
            elif edge["to"] == product_id and edge["relation"] == relation_type:
                source_id = edge["from"]
                if source_id in self.nodes:
                    result.append(self.nodes[source_id])
        return result

    def get_same_style_products(self, product_id: str) -> List[Dict[str, Any]]:
        """获取同款替代商品列表"""
        return self.get_related_products(product_id, "same_product")

    def get_match_products(self, product_id: str) -> List[Dict[str, Any]]:
        """获取搭配推荐商品列表"""
        return self.get_related_products(product_id, "match_product")

    def get_same_sub_category_products(self, product_id: str, limit: int = 2) -> List[Dict[str, Any]]:
        """获取同一子分类下的竞品商品列表（不含自身）

        通过比较节点属性中的 sub_category 字段查找同品类商品。

        Args:
            product_id: 中心商品ID（如 "product:p_clothes_005"）
            limit: 最多返回的竞品数量

        Returns:
            同子分类商品节点列表，按价格从低到高排列（不含自身）
        """
        node_key = product_id if product_id.startswith("product:") else f"product:{product_id}"
        if node_key not in self.nodes:
            return []

        my_props = self.nodes[node_key].get("properties", {})
        my_sub = my_props.get("sub_category", "")
        my_price = float(my_props.get("price", 0))

        if not my_sub:
            return []

        same_list = []
        for nid, node in self.nodes.items():
            if nid == node_key or not nid.startswith("product:"):
                continue
            props = node.get("properties", {})
            if props.get("sub_category", "") == my_sub:
                same_list.append((float(props.get("price", 0)), node))

        same_list.sort(key=lambda x: x[0])
        return [node for _, node in same_list[:limit]]

    def build_same_sub_category_edges(self):
        """自动为所有商品按 sub_category 属性建 same_sub_category 边

        遍历所有 product 节点，将同一 sub_category 下的商品两两互连，
        跳过已存在相同边的节点对。适用于从商品 JSON 数据批量填充图谱后调用。
        所有边添加完后只持久化一次，避免逐条写入磁盘。
        """
        existing_edges = set()
        for e in self.edges:
            if e.get("relation") == "same_sub_category":
                existing_edges.add((e["from"], e["to"]))

        cat_groups: Dict[str, List[str]] = {}
        for nid, node in self.nodes.items():
            if not nid.startswith("product:"):
                continue
            sub = node.get("properties", {}).get("sub_category", "")
            if sub:
                cat_groups.setdefault(sub, []).append(nid)

        added = 0
        for sub, pids in cat_groups.items():
            for i in range(len(pids)):
                for j in range(i + 1, len(pids)):
                    pair = (pids[i], pids[j])
                    rev_pair = (pids[j], pids[i])
                    if pair in existing_edges or rev_pair in existing_edges:
                        continue
                    self.edges.append({
                        "from": pids[i],
                        "to": pids[j],
                        "relation": "same_sub_category",
                        "properties": {}
                    })
                    existing_edges.add(pair)
                    added += 1

        self._save_graph()
        print(f"[图谱] same_sub_category 边自动构建完成，共 {added} 条，覆盖 {len(cat_groups)} 个子分类")

    def get_products_by_brand(self, brand_id: str) -> List[Dict[str, Any]]:
        """查询指定品牌下的所有商品"""
        result = []
        for edge in self.edges:
            if edge["to"] == brand_id and edge["relation"] == "belongs_to_brand":
                source_id = edge["from"]
                if source_id in self.nodes:
                    result.append(self.nodes[source_id])
        return result

    def get_products_by_category(self, category_id: str) -> List[Dict[str, Any]]:
        """查询指定分类下的所有商品"""
        result = []
        for edge in self.edges:
            if edge["to"] == category_id and edge["relation"] == "belongs_to_category":
                source_id = edge["from"]
                if source_id in self.nodes:
                    result.append(self.nodes[source_id])
        return result

    def build_from_product_json(self, product_data: Dict[str, Any]):
        """
        从商品结构化JSON数据自动构建图谱节点和基础关联

        Args:
            product_data: 商品JSON字典
        """
        product_id = product_data.get("product_id", "")
        title = product_data.get("title", "")
        brand = product_data.get("brand", "")
        category = product_data.get("category", "")
        price = product_data.get("base_price", product_data.get("price", 0))

        # 添加商品节点
        self.add_node(
            f"product:{product_id}",
            "product",
            {
                "title": title,
                "product_id": product_id,
                "price": float(price or 0),
                "brand_name": brand,
                "category": category,
                "sub_category": product_data.get("sub_category", ""),
            }
        )

        # 添加品牌节点和关联
        if brand:
            brand_node_id = f"brand:{brand}"
            if brand_node_id not in self.nodes:
                self.add_node(brand_node_id, "brand", {"name": brand})
            self.add_edge(f"product:{product_id}", brand_node_id, "belongs_to_brand")

        # 添加分类节点和关联
        if category:
            category_node_id = f"category:{category}"
            if category_node_id not in self.nodes:
                self.add_node(category_node_id, "category", {"name": category})
            self.add_edge(f"product:{product_id}", category_node_id, "belongs_to_category")

        print(f"✅ 商品 {product_id} 已成功加入图谱")

    def export_debug_info(self) -> Dict[str, Any]:
        """导出图谱调试统计信息"""
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "node_types": list(set([n["type"] for n in self.nodes.values()])),
            "relation_types": list(set([e["relation"] for e in self.edges]))
        }
