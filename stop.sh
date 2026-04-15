#!/bin/bash
# 停止航班延误AI审核系统

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/production.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "系统未运行 (未找到 PID 文件)"
    exit 0
fi

PID=$(cat "$PID_FILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "系统未运行 (PID $PID 不存在)"
    rm -f "$PID_FILE"
    exit 0
fi

echo "停止系统 (PID: $PID)..."
kill -TERM "$PID"

# 等待最多10秒
for i in $(seq 1 10); do
    if ! kill -0 "$PID" 2>/dev/null; then
        break
    fi
    sleep 1
done

# 仍未停止则强制终止
if kill -0 "$PID" 2>/dev/null; then
    echo "强制终止..."
    kill -KILL "$PID"
fi

rm -f "$PID_FILE"
echo "系统已停止"
