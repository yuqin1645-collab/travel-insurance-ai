#!/bin/bash
# CentOS 7 服务器初始化安装脚本
# 安装 Python 3.9 + 项目依赖

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "========================================"
echo "CentOS 7 环境初始化"
echo "========================================"

# ── 1. 安装系统依赖 ──────────────────────────────────────
echo "[1/5] 安装系统依赖..."
sudo yum install -y epel-release centos-release-scl
sudo yum install -y \
    devtoolset-9-gcc devtoolset-9-gcc-c++ \
    make \
    openssl-devel bzip2-devel libffi-devel zlib-devel \
    readline-devel sqlite-devel \
    wget curl git \
    poppler-utils \
    libGL libGLU

# 激活 gcc 9（仅当前 shell 生效，编译时用）
source /opt/rh/devtoolset-9/enable

# ── 2. 安装 Python 3.9 ───────────────────────────────────
echo "[2/5] 安装 Python 3.9..."
if ! command -v python3.9 &>/dev/null; then
    cd /tmp
    wget -q https://www.python.org/ftp/python/3.9.18/Python-3.9.18.tgz
    tar xzf Python-3.9.18.tgz
    cd Python-3.9.18
    ./configure --enable-optimizations --with-ensurepip=install
    make -j$(nproc)
    sudo make altinstall
    cd "$PROJECT_DIR"
    echo "Python 3.9 安装完成"
else
    echo "Python 3.9 已存在，跳过"
fi

PYTHON=python3.9

# ── 3. 创建虚拟环境 ──────────────────────────────────────
echo "[3/5] 创建虚拟环境..."
cd "$PROJECT_DIR"
if [ ! -d "venv" ]; then
    $PYTHON -m venv venv
    echo "虚拟环境创建完成"
else
    echo "虚拟环境已存在，跳过"
fi

source venv/bin/activate

# ── 4. 安装 Python 依赖 ──────────────────────────────────
echo "[4/5] 安装 Python 依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# ── 5. 创建必要目录 ──────────────────────────────────────
echo "[5/5] 创建目录结构..."
mkdir -p logs claims_data review_results

# ── 检查 .env ────────────────────────────────────────────
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo ""
    echo "[警告] 已从 .env.example 复制 .env，请编辑填写实际配置："
    echo "  vi $PROJECT_DIR/.env"
fi

echo ""
echo "========================================"
echo "初始化完成！"
echo "========================================"
echo "下一步："
echo "  1. 编辑配置: vi .env"
echo "  2. 启动系统: ./start.sh"
echo "  3. 查看日志: tail -f logs/production_\$(date +%Y%m%d).log"
