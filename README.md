# 电商 RAG 智能导购系统

基于 RAG（检索增强生成）技术的智能电商导购助手，支持文本/图片/语音三模态输入，提供商品推荐、筛选、对比、场景化搭配及购物车闭环全链路能力。

## 技术栈

| 分层 | 技术 |
|------|------|
| RAG 服务 | Python 3.11 + FastAPI + ChromaDB |
| API 网关 | Go + Gin + MySQL |
| LLM | 通义千问 (DashScope) |
| 客户端 | Android (Kotlin) |

## Docker 一键启动

### 前置条件

- [Docker Desktop](https://www.docker.com/products/docker-desktop) 或 Docker Engine
- 通义千问 API Key（免费注册 https://dashscope.aliyun.com/）

### 启动

```bash
# 1. 创建 .env 并填入你的 API Key
cp .env.example .env
# 编辑 .env，修改 OPENAI_API_KEY=sk-你的Key

# 2. 启动所有服务
# Windows:
docker compose up -d --build

# Linux/Mac:
docker compose up -d --build

# 停止服务
docker compose down -v

```

### 服务端口

| 服务 | 地址 |
|------|------|
| Go API 网关 | http://localhost:8080 |
| Python RAG | http://localhost:9000 |
| API 文档 (Swagger) | http://localhost:9000/docs |

### 常用命令

```bash
docker compose up -d --build   # 构建并启动
docker compose logs -f          # 查看日志
docker compose down             # 停止所有服务
```

## 本地开发启动（不用 Docker）

### 前置条件

- Python 3.11+
- Go 1.25+
- MySQL 8.0 或 MariaDB

### 1. 初始化 MySQL

```bash
# 启动 MySQL 后执行建表
mysql -u root -p < sql/init_cart.sql
```

默认连接：`root:root123@tcp(127.0.0.1:3306)/ecommerce_cart`

### 2. 启动 Python RAG 服务

```bash
cd python-rag

# 创建虚拟环境（可选）
python -m venv venv
# Windows: .\venv\Scripts\activate
# Linux/Mac: source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 创建 .env 配置文件
cp ../.env.example .env
# 编辑 .env，填入 OPENAI_API_KEY=sk-你的Key

# 启动 (端口 9000)
uvicorn main:app --host 0.0.0.0 --port 9000 --reload
```

### 3. 启动 Go API 网关

```bash
cd go-server

# 设置环境变量（或沿用默认值）
# Windows:
$env:PYTHON_RAG_URL="http://localhost:9000"
$env:MYSQL_DSN="root:root123@tcp(127.0.0.1:3306)/ecommerce_cart?charset=utf8mb4&parseTime=True"
$env:GIN_MODE="debug"

# Linux/Mac:
export PYTHON_RAG_URL="http://localhost:9000"
export MYSQL_DSN="root:root123@tcp(127.0.0.1:3306)/ecommerce_cart?charset=utf8mb4&parseTime=True"
export GIN_MODE="debug"

# 启动 (端口 8080)
go run .
```

### 4. 验证

| 服务 | 地址 |
|------|------|
| Go API 网关 | http://localhost:8080 |
| Python RAG | http://localhost:9000 |
| API 文档 | http://localhost:9000/docs |

## Android 客户端

### 构建

```bash
cd android-app
./gradlew assembleDebug
```

APK 输出路径：`app/build/outputs/apk/debug/app-debug.apk`

### ADB 连接

#### 模拟器

Android Studio 自带的 AVD 模拟器启动后 adb 自动连接，无需额外操作。

#### 真机（USB）

1. 手机上开启 **开发者选项** → **USB 调试**
2. USB 连接电脑，手机上点击"允许 USB 调试"
3. 验证连接：

```bash
adb devices
# 应输出: 设备号   device
```

#### 真机（WiFi 无线调试，Android 11+）

1. 确保手机和电脑在同一 WiFi
2. 先在手机上开启 **开发者选项** → **无线调试**
3. USB 连接后执行：

```bash
# 先在手机设置 → 关于手机 → 状态信息 中查看手机 IP（如 192.168.1.88）
adb tcpip 5555
adb connect 192.168.1.88:5555    # 替换为你的手机 IP
# 拔掉 USB，验证: adb devices
```

或直接在手机上进入 **无线调试** → **使用配对码配对设备**，在电脑执行：

```bash
adb pair 192.168.1.88:配对端口     # 替换为手机显示的 IP 和端口
# 输入配对码后，再执行:
adb connect 192.168.1.88:连接端口  # 替换为手机显示的 IP 和端口
```

### 安装 APK

```bash
adb install app/build/outputs/apk/debug/app-debug.apk
```

### 网络说明

| 场景 | 连接方式 |
|------|---------|
| 模拟器 | 自动通过 `10.0.2.2` 桥接宿主机，无需配置 |
| 真机 | 编译时将宿主机局域网 IP 注入 APK，手机和电脑需在同一 WiFi |

> **注意**：切换 WiFi 导致宿主机 IP 变化时，需重新执行 `./gradlew assembleDebug` 构建 APK，因为 IP 是编译时写入的。

## 项目结构

```
├── python-rag/          # Python RAG 核心服务
│   ├── agent/           # Agent 智能体（意图识别 + ReAct + 工具编排）
│   ├── rag_service/     # RAG 管线（检索 + 融合 + 重排 + 生成）
│   ├── api/             # FastAPI 接口
│   ├── knowledge_graph/ # 轻量商品关系图谱
│   ├── memory/          # 多轮对话记忆系统
│   ├── speech/          # 语音识别/合成
│   ├── vision/          # 以图搜图
│   └── prompts/         # LLM 提示词模板
├── go-server/           # Go API 网关
│   ├── handlers/        # 会话管理 + 反向代理
│   ├── store/           # MySQL 持久化
│   └── db/              # 数据库连接池
├── android-app/         # Android 客户端
├── sql/                 # 数据库初始化脚本
├── docs/                # 测试商品数据（100款）
├── docker-compose.yml   # 服务编排
├── .env.example         # 环境变量模板
└── .gitignore
```
