#!/usr/bin/env bash
# allwin SQLite 三库备份(生产可靠性收口)
#
# 完整性不变量:一份"完整备份"必须同时包含 allwin.db + platform.db + odds.db,
# 缺任何一个都是失败,不是"跳过并成功"。
#
# 原子性:先在 .incomplete-<TS>-<PID>/ 临时目录里做完 .backup + integrity_check +
# 写 backup_metadata.json(size/sha256/integrity_check),全部通过后才用同目录内的
# `mv` 原子改名成 <UTC 时间戳>/(POSIX rename(2) 在同一文件系统内是原子操作)。
# 任何一步失败,临时目录被清理,BACKUP_ROOT 下不会出现一个"看起来完整但其实
# 半成品"的时间戳目录。
#
# 并发保护:单个 lock 文件 + noclobber(等价 O_CREAT|O_EXCL),跨 Linux/macOS
# 行为一致,不依赖 flock CLI(生产 Linux 与本地 macOS 测试行为相同);持有者
# 进程已死的陈锁自动清理并重试,仍被存活进程持有则明确"跳过"(exit 75),
# 不静默成功、不互相覆盖。
#
# 用法:bash deploy/scripts/backup_sqlite.sh
# 环境变量:ALLWIN_DATA_DIR(默认 <repo>/data)、BACKUP_KEEP(默认 14,必须为正整数)、
#           S3_BACKUP_BUCKET(可选)

set -euo pipefail
umask 077   # 备份/manifest/metadata 默认不允许组或其他用户读取

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_DIR="${ALLWIN_DATA_DIR:-$ROOT/data}"
BACKUP_ROOT="$DATA_DIR/backups"
MANIFEST_ROOT="$BACKUP_ROOT/manifests"
KEEP="${BACKUP_KEEP:-14}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
STAGING="$BACKUP_ROOT/.incomplete-$TS-$$"
DEST="$BACKUP_ROOT/$TS"
TS_PATTERN='^[0-9]{8}T[0-9]{6}Z$'
REQUIRED_DBS=(allwin.db platform.db odds.db)

log() { echo "[backup] $*"; }
die() { echo "[backup] ERROR: $*" >&2; exit 1; }

_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    die "找不到 sha256sum 或 shasum(计算备份校验和需要其中之一)"
  fi
}

_pid_alive() {
  kill -0 "$1" 2>/dev/null
}

command -v sqlite3 >/dev/null || die "找不到 sqlite3 CLI"
[ -d "$DATA_DIR" ] || die "数据目录不存在: $DATA_DIR"

# BACKUP_KEEP 必须是正整数,防止空/非法值进入后续清理逻辑(rm -rf 的边界安全)
case "$KEEP" in
  ''|*[!0-9]*) die "BACKUP_KEEP 必须是正整数,当前值: '$KEEP'" ;;
esac
[ "$KEEP" -ge 1 ] || die "BACKUP_KEEP 必须 >= 1,当前值: $KEEP"

mkdir -p "$BACKUP_ROOT"

# ── 并发保护 ─────────────────────────────────────────────────────────
# 用单个 lock 文件 + noclobber(等价 O_CREAT|O_EXCL)而不是"mkdir 目录 + 目录内
# 再写一个 pid 文件"——后者在两次独立操作之间存在窗口:目录已创建但 pid 文件
# 还没写完时,另一进程可能把它误判成"陈锁"进而误删,而 noclobber 重定向创建
# 文件与写入 PID 是同一条语句,不存在"已创建但内容为空"的可观察窗口。
LOCK_FILE="$BACKUP_ROOT/.backup.lock"
_lock_acquired=0
_acquire_lock() {
  local attempt holder_pid
  for attempt in 1 2; do
    if (set -o noclobber; echo "$$" > "$LOCK_FILE") 2>/dev/null; then
      _lock_acquired=1
      return 0
    fi
    holder_pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
    if [ -n "$holder_pid" ] && _pid_alive "$holder_pid"; then
      return 1   # 锁被存活进程持有,明确跳过
    fi
    log "发现陈锁(持有者 pid=${holder_pid:-unknown} 已不存在),清理后重试"
    rm -f "$LOCK_FILE"
  done
  return 1
}

