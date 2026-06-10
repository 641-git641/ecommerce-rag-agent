// Package store 购物车 MySQL 持久化存储
//
// 支持操作：添加/删除/修改数量/切换勾选/查看/清空/下单
package store

import (
	"database/sql"
	"fmt"
	"time"

	"github.com/google/uuid"
)

// CartItem 购物车中的单件商品
type CartItem struct {
	ID          int     `json:"id"`
	ProductID   string  `json:"product_id"`
	ProductName string  `json:"product_name"`
	Price       float64 `json:"price"`
	Quantity    int     `json:"quantity"`
	Selected    bool    `json:"selected"`
}

// CartSummary 购物车汇总信息
type CartSummary struct {
	Items     []CartItem `json:"items"`
	Total     float64    `json:"total"`
	Count     int        `json:"count"`
	SelectedCount int    `json:"selected_count"`
}

// OrderResult 下单结果
type OrderResult struct {
	OrderNo     string      `json:"order_no"`
	TotalAmount float64     `json:"total_amount"`
	Items       []CartItem  `json:"items"`
	Address     string      `json:"address"`
	Status      string      `json:"status"`
}

// CartStore MySQL 购物车存储
type CartStore struct {
	db *sql.DB
}

// NewCartStore 创建购物车存储实例
func NewCartStore(db *sql.DB) *CartStore {
	s := &CartStore{db: db}
	s.initTable()
	return s
}

func (s *CartStore) initTable() {
	_, err := s.db.Exec(`
		CREATE TABLE IF NOT EXISTS cart_items (
			id INT AUTO_INCREMENT PRIMARY KEY,
			user_id VARCHAR(36) NOT NULL,
			product_id VARCHAR(100) NOT NULL DEFAULT '',
			product_name VARCHAR(200) NOT NULL DEFAULT '未知商品',
			price DOUBLE NOT NULL DEFAULT 0,
			quantity INT NOT NULL DEFAULT 1,
			selected TINYINT(1) NOT NULL DEFAULT 1,
			created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
			updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
			UNIQUE KEY uk_user_product (user_id, product_id),
			INDEX idx_user_id (user_id)
		) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
	`)
	if err != nil {
		fmt.Printf("[cart] 建表失败: %v\n", err)
	}
}

// ── 购物车 CRUD ──────────────────────────────────────────────

// Add 添加商品到购物车（已存在则数量 +1）
func (s *CartStore) Add(userID, productID, productName string, price float64) (*CartSummary, error) {
	// upsert: 存在则 quantity+1，不存在则 INSERT
	_, err := s.db.Exec(`
		INSERT INTO cart_items (user_id, product_id, product_name, price, quantity, selected)
		VALUES (?, ?, ?, ?, 1, 1)
		ON DUPLICATE KEY UPDATE quantity = quantity + 1, updated_at = NOW()
	`, userID, productID, productName, price)
	if err != nil {
		return nil, fmt.Errorf("add cart item: %w", err)
	}
	return s.List(userID)
}

// Remove 移除商品（按 product_id 或按 cart_items.id 精确匹配）
// identifier 可以是 "product_id" 或 "cart_item_id:123"
func (s *CartStore) Remove(userID, identifier string, byID bool) (*CartSummary, error) {
	var err error
	if byID {
		_, err = s.db.Exec(`DELETE FROM cart_items WHERE user_id=? AND id=?`, userID, identifier)
	} else {
		_, err = s.db.Exec(`DELETE FROM cart_items WHERE user_id=? AND product_id=?`, userID, identifier)
	}
	if err != nil {
		return nil, fmt.Errorf("remove cart item: %w", err)
	}
	return s.List(userID)
}

// UpdateQuantity 修改商品数量
func (s *CartStore) UpdateQuantity(userID, productID string, quantity int) (*CartSummary, error) {
	_, err := s.db.Exec(`UPDATE cart_items SET quantity=? WHERE user_id=? AND product_id=?`,
		quantity, userID, productID)
	if err != nil {
		return nil, fmt.Errorf("update quantity: %w", err)
	}
	return s.List(userID)
}

// ToggleSelect 切换商品勾选状态
func (s *CartStore) ToggleSelect(userID, productID string) (*CartSummary, error) {
	_, err := s.db.Exec(`
		UPDATE cart_items SET selected = 1 - selected
		WHERE user_id=? AND product_id=?
	`, userID, productID)
	if err != nil {
		return nil, fmt.Errorf("toggle select: %w", err)
	}
	return s.List(userID)
}

// Clear 清空购物车
func (s *CartStore) Clear(userID string) error {
	_, err := s.db.Exec(`DELETE FROM cart_items WHERE user_id=?`, userID)
	return err
}

