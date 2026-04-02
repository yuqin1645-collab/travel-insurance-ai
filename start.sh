#!/bin/bash
# 启动航班延误AI审核系统

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

echo "启动系统..."
nohup python start_production.py >> "$LOG_FILE" 2>&1 &
PID=$!
echo $PID > "$PID_FILE"

echo "系统已启动 (PID: $PID)"
echo "日志文件: $LOG_FILE"
echo "查看日志: tail -f $LOG_FILE"
