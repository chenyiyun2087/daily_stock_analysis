#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PID_FILE="$PROJECT_DIR/logs/app.pid"

# 检查是否已经启动（通过标记及进程状态判断）
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    # 检查进程是否存在
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "👉 daily_stock_analysis 项目已在运行中 (PID: $PID)，忽略启动指令。"
        exit 0
    else
        echo "🧹 发现过期的 PID 文件，正在清理..."
        rm -f "$PID_FILE"
    fi
else
    # 也可以通过特征查询防止遗漏
    EXISTING_PID=$(ps aux | grep "[p]ython main.py --webui" | awk '{print $2}' | head -n 1 || true)
    if [ -n "$EXISTING_PID" ]; then
        echo "👉 检测到 daily_stock_analysis 项目已在运行中 (PID: $EXISTING_PID)，忽略启动指令。"
        # 补齐 PID 标记文件
        mkdir -p logs
        echo "$EXISTING_PID" > "$PID_FILE"
        exit 0
    fi
fi

# 检查虚拟环境是否存在
if [ ! -d ".venv" ]; then
    echo "❌ 未找到 .venv 虚拟环境，请先执行环境安装！"
    exit 1
fi

# 激活虚拟环境
source .venv/bin/activate
mkdir -p logs

echo "🚀 检测到项目未启动，正在启动 daily_stock_analysis..."

# 后台启动并输出到 output.log
nohup python main.py --webui-only > logs/output.log 2>&1 &
NEW_PID=$!

# 将新 PID 写入标记文件
echo "$NEW_PID" > "$PID_FILE"

echo "✅ 项目已在后台启动！(PID: $NEW_PID)"
echo "💡 可以通过运行 'tail -f $PROJECT_DIR/logs/output.log' 查看启动日志。"
