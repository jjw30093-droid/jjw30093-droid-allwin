#!/usr/bin/env bash
# allwin 恢复演练(P0.11):取最近一份备份恢复到临时目录并逐库校验。
#
# - 找 data/backups/ 下最新的 <UTC时间戳> 目录;
# - 逐库复制到 mktemp 临时目录(模拟"恢复到新位置"),跑 PRAGMA integrity_check;
# - 附带打印每库表数量与关键表行数,证明恢复出来的库真实可查询;
# - 打印真实结果;任何一库失败,退出码非 0。
#
# 用法:bash deploy/scripts/restore_verify.sh
# 环境变量:ALLWIN_DATA_DIR(默认 <repo>/data)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_DIR="${ALLWIN_DATA_DIR:-$ROOT/data}"
BACKUP_ROOT="$DATA_DIR/backups"
TS_PATTERN='^[0-9]{8}T[0-9]{6}Z$'

command -v sqlite3 >/dev/null || { echo "ERROR: 找不到 sqlite3 CLI" >&2; exit 1; }
[ -d "$BACKUP_ROOT" ] || { echo "ERROR: 备份目录不存在: $BACKUP_ROOT(先跑 backup_sqlite.sh)" >&2; exit 1; }

LATEST="$(cd "$BACKUP_ROOT" && ls -1 2>/dev/null | grep -E "$TS_PATTERN" | sort | tail -n 1 || true)"
[ -n "$LATEST" ] || { echo "ERROR: $BACKUP_ROOT 下没有 <UTC时间戳> 备份目录" >&2; exit 1; }

SRC="$BACKUP_ROOT/$LATEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== allwin restore verify =="
echo "backup  : $SRC"
echo "restore : $TMP(临时目录,脚本结束后清理)"

FAIL=0
CHECKED=0
for db in "$SRC"/*.db; do
  [ -f "$db" ] || continue
  name="$(basename "$db")"
  cp "$db" "$TMP/$name"
  check="$(sqlite3 "$TMP/$name" "PRAGMA integrity_check;")"
  tables="$(sqlite3 "$TMP/$name" "SELECT count(*) FROM sqlite_master WHERE type='table';")"
  if [ "$check" = "ok" ]; then
    echo "-- $name: integrity_check=ok,tables=$tables"
  else
    echo "-- $name: integrity_check 失败: $check" >&2
    FAIL=1
  fi
  case "$name" in
    allwin.db)
      echo "   dim_match=$(sqlite3 "$TMP/$name" 'SELECT count(*) FROM dim_match;' 2>/dev/null || echo '查询失败')" ;;
    platform.db)
      echo "   job_runs=$(sqlite3 "$TMP/$name" 'SELECT count(*) FROM job_runs;' 2>/dev/null || echo '查询失败')" \
           "prediction_snapshots=$(sqlite3 "$TMP/$name" 'SELECT count(*) FROM prediction_snapshots;' 2>/dev/null || echo '查询失败')" ;;
  esac
  CHECKED=$((CHECKED + 1))
done

if [ "$CHECKED" -eq 0 ]; then
  echo "ERROR: $SRC 下没有 .db 文件" >&2
  exit 1
fi

if [ "$FAIL" -ne 0 ]; then
  echo "== 恢复演练失败:存在 integrity_check 不通过的库 ==" >&2
  exit 1
fi
echo "== 恢复演练通过:$CHECKED 库全部 integrity_check=ok =="
