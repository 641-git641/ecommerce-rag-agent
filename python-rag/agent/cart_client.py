"""购物车 API 客户端 — 对接 Go 服务端 MySQL 持久化

通过 HTTP 调用 Go 网关的购物车 API，提供增删改查操作。
与 Agent 工具逻辑完全解耦。
"""

from typing import Any, Dict


class CartAPIClient:
    """通过 HTTP 调用 Go 网关的购物车 API"""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _get(self, path: str, user_id: str) -> Dict[str, Any]:
        import requests
        resp = requests.get(f"{self.base_url}{path}", params={"user_id": user_id}, timeout=5)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, user_id: str, body: Dict[str, Any] = None) -> Dict[str, Any]:
        import requests
        resp = requests.post(
            f"{self.base_url}{path}",
            params={"user_id": user_id},
            json=body or {},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str, user_id: str, params: Dict[str, str] = None) -> Dict[str, Any]:
        import requests
        resp = requests.delete(
            f"{self.base_url}{path}",
            params={"user_id": user_id, **(params or {})},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()

    def _put(self, path: str, user_id: str, body: Dict[str, Any], params: Dict[str, str] = None) -> Dict[str, Any]:
        import requests
        resp = requests.put(
            f"{self.base_url}{path}",
            params={"user_id": user_id, **(params or {})},
            json=body,
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()

    def list_cart(self, user_id: str) -> Dict[str, Any]:
        return self._get("/api/cart/list", user_id)

    def add_item(self, user_id: str, product_id: str, product_name: str, price: float = 0, quantity: int = 1) -> Dict[str, Any]:
        return self._post("/api/cart/add", user_id, {
            "product_id": product_id,
            "product_name": product_name,
            "price": price,
            "quantity": quantity,
        })

    def remove_item(self, user_id: str, product_id: str = "", product_name: str = "", index: int = 0) -> Dict[str, Any]:
        params = {}
        if index > 0:
            params["index"] = str(index)
        elif product_id:
            params["product_id"] = product_id
        elif product_name:
            params["product_name"] = product_name
        return self._delete("/api/cart/remove", user_id, params)

    def update_qty(self, user_id: str, product_id: str, quantity: int, index: int = 0) -> Dict[str, Any]:
        params = {}
        if index > 0:
            params["index"] = str(index)
        return self._put("/api/cart/update-qty", user_id, {"product_id": product_id, "quantity": quantity}, params)

    def clear_cart(self, user_id: str) -> Dict[str, Any]:
        return self._delete("/api/cart/clear", user_id, {})

    def order_preview(self, user_id: str) -> Dict[str, Any]:
        return self._get("/api/cart/order/preview", user_id)

    def order_confirm(self, user_id: str, address: str = "", contact_name: str = "", contact_phone: str = "") -> Dict[str, Any]:
        return self._post("/api/cart/order/confirm", user_id, {
            "address": address,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
        })

    def get_cart_for_llm(self, user_id: str) -> Dict[str, Any]:
        """获取购物车状态，转为 LLM 友好格式（含序号映射）"""
        result = self.list_cart(user_id)
        cart_data = result.get("cart", {})
        items = cart_data.get("items", [])
        index_map = {}
        for i, item in enumerate(items):
            idx = i + 1
            item["_pos"] = idx
            item["_label"] = f"第{idx}个"
            index_map[str(idx)] = item.get("product_id", "")
        return {
            "items": items,
            "total": cart_data.get("total", 0),
            "count": cart_data.get("count", 0),
            "selected_count": cart_data.get("selected_count", 0),
            "index_map": index_map,
        }
