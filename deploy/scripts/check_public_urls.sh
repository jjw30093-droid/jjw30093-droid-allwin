#!/usr/bin/env bash
# 公开 SEO/GEO 产物门禁(宪法 §10.3 / §14.2):
# sitemap.xml / robots.txt / llms.txt 里的绝对 URL 不得指向占位域名,且三者
# 引用的 host 必须彼此一致。
#
# 原因:三者的绝对 URL 都来自 frontend/lib/site.ts 的 SITE_URL,该值由
# NEXT_PUBLIC_SITE_URL 在 next build 时内联;这是构建期变量,生产忘记设置
# 就会回退到本地默认值,导致三份产物一起指向不可索引的地址而不自知。
#
# 用法:
#   bash deploy/scripts/check_public_urls.sh [frontend目录]
# 默认检查仓库内 frontend/;release.sh 传入 release 目录下的 frontend/。
# 退出码:0 = 干净;1 = 产物缺失、含占位域名或三者 host 不一致。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${1:-$SCRIPT_DIR/../../frontend}"
APP_DIR="$FRONTEND_DIR/.next/server/app"

SITEMAP="$APP_DIR/sitemap.xml.body"
ROBOTS="$APP_DIR/robots.txt.body"
LLMS="$APP_DIR/llms.txt.body"

log() { echo "[check-public-urls] $*"; }

fail=0

for f in "$SITEMAP" "$ROBOTS" "$LLMS"; do
  if [ ! -f "$f" ]; then
    log "ERROR: 找不到 $f(先在 $FRONTEND_DIR 执行 npm run build)" >&2
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  exit 1
fi

# 占位域名检查:三份产物都不得出现 example.com/example.org 等占位域名。
if grep -RIlsqiE -- 'example\.(com|org|net)' "$SITEMAP" "$ROBOTS" "$LLMS"; then
  log "FAIL: 产物包含占位域名(NEXT_PUBLIC_SITE_URL 未在构建前设置为真实域名):" >&2
  grep -RInsiE -- 'example\.(com|org|net)' "$SITEMAP" "$ROBOTS" "$LLMS" >&2 || true
  exit 1
fi

# host 一致性检查:sitemap 的 <loc>、robots 的 Sitemap: 行、llms 的链接必须
# 指向同一个 host(同一个 SITE_URL)。
extract_host() {
  # 从任意一行里抠出第一个 http(s):// URL 的 scheme://host 部分。
  grep -oE 'https?://[^/"<> ]+' "$1" | head -n1
}

# sitemap.xml 顶部有固定的 XML 命名空间 URL(http://www.sitemaps.org/...),
# 不能用通用 extract_host(会先抓到命名空间而不是真实 <loc>);只从 <loc> 标签取。
sitemap_host="$(grep -oE '<loc>https?://[^/"<> ]+' "$SITEMAP" | head -n1 | sed -E 's/^<loc>//' || true)"
robots_host="$(grep -oE 'Sitemap:\s*https?://[^/"<> ]+' "$ROBOTS" | head -n1 | grep -oE 'https?://[^/"<> ]+' || true)"
llms_host="$(extract_host "$LLMS" || true)"

if [ -z "$sitemap_host" ] || [ -z "$robots_host" ] || [ -z "$llms_host" ]; then
  log "FAIL: 未能从产物中解析出 host(sitemap=$sitemap_host robots=$robots_host llms=$llms_host)" >&2
  exit 1
fi

if [ "$sitemap_host" != "$robots_host" ] || [ "$sitemap_host" != "$llms_host" ]; then
  log "FAIL: sitemap/robots/llms 指向的 host 不一致:" >&2
  log "  sitemap.xml -> $sitemap_host" >&2
  log "  robots.txt  -> $robots_host" >&2
  log "  llms.txt    -> $llms_host" >&2
  exit 1
fi

log "OK: sitemap.xml/robots.txt/llms.txt 均指向 $sitemap_host,不含占位域名"
