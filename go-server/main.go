// Package main Go API 网关
//
// 职责：
//   - 会话管理：create / list / history / delete / save-message（MySQL 持久化）
//   - 购物车：MySQL 持久化，RESTful API（Go 自管）
//   - 反向代理：/api/* → 去掉 /api 前缀 → 透传到 Python FastAPI
//   - 静态文件：/voice/playback/*, /product-images/* → 直接透传
package main

import (
	"log"
	"net/http"
	"strings"

	"ecommerce-rag-agent/go-server/config"
	"ecommerce-rag-agent/go-server/db"
	"ecommerce-rag-agent/go-server/handlers"
	"ecommerce-rag-agent/go-server/store"

	"github.com/gin-gonic/gin"
)

func main() {
	cfg := config.Load()

	// MySQL 连接池（购物车 + 会话 共用）
	mysqlDB, err := db.NewMySQL(cfg.MySQL.DSN)
	if err != nil {
		log.Fatalf("[Gateway] MySQL 连接失败: %v", err)
	}
	defer mysqlDB.Close()

	// 会话持久化（MySQL）
	sess := store.NewSessionStore(mysqlDB)

	h := handlers.NewHandler(cfg.PythonRAG.BaseURL, sess)

	// 购物车持久化（MySQL，复用同一连接池）
	cartStore := store.NewCartStore(mysqlDB)
	cartH := handlers.NewCartHandler(cartStore)

	gin.SetMode(cfg.Server.Mode)
	r := gin.Default()

	// 直接代理（路径与 Python 一致，不修改）
	r.Any("/voice/playback/*path", h.DirectProxy)
	r.Any("/product-images/*path", h.DirectProxy)

	api := r.Group("/api")
	{
		// ── 会话管理（Go 自管）──
		api.POST("/session/create", h.CreateSession)
		api.GET("/sessions", h.ListSessions)
		api.GET("/session/:id/history", h.GetSessionHistory)
		api.DELETE("/sessions/:id", h.DeleteSession)
		api.POST("/session/:id/message", h.SaveSessionMessage)

		// ── 购物车（Go 自管，MySQL 持久化）──
		api.GET("/cart/list", cartH.CartList)
		api.POST("/cart/add", cartH.CartAdd)
		api.DELETE("/cart/remove", cartH.CartRemove)
		api.PUT("/cart/update-qty", cartH.CartUpdateQty)
		api.POST("/cart/toggle-select", cartH.CartToggleSelect)
		api.DELETE("/cart/clear", cartH.CartClear)
		api.GET("/cart/order/preview", cartH.CartOrderPreview)
		api.POST("/cart/order/confirm", cartH.CartOrderConfirm)
	}

	// ── 兜底：未匹配的 /api/* 透传到 Python ──
	r.NoRoute(func(c *gin.Context) {
		path := c.Request.URL.Path
		if strings.HasPrefix(path, "/api/") {
			h.APIProxy(c)
			return
		}
		c.JSON(http.StatusNotFound, gin.H{"error": "not found"})
	})

	println("[Gateway] 启动中... 目标: " + cfg.PythonRAG.BaseURL + " 端口: " + cfg.Server.Port)
	r.Run(cfg.Server.Port)
}
