#!/bin/bash
# ============================================================
# 电商 RAG 系统 — Docker 一键启动脚本 (Linux / macOS)
# 用法: ./docker-start.sh
# ============================================================
set -e

echo "========================================"
echo "  电商 RAG 智能导购 — Docker 一键启动"
echo "========================================"

# 检查 Docker
if ! command -v docker &> /dev/null; then
  echo "[错误] 未检测到 Docker，请先安装 Docker Desktop 或 Docker Engine"
  echo "  下载: https://www.docker.com/products/docker-desktop"
  exit 1
fi

if ! docker compose version &> /dev/null; then
  echo "[错误] 需要 Docker Compose v2+，请升级 Docker"
  exit 1
fi

# 检查 .env 文件
if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  已从 .env.example 自动创建 .env 文件"
    echo "⚠️  请编辑 .env 填入你的 OPENAI_API_KEY 后重新运行"
    echo ""
    echo "  Linux/Mac:  vim .env"
    echo "  Windows:    notepad .env"
    echo ""
    exit 0
  else
    echo "[错误] 未找到 .env.example 文件"
    exit 1
  fi
fi

# 检查 API Key 是否已填写
if grep -q "sk-your-api-key-here" .env 2>/dev/null; then
  echo ""
  echo "⚠️  请先编辑 .env 文件，将 OPENAI_API_KEY 替换为你的真实 API Key"
  echo "  获取地址: https://dashscope.aliyun.com/"
  echo ""
  exit 1
fi

# 构建并启动
echo ""
echo "[1/2] 构建镜像..."
docker compose build --parallel

echo ""
echo "[2/2] 启动服务..."
docker compose up -d

echo ""
echo "========================================"
echo "  启动成功！"
echo "========================================"
echo ""
echo "  API 网关:  http://localhost:8080"
echo "  API 文档:  http://localhost:9000/docs"
echo ""
echo "  查看日志:  docker compose logs -f"
echo "  停止服务:  ./docker-stop.sh"
echo ""
