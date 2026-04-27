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

is_stock_analysis_process() {
  local command_line="$1"

  [[ "$command_line" == *"python"* ]] || return 1
  [[ "$command_line" == *"daily_stock_analysis"* ]] && return 0
  [[ "$command_line" == *"main.py --webui"* ]] && return 0
  [[ "$command_line" == *"main.py --serve"* ]] && return 0
  [[ "$command_line" == *"webui.py"* ]] && return 0

  return 1
}

wait_for_port() {
  local port="$1"
  local pid="$2"

  for _ in {1..30}; do
    if ! is_running "$pid"; then
      return 1
    fi
    if lsof -tiTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done

  return 1
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
webui_host="$(get_env_value WEBUI_HOST)"
webui_host="${webui_host:-127.0.0.1}"
listener_pid="$(lsof -tiTCP:"$webui_port" -sTCP:LISTEN -n -P 2>/dev/null | head -n 1 || true)"
if [[ -n "$listener_pid" ]]; then
  command_line="$(ps -p "$listener_pid" -o command= 2>/dev/null || true)"
  if is_stock_analysis_process "$command_line"; then
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
WEBUI_HOST="$webui_host" WEBUI_PORT="$webui_port" \
  nohup "$PROJECT_DIR/.venv/bin/python" -u webui.py > "$LOG_FILE" 2>&1 &
new_pid=$!
disown "$new_pid" 2>/dev/null || true
echo "$new_pid" > "$PID_FILE"

if ! wait_for_port "$webui_port" "$new_pid"; then
  echo "❌ daily_stock_analysis 启动失败，进程已退出。请查看日志: $LOG_FILE"
  rm -f "$PID_FILE"
  exit 1
fi

echo "✅ daily_stock_analysis 已重启 (PID: $new_pid)"
echo "🌐 WebUI: http://$webui_host:$webui_port"
echo "📄 日志: $LOG_FILE"