if ! _acquire_lock; then
  log "另一个备份进程正在运行中(锁: $LOCK_FILE),本次明确跳过(不是失败,也不是静默成功)"
  exit 75
fi

_cleanup() {
  [ -d "${STAGING:-}" ] && rm -rf "$STAGING"
  [ "$_lock_acquired" -eq 1 ] && rm -f "$LOCK_FILE"
}
trap _cleanup EXIT

[ ! -e "$DEST" ] || die "备份目标已存在(同一 UTC 秒内的重复调用?): $DEST"

# 清理陈旧的 .incomplete-* 目录(上次运行被强杀 SIGKILL,trap 未执行时留下的);
# 目录名内嵌 PID,只清理持有进程已死的,不动其他并发实例正在写入的 staging。
for d in "$BACKUP_ROOT"/.incomplete-*; do
  [ -d "$d" ] || continue
  old_pid="${d##*-}"
  if ! [[ "$old_pid" =~ ^[0-9]+$ ]] || ! _pid_alive "$old_pid"; then
    log "清理陈旧的未完成备份目录: $(basename "$d")"
    rm -rf "$d"
  fi
done

mkdir -p "$STAGING"
echo "== allwin sqlite backup =="
echo "data dir : $DATA_DIR"
echo "staging  : $STAGING"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

META_ARGS=()
for name in "${REQUIRED_DBS[@]}"; do
  src="$DATA_DIR/$name"
  [ -f "$src" ] || die "源文件不存在: $src(三库必须齐全才算一次完整备份,缺一律失败)"
  out="$STAGING/$name"
  # .timeout 必须显式设(sqlite3 CLI 默认 0,遇到源库当下有并发写事务立即报
  # "database is locked",不重试)——2026-08-14 真实部署在有正常线上流量的
  # 服务器上复现:allwin.db 备份成功后 platform.db 恰好撞上一次会话写入,
  # 直接失败退出。30000ms 与 backend/db/connections.py 的
  # PRAGMA busy_timeout=30000 同一约定,给并发写事务让出提交窗口再重试。
  sqlite3 "$src" ".timeout 30000" ".backup '$out'"
  check="$(sqlite3 "$out" "PRAGMA integrity_check;")"
  if [ "$check" != "ok" ]; then
    die "$name 备份 integrity_check 失败: $check"
  fi
  size="$(wc -c < "$out" | tr -d ' ')"
  sha="$(_sha256 "$out")"
  echo "-- $name: .backup 完成,integrity_check=ok,${size} bytes,sha256=${sha}"
  META_ARGS+=("$name" "$size" "$sha" "$check")
done

# ── 写完整性元数据(JSON 由 Python 生成,避免手拼字符串转义问题) ────────
"$PY" - "$STAGING" "$TS" "${META_ARGS[@]}" <<'PYEOF'
import json
import sys

staging, ts = sys.argv[1], sys.argv[2]
rest = sys.argv[3:]
databases = {}
for i in range(0, len(rest), 4):
    name, size, sha, check = rest[i], int(rest[i + 1]), rest[i + 2], rest[i + 3]
    databases[name] = {"size": size, "sha256": sha, "integrity_check": check}
meta = {
    "created_at": ts,
    "databases": databases,
    "complete": len(databases) == 3 and all(v["integrity_check"] == "ok" for v in databases.values()),
}
with open(f"{staging}/backup_metadata.json", "w", encoding="utf-8") as f:
    json.dump(meta, f, indent=2, sort_keys=True)
    f.write("\n")
PYEOF

