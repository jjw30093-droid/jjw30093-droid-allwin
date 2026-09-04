#!/usr/bin/env bash
# allwin 服务器端发布脚本(生产可靠性收口)。
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
# 流程:preflight → rsync 代码(不可变 release,拒绝覆盖已存在的同 SHA 目录)
#       → 装 .venv → 前端构建 → 浏览器产物门禁 → 三库严格备份(缺一律失败)
#       → migration → 候选进程冒烟 → 切 current 软链 → 重启 systemd
#       → 线上 healthz/readyz/首页验收 → 业务冒烟 → 失败在任一阶段自动回滚
#       上一 release 并重新验收 → 成功后清理旧 release(current/previous 永不清理)。
#
# 用法(在服务器上):
#   cd /opt/allwin/source && git fetch && git checkout <ref>
#   bash deploy/scripts/release.sh
#
# 可测试性:所有外部命令(git/rsync/python3/npm/curl/sqlite3/sudo/systemctl)均可
# 通过 PATH 替换为测试用的假实现;`ALLWIN_APP_ROOT` 可整体重定向到临时目录。
# 设置 RELEASE_SH_SOURCE_ONLY=1 后 source 本文件,可单独调用其中的函数
# (preflight/rollback 等)用于单元级测试,不会自动跑 main()。

set -euo pipefail

APP_ROOT="${ALLWIN_APP_ROOT:-/opt/allwin}"
SOURCE_DIR="$APP_ROOT/source"
RELEASES_DIR="$APP_ROOT/releases"
SHARED_DIR="$APP_ROOT/shared"
CURRENT_LINK="$APP_ROOT/current"
SMOKE_PORT="${SMOKE_PORT:-8001}"
LIVE_API_PORT="${LIVE_API_PORT:-8000}"
LIVE_WEB_PORT="${LIVE_WEB_PORT:-3000}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
MIN_FREE_DISK_MB="${MIN_FREE_DISK_MB:-2048}"

# 2026-08-23 首页信息架构重排后,FreshnessBlock 默认态文案从"赛程更新
# {时间}"改成"数据更新:赛程 {时间} · 赔率 {时间}"("赛程更新"四字连续只在
# STALE/UNAVAILABLE 才展开的降级态里出现)——原标记在默认(健康)态测不到,
# 会把一次正常发布误判成业务冒烟失败。"赛程"两字在默认态与降级态都存在,
# 改用它作为标记,语义不变(仍是确认首页真的渲染出赛程新鲜度这块 API 数据,
# 不是空页/错误页)。
SMOKE_HTML_MARKER="${SMOKE_HTML_MARKER:-赛程}"
# 冒烟重试次数/间隔可覆盖,测试环境用更短的等待,不必等生产的 30×1s
SMOKE_RETRIES="${SMOKE_RETRIES:-30}"
SMOKE_INTERVAL="${SMOKE_INTERVAL:-1}"
BUSINESS_SMOKE_RETRIES="${BUSINESS_SMOKE_RETRIES:-18}"
BUSINESS_SMOKE_INTERVAL="${BUSINESS_SMOKE_INTERVAL:-10}"

log() { echo "[release] $*"; }
die() { echo "[release] ERROR: $*" >&2; exit 1; }

