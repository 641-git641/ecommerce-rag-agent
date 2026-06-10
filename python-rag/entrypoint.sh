#!/bin/sh
# Docker 入口脚本：首次启动时自动使用预构建的 ChromaDB 数据
set -e

CHROMA_DIR="/app/chroma_db"
CHROMA_INIT="/app/chroma_db_init"

# 如果 chroma_db 为空且存在预构建数据，直接复制
if [ ! -f "$CHROMA_DIR/chroma.sqlite3" ] && [ -d "$CHROMA_INIT" ] && [ -f "$CHROMA_INIT/chroma.sqlite3" ]; then
    echo "[Entrypoint] 使用预构建 ChromaDB 数据，跳过 API 嵌入初始化..."
    cp -r "$CHROMA_INIT"/* "$CHROMA_DIR"/
    echo "[Entrypoint] ChromaDB 数据已就绪"
fi

exec uvicorn main:app --host 0.0.0.0 --port 9000
