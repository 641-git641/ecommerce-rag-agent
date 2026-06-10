-- ============================================================
-- 电商 RAG 系统 — MySQL 建表 DDL（购物车 + 会话管理）
-- 执行方式: mysql -u root -p < sql/init_cart.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS ecommerce_cart
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE ecommerce_cart;

-- 购物车表：一个用户对同一商品只保留一条记录
CREATE TABLE IF NOT EXISTS cart_items (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     VARCHAR(64)  NOT NULL COMMENT '用户ID（会话级使用 session_id）',
    product_id  VARCHAR(64)  NOT NULL COMMENT '商品编码',
    product_name VARCHAR(255) NOT NULL COMMENT '商品名称',
    price       DECIMAL(10,2) DEFAULT 0.00 COMMENT '单价',
    quantity    INT           DEFAULT 1      COMMENT '数量',
    selected    TINYINT(1)    DEFAULT 1      COMMENT '是否勾选 1=选中 0=取消',
    created_at  TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_product (user_id, product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='购物车明细';

-- 订单主表
CREATE TABLE IF NOT EXISTS orders (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    user_id        VARCHAR(64)  NOT NULL COMMENT '用户ID',
    order_no       VARCHAR(32)  NOT NULL UNIQUE COMMENT '订单编号',
    address        VARCHAR(500) DEFAULT '' COMMENT '收货地址',
    contact_name   VARCHAR(100) DEFAULT '' COMMENT '联系人',
    contact_phone  VARCHAR(20)  DEFAULT '' COMMENT '联系电话',
    total_amount   DECIMAL(10,2) DEFAULT 0.00 COMMENT '订单总金额',
    status         VARCHAR(20)  DEFAULT 'pending' COMMENT 'pending/confirmed',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单主表';

-- 订单明细表
CREATE TABLE IF NOT EXISTS order_items (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    order_id     INT          NOT NULL COMMENT '关联 orders.id',
    product_id   VARCHAR(64)  NOT NULL COMMENT '商品编码',
    product_name VARCHAR(255) NOT NULL COMMENT '商品名称（快照）',
    price        DECIMAL(10,2) DEFAULT 0.00 COMMENT '下单时单价',
    quantity     INT          DEFAULT 1     COMMENT '数量',
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单明细';

-- ============================================================
-- 会话管理
-- ============================================================

CREATE TABLE IF NOT EXISTS sessions (
    id         VARCHAR(36)  PRIMARY KEY,
    title      VARCHAR(200) NOT NULL DEFAULT '新对话',
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    msg_count  INT          NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS session_messages (
    id         BIGINT       AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(36)  NOT NULL,
    role       VARCHAR(16)  NOT NULL,
    content    TEXT         NOT NULL,
    cards      TEXT,
    voice_url  VARCHAR(500),
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX      idx_session_id (session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