# 目标路径必须落在 APP_ROOT 内部,防止 KEEP_RELEASES/RELEASES_DIR 被错误配置后
# 清理逻辑波及仓库外任意路径。
_within_app_root() {
  case "$1" in
    "$APP_ROOT"/*) return 0 ;;
    *) return 1 ;;
  esac
}

_require_commands() {
  local missing=() cmd
  for cmd in git rsync python3 npm curl sqlite3 sudo systemctl; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
  done
  [ ${#missing[@]} -eq 0 ] || die "缺少必需命令: ${missing[*]}"
}

_free_disk_mb() {
  df -Pk "$APP_ROOT" 2>/dev/null | tail -n 1 | awk '{print int($4/1024)}'
}

# ── 阶段 1:preflight(在任何构建/备份/migration/切换之前) ────────────
preflight() {
  log "preflight 检查"
  _require_commands

  [ -d "$SOURCE_DIR/.git" ] || die "$SOURCE_DIR 不是 git 检出(本脚本面向服务器,不在开发机执行)"
  [ -f "$SHARED_DIR/.env" ] || die "缺少 $SHARED_DIR/.env(生产环境变量)"
  [ -d "$SHARED_DIR/data" ] || die "缺少 $SHARED_DIR/data(SQLite 数据目录)"
  [ -f "$SHARED_DIR/models/wdl_baseline_params.pkl" ] \
    || die "缺少 $SHARED_DIR/models/wdl_baseline_params.pkl(模型二进制不进 Git,首次部署需手动放置一次到 shared/models/)"

  for db in allwin.db platform.db odds.db; do
    [ -f "$SHARED_DIR/data/$db" ] || die "缺少 $SHARED_DIR/data/$db(三库必须齐全才能安全 migration)"
  done

  local free_mb
  free_mb="$(_free_disk_mb)"
  if [ -n "$free_mb" ] && [ "$free_mb" -lt "$MIN_FREE_DISK_MB" ]; then
    die "磁盘可用空间不足: ${free_mb}MB < 下限 ${MIN_FREE_DISK_MB}MB(MIN_FREE_DISK_MB 可覆盖)"
  fi

  # current/previous 软链形状必须合法:不存在,或指向 RELEASES_DIR 下的目录
  if [ -e "$CURRENT_LINK" ]; then
    [ -L "$CURRENT_LINK" ] || die "$CURRENT_LINK 存在但不是软链接,拒绝继续(需要人工检查)"
    local target
    target="$(cd "$(dirname "$CURRENT_LINK")" && readlink "$CURRENT_LINK" || true)"
    case "$target" in
      "$RELEASES_DIR"/*|releases/*) ;;
      *) die "$CURRENT_LINK 指向非法目标(不在 $RELEASES_DIR 下): ${target:-<空>}" ;;
    esac
  fi

  # 默认拒绝 dirty 源码树(未提交/未跟踪改动都算 dirty)
  if [ -n "$(git -C "$SOURCE_DIR" status --porcelain)" ]; then
    die "$SOURCE_DIR 存在未提交/未跟踪改动,拒绝发布 dirty 源码树" \
        "(先在 source 里 git status 确认干净,或 git checkout/clean 到目标提交)"
  fi

  SHA="$(git -C "$SOURCE_DIR" rev-parse --short=12 HEAD)"
  RELEASE_DIR="$RELEASES_DIR/$SHA"
  PREVIOUS="$(cd "$APP_ROOT" 2>/dev/null && readlink "$CURRENT_LINK" 2>/dev/null || true)"
  if [ -n "$PREVIOUS" ] && [[ "$PREVIOUS" != /* ]]; then
    PREVIOUS="$APP_ROOT/$PREVIOUS"   # readlink 可能返回相对路径
  fi

  # release 一旦构建即视为不可变:同一 SHA 的目录已存在就拒绝(不重新 rsync 覆盖)
  [ ! -e "$RELEASE_DIR" ] || die "release 目录已存在,拒绝覆盖(release 不可变): $RELEASE_DIR" \
      "(如需重新发布同一提交,先人工确认并移除该目录,不由脚本自动覆盖)"

  log "preflight 通过:发布 $SHA(上一 release: ${PREVIOUS:-无})"
}

# 纵深防御:rsync --exclude 已经是排除凭证文件的主机制,这里只是复制完成后
# 再扫一遍 release 目录确认没有已知凭证文件混进去——不能替代上面的 --exclude
# (更不能只依赖这一步事后检测),只是双保险,避免未来有人改动 --exclude 列表
# 时悄悄削弱它却没人注意到。
assert_no_credentials_in_release() {
  local hit
  hit="$(find "$RELEASE_DIR" -type f \( \
      -name '.env' -o -name '.env.*' -o \
      -name 'id_rsa' -o -name 'id_rsa.pub' -o \
      -name 'id_dsa' -o -name 'id_dsa.pub' -o \
      -name 'id_ecdsa' -o -name 'id_ecdsa.pub' -o \
      -name 'id_ed25519' -o -name 'id_ed25519.pub' -o \
      -name '*.pem' -o -name '*.key' -o -name '*.p12' -o -name '*.pfx' -o \
      -name 'credentials.json' \
    \) -print -o -type d -name '.ssh' -print -o -type d -name '.aws' -print \
    2>/dev/null | head -n 1)"
  [ -z "$hit" ] || die "release 目录混入疑似凭证文件,rsync 排除规则未生效: $hit" \
      "(这是复制后的纵深防御检测,说明 --exclude 列表本身需要修复,不是这里的 bug)"
}

# ── 阶段 2a:rsync 代码进不可变 release 目录(单独成函数,便于只测这一步) ──
do_rsync() {
  mkdir -p "$RELEASE_DIR"
  log "rsync 代码进不可变 release 目录"
  # 排除模式不带 '/' 时,rsync 按 basename 在任意目录深度匹配(而不仅仅是
  # SOURCE_DIR 根目录)——根目录和任意嵌套子目录里的这些文件/目录同样被排除,
  # 不需要额外的 **/ 前缀。凭证类排除是"复制阶段就不进 release"的第一道防线,
  # 不是"发现后再报错"那种事后检测——见下方 do_rsync 之后的 assert_no_credentials
  # 扫描,那只是纵深防御,不能替代这里的 --exclude。
  rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
    --exclude 'data' --exclude '__pycache__' --exclude '.next' \
    --exclude '.env' --exclude '.env.*' --exclude 'frontend/.env.local' \
    --exclude '.pytest_cache' --exclude 'test-results' --exclude 'playwright-report' \
    --exclude '.claude' --exclude '.codex' --exclude '*.pyc' \
    --exclude '.ssh' --exclude '.aws' \
    --exclude 'id_rsa' --exclude 'id_rsa.pub' \
    --exclude 'id_dsa' --exclude 'id_dsa.pub' \
    --exclude 'id_ecdsa' --exclude 'id_ecdsa.pub' \
    --exclude 'id_ed25519' --exclude 'id_ed25519.pub' \
    --exclude '*.pem' --exclude '*.key' --exclude '*.p12' --exclude '*.pfx' \
    --exclude 'credentials.json' \
    "$SOURCE_DIR/" "$RELEASE_DIR/"
  assert_no_credentials_in_release
}

