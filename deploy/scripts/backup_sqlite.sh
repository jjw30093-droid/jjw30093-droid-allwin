#!/usr/bin/env bash
# allwin SQLite 备份(P0.11)
#
# - 对 data/{allwin,platform,odds}.db 逐库执行 sqlite3 ".backup"(WAL 安全,得到一致性快照);
# - 每个备份文件跑 PRAGMA integrity_check,不通过立即失败(绝不留下坏备份还报成功);
# - 备份落 data/backups/<UTC时间戳>/;prediction manifest 导出单独落 data/backups/manifests/<UTC时间戳>/;
# - 本地保留最近 BACKUP_KEEP 份(默认 14),多余的删除;
# - 配置了 S3_BACKUP_BUCKET + AWS 凭证(env 或 EC2 instance role)才上传 S3;
#   没配置就明确打印"S3 未配置,仅本地备份",绝不假装上传。
#
# 用法:bash deploy/scripts/backup_sqlite.sh
# 环境变量:ALLWIN_DATA_DIR(默认 <repo>/data)、BACKUP_KEEP(默认 14)、S3_BACKUP_BUCKET(可选)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_DIR="${ALLWIN_DATA_DIR:-$ROOT/data}"
BACKUP_ROOT="$DATA_DIR/backups"
MANIFEST_ROOT="$BACKUP_ROOT/manifests"
KEEP="${BACKUP_KEEP:-14}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_ROOT/$TS"
TS_PATTERN='^[0-9]{8}T[0-9]{6}Z$'

command -v sqlite3 >/dev/null || { echo "ERROR: 找不到 sqlite3 CLI" >&2; exit 1; }
[ -d "$DATA_DIR" ] || { echo "ERROR: 数据目录不存在: $DATA_DIR" >&2; exit 1; }

mkdir -p "$DEST"
echo "== allwin sqlite backup =="
echo "data dir : $DATA_DIR"
echo "dest     : $DEST"

BACKED_UP=0
for name in allwin.db platform.db odds.db; do
  src="$DATA_DIR/$name"
  if [ ! -f "$src" ]; then
    echo "-- $name: 源文件不存在,跳过"
    continue
  fi
  out="$DEST/$name"
  sqlite3 "$src" ".backup '$out'"
  check="$(sqlite3 "$out" "PRAGMA integrity_check;")"
  if [ "$check" != "ok" ]; then
    echo "ERROR: $name 备份 integrity_check 失败: $check" >&2
    exit 1
  fi
  size="$(wc -c < "$out" | tr -d ' ')"
  echo "-- $name: .backup 完成,integrity_check=ok,${size} bytes"
  BACKED_UP=$((BACKED_UP + 1))
done

if [ "$BACKED_UP" -eq 0 ]; then
  echo "ERROR: 一个库都没备份到($DATA_DIR 下无 allwin/platform/odds.db)" >&2
  rmdir "$DEST" 2>/dev/null || true
  exit 1
fi

# ── prediction manifest 导出(与数据库备份分开目录) ────────────────
if [ -f "$DEST/platform.db" ]; then
  has_table="$(sqlite3 "$DEST/platform.db" "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='prediction_manifests';")"
  if [ "$has_table" = "1" ]; then
    mkdir -p "$MANIFEST_ROOT/$TS"
    sqlite3 -json "$DEST/platform.db" \
      "SELECT id, manifest_date, version, manifest_json, manifest_hash, created_at, s3_key, uploaded_at FROM prediction_manifests ORDER BY manifest_date, version;" \
      > "$MANIFEST_ROOT/$TS/prediction_manifests.json"
    rows="$(sqlite3 "$DEST/platform.db" "SELECT count(*) FROM prediction_manifests;")"
    echo "-- manifests: 导出 $rows 行 → $MANIFEST_ROOT/$TS/prediction_manifests.json"
  else
    echo "-- manifests: platform.db 无 prediction_manifests 表,跳过导出"
  fi
fi

# ── 保留最近 KEEP 份(数据库备份与 manifest 导出分别清理) ──────────
prune() {
  local root="$1" label="$2"
  [ -d "$root" ] || return 0
  local dirs
  dirs="$(cd "$root" && ls -1 2>/dev/null | grep -E "$TS_PATTERN" | sort || true)"
  local total
  total="$(printf '%s\n' "$dirs" | grep -c . || true)"
  if [ "$total" -gt "$KEEP" ]; then
    printf '%s\n' "$dirs" | head -n "$((total - KEEP))" | while read -r d; do
      rm -rf "${root:?}/$d"
      echo "-- $label: 清理旧备份 $d(保留最近 $KEEP 份)"
    done
  else
    echo "-- $label: 当前 $total 份 ≤ 保留上限 $KEEP,无需清理"
  fi
}
prune "$BACKUP_ROOT" "db"
prune "$MANIFEST_ROOT" "manifests"

# ── S3 上传(显式配置才上传,失败就是失败) ────────────────────────
if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
  command -v aws >/dev/null || { echo "ERROR: 配置了 S3_BACKUP_BUCKET 但找不到 aws CLI,上传失败" >&2; exit 1; }
  aws s3 cp --recursive "$DEST" "s3://$S3_BACKUP_BUCKET/db/$TS/"
  if [ -d "$MANIFEST_ROOT/$TS" ]; then
    aws s3 cp --recursive "$MANIFEST_ROOT/$TS" "s3://$S3_BACKUP_BUCKET/manifests/$TS/"
  fi
  echo "-- S3: 已上传到 s3://$S3_BACKUP_BUCKET/{db,manifests}/$TS/"
else
  echo "-- S3 未配置(S3_BACKUP_BUCKET 为空),仅本地备份"
fi

echo "== 备份完成: $DEST($BACKED_UP 库,integrity_check 全部 ok)=="
