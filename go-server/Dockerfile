# ============================================================
# Go Server Dockerfile
# 构建上下文: 项目根目录 (Ag/)
# ============================================================
FROM golang:1.25-alpine AS builder

WORKDIR /build

COPY go-server/go.mod go-server/go.sum ./
ENV GOPROXY=https://goproxy.cn,direct
RUN go mod download

COPY go-server/ ./
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /build/server .

# ── 运行阶段 ──────────────────────────────────────────
FROM alpine:3.21

RUN apk add --no-cache ca-certificates tzdata
ENV TZ=Asia/Shanghai

WORKDIR /app

COPY --from=builder /build/server .

RUN mkdir -p /app/logs /app/data

EXPOSE 8080
ENV GIN_MODE=release

CMD ["./server"]