# 本脚本以构建用户(如 ubuntu)身份跑,构建产物默认属主/组也是它——但线上
# 服务以 allwin-web systemd 单元的 User=allwin 运行,不在构建用户的组里,
# 目录权限落到 rwxrwxr-x 的 "other" 档只有 r-x,没有写权限。CLAUDE.md §4
# 要求单实例阶段用 Next.js 本地持久 ISR 缓存,这意味着 allwin-web 运行时
# 必须能写回 .next/cache/fetch-cache 与 .next/server/app/*.html 做按需
# 重新验证——2026-08-14 真实发布验证时在 journalctl 里发现每次重新验证都
# 是 EACCES 静默失败(Next.js 只记日志不崩溃,不会让 release.sh 冒烟失败),
# 说明这个缺口已经在之前的历次发布里一直存在。单独成函数,便于只测这一步。
fix_next_permissions() {
  sudo chgrp -R allwin "$RELEASE_DIR/frontend/.next"
  sudo chmod -R g+w "$RELEASE_DIR/frontend/.next"
}

# 模型参数文件是训练产物,不进 Git(CLAUDE.md §14.1),rsync 从 source/ 检出
# 天然不会带上它——持久化在 shared/models/ 下(与 shared/data、shared/.env
# 同一模式:不随 release 增删,跨 release 复用),每次发布显式拷贝进新
# release 目录。2026-08-17 真实发现:此前所有 release 都缺这个文件,
# model_predict 从未在生产成功跑过一次,只是没有定时器触发过才没暴露。
# 2026-08-18 真实生产事故(首次装上 allwin-postmatch.timer 后立即暴露):
# 裸 cp 不带 -p,新文件属组是运行本脚本的部署用户(ubuntu)的默认组,不是
# allwin——线上服务以 User=allwin 运行,读这份文件时 PermissionError(mode
# 640、group=ubuntu,allwin 用户不在 ubuntu 组,落到"其它用户"权限位,此处
# 恰好是 0)。同 fix_next_permissions 的先例,显式 chgrp+chmod 到 allwin 组,
# 不依赖 cp 的默认行为。
copy_model_artifacts() {
  mkdir -p "$RELEASE_DIR/backend/models/artifacts"
  local dest="$RELEASE_DIR/backend/models/artifacts/wdl_baseline_params.pkl"
  cp "$SHARED_DIR/models/wdl_baseline_params.pkl" "$dest"
  sudo chgrp allwin "$dest"
  sudo chmod g+r "$dest"
}

