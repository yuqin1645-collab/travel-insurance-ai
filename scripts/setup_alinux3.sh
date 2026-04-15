#!/bin/bash
# 阿里云 Linux 3 (AlmaLinux 8 兼容) 服务器初始化部署脚本
# 适用系统：Alibaba Cloud Linux 3 / CentOS 8 / Rocky Linux 8 / AlmaLinux 8

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "========================================"
echo "  阿里云 Linux 3 环境初始化部署"
echo "========================================"
echo "项目目录: $PROJECT_DIR"
echo ""

# ── 1. 安装系统依赖 ──────────────────────────────────────
echo "[1/6] 安装系统依赖..."

# 阿里云 Linux 3 自带阿里云 yum 源，无需切换镜像
sudo dnf install -y \
    python3 python3-pip python3-devel \
    gcc gcc-c++ make \
    openssl-devel bzip2-devel libffi-devel zlib-devel \
    wget curl git \
    poppler-utils \
    mesa-libGL mesa-libGLU

echo "  系统依赖安装完成"

# ── 2. 检查 Python 版本 ───────────────────────────────────
echo "[2/6] 检查 Python 版本..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "  系统 Python 版本: $PYTHON_VERSION"

# 阿里云 Linux 3 自带 Python 3.9+，直接使用
PYTHON=python3

# ── 3. 获取代码 ──────────────────────────────────────────
echo "[3/6] 获取最新代码..."
cd "$PROJECT_DIR"

if [ -d ".git" ]; then
    # 已有 git 仓库，直接拉取最新
    echo "  检测到 git 仓库，拉取最新代码..."
    git fetch origin
    git reset --hard origin/main
    echo "  代码更新完成"
else
    echo "  [提示] 当前目录无 git 仓库"
    echo "  请手动执行以下命令之一："
    echo ""
    echo "  方式一（能访问 GitHub）："
    echo "    git clone https://github.com/yuqin1645-collab/travel-insurance-ai.git ."
    echo ""
    echo "  方式二（无法访问 GitHub，从本地上传）："
    echo "    在本地执行: scp -r ./travel-insurance-ai-code/* 用户名@服务器IP:/部署路径/"
    echo ""
    echo "  上传后重新运行此脚本"
    exit 1
fi

# ── 4. 创建虚拟环境 ──────────────────────────────────────
echo "[4/6] 创建 Python 虚拟环境..."
cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    $PYTHON -m venv venv
    echo "  虚拟环境创建完成"
else
    echo "  虚拟环境已存在，跳过"
fi

source venv/bin/activate

# ── 5. 安装 Python 依赖 ──────────────────────────────────
echo "[5/6] 安装 Python 依赖..."

# 升级 pip 并使用阿里云镜像加速
pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/

# 安装项目依赖（阿里云 Linux 3 无 CentOS 7 的 gcc 版本限制，pymupdf 可用新版）
# 但保留 requirements.txt 中锁定的版本以确保兼容性
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

echo "  Python 依赖安装完成"

# ── 6. 创建必要目录结构 ──────────────────────────────────
echo "[6/6] 创建目录结构..."
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/claims_data"
mkdir -p "$PROJECT_DIR/review_results"
mkdir -p "$PROJECT_DIR/.cache/ocr"
mkdir -p "$PROJECT_DIR/.cache/docs"

# ── 配置 .env ────────────────────────────────────────────
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo ""
    echo "  [重要] 已创建 .env 配置文件，请编辑填写实际配置："
    echo "    vi $PROJECT_DIR/.env"
    echo ""
    echo "  必填项："
    echo "    DASHSCOPE_API_KEY=    # 通义千问/阿里云 DashScope API Key"
    echo "    DB_HOST=              # 数据库地址（如已有数据库）"
    echo "    DB_PASSWORD=          # 数据库密码"
    echo "    CLAIMS_API_URL=       # 案件数据接口地址"
    echo "    FRONTEND_API_URL=     # 审核结果推送接口地址"
else
    echo "  .env 已存在，跳过"
fi

# ── 迁移旧服务器数据（可选提示）────────────────────────────
echo ""
echo "========================================"
echo "  初始化完成！"
echo "========================================"
echo ""
echo "【如需从旧服务器迁移数据】"
echo "  在旧服务器执行（迁移案件数据和审核结果）："
echo "    rsync -avz /旧路径/claims_data/   用户名@新服务器IP:/部署路径/claims_data/"
echo "    rsync -avz /旧路径/review_results/ 用户名@新服务器IP:/部署路径/review_results/"
echo ""
echo "【启动前检查清单】"
echo "  1. 编辑配置:   vi $PROJECT_DIR/.env"
echo "  2. 验证配置:   source venv/bin/activate && python -c \"from app.config import config; print('配置加载成功')\""
echo "  3. 测试数据库: source venv/bin/activate && python scripts/db/run_migration.py"
echo "  4. 启动系统:   ./start.sh"
echo "  5. 查看日志:   tail -f logs/production_\$(date +%Y%m%d).log"
echo ""
echo "【快捷命令】"
echo "  启动: ./start.sh"
echo "  停止: ./stop.sh"
echo "  日志: tail -f logs/production_\$(date +%Y%m%d).log"
echo ""