// List 查询当前购物车
func (s *CartStore) List(userID string) (*CartSummary, error) {
	rows, err := s.db.Query(`
		SELECT id, product_id, product_name, price, quantity, selected
		FROM cart_items WHERE user_id=?
		ORDER BY id
	`, userID)
	if err != nil {
		return nil, fmt.Errorf("list cart: %w", err)
	}
	defer rows.Close()

	summary := &CartSummary{Items: []CartItem{}}
	for rows.Next() {
		var item CartItem
		if err := rows.Scan(&item.ID, &item.ProductID, &item.ProductName,
			&item.Price, &item.Quantity, &item.Selected); err != nil {
			return nil, fmt.Errorf("scan cart item: %w", err)
		}
		summary.Items = append(summary.Items, item)
		summary.Count++
		if item.Selected {
			summary.SelectedCount++
			summary.Total += item.Price * float64(item.Quantity)
		}
	}
	return summary, nil
}

// FindByIndex 按顺序索引获取购物车中的商品（1-based，"第一个"=1）
func (s *CartStore) FindByIndex(userID string, index int) (*CartItem, error) {
	items, err := s.List(userID)
	if err != nil {
		return nil, err
	}
	if index < 1 || index > len(items.Items) {
		return nil, fmt.Errorf("index %d out of range (1-%d)", index, len(items.Items))
	}
	return &items.Items[index-1], nil
}

// FindByName 按名称模糊匹配购物车中的商品
func (s *CartStore) FindByName(userID, name string) (*CartItem, error) {
	rows, err := s.db.Query(`
		SELECT id, product_id, product_name, price, quantity, selected
		FROM cart_items WHERE user_id=? AND product_name LIKE ?
		ORDER BY id LIMIT 1
	`, userID, "%"+name+"%")
	if err != nil {
		return nil, fmt.Errorf("find cart item by name: %w", err)
	}
	defer rows.Close()

	if !rows.Next() {
		return nil, fmt.Errorf("item '%s' not found in cart", name)
	}
	var item CartItem
	if err := rows.Scan(&item.ID, &item.ProductID, &item.ProductName,
		&item.Price, &item.Quantity, &item.Selected); err != nil {
		return nil, fmt.Errorf("scan item: %w", err)
	}
	return &item, nil
}

// ── 下单 ────────────────────────────────────────────────────

// ParseChineseIndex 将中文序号转成 1-based 数字
// "第一个"→1, "第二个"→2, "一"→1, "二"→2
var chineseIndexMap = map[string]int{
	"第一个": 1, "第二个": 2, "第三个": 3, "第四个": 4, "第五个": 5,
	"第一": 1, "第二": 2, "第三": 3, "第四": 4, "第五": 5,
	"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
}

func ParseChineseIndex(s string) (int, bool) {
	if idx, ok := chineseIndexMap[s]; ok {
		return idx, true
	}
	return 0, false
}

// PreviewOrder 生成订单预览（计算选中商品总价）
func (s *CartStore) PreviewOrder(userID string) (*CartSummary, error) {
	summary, err := s.List(userID)
	if err != nil {
		return nil, err
	}
	// 只计算选中的
	summary.Total = 0
	summary.SelectedCount = 0
	selectedItems := []CartItem{}
	for _, item := range summary.Items {
		if item.Selected {
			summary.Total += item.Price * float64(item.Quantity)
			summary.SelectedCount++
			selectedItems = append(selectedItems, item)
		}
	}
	summary.Items = selectedItems // 只返回选中的
	return summary, nil
}

// CreateOrder 从购物车生成订单（使用已勾选的商品）
func (s *CartStore) CreateOrder(userID, address, contactName, contactPhone string) (*OrderResult, error) {
	preview, err := s.PreviewOrder(userID)
	if err != nil {
		return nil, err
	}
	if len(preview.Items) == 0 {
		return nil, fmt.Errorf("没有选中商品，无法下单")
	}

	tx, err := s.db.Begin()
	if err != nil {
		return nil, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	orderNo := "ORD" + time.Now().Format("20060102150405") + uuid.New().String()[:6]
	res, err := tx.Exec(`
		INSERT INTO orders (user_id, order_no, address, contact_name, contact_phone, total_amount, status)
		VALUES (?, ?, ?, ?, ?, ?, 'confirmed')
	`, userID, orderNo, address, contactName, contactPhone, preview.Total)
	if err != nil {
		return nil, fmt.Errorf("insert order: %w", err)
	}
	orderID, _ := res.LastInsertId()

	for _, item := range preview.Items {
		_, err := tx.Exec(`
			INSERT INTO order_items (order_id, product_id, product_name, price, quantity)
			VALUES (?, ?, ?, ?, ?)
		`, orderID, item.ProductID, item.ProductName, item.Price, item.Quantity)
		if err != nil {
			return nil, fmt.Errorf("insert order item: %w", err)
		}
	}

	// 下单成功后清空购物车中已勾选的商品
	_, err = tx.Exec(`DELETE FROM cart_items WHERE user_id=? AND selected=1`, userID)
	if err != nil {
		return nil, fmt.Errorf("clear selected cart items: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit tx: %w", err)
	}

	return &OrderResult{
		OrderNo:     orderNo,
		TotalAmount: preview.Total,
		Items:       preview.Items,
		Address:     address,
		Status:      "confirmed",
	}, nil
}