# ── 阶段 2b:依赖 + 前端构建 + 浏览器产物门禁 ─────────────────────────
do_build() {
  do_rsync
  copy_model_artifacts

  log "安装 Python 依赖"
  python3 -m venv "$RELEASE_DIR/.venv"
  "$RELEASE_DIR/.venv/bin/pip" install --quiet --upgrade pip
  "$RELEASE_DIR/.venv/bin/pip" install --quiet -r "$RELEASE_DIR/requirements.txt"

  log "前端 npm ci + next build(先 source shared/.env)"
  ( cd "$RELEASE_DIR/frontend" \
    && set -a && . "$SHARED_DIR/.env" && set +a \
    && npm ci --silent && npm run build )

  fix_next_permissions

  bash "$RELEASE_DIR/deploy/scripts/check_browser_bundle.sh" "$RELEASE_DIR/frontend" \
    || die "浏览器构建产物含 127.0.0.1/localhost API 地址;检查 shared/.env 的 NEXT_PUBLIC_API_BASE(生产应留空)与残留的 frontend/.env.local"
  bash "$RELEASE_DIR/deploy/scripts/check_public_urls.sh" "$RELEASE_DIR/frontend" \
    || die "sitemap.xml/robots.txt/llms.txt 含占位域名或 host 不一致;检查 shared/.env 的 NEXT_PUBLIC_SITE_URL 是否设为 https://miaomiaodi.vip"
  "$RELEASE_DIR/.venv/bin/python" "$RELEASE_DIR/scripts/verify_next_assets.py" \
    --frontend "$RELEASE_DIR/frontend" \
    || die "Next 构建 HTML/manifest 引用了不存在的静态资源"
}

# ── 阶段 3:migration 前先备份(备份失败 = 发布失败,绝不带病迁移) ────
do_backup_and_migrate() {
  log "migration 前备份三库(缺一律失败,不带病迁移)"
  ( cd "$RELEASE_DIR" \
    && set -a && . "$SHARED_DIR/.env" && set +a \
    && ALLWIN_DATA_DIR="$SHARED_DIR/data" bash deploy/scripts/backup_sqlite.sh ) \
    || die "备份失败,不执行 migration,current 未切换,线上不受影响"

  log "python -m backend.db.migrate --all"
  ( cd "$RELEASE_DIR" \
    && set -a && . "$SHARED_DIR/.env" && set +a \
    && ALLWIN_DATA_DIR="$SHARED_DIR/data" "$RELEASE_DIR/.venv/bin/python" -m backend.db.migrate --all ) \
    || die "migration 失败,current 未切换,线上不受影响(数据库可能已应用部分迁移,\
迁移本身是单事务/幂等的,重新执行 release 前请先人工确认库状态)"
}

