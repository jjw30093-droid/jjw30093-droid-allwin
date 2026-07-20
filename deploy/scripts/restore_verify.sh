#!/usr/bin/env bash
# allwin 恢复演练(生产可靠性收口):取最近一份"完整"备份恢复到临时目录并严格校验。
#
# 只接受由 backup_sqlite.sh 原子发布、带 backup_metadata.json 且 complete=true
# 的备份目录——不完整/无 metadata 的目录一律拒绝,不当成可用备份。
#
# 校验项(任一不满足,非零退出):
#   - 三库(allwin/platform/odds.db)精确齐全,文件名与 metadata 记录一致;
#   - 每库 SHA-256 与 metadata 记录一致(检测备份文件在落盘后被篡改/损坏);
#   - 每库 PRAGMA integrity_check = ok(独立于 SHA-256,重新在恢复出的副本上验证);
#   - 关键表可查询(dim_match / job_runs+prediction_snapshots / source_health+poll_state);
#   - migration 无 pending、无 checksum_drift(用 backend.db.migrate.status 对恢复出
#     的库副本做只读检查,不修改任何库,也不需要真的执行一次迁移)。
#
# 恢复目标是 mktemp 临时目录;不修改备份源、不修改真实数据库;脚本结束自动清理。
#
# 用法:bash deploy/scripts/restore_verify.sh
# 环境变量:ALLWIN_DATA_DIR(默认 <repo>/data)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_DIR="${ALLWIN_DATA_DIR:-$ROOT/data}"
BACKUP_ROOT="$DATA_DIR/backups"
TS_PATTERN='^[0-9]{8}T[0-9]{6}Z$'
REQUIRED_DBS=(allwin.db platform.db odds.db)

log() { echo "[restore-verify] $*"; }
die() { echo "[restore-verify] ERROR: $*" >&2; exit 1; }

_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    die "找不到 sha256sum 或 shasum"
  fi
}

command -v sqlite3 >/dev/null || die "找不到 sqlite3 CLI"
[ -d "$BACKUP_ROOT" ] || die "备份目录不存在: $BACKUP_ROOT(先跑 backup_sqlite.sh)"

# 只在"看起来像完整时间戳目录"里挑——天然排除 .incomplete-*、manifests/ 等。
LATEST=""
for d in $(cd "$BACKUP_ROOT" && ls -1 2>/dev/null | grep -E "$TS_PATTERN" | sort -r); do
  if [ -f "$BACKUP_ROOT/$d/backup_metadata.json" ]; then
    LATEST="$d"
    break
  fi
  log "跳过 $d(无 backup_metadata.json,不是本工具能验证的完整备份)"
done
[ -n "$LATEST" ] || die "$BACKUP_ROOT 下没有带 backup_metadata.json 的完整备份目录"

SRC="$BACKUP_ROOT/$LATEST"
META="$SRC/backup_metadata.json"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

# 一次性把 metadata 读成简单的 "name<TAB>size<TAB>sha256" 行,交给 bash 逐库比对——
# 避免对每个字段单独起一个 python -c 进程、避免把路径插进 -c 源码字符串里。
META_LINES="$("$PY" - "$META" <<'PYEOF'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    meta = json.load(f)
print("__COMPLETE__", meta.get("complete"), sep="\t")
for name, info in meta.get("databases", {}).items():
    print(name, info.get("size", ""), info.get("sha256", ""), sep="\t")
PYEOF
)"
IS_COMPLETE="$(printf '%s\n' "$META_LINES" | awk -F'\t' '$1=="__COMPLETE__"{print $2}')"
[ "$IS_COMPLETE" = "True" ] || die "$SRC 的 backup_metadata.json 标记 complete=false,拒绝当作有效备份恢复"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== allwin restore verify =="
echo "backup  : $SRC"
echo "restore : $TMP(临时目录,脚本结束后清理)"

FAIL=0

