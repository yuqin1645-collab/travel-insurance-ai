#!/bin/bash
# 启动航班延误AI审核系统（后台守护模式，断开SSH不停止）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/production.pid"
LOG_FILE="$SCRIPT_DIR/logs/production_$(date +%Y%m%d).log"

# 检查是否已在运行
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "系统已在运行 (PID: $PID)"
        exit 1
    else
        rm -f "$PID_FILE"
    fi
fi

mkdir -p "$SCRIPT_DIR/logs"

# 激活虚拟环境
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
else
    echo "错误: 未找到虚拟环境 venv/bin/activate"
    exit 1
fi

echo "启动系统（后台运行）..."

# ===================== 后台永久运行（日志由 Python FileHandler 管理，不重定向）=====================
nohup python start_production.py > /dev/null 2>&1 &
# ================================================================

PID=$!
echo $PID > "$PID_FILE"

echo "=========================================="
echo "系统已启动成功 (PID: $PID)"
echo "日志文件: $LOG_FILE"
echo "查看实时日志: tail -f $LOG_FILE"
echo "停止服务命令: ./stop.sh"
echo "=========================================="