# ── 阶段 4:候选进程冒烟(临时端口,不影响线上) ───────────────────────
candidate_smoke() {
  log "候选 API 冒烟(127.0.0.1:$SMOKE_PORT)"
  local smoke_ok=0 smoke_pid=""
  # 2026-08-20 真实生产发现:uvicorn 前面必须加 exec。子 shell 里
  # "cd && . 文件 && VAR=val cmd" 这条链子如果不用 exec 收尾,bash 不保证把
  # 子 shell 尾调用优化成进程替换——子 shell 会再 fork 一个真正跑 uvicorn 的
  # 孙进程,`smoke_pid=$!` 拿到的是子 shell 自己的 PID,不是 uvicorn 的。下面
  # `kill "$smoke_pid"` 杀的因此是子 shell,孙进程随即被重新挂到 init
  # (PPID=1)继续占用 SMOKE_PORT,永久残留——2026-08-20 当天三次真实生产发布
  # 都复现了同一个残留进程,且残留期间进程仍持有子 shell 继承来的 stdout/
  # stderr,导致外层等待这次发布输出的调用方(SSH 会话/subprocess.communicate)
  # 一并挂起,直到人工发现并手动 kill 掉这个孤儿。exec 让子 shell 进程自己被
  # uvicorn 替换(同一个 PID,不再多 fork 一层),`kill "$smoke_pid"` 因此直接
  # 命中真实进程,不留孤儿(见 tests/backend/test_release_rollback.py::
  # TestCandidateSmokeProcessCleanup,用真实假 uvicorn 复现过修复前的挂起)。
  (
    cd "$RELEASE_DIR" \
    && set -a && . "$SHARED_DIR/.env" && set +a \
    && ALLWIN_DATA_DIR="$SHARED_DIR/data" \
       exec "$RELEASE_DIR/.venv/bin/uvicorn" backend.api.app:app --host 127.0.0.1 --port "$SMOKE_PORT"
  ) & smoke_pid=$!
  # 即便脚本本身被中途打断(Ctrl-C/SIGTERM),候选进程也不能变成孤儿常驻。
  # shellcheck disable=SC2064 -- 故意在设置时就展开 $smoke_pid,不是等 trap 触发时才展开
  trap "kill '$smoke_pid' 2>/dev/null || true; wait '$smoke_pid' 2>/dev/null || true" EXIT

  local i
  for i in $(seq 1 "$SMOKE_RETRIES"); do
    if curl -sf "http://127.0.0.1:$SMOKE_PORT/healthz" >/dev/null \
       && curl -sf "http://127.0.0.1:$SMOKE_PORT/readyz" >/dev/null; then
      smoke_ok=1
      break
    fi
    sleep "$SMOKE_INTERVAL"
  done
  kill "$smoke_pid" 2>/dev/null || true
  wait "$smoke_pid" 2>/dev/null || true
  trap - EXIT
  [ "$smoke_ok" -eq 1 ]
}

# ── 阶段 5:原子切换 current 软链 + 重启服务 ──────────────────────────
switch_current() {
  log "切换 current -> $RELEASE_DIR"
  ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
  sudo systemctl restart allwin-api allwin-web
}

# ── 阶段 6:线上验收(真实端口 healthz/readyz/首页) ───────────────────
verify_live() {
  local ok=0 i
  for i in $(seq 1 "$SMOKE_RETRIES"); do
    if curl -sf "http://127.0.0.1:$LIVE_API_PORT/healthz" >/dev/null \
       && curl -sf "http://127.0.0.1:$LIVE_API_PORT/readyz" >/dev/null \
       && curl -sf "http://127.0.0.1:$LIVE_WEB_PORT/" >/dev/null; then
      ok=1
      break
    fi
    sleep "$SMOKE_INTERVAL"
  done
  [ "$ok" -eq 1 ]
}

