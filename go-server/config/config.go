// config 包管理应用配置，支持环境变量覆盖
package config

import "os"

type Config struct {
	Server    ServerConfig
	PythonRAG PythonRAGConfig
	MySQL     MySQLConfig
}

type ServerConfig struct {
	Port string // 监听端口，如 ":8080"
	Mode string // gin 模式: debug / release / test
}

type PythonRAGConfig struct {
	BaseURL string // Python 服务地址，如 "http://localhost:9000"
}

type MySQLConfig struct {
	DSN string // MySQL 连接串，如 "user:pass@tcp(127.0.0.1:3306)/ecommerce_cart?charset=utf8mb4&parseTime=True"
}

func envOr(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}

func Load() *Config {
	return &Config{
		Server: ServerConfig{
			Port: envOr("SERVER_PORT", ":8080"),
			Mode: envOr("GIN_MODE", "debug"),
		},
		PythonRAG: PythonRAGConfig{
			BaseURL: envOr("PYTHON_RAG_URL", "http://localhost:9000"),
		},
		MySQL: MySQLConfig{
		DSN: envOr("MYSQL_DSN", "root:root123@tcp(127.0.0.1:3307)/ecommerce_cart?charset=utf8mb4&parseTime=True"),
	},
	}
}
