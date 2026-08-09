#!/usr/bin/env bash
# 本地(macOS)赛前采集轮询包装脚本 —— deploy/systemd/allwin-poll.service 的
# launchd 等价物。这台机器没有 systemd,deploy/systemd/*.timer 无法运行,
# 用 launchd 复刻同一语义:高频触发"到期判断",真正是否请求数据源完全交给
# backend/ingest/poll_windows.py 的 poll_state 节流决定(CLAUDE.md §6.3)。
#
# 触发间隔特意设为 60 秒而不是 300 秒:is_due() 用 elapsed >= interval_seconds
# 判定,mark_polled() 存的是"本轮启动后"的时刻;若触发间隔本身就等于节流阈值
# (300s/900s),两次启动之间的调度抖动会让 elapsed 偶发落在阈值之下、整跳过
# 一拍,导致稳态节流不是 300s/900s 而是接近 400s/1100s。60 秒触发+真实节流
# 阈值不变,才能让节流阈值本身就是精确值,不被触发抖动污染。
#
# 用法(手动一次性调用,便于验证):bash deploy/scripts/poll_local.sh
# launchd 用法见同目录 com.allwin.poll.plist。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/runtime/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/poll_local.log"

# launchd 不继承登录 shell 的环境变量,必须显式加载 .env(THORDATA_PROXY 等)。
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

{
  echo "== $(date -u +%Y-%m-%dT%H:%M:%SZ) poll_local tick =="
  rc=0
  "$PY" -m backend.worker.poll_wrapper || rc=$?
  echo "-- exit=$rc --"
} >> "$LOG_FILE" 2>&1
