#!/usr/bin/env bash
# allwin 服务器端发布脚本(P0.11)。
#
# !!! 本脚本面向服务器(/opt/allwin 布局),不在开发机上执行 !!!
#
# 目录布局:
#   /opt/allwin/
#   ├── source/              # git 裸检出(git fetch 的工作副本)
#   ├── releases/<git-sha>/  # 每次发布一个不可变目录(代码 + .venv + 前端构建产物)
#   ├── current -> releases/<git-sha>   # 原子切换的软链,systemd 单元都指它
#   └── shared/
#       ├── .env             # 生产环境变量(唯一副本,不进 git)
#       └── data/            # SQLite 三库(ALLWIN_DATA_DIR 指到这里,release 之间不动)
#
# 流程:rsync 代码 → 装 .venv → 前端构建 → 备份 → migration → 候选进程冒烟
#       → 切 current 软链 → 重启 systemd → healthz/readyz 验收 → 失败自动回滚上一 release。
#
# 用法(在服务器上):
#   cd /opt/allwin/source && git fetch && git checkout <ref>
#   bash deploy/scripts/release.sh

set -euo pipefail

APP_ROOT="${ALLWIN_APP_ROOT:-/opt/allwin}"
SOURCE_DIR="$APP_ROOT/source"
RELEASES_DIR="$APP_ROOT/releases"
SHARED_DIR="$APP_ROOT/shared"
CURRENT_LINK="$APP_ROOT/current"
SMOKE_PORT="${SMOKE_PORT:-8001}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"

log() { echo "[release] $*"; }
die() { echo "[release] ERROR: $*" >&2; exit 1; }

[ -d "$SOURCE_DIR/.git" ] || die "$SOURCE_DIR 不是 git 检出(本脚本面向服务器,不在开发机执行)"
[ -f "$SHARED_DIR/.env" ] || die "缺少 $SHARED_DIR/.env(生产环境变量)"
[ -d "$SHARED_DIR/data" ] || die "缺少 $SHARED_DIR/data(SQLite 数据目录)"

SHA="$(git -C "$SOURCE_DIR" rev-parse --short=12 HEAD)"
RELEASE_DIR="$RELEASES_DIR/$SHA"
PREVIOUS="$(readlink "$CURRENT_LINK" 2>/dev/null || true)"

log "发布 $SHA(上一 release: ${PREVIOUS:-无})"

# ── 1. rsync 代码进不可变 release 目录 ──────────────────────────────
mkdir -p "$RELEASE_DIR"
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude 'data' --exclude '__pycache__' --exclude '.next' \
  "$SOURCE_DIR/" "$RELEASE_DIR/"

# ── 2. Python 依赖(release 内独立 .venv,避免跨 release 污染) ──────
log "安装 Python 依赖"
python3 -m venv "$RELEASE_DIR/.venv"
"$RELEASE_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$RELEASE_DIR/.venv/bin/pip" install --quiet -r "$RELEASE_DIR/requirements.txt"

# ── 3. 前端构建 ─────────────────────────────────────────────────────
log "前端 npm ci + next build"
( cd "$RELEASE_DIR/frontend" && npm ci --silent && npm run build )

# ── 4. 迁移前先备份(备份失败 = 发布失败,绝不带病迁移) ────────────
log "migration 前备份三库"
( cd "$RELEASE_DIR" \
  && set -a && . "$SHARED_DIR/.env" && set +a \
  && ALLWIN_DATA_DIR="$SHARED_DIR/data" bash deploy/scripts/backup_sqlite.sh )

# ── 5. migration(幂等;checksum 漂移会拒绝执行) ───────────────────
log "python -m backend.db.migrate --all"
( cd "$RELEASE_DIR" \
  && set -a && . "$SHARED_DIR/.env" && set +a \
  && ALLWIN_DATA_DIR="$SHARED_DIR/data" "$RELEASE_DIR/.venv/bin/python" -m backend.db.migrate --all )

# ── 6. 候选进程冒烟:临时端口起 API,healthz/readyz 都要 200 ────────
log "候选 API 冒烟(127.0.0.1:$SMOKE_PORT)"
smoke_ok=0
(
  cd "$RELEASE_DIR" \
  && set -a && . "$SHARED_DIR/.env" && set +a \
  && ALLWIN_DATA_DIR="$SHARED_DIR/data" \
     "$RELEASE_DIR/.venv/bin/uvicorn" backend.api.app:app --host 127.0.0.1 --port "$SMOKE_PORT"
) & SMOKE_PID=$!
trap '[ -n "${SMOKE_PID:-}" ] && kill "$SMOKE_PID" 2>/dev/null || true' EXIT

for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$SMOKE_PORT/healthz" >/dev/null \
     && curl -sf "http://127.0.0.1:$SMOKE_PORT/readyz" >/dev/null; then
    smoke_ok=1
    break
  fi
  sleep 1
done
kill "$SMOKE_PID" 2>/dev/null || true
wait "$SMOKE_PID" 2>/dev/null || true
SMOKE_PID=""
[ "$smoke_ok" -eq 1 ] || die "候选进程 healthz/readyz 冒烟失败,current 软链未切换,线上不受影响"

# ── 7. 原子切换 current 软链 + 重启服务 ─────────────────────────────
rollback() {
  if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then
    log "!!! 验收失败,回滚到 $PREVIOUS"
    ln -sfn "$PREVIOUS" "$CURRENT_LINK"
    sudo systemctl restart allwin-api allwin-web
    die "已回滚到上一 release($PREVIOUS)"
  fi
  die "验收失败且无上一 release 可回滚,请人工介入"
}

log "切换 current -> $RELEASE_DIR"
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
sudo systemctl restart allwin-api allwin-web

# ── 8. 线上验收:真实端口 healthz/readyz ────────────────────────────
verify_ok=0
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:8000/healthz" >/dev/null \
     && curl -sf "http://127.0.0.1:8000/readyz" >/dev/null \
     && curl -sf "http://127.0.0.1:3000/" >/dev/null; then
    verify_ok=1
    break
  fi
  sleep 1
done
[ "$verify_ok" -eq 1 ] || rollback

# ── 9. 清理旧 release(保留最近 KEEP_RELEASES 个 + 当前) ───────────
( cd "$RELEASES_DIR" && ls -1t | tail -n "+$((KEEP_RELEASES + 1))" | while read -r d; do
    [ "$RELEASES_DIR/$d" = "$RELEASE_DIR" ] && continue
    [ "$RELEASES_DIR/$d" = "$PREVIOUS" ] && continue
    rm -rf "${RELEASES_DIR:?}/$d"
    log "清理旧 release: $d"
  done )

log "发布完成: current -> $RELEASE_DIR"
log "提醒:Cloudflare purge 静态缓存 + 隐私模式核验(CLAUDE.md §10);"
log "     三必查:systemctl show allwin-api -p ExecStart / nginx -T 的 proxy_pass / curl 域名指纹"
