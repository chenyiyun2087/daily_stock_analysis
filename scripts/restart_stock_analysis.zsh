#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Volumes/extension/projects/daily_stock_analysis"
PID_FILE="$PROJECT_DIR/logs/app.pid"
LOG_FILE="$PROJECT_DIR/logs/output.log"
ENV_FILE="$PROJECT_DIR/.env"

cd "$PROJECT_DIR"
mkdir -p logs

get_env_value() {
  local key="$1"
  if [[ -f "$ENV_FILE" ]]; then
    awk -F= -v key="$key" '
      $1 ~ "^[[:space:]]*" key "[[:space:]]*$" {
        value=$0
        sub("^[^=]*=", "", value)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        gsub(/^["'\''"]|["'\''"]$/, "", value)
        print value
        exit
      }
    ' "$ENV_FILE"
  fi
}

is_running() {
  local pid="$1"
  [[ -n "$pid" ]] && ps -p "$pid" >/dev/null 2>&1
}

stop_pid() {
  local pid="$1"
  local label="$2"

  if ! is_running "$pid"; then
    return
  fi

  echo "🛑 停止 $label (PID: $pid)..."
  kill "$pid" >/dev/null 2>&1 || true

  for _ in {1..20}; do
    if ! is_running "$pid"; then
      return
    fi
    sleep 0.2
  done

  echo "⚠️  进程未正常退出，强制停止 (PID: $pid)..."
  kill -9 "$pid" >/dev/null 2>&1 || true
}

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]]; then
    stop_pid "$old_pid" "daily_stock_analysis"
  fi
  rm -f "$PID_FILE"
fi

webui_port="$(get_env_value WEBUI_PORT)"
webui_port="${webui_port:-8000}"
listener_pid="$(lsof -tiTCP:"$webui_port" -sTCP:LISTEN -n -P 2>/dev/null | head -n 1 || true)"
if [[ -n "$listener_pid" ]]; then
  command_line="$(ps -p "$listener_pid" -o command= 2>/dev/null || true)"
  if [[ "$command_line" == *"python"* && "$command_line" == *"main.py --webui"* ]]; then
    stop_pid "$listener_pid" "daily_stock_analysis 端口 $webui_port"
  else
    echo "❌ 端口 $webui_port 已被其它进程占用: $command_line"
    exit 1
  fi
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  echo "❌ 未找到虚拟环境 Python: $PROJECT_DIR/.venv/bin/python"
  exit 1
fi

echo "🚀 启动 daily_stock_analysis..."
: > "$LOG_FILE"
nohup "$PROJECT_DIR/.venv/bin/python" main.py --webui-only > "$LOG_FILE" 2>&1 &
new_pid=$!
disown "$new_pid" 2>/dev/null || true
echo "$new_pid" > "$PID_FILE"

sleep 2
if ! is_running "$new_pid"; then
  echo "❌ daily_stock_analysis 启动失败，进程已退出。请查看日志: $LOG_FILE"
  exit 1
fi

echo "✅ daily_stock_analysis 已重启 (PID: $new_pid)"
echo "🌐 WebUI: http://$(get_env_value WEBUI_HOST || echo 127.0.0.1):$webui_port"
echo "📄 日志: $LOG_FILE"
