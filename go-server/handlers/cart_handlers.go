// Package handlers 购物车 HTTP API
package handlers

import (
	"log"
	"net/http"
	"strconv"

	"ecommerce-rag-agent/go-server/store"

	"github.com/gin-gonic/gin"
)

// CartHandler 购物车 HTTP 处理器
type CartHandler struct {
	cart *store.CartStore
}

// NewCartHandler 创建购物车处理器
func NewCartHandler(cartStore *store.CartStore) *CartHandler {
	return &CartHandler{cart: cartStore}
}

// ── 请求模型 ────────────────────────────────────────────────

type AddCartReq struct {
	ProductID   string  `json:"product_id" binding:"required"`
	ProductName string  `json:"product_name" binding:"required"`
	Price       float64 `json:"price"`
	Quantity    int     `json:"quantity"`
}

type RemoveCartReq struct {
	ProductID string `json:"product_id"`
	ProductName string `json:"product_name"` // 按名称删除
	ByIndex   *int   `json:"by_index"`        // 按序号删除 (1-based)
}

type UpdateQtyReq struct {
	ProductID string `json:"product_id"`
	Quantity  int    `json:"quantity" binding:"required"`
}

type OrderReq struct {
	Address      string `json:"address"`
	ContactName  string `json:"contact_name"`
	ContactPhone string `json:"contact_phone"`
}

// userID 从 query 参数获取，默认值 "default"
func getUserID(c *gin.Context) string {
	uid := c.Query("user_id")
	if uid == "" {
		uid = "default"
	}
	return uid
}

// ── 购物车 CRUD API ──────────────────────────────────────────

// CartList GET /api/cart/list — 查看购物车
func (h *CartHandler) CartList(c *gin.Context) {
	uid := getUserID(c)
	summary, err := h.cart.List(uid)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	log.Printf("[Cart] list user_id=%s items=%d", uid, summary.Count)
	c.JSON(http.StatusOK, gin.H{"cart": summary})
}

// CartAdd POST /api/cart/add — 添加商品到购物车
func (h *CartHandler) CartAdd(c *gin.Context) {
	var req AddCartReq
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	if req.Quantity <= 0 {
		req.Quantity = 1
	}
	// 简单实现：循环调用 Add（每次 quantity+1）来处理指定数量
	summary, err := h.cart.Add(getUserID(c), req.ProductID, req.ProductName, req.Price)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	// 如果 quantity > 1，再追加 quantity-1 次
	if req.Quantity > 1 {
		for i := 1; i < req.Quantity; i++ {
			summary, err = h.cart.Add(getUserID(c), req.ProductID, req.ProductName, req.Price)
			if err != nil {
				break
			}
		}
	}
	c.JSON(http.StatusOK, gin.H{
		"action":  "add",
		"cart":    summary,
		"message": summaryMsg("add", summary, req.ProductName),
	})
}

// CartRemove DELETE /api/cart/remove — 移除商品
// 支持 query: ?product_id=xxx 或 ?product_name=xxx 或 ?index=1
func (h *CartHandler) CartRemove(c *gin.Context) {
	userID := getUserID(c)

	// 按序号删除
	if indexStr := c.Query("index"); indexStr != "" {
		idx, err := strconv.Atoi(indexStr)
		if err == nil {
			item, ferr := h.cart.FindByIndex(userID, idx)
			if ferr != nil {
				c.JSON(http.StatusNotFound, gin.H{"error": ferr.Error()})
				return
			}
			summary, err := h.cart.Remove(userID, strconv.Itoa(item.ID), true)
			if err != nil {
				c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
				return
			}
			c.JSON(http.StatusOK, gin.H{"action": "remove", "cart": summary})
			return
		}
	}

	// 按 product_id 删除
	if pid := c.Query("product_id"); pid != "" {
		summary, err := h.cart.Remove(userID, pid, false)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
		c.JSON(http.StatusOK, gin.H{"action": "remove", "cart": summary})
		return
	}

	// 按名称删除
	if name := c.Query("product_name"); name != "" {
		item, err := h.cart.FindByName(userID, name)
		if err != nil {
			c.JSON(http.StatusNotFound, gin.H{"error": err.Error()})
			return
		}
		summary, err := h.cart.Remove(userID, strconv.Itoa(item.ID), true)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
		c.JSON(http.StatusOK, gin.H{"action": "remove", "cart": summary})
		return
	}

	c.JSON(http.StatusBadRequest, gin.H{"error": "需要提供 product_id、product_name 或 index 参数"})
}

// CartUpdateQty PUT /api/cart/update-qty — 修改数量
func (h *CartHandler) CartUpdateQty(c *gin.Context) {
	userID := getUserID(c)

	// 按 index 定位商品
	if indexStr := c.Query("index"); indexStr != "" {
		idx, err := strconv.Atoi(indexStr)
		if err == nil {
			item, ferr := h.cart.FindByIndex(userID, idx)
			if ferr != nil {
				c.JSON(http.StatusNotFound, gin.H{"error": ferr.Error()})
				return
			}
			var req UpdateQtyReq
			if err := c.ShouldBindJSON(&req); err != nil {
				c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
				return
			}
			summary, err := h.cart.UpdateQuantity(userID, item.ProductID, req.Quantity)
			if err != nil {
				c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
				return
			}
			c.JSON(http.StatusOK, gin.H{"action": "update_qty", "cart": summary})
			return
		}
	}

	// 按 product_id 修改
	var req UpdateQtyReq
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	if req.ProductID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "需要提供 product_id 或 index 参数"})
		return
	}
	summary, err := h.cart.UpdateQuantity(userID, req.ProductID, req.Quantity)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"action": "update_qty", "cart": summary})
}

// CartToggleSelect POST /api/cart/toggle-select — 切换勾选
func (h *CartHandler) CartToggleSelect(c *gin.Context) {
	userID := getUserID(c)
	var req struct {
		ProductID string `json:"product_id" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	summary, err := h.cart.ToggleSelect(userID, req.ProductID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"action": "toggle_select", "cart": summary})
}

// CartClear DELETE /api/cart/clear — 清空购物车
func (h *CartHandler) CartClear(c *gin.Context) {
	if err := h.cart.Clear(getUserID(c)); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"action": "clear", "cart": &store.CartSummary{Items: []store.CartItem{}}})
}

// ── 下单 API ─────────────────────────────────────────────────

// CartOrderPreview GET /api/cart/order/preview — 订单预览
func (h *CartHandler) CartOrderPreview(c *gin.Context) {
	summary, err := h.cart.PreviewOrder(getUserID(c))
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"preview": summary})
}

// CartOrderConfirm POST /api/cart/order/confirm — 确认下单
func (h *CartHandler) CartOrderConfirm(c *gin.Context) {
	var req OrderReq
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	result, err := h.cart.CreateOrder(getUserID(c), req.Address, req.ContactName, req.ContactPhone)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"action": "order_confirm", "order": result})
}

// ── 辅助函数 ─────────────────────────────────────────────────

func summaryMsg(action string, summary *store.CartSummary, productName string) string {
	switch action {
	case "add":
		return productName + " 已加入购物车（共 " + strconv.Itoa(summary.Count) + " 件）"
	default:
		return "购物车已更新"
	}
}
