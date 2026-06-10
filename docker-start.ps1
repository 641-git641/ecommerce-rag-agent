# ============================================================
# 电商 RAG 系统 — Docker 一键启动脚本 (Windows PowerShell)
# 用法: .\docker-start.ps1
# ============================================================
$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  电商 RAG 智能导购 — Docker 一键启动" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 检查 Docker
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    Write-Host "[错误] 未检测到 Docker，请先安装 Docker Desktop" -ForegroundColor Red
    Write-Host "  下载: https://www.docker.com/products/docker-desktop" -ForegroundColor Yellow
    exit 1
}

# 检查 .env 文件
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host ""
        Write-Host "⚠️  已从 .env.example 自动创建 .env 文件" -ForegroundColor Yellow
        Write-Host "⚠️  请编辑 .env 填入你的 OPENAI_API_KEY 后重新运行" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  编辑: notepad .env" -ForegroundColor White
        Write-Host ""
        exit 0
    } else {
        Write-Host "[错误] 未找到 .env.example 文件" -ForegroundColor Red
        exit 1
    }
}

# 检查 API Key
$envContent = Get-Content ".env" -Raw
if ($envContent -match "sk-your-api-key-here") {
    Write-Host ""
    Write-Host "⚠️  请先编辑 .env 文件，将 OPENAI_API_KEY 替换为你的真实 API Key" -ForegroundColor Yellow
    Write-Host "  获取地址: https://dashscope.aliyun.com/" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# 构建并启动
Write-Host ""
Write-Host "[1/2] 构建镜像..." -ForegroundColor White
docker compose build --parallel

Write-Host ""
Write-Host "[2/2] 启动服务..." -ForegroundColor White
docker compose up -d

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  启动成功！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  API 网关:  http://localhost:8080" -ForegroundColor White
Write-Host "  API 文档:  http://localhost:9000/docs" -ForegroundColor White
Write-Host ""
Write-Host "  查看日志:  docker compose logs -f" -ForegroundColor Gray
Write-Host "  停止服务:  .\docker-stop.ps1" -ForegroundColor Gray
Write-Host ""
