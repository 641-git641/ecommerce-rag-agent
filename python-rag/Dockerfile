# ============================================================
# Python RAG ?? Dockerfile
# ?????: ????? (Ag/)
# ============================================================
FROM python:3.11-slim

LABEL service="ecommerce-python-rag"

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

RUN apt-get update -y \
    && apt-get install -y --no-install-recommends \
        libsqlite3-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY python-rag/requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt

COPY python-rag/ .

# 入口脚本
COPY python-rag/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# 商品 JSON + 图片
COPY docs/ /docs/

RUN mkdir -p /app/chroma_db /app/profiles /app/uploads/audio

EXPOSE 9000

ENTRYPOINT ["/app/entrypoint.sh"]