# ── 阶段 6b:业务冒烟(不只看进程存活和 HTTP 200) ─────────────────────
business_smoke() {
  local release_py="$RELEASE_DIR/.venv/bin/python" html_ok=0 i
  curl -sf "http://127.0.0.1:$LIVE_API_PORT/api/v1/products" | "$release_py" -m json.tool >/dev/null \
    || { log "业务冒烟失败:/api/v1/products 非可解析 JSON"; return 1; }
  curl -sf "http://127.0.0.1:$LIVE_API_PORT/api/v1/matches" | "$release_py" -m json.tool >/dev/null \
    || { log "业务冒烟失败:/api/v1/matches 非可解析 JSON"; return 1; }
  "$release_py" "$RELEASE_DIR/scripts/verify_next_assets.py" \
    --base-url "http://127.0.0.1:$LIVE_WEB_PORT" \
    || { log "业务冒烟失败:核心页面或 CSS/JS 静态资源不可用"; return 1; }

  # 2026-09-04:这里**不能**写成 `curl ... | grep -qF`。本脚本顶部是
  # `set -euo pipefail`,而 `grep -q` 命中后立即退出并关闭管道读端;首页 HTML
  # 已达 ~131KB,远超管道缓冲区(64KB),curl 必然还在写 → 收到 SIGPIPE → 非零
  # 退出 → pipefail 把整条管道判为失败,于是"页面明明含标记"却恒判失败。
  #
  # 真实事故:2026-09-04 连续两次发布(含回滚后对旧 release 的复验)都报
  # 「业务冒烟失败:首页 HTML 不含 API 数据标志(赛程)」,但同期每 4 秒一次的
  # 并行探针显示首页始终 http=200 / 131640 字节 / 含「赛程」2 次。在服务器上
  # 直接对拍:开 pipefail 跑 20 次 PASS=0,关掉 pipefail 跑 20 次 PASS=20。
  # 页面越大越容易触发,所以它是随页面增长才浮现的竞态,不是一直坏。
  #
  # 改成先把响应收进变量、再用 bash 自带的模式匹配:**全程没有任何管道**。
  # 注意 `printf '%s' "$html" | grep -qF` 同样不行——grep -q 提前退出会让
  # printf 吃 SIGPIPE,pipefail 下依旧误判;只有不建管道才根治。
  # 顺带把「curl 取不到」与「取到了但没有标记」分成两种可区分的失败。
  for i in $(seq 1 "$BUSINESS_SMOKE_RETRIES"); do
    if smoke_html="$(curl -sf "http://127.0.0.1:$LIVE_WEB_PORT/")"; then
      if [[ "$smoke_html" == *"$SMOKE_HTML_MARKER"* ]]; then
        html_ok=1
        break
      fi
      log "业务冒烟:首页已取到($((${#smoke_html})) 字节)但不含标记(${SMOKE_HTML_MARKER}),重试 $i/$BUSINESS_SMOKE_RETRIES"
    else
      log "业务冒烟:首页取不到(curl 非零),重试 $i/$BUSINESS_SMOKE_RETRIES"
    fi
    sleep "$BUSINESS_SMOKE_INTERVAL"
  done
  if [ "$html_ok" -ne 1 ]; then
    log "业务冒烟失败:首页 HTML 不含 API 数据标志(${SMOKE_HTML_MARKER})"
    return 1
  fi
  return 0
}

