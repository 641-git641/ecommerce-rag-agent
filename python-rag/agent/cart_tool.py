"""购物车工具 — CartTool + 6 个操作实现

从 tools.py 中解耦出来，依赖 CartAPIClient 做 HTTP 通信。
"""

import json
from typing import Any, Dict

import requests

from .cart_client import CartAPIClient
from .tools import AgentToolBase


class CartTool(AgentToolBase):
    def name(self): return "cart"

    def description(self):
        return "购物车管理，支持操作: add(加购)/remove(删除)/view(查看)/clear(清空)/update_qty(改数量)/order_preview(订单预览)/order_confirm(确认下单)"

    def __init__(self, rag_service, llm_service, cart_api_client=None):
        super().__init__(rag_service, llm_service)
        self.cart_api = cart_api_client

    def execute(self, params, session_id="default"):
        action = params.get("action", "view")
        product = params.get("product", params.get("query", ""))
        quantity = params.get("quantity", 1)

        if self.cart_api is None:
            return {"answer": "购物车服务暂未连接，请联系管理员。", "type": "cart_error"}

        try:
            if action == "add":
                return self._handle_add(session_id, product, quantity)

            elif action == "remove":
                return self._handle_remove(session_id, product)

            elif action == "update_qty":
                return self._handle_update_qty(session_id, product, quantity)

            elif action == "clear":
                self.cart_api.clear_cart(session_id)
                return {"answer": "购物车已清空", "action": "clear", "type": "cart_action"}

            elif action == "order_preview":
                return self._handle_order_preview(session_id)

            elif action == "order_confirm":
                address = params.get("address", "")
                contact_name = params.get("contact_name", "")
                contact_phone = params.get("contact_phone", "")
                return self._handle_order_confirm(session_id, address, contact_name, contact_phone)

            else:  # view
                return self._handle_view(session_id)

        except requests.RequestException as e:
            print(f"[CartTool] API 调用失败: {e}")
            return {"answer": "购物车服务暂时无法访问，请稍后重试。", "type": "cart_error"}

    # ── 操作实现 ──────────────────────────────────────────

    def _handle_add(self, user_id, product, quantity):
        """加购：解析商品信息并调用 Go API"""
        product_id = ""
        product_name = product or "未知商品"
        price = 0.0

        if isinstance(product, dict):
            product_name = product.get("name", product.get("product_name", "未知商品"))
            product_id = product.get("product_id", "")
            price = float(product.get("price", 0) or 0)
        elif isinstance(product, str) and product.startswith("{"):
            try:
                d = json.loads(product)
                product_name = d.get("name", d.get("product_name", product_name))
                product_id = d.get("product_id", "")
                price = float(d.get("price", 0) or 0)
            except (json.JSONDecodeError, ValueError):
                pass

        result = self.cart_api.add_item(user_id, product_id, product_name, price, quantity)
        cart = result.get("cart", {})

        answer = f"已将「{product_name}」加入购物车！"
        if cart.get("count", 0) > 1:
            answer += f" 当前共 {cart['count']} 件商品"
            if cart.get("total", 0) > 0:
                answer += f"，合计 ¥{cart['total']:.0f}"
        return {"answer": answer, "action": "add", "cart": cart, "type": "cart_action"}

    def _handle_remove(self, user_id, product):
        """删除商品：支持名称/序号/JSON对象"""
        if isinstance(product, dict):
            product_id = product.get("product_id", "")
            product_name = product.get("name", product.get("product_name", ""))
            result = self.cart_api.remove_item(user_id, product_id=product_id)
            return {"answer": f"已从购物车移除「{product_name}」", "action": "remove",
                    "cart": result.get("cart", {}), "type": "cart_action"}

        cart_info = self.cart_api.get_cart_for_llm(user_id)
        items = cart_info.get("items", [])

        if not items:
            return {"answer": "购物车是空的，没有可以删除的商品。", "type": "cart_action"}

        product_id = ""
        product_name = product
        index = self._parse_index(product)

        if index > 0 and index <= len(items):
            target = items[index - 1]
            product_id = target.get("product_id", "")
            product_name = target.get("product_name", target.get("name", product))
        elif not product_id:
            for item in items:
                name = item.get("product_name", item.get("name", ""))
                if product and product.lower() in name.lower():
                    product_id = item.get("product_id", "")
                    product_name = name
                    break

        if not product_id:
            item_list = "、".join(
                f"{i + 1}. {it.get('product_name', it.get('name', '?'))}"
                for i, it in enumerate(items)
            )
            return {"answer": f"未找到「{product}」。当前购物车有：{item_list}。请指定序号或商品名。",
                    "type": "cart_action"}

        result = self.cart_api.remove_item(user_id, product_id=product_id)
        return {"answer": f"已从购物车移除「{product_name}」", "action": "remove",
                "cart": result.get("cart", {}), "type": "cart_action"}

    def _handle_update_qty(self, user_id, product, quantity):
        """修改数量：支持名称/序号/JSON对象"""
        if isinstance(product, dict):
            product_id = product.get("product_id", "")
            product_name = product.get("name", product.get("product_name", ""))
            result = self.cart_api.update_qty(user_id, product_id, quantity)
            return {"answer": f"已将「{product_name}」的数量修改为 {quantity}",
                    "action": "update_qty", "cart": result.get("cart", {}), "type": "cart_action"}

        cart_info = self.cart_api.get_cart_for_llm(user_id)
        items = cart_info.get("items", [])

        if not items:
            return {"answer": "购物车是空的，没有可以修改的商品。", "type": "cart_action"}

        product_id = ""
        product_name = product
        index = self._parse_index(product)

        if index > 0 and index <= len(items):
            target = items[index - 1]
            product_id = target.get("product_id", "")
            product_name = target.get("product_name", target.get("name", product))
        else:
            for item in items:
                name = item.get("product_name", item.get("name", ""))
                if product and product.lower() in name.lower():
                    product_id = item.get("product_id", "")
                    product_name = name
                    break

        if not product_id:
            return {"answer": f"未找到「{product}」对应的商品。", "type": "cart_action"}

        result = self.cart_api.update_qty(user_id, product_id, quantity, index=index)
        return {"answer": f"已将「{product_name}」的数量修改为 {quantity}",
                "action": "update_qty", "cart": result.get("cart", {}), "type": "cart_action"}

    def _handle_view(self, user_id):
        """查看购物车"""
        cart_info = self.cart_api.get_cart_for_llm(user_id)
        items = cart_info.get("items", [])
        total = cart_info.get("total", 0)
        count = cart_info.get("count", 0)

        if count == 0:
            return {"answer": "您的购物车还是空的，去看看有什么好物吧！",
                    "cart": {"items": [], "total": 0, "count": 0}, "type": "cart_view"}

        lines = [f"## 您的购物车\n\n共 {count} 件商品\n"]
        lines.append("| # | 商品 | 单价 | 数量 |")
        lines.append("|---|---|---|---|")
        for item in items:
            name = item.get("product_name", item.get("name", "?"))
            price = f"¥{item.get('price', 0):.0f}" if item.get("price", 0) > 0 else "-"
            qty = item.get("quantity", 1)
            lines.append(f"| {item.get('_pos', '?')} | {name} | {price} | {qty} |")
        if total > 0:
            lines.append(f"\n**合计: ¥{total:.0f}**")
        lines.append("\n可以对我说「删除第X个」「把数量改成N」或「去下单」来管理购物车。")

        return {"answer": "\n".join(lines), "cart": cart_info, "type": "cart_view"}

    def _handle_order_preview(self, user_id):
        """订单预览"""
        try:
            preview = self.cart_api.order_preview(user_id)
            preview_data = preview.get("preview", {})
            items = preview_data.get("items", [])
            total = preview_data.get("total", 0)

            if not items:
                return {"answer": "没有选中商品（请先勾选要购买的商品），无法生成订单预览。",
                        "type": "cart_action"}

            lines = [f"## 订单预览\n"]
            lines.append("| # | 商品 | 单价 | 数量 |")
            lines.append("|---|---|---|---|")
            for i, item in enumerate(items):
                name = item.get("product_name", "?")
                price = f"¥{item.get('price', 0):.0f}"
                qty = item.get("quantity", 1)
                lines.append(f"| {i + 1} | {name} | {price} | {qty} |")
            lines.append(f"\n**合计: ¥{total:.0f}**")
            lines.append("\n请确认收货地址和联系方式，回复「确认下单 地址：xxx 姓名：xxx 电话：xxx」完成下单。")

            return {"answer": "\n".join(lines), "preview": preview_data, "type": "cart_action"}

        except Exception as e:
            return {"answer": f"订单预览失败: {e}", "type": "cart_error"}

    def _handle_order_confirm(self, user_id, address, contact_name, contact_phone):
        """确认下单"""
        try:
            result = self.cart_api.order_confirm(user_id, address, contact_name, contact_phone)
            order = result.get("order", {})
            order_no = order.get("order_no", "?")
            total = order.get("total_amount", 0)
            items = order.get("items", [])

            answer = f"## 下单成功！\n\n"
            answer += f"订单编号：**{order_no}**\n"
            answer += f"收货地址：{address or '未填写'}\n"
            answer += f"联系人：{contact_name or '未填写'}\n"
            if total > 0:
                answer += f"支付金额：**¥{total:.0f}**\n"
            answer += f"\n共 {len(items)} 件商品：\n"
            for item in items:
                name = item.get("product_name", "?")
                qty = item.get("quantity", 1)
                price = item.get("price", 0)
                answer += f"  - {name} x{qty} (¥{price:.0f})\n"
            answer += "\n感谢您的购买！我们会尽快为您发货。"

            return {"answer": answer, "action": "order_confirm", "order": order, "type": "cart_action"}

        except Exception as e:
            return {"answer": f"下单失败: {e}。请检查地址信息是否完整。", "type": "cart_error"}

    @staticmethod
    def _parse_index(product: str) -> int:
        """解析中文序号: 第一个/第二/3 → 整数"""
        chinese_nums = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                        "第一": 1, "第二": 2, "第三": 3, "第四": 4, "第五": 5}
        for label, num in chinese_nums.items():
            if label in product:
                return num
        try:
            return int(product)
        except ValueError:
            return 0
