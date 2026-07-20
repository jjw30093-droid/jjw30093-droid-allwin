#!/usr/bin/env bash
# 浏览器构建产物门禁(宪法 §10.3 / §14.2):
# .next/static(浏览器 chunk)不得包含 http://127.0.0.1:8000 或 http://localhost:8000。
#
# 原因:NEXT_PUBLIC_* 在 next build 时被内联进浏览器产物;一旦回环地址被打进
# 浏览器 chunk,生产用户的浏览器会去请求"用户自己机器"的 8000 端口,必然失败。
# 生产构建必须让浏览器走同源相对 /api/v1(NEXT_PUBLIC_API_BASE 留空)。
#
# 用法:
#   bash deploy/scripts/check_browser_bundle.sh [frontend目录]
# 默认检查仓库内 frontend/;release.sh 传入 release 目录下的 frontend/。
# 退出码:0 = 干净;1 = 发现回环地址或产物缺失。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${1:-$SCRIPT_DIR/../../frontend}"
STATIC_DIR="$FRONTEND_DIR/.next/static"

log() { echo "[check-bundle] $*"; }

if [ ! -d "$STATIC_DIR" ]; then
  log "ERROR: 找不到 $STATIC_DIR(先在 $FRONTEND_DIR 执行 npm run build)" >&2
  exit 1
fi

# 只匹配完整回环 API 地址;E2E 构建的 :8010 等测试端口不受影响。
PATTERN='http://127\.0\.0\.1:8000|http://localhost:8000'

if grep -RIlsqE -- "$PATTERN" "$STATIC_DIR"; then
  log "FAIL: 浏览器产物包含服务端回环 API 地址(生产用户浏览器不可达):" >&2
  grep -RIlsE -- "$PATTERN" "$STATIC_DIR" >&2 || true
  log "修复:构建前确保 NEXT_PUBLIC_API_BASE 为空(生产走同源相对 /api/v1);" >&2
  log "     并检查 frontend/.env.local 是否残留了本地地址(next build 会读取它)。" >&2
  exit 1
fi

log "OK: $STATIC_DIR 不含 http://127.0.0.1:8000 / http://localhost:8000"
