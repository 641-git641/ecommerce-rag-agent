// Package store 会话持久化存储（MySQL）
//
// 使用 MySQL 存储会话和消息，与购物车共享同一数据库连接。
// 需要预先执行建表 SQL（见 migrations/ 目录）。
package store

import (
	"database/sql"
	"fmt"
	"time"

	"github.com/google/uuid"
)

// Message 单条聊天消息
type Message struct {
	Role     string `json:"role"` // "user" / "assistant"
	Content  string `json:"content"`
	Time     int64  `json:"time"`                // unix seconds
	Cards    string `json:"cards,omitempty"`     // JSON array of cards
	VoiceUrl string `json:"voice_url,omitempty"` // TTS audio URL
}

// Session 会话元数据 + 消息列表
type Session struct {
	ID        string    `json:"id"`
	Title     string    `json:"title"`
	CreatedAt time.Time `json:"created_at"`
	Messages  []Message `json:"messages"`
	MsgCount  int       `json:"msg_count"`
}

// SessionStore 线程安全的 MySQL 会话存储
// 注意：MySQL 连接池本身线程安全，这里不再需要 sync.RWMutex
type SessionStore struct {
	db *sql.DB
}

// NewSessionStore 创建会话存储实例
// 建表 SQL 已迁移到 migrations/001_init_sessions.sql，需提前手动执行
func NewSessionStore(db *sql.DB) *SessionStore {
	return &SessionStore{db: db}
}

// Create 创建新会话，返回会话 ID
func (s *SessionStore) Create() (string, error) {
	id := uuid.New().String()
	_, err := s.db.Exec(
		`INSERT INTO sessions (id, title, created_at) VALUES (?, '新对话', NOW())`,
		id,
	)
	if err != nil {
		return "", fmt.Errorf("create session: %w", err)
	}
	return id, nil
}

// List 返回所有会话摘要（不含消息体）
func (s *SessionStore) List() []map[string]interface{} {
	rows, err := s.db.Query(
		`SELECT id, title, created_at, msg_count FROM sessions ORDER BY created_at DESC`,
	)
	if err != nil {
		fmt.Printf("[store] 查询会话列表失败: %v\n", err)
		return []map[string]interface{}{}
	}
	defer rows.Close()

	result := make([]map[string]interface{}, 0)
	for rows.Next() {
		var id, title string
		var createdAt time.Time
		var msgCount int
		if err := rows.Scan(&id, &title, &createdAt, &msgCount); err != nil {
			continue
		}
		result = append(result, map[string]interface{}{
			"id":         id,
			"title":      title,
			"created_at": createdAt.Format("2006-01-02T15:04:05"),
			"msg_count":  msgCount,
		})
	}
	return result
}

// Get 获取单个会话（含全部消息）
func (s *SessionStore) Get(id string) (*Session, bool) {
	// 1. 查会话元数据
	var title string
	var createdAt time.Time
	var msgCount int
	err := s.db.QueryRow(
		`SELECT title, created_at, msg_count FROM sessions WHERE id = ?`, id,
	).Scan(&title, &createdAt, &msgCount)
	if err == sql.ErrNoRows {
		return nil, false
	}
	if err != nil {
		fmt.Printf("[store] 查询会话失败: %v\n", err)
		return nil, false
	}

	// 2. 查消息
	msgRows, err := s.db.Query(
		`SELECT role, content, cards, voice_url, UNIX_TIMESTAMP(created_at)
		 FROM session_messages WHERE session_id = ? ORDER BY id ASC`, id,
	)
	if err != nil {
		fmt.Printf("[store] 查询消息失败: %v\n", err)
		return nil, false
	}
	defer msgRows.Close()

	messages := make([]Message, 0)
	for msgRows.Next() {
		var role, content, cards, voiceUrl string
		var timeVal int64
		var cardsNull, voiceNull sql.NullString
		msgRows.Scan(&role, &content, &cardsNull, &voiceNull, &timeVal)
		cards = cardsNull.String
		voiceUrl = voiceNull.String
		messages = append(messages, Message{
			Role:     role,
			Content:  content,
			Time:     timeVal,
			Cards:    cards,
			VoiceUrl: voiceUrl,
		})
	}

	return &Session{
		ID:        id,
		Title:     title,
		CreatedAt: createdAt,
		Messages:  messages,
		MsgCount:  msgCount,
	}, true
}

// Delete 删除会话（级联删除消息）
func (s *SessionStore) Delete(id string) error {
	_, err := s.db.Exec(`DELETE FROM sessions WHERE id = ?`, id)
	return err
}

// SaveMessage 向指定会话追加一条消息
// 首次保存 user 消息时自动用前 30 字作为标题
// 如果会话不存在则自动创建（兼容 Python 直接生成 UUID 的场景）
func (s *SessionStore) SaveMessage(sessionID, role, content, cards, voiceUrl string) error {
	// 0. 确保会话存在（Python 侧可能直接用 UUID 创建，Go 侧还没记录）
	s.ensureSession(sessionID)

	// 1. 插入消息
	_, err := s.db.Exec(
		`INSERT INTO session_messages (session_id, role, content, cards, voice_url, created_at)
		 VALUES (?, ?, ?, NULLIF(?,''), NULLIF(?,''), NOW())`,
		sessionID, role, content, cards, voiceUrl,
	)
	if err != nil {
		return fmt.Errorf("save message: %w", err)
	}

	// 2. 更新 msg_count
	s.db.Exec(
		`UPDATE sessions SET msg_count = (
			SELECT COUNT(*) FROM session_messages WHERE session_id = ?
		) WHERE id = ?`,
		sessionID, sessionID,
	)

	// 3. 首条用户消息做标题（仅当标题仍为默认值且当前是 user 消息）
	if role == "user" {
		title := content
		if len([]rune(title)) > 30 {
			title = string([]rune(title)[:30]) + "…"
		}
		s.db.Exec(
			`UPDATE sessions SET title = ? WHERE id = ? AND title = '新对话'`,
			title, sessionID,
		)
	}
	return nil
}

// ensureSession 确保会话记录存在，不存在则插入（幂等）
func (s *SessionStore) ensureSession(id string) {
	var exists int
	err := s.db.QueryRow(`SELECT COUNT(1) FROM sessions WHERE id = ?`, id).Scan(&exists)
	if err != nil || exists > 0 {
		return
	}
	s.db.Exec(`INSERT IGNORE INTO sessions (id, title, created_at) VALUES (?, '新对话', NOW())`, id)
}