chmod 700 "$STAGING"
chmod 600 "$STAGING"/*.db "$STAGING/backup_metadata.json"

# ── 原子发布:同一文件系统内的 mv = POSIX rename(2),要么整体可见要么不可见 ──
mv "$STAGING" "$DEST"
STAGING=""   # 已改名,cleanup trap 不应再尝试删除旧路径

echo "== 备份完成: $DEST(3 库,integrity_check 全部 ok,原子发布)=="

# ── prediction manifest 导出(与数据库备份分开目录,非完整性判据的一部分) ──
if [ -f "$DEST/platform.db" ]; then
  has_table="$(sqlite3 "$DEST/platform.db" "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='prediction_manifests';")"
  if [ "$has_table" = "1" ]; then
    mkdir -p "$MANIFEST_ROOT/$TS"
    chmod 700 "$MANIFEST_ROOT/$TS"
    sqlite3 -json "$DEST/platform.db" \
      "SELECT id, manifest_date, version, manifest_json, manifest_hash, created_at, s3_key, uploaded_at FROM prediction_manifests ORDER BY manifest_date, version;" \
      > "$MANIFEST_ROOT/$TS/prediction_manifests.json"
    chmod 600 "$MANIFEST_ROOT/$TS/prediction_manifests.json"
    rows="$(sqlite3 "$DEST/platform.db" "SELECT count(*) FROM prediction_manifests;")"
    echo "-- manifests: 导出 $rows 行 → $MANIFEST_ROOT/$TS/prediction_manifests.json"
  else
    echo "-- manifests: platform.db 无 prediction_manifests 表,跳过导出"
  fi
fi

# ── 保留最近 KEEP 份(只 prune 完整备份目录,不误删 .incomplete-*/manifests) ──
#
# 2026-08-25 真实生产发现:旧备份目录可能由不同用户创建(如 systemd 定时器以
# allwin 用户跑的日常备份,umask 077 → 700 权限),本脚本换一个用户手动/由
# release.sh 触发执行时对那些目录没有删除权限,plain `rm -rf` 会返回非零、
# 在 set -euo pipefail 下直接杀死整个备份脚本——此时三库 .backup 已经全部
# 成功且 integrity_check=ok,只是"清理旧备份"这一步失败,却被上游 release.sh
# 误判成"备份失败,不执行 migration"。先尝试 plain rm(本地测试/同用户场景不
# 需要 sudo、不触发交互式密码提示),失败了再退避到 sudo rm(同 release.sh::
# cleanup_old_releases() 既有先例);两者都失败也只是警告,不让单个 prune
# 失败拖垮整个备份脚本的退出码。
prune() {
  local root="$1" label="$2"
  [ -d "$root" ] || return 0
  local dirs total
  dirs="$(cd "$root" && ls -1 2>/dev/null | grep -E "$TS_PATTERN" | sort || true)"
  total="$(printf '%s\n' "$dirs" | grep -c . || true)"
  if [ "$total" -gt "$KEEP" ]; then
    printf '%s\n' "$dirs" | head -n "$((total - KEEP))" | while read -r d; do
      [ -n "$d" ] || continue
      if rm -rf "${root:?}/$d" 2>/dev/null || sudo -n rm -rf "${root:?}/$d" 2>/dev/null; then
        echo "-- $label: 清理旧备份 $d(保留最近 $KEEP 份)"
      else
        echo "-- $label: 警告:清理旧备份 $d 失败(不影响本次备份结果)" >&2
      fi
    done
  else
    echo "-- $label: 当前 $total 份 ≤ 保留上限 $KEEP,无需清理"
  fi
}
prune "$BACKUP_ROOT" "db"
prune "$MANIFEST_ROOT" "manifests"

# ── S3 上传(只上传已原子发布、验证过的完整备份;未配置明确 LOCAL_ONLY) ──
if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
  command -v aws >/dev/null || die "配置了 S3_BACKUP_BUCKET 但找不到 aws CLI,上传失败"
  is_complete="$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['complete'])" "$DEST/backup_metadata.json")"
  [ "$is_complete" = "True" ] || die "备份 metadata 未标记 complete=true,拒绝上传(不上传半成品)"
  if ! aws s3 cp --recursive --sse AES256 "$DEST" "s3://$S3_BACKUP_BUCKET/db/$TS/"; then
    die "S3 上传失败: s3://$S3_BACKUP_BUCKET/db/$TS/"
  fi
  if [ -d "$MANIFEST_ROOT/$TS" ]; then
    if ! aws s3 cp --recursive --sse AES256 "$MANIFEST_ROOT/$TS" "s3://$S3_BACKUP_BUCKET/manifests/$TS/"; then
      die "S3 上传失败(manifests): s3://$S3_BACKUP_BUCKET/manifests/$TS/"
    fi
  fi
  echo "== S3: 已上传到 s3://$S3_BACKUP_BUCKET/{db,manifests}/$TS/ =="
else
  echo "== S3: LOCAL_ONLY(S3_BACKUP_BUCKET 未配置,仅本地备份)=="
fi
