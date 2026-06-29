// Package handlers Go 网关处理器
//
// 两类 Handler：
//   - 反向代理：/api/* → 去掉 /api 前缀 → 透传 Python
//   - 会话管理：create / list / history / delete / save-message
package handlers

import (
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"

	"ecommerce-rag-agent/go-server/store"

	"github.com/gin-gonic/gin"
)

// Handler 网关处理器，聚合代理 + 会话存储
type Handler struct {
	proxy   *httputil.ReverseProxy
	session *store.SessionStore
}

// NewHandler 创建处理器，配置 SSE 流代理支持
func NewHandler(targetURL string, sess *store.SessionStore) (*Handler, error) {
	target, err := url.Parse(targetURL)
	if err != nil {
		return nil, err
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	// 启用定期刷新以支持 SSE 流式传输
	proxy.FlushInterval = 50 * time.Millisecond
	return &Handler{
		proxy:   proxy,
		session: sess,
	}, nil
}

// ── 反向代理 ────────────────────────────────────────────────

// DirectProxy 直接代理（不修改路径）→ 用于 /voice/playback/*, /product-images/*
func (h *Handler) DirectProxy(c *gin.Context) {
	h.proxy.ServeHTTP(c.Writer, c.Request)
}

// APIProxy 去掉 /api 前缀后代理到 Python
func (h *Handler) APIProxy(c *gin.Context) {
	c.Request.URL.Path = strings.TrimPrefix(c.Request.URL.Path, "/api")
	h.proxy.ServeHTTP(c.Writer, c.Request)
}

// ── 会话管理 ────────────────────────────────────────────────

// CreateSession POST /api/session/create
func (h *Handler) CreateSession(c *gin.Context) {
	id, err := h.session.Create()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"session_id": id})
}

// ListSessions GET /api/sessions
func (h *Handler) ListSessions(c *gin.Context) {
	sessions := h.session.List()
	if sessions == nil {
		sessions = []map[string]interface{}{}
	}
	c.JSON(http.StatusOK, gin.H{"sessions": sessions})
}

// GetSessionHistory GET /api/session/:id/history
func (h *Handler) GetSessionHistory(c *gin.Context) {
	id := c.Param("id")
	sess, ok := h.session.Get(id)
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "会话不存在"})
		return
	}
	history := make([]map[string]interface{}, 0, len(sess.Messages))
	for _, m := range sess.Messages {
		entry := map[string]interface{}{
			"role":    m.Role,
			"content": m.Content,
			"time":    m.Time,
		}
		if m.Cards != "" {
			entry["cards"] = m.Cards
		}
		if m.VoiceUrl != "" {
			entry["voice_url"] = m.VoiceUrl
		}
		history = append(history, entry)
	}
	c.JSON(http.StatusOK, gin.H{"history": history})
}

// DeleteSession DELETE /api/sessions/:id
func (h *Handler) DeleteSession(c *gin.Context) {
	id := c.Param("id")
	if err := h.session.Delete(id); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

// SaveSessionMessage POST /api/session/:id/message
func (h *Handler) SaveSessionMessage(c *gin.Context) {
	id := c.Param("id")
	var req struct {
		Role     string `json:"role" binding:"required"`
		Content  string `json:"content" binding:"required"`
		Cards    string `json:"cards,omitempty"`
		VoiceUrl string `json:"voice_url,omitempty"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	if err := h.session.SaveMessage(id, req.Role, req.Content, req.Cards, req.VoiceUrl); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"ok": true})
}
