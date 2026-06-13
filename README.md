# 电商 RAG 智能导购系统

基于 RAG（检索增强生成）技术的智能电商导购助手，支持文本/图片/语音三模态输入，提供商品推荐、筛选、对比、场景化搭配及购物车闭环全链路能力。

## 技术栈

| 分层 | 技术 |
|------|------|
| RAG 服务 | Python 3.11 + FastAPI + ChromaDB |
| API 网关 | Go + Gin + MariaDB |
| LLM | 通义千问 (DashScope) |
| 客户端 | Android (Kotlin) |

---

# 一、部署版试用（面向普通用户）

> 后端已部署在云服务器，你只需要安装 APK 即可试用。

## 1.1 获取 APK

从 [GitHub Releases](#github-releases) 下载最新 `app-debug.apk`。

## 1.2 安装到手机

1. 将 APK 传到手机（微信文件传输助手 / QQ / USB 连线均可）
2. 手机上点击 APK 文件 → 允许"未知来源"安装
3. 打开 App，直接使用

## 1.3 服务器地址

APK 已内置云服务器地址，无需用户做任何配置。

> **网络要求**：手机能正常访问外网即可，无需和服务器在同一 WiFi。

---

# 二、开发者 — 本地调试

## 2.1 前置条件

- [Docker Desktop](https://www.docker.com/products/docker-desktop) 或 Docker Engine
- 通义千问 API Key（免费注册 https://dashscope.aliyun.com/）
- JDK 17+（仅编译 Android APK 时需要）

## 2.2 一键启动后端（Docker）

```bash
# 1. 创建 .env 并填入你的 API Key
cp .env.example .env
# 编辑 .env，修改 OPENAI_API_KEY=sk-你的Key

# 2. 启动所有服务
docker compose up -d --build

# 3. 查看日志确认启动成功
docker compose logs -f

# 停止服务
docker compose down
```

## 2.3 服务端口

| 服务 | 地址 | 说明 |
|------|------|------|
| Go API 网关 | `http://localhost:8080` | 所有请求的统一入口 |
| Python RAG | `http://localhost:9000` | AI 推理服务 |
| API 文档 (Swagger) | `http://localhost:9000/docs` | 可在线调试各接口 |
| MariaDB | `localhost:3307` | 购物车/会话数据库（宿主机端口） |

## 2.4 验证后端

```bash
# 测试 Go 网关（返回空数组即正常）
curl http://localhost:8080/api/sessions

# 测试 RAG 问答
curl -X POST http://localhost:8080/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"推荐一款跑鞋","session_id":"test-001"}'
```

## 2.5 Android 客户端

Android App 连接服务器的行为**完全取决于编译时写入的 IP**，不同场景处理方式不同：

### 场景 A：模拟器调试（最常用）

模拟器内置 `10.0.2.2` 自动指向宿主机 `localhost`，App 代码自动检测模拟器并使用此地址。

**操作**：在 Android Studio 中直接 Run，无需任何配置。后端在 `localhost` 跑 Docker 即可。

### 场景 B：真机 + 同一 WiFi（本地调试）

手机和电脑在同一个局域网，编译时 Gradle 脚本自动获取电脑的局域网 IP（如 `192.168.1.x`）写入 APK。

**操作**：直接 `./gradlew assembleDebug` 构建，连接同一 WiFi 即可。

```bash
# 手机安装
adb install app/build/outputs/apk/debug/app-debug.apk
```

> **注意**：换 WiFi 导致电脑 IP 变化后，需重新编译 APK。

### 三种场景对照

| 场景 | APK 连接目标 | 手机要求 | HOST_IP 配置 |
|------|------------|---------|-------------|
| 模拟器 | `10.0.2.2`（宿主机） | 无需手机，模拟器即可 | 自动检测，不用改 |
| 真机 + 同 WiFi | 电脑局域网 IP | 同一 WiFi | 自动获取，不用改 |
| 真机 + 云服务器 | 服务器公网 IP | 能上外网即可 | 手动写死服务器 IP |

---

# 三、开发者 — 本地裸跑（不用 Docker）

### 前置条件

- Python 3.11+
- Go 1.21+
- MySQL 8.0 或 MariaDB

### 1. 初始化数据库

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

# 配置 API Key
cp ../.env.example .env
# 编辑 .env，填入 OPENAI_API_KEY=sk-你的Key

# 启动 (端口 9000)
uvicorn main:app --host 0.0.0.0 --port 9000 --reload
```

### 3. 启动 Go API 网关

```bash
cd go-server

# Windows:
$env:PYTHON_RAG_URL="http://localhost:9000"
$env:MYSQL_DSN="root:root123@tcp(127.0.0.1:3306)/ecommerce_cart?charset=utf8mb4&parseTime=True"

# Linux/Mac:
export PYTHON_RAG_URL="http://localhost:9000"
export MYSQL_DSN="root:root123@tcp(127.0.0.1:3306)/ecommerce_cart?charset=utf8mb4&parseTime=True"

# 启动 (端口 8080)
go run .
```

---

# 四、通过 GitHub 分发 APK

## 4.1 .gitignore 说明

项目 `.gitignore` 已排除：
- `.env`（API Key 等敏感信息）
- `android-app/app/build/`（编译产物，含 APK）
- `python-rag/chroma_db/`（向量数据库文件）
- `*.db` / `*.exe`（运行时产物）

## 4.2 推荐方式：GitHub Releases（对外分发）

将 APK 托管在 GitHub Releases 中，用户可以随时下载最新版本：

```bash
# 1. 编译 release 版 APK（体积更小）
cd android-app
./gradlew assembleRelease

# 2. 推送到 GitHub
git add .
git commit -m "release: v1.0"
git tag v1.0
git push origin main --tags
```

然后：

1. 打开 GitHub 仓库页面 → **Releases** → **Create a new release**
2. Tag 选择 `v1.0`
3. 填写 Release title：`v1.0 — 首次发布`
4. 将 `android-app/app/build/outputs/apk/release/app-release.apk` 拖拽到附件区域
5. 点击 **Publish release**

用户访问 `https://github.com/你的用户名/仓库名/releases` 即可下载最新 APK。

## 4.3 备选方式：直接提交 APK 到仓库（简单但不推荐）

如果不想用 Releases，也可以将 APK 纳入版本管理：

```bash
# 1. 修改 .gitignore，去掉对 APK 的排除
# 在 .gitignore 中注释或删除 android-app/app/build/

# 2. 将 APK 放到项目根目录
cp android-app/app/build/outputs/apk/debug/app-debug.apk ./app-debug.apk

# 3. 提交
git add app-debug.apk
git commit -m "add APK"
git push
```

> **缺点**：APK 较大（~10-20MB），每次更新都会让仓库体积膨胀。建议用 Releases 方式。

---

# 五、常用 Docker 命令

```bash
docker compose up -d --build   # 构建并启动
docker compose logs -f          # 查看所有日志
docker compose logs -f python-rag  # 只看 RAG 日志
docker compose restart python-rag  # 重启单个服务
docker compose down             # 停止所有服务
docker compose down -v          # 停止并清除数据卷（危险）
docker stats                    # 查看资源占用
```

---

# 六、项目结构

```
├── python-rag/              # Python RAG 核心服务
│   ├── agent/               # Agent 智能体（意图识别 + ReAct + 工具编排）
│   ├── rag_service/         # RAG 管线（检索 + 融合 + 重排 + 生成）
│   ├── api/                 # FastAPI 接口
│   ├── knowledge_graph/     # 轻量商品关系图谱
│   ├── memory/              # 多轮对话记忆系统
│   ├── speech/              # 语音识别/合成
│   ├── vision/              # 以图搜图
│   └── prompts/             # LLM 提示词模板
├── go-server/               # Go API 网关 + 购物车服务
│   ├── handlers/            # 会话管理 + 反向代理
│   ├── store/               # MySQL/MariaDB 持久化
│   └── db/                  # 数据库连接池
├── android-app/             # Android 客户端
│   └── app/src/main/java/   # Kotlin 源码
├── sql/                     # 数据库建表脚本
├── docs/test/               # 测试商品数据（100款，4品类）
├── docker-compose.yml       # Docker 服务编排
├── .env.example             # 环境变量模板
└── .gitignore
```