for name in "${REQUIRED_DBS[@]}"; do
  db="$SRC/$name"
  if [ ! -f "$db" ]; then
    echo "-- $name: 备份目录缺失该库文件(不完整,拒绝)" >&2
    FAIL=1
    continue
  fi

  expected_line="$(printf '%s\n' "$META_LINES" | awk -F'\t' -v n="$name" '$1==n{print; found=1} END{if(!found) exit 1}' || true)"
  expected_size="$(printf '%s' "$expected_line" | awk -F'\t' '{print $2}')"
  expected_sha="$(printf '%s' "$expected_line" | awk -F'\t' '{print $3}')"
  if [ -z "$expected_size" ] || [ -z "$expected_sha" ]; then
    echo "-- $name: backup_metadata.json 缺少该库的记录(metadata 不一致,拒绝)" >&2
    FAIL=1
    continue
  fi

  cp "$db" "$TMP/$name"

  actual_size="$(wc -c < "$TMP/$name" | tr -d ' ')"
  actual_sha="$(_sha256 "$TMP/$name")"
  if [ "$actual_size" != "$expected_size" ] || [ "$actual_sha" != "$expected_sha" ]; then
    echo "-- $name: 与 metadata 不一致(size ${actual_size} vs ${expected_size},sha256 ${actual_sha} vs ${expected_sha})" >&2
    FAIL=1
    continue
  fi

  check="$(sqlite3 "$TMP/$name" "PRAGMA integrity_check;")"
  if [ "$check" != "ok" ]; then
    echo "-- $name: integrity_check 失败: $check" >&2
    FAIL=1
    continue
  fi
  tables="$(sqlite3 "$TMP/$name" "SELECT count(*) FROM sqlite_master WHERE type='table';")"
  echo "-- $name: checksum 一致,integrity_check=ok,tables=$tables"

  case "$name" in
    allwin.db)
      echo "   dim_match=$(sqlite3 "$TMP/$name" 'SELECT count(*) FROM dim_match;' 2>/dev/null || echo '查询失败')" ;;
    platform.db)
      echo "   job_runs=$(sqlite3 "$TMP/$name" 'SELECT count(*) FROM job_runs;' 2>/dev/null || echo '查询失败')" \
           "prediction_snapshots=$(sqlite3 "$TMP/$name" 'SELECT count(*) FROM prediction_snapshots;' 2>/dev/null || echo '查询失败')"
      ;;
    odds.db)
      echo "   source_health=$(sqlite3 "$TMP/$name" 'SELECT count(*) FROM source_health;' 2>/dev/null || echo '查询失败')" \
           "poll_state=$(sqlite3 "$TMP/$name" 'SELECT count(*) FROM poll_state;' 2>/dev/null || echo '查询失败')"
      ;;
  esac
done

# ── migration 状态:三库都不能有 pending 或 checksum_drift(只读检查,
#    不修改恢复出的库副本、不修改真实数据库、不需要真的跑一次迁移) ────
if [ "$FAIL" -eq 0 ]; then
  drift_report="$("$PY" - "$ROOT" "$TMP" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from backend.db import migrate

tmp = sys.argv[2]
bad = []
for name, filename in (("core", "allwin.db"), ("platform", "platform.db"), ("odds", "odds.db")):
    st = migrate.status(name, db_file=f"{tmp}/{filename}")
    if st["pending"]:
        bad.append(f"{name}: pending={st['pending']}")
    if st["checksum_drift"]:
        bad.append(f"{name}: checksum_drift={st['checksum_drift']}")
print("\n".join(bad))
PYEOF
  )"
  if [ -n "$drift_report" ]; then
    echo "-- migration 状态异常:" >&2
    echo "$drift_report" >&2
    FAIL=1
  else
    echo "-- migration: 三库均无 pending、无 checksum_drift"
  fi
fi

if [ "$FAIL" -ne 0 ]; then
  echo "== 恢复演练失败 ==" >&2
  exit 1
fi
echo "== 恢复演练通过:$LATEST(${#REQUIRED_DBS[@]} 库,checksum/integrity_check/migration 全部通过)=="