# ── 回滚:切回 previous 并重新验收(不是切完就假定成功) ───────────────
rollback() {
  if [ -z "${PREVIOUS:-}" ] || [ ! -d "$PREVIOUS" ]; then
    die "验收失败且无上一 release 可回滚,请人工介入(当前 current 可能仍指向有问题的新 release)"
  fi
  log "!!! 验收失败,回滚到 $PREVIOUS"
  ln -sfn "$PREVIOUS" "$CURRENT_LINK"
  sudo systemctl restart allwin-api allwin-web

  log "回滚后重新验收旧 release 的 healthz/readyz/首页 + 业务冒烟"
  if verify_live && business_smoke; then
    die "已回滚到上一 release 并验证健康($PREVIOUS)——本次发布失败,但线上已恢复"
  fi
  die "回滚到 $PREVIOUS 后验收仍失败,请人工介入(current 已指向 previous,但健康检查未通过——\
不要假设服务正常,需要立即人工检查 allwin-api/allwin-web 日志)"
}

# ── 收尾:清理旧 release(保留最近 KEEP_RELEASES 个;current/previous 永不清理) ──
cleanup_old_releases() {
  _within_app_root "$RELEASES_DIR" || die "RELEASES_DIR 不在 APP_ROOT 下,拒绝清理: $RELEASES_DIR"
  case "$KEEP_RELEASES" in
    ''|*[!0-9]*) die "KEEP_RELEASES 必须是正整数,当前值: '$KEEP_RELEASES'" ;;
  esac
  [ "$KEEP_RELEASES" -ge 1 ] || die "KEEP_RELEASES 必须 >= 1,当前值: $KEEP_RELEASES"

  # 实测踩坑(2026-08-23):曾经当过 current 的 release,其 backend/**/__pycache__
  # 是被以 allwin 用户运行的 systemd 服务(allwin-api/allwin-web)导入模块时写入的,
  # 属主是 allwin、权限 700——部署账号 ubuntu 虽在 allwin 组但 700 对组内成员
  # 完全不开放,plain rm -rf 会报 Permission denied。这一步发生在 business_smoke
  # 通过、线上已经切到新 release 之后,清理失败不代表本次发布失败,不应该让
  # set -e 把整个脚本判成非零退出、误导后续人工排查——因此这里改用 sudo 且
  # 不让单个 release 的清理失败中断循环或整体脚本。
  ( cd "$RELEASES_DIR" && ls -1t | tail -n "+$((KEEP_RELEASES + 1))" | while read -r d; do
      [ -n "$d" ] || continue
      local target="$RELEASES_DIR/$d"
      _within_app_root "$target" || continue
      [ "$target" = "$RELEASE_DIR" ] && continue
      [ "$target" = "${PREVIOUS:-}" ] && continue
      if sudo rm -rf "${RELEASES_DIR:?}/${d:?}"; then
        log "清理旧 release: $d"
      else
        log "警告:清理旧 release 失败(不影响本次发布结果): $d"
      fi
    done )
}

main() {
  preflight
  do_build
  do_backup_and_migrate

  candidate_smoke || die "候选进程 healthz/readyz 冒烟失败,current 软链未切换,线上不受影响"

  switch_current

  log "线上验收:healthz/readyz/首页"
  if ! verify_live; then
    rollback
  fi

  log "业务冒烟:/api/v1/products、/api/v1/matches JSON + 首页 API 数据标志"
  if ! business_smoke; then
    rollback
  fi

  cleanup_old_releases

  log "发布完成: current -> $RELEASE_DIR"
  log "提醒:Cloudflare purge 静态缓存 + 隐私模式核验(CLAUDE.md §10);"
  log "     三必查:systemctl show allwin-api -p ExecStart / nginx -T 的 proxy_pass / curl 域名指纹"
  log "     注意:回滚只切代码,不自动恢复数据库——新版本的 migration 必须向后兼容"
  log "     (旧版本代码仍可运行在已迁移的新 schema 上),否则回滚后旧代码可能无法读写新 schema。"
}

if [ "${RELEASE_SH_SOURCE_ONLY:-0}" != "1" ]; then
  main "$@"
fi
