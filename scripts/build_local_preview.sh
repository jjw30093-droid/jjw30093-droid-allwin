#!/usr/bin/env bash
# Build a production preview in an immutable staging directory, then atomically
# switch runtime/previews/current. Existing Next processes keep their old files.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREVIEW_ROOT="${ALLWIN_PREVIEW_ROOT:-$REPO_ROOT/runtime/previews}"
RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
RELEASE_DIR="$PREVIEW_ROOT/releases/$RELEASE_ID"
CURRENT_LINK="$PREVIEW_ROOT/current"
TMP_LINK="$PREVIEW_ROOT/.current-$RELEASE_ID"

command -v rsync >/dev/null
command -v npm >/dev/null
[ -d "$REPO_ROOT/frontend/node_modules" ] || {
  echo "preview build failed: frontend dependencies are unavailable" >&2
  exit 1
}
[ ! -e "$RELEASE_DIR" ] || {
  echo "preview build failed: staging release already exists" >&2
  exit 1
}

mkdir -p "$RELEASE_DIR"
rsync -a \
  --exclude '.next' \
  --exclude 'node_modules' \
  --exclude '.env.local' \
  "$REPO_ROOT/frontend/" "$RELEASE_DIR/frontend/"
# Turbopack intentionally rejects a project-local node_modules symlink that
# points outside the staged project root. macOS clone-copy keeps this fast and
# isolated; the rsync fallback remains offline and portable.
if ! cp -cR "$REPO_ROOT/frontend/node_modules" "$RELEASE_DIR/frontend/node_modules" 2>/dev/null; then
  rsync -a "$REPO_ROOT/frontend/node_modules/" "$RELEASE_DIR/frontend/node_modules/"
fi

(
  cd "$RELEASE_DIR/frontend"
  npm run build
)

bash "$REPO_ROOT/deploy/scripts/check_browser_bundle.sh" "$RELEASE_DIR/frontend"
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/verify_next_assets.py" \
  --frontend "$RELEASE_DIR/frontend"

mkdir -p "$PREVIEW_ROOT"
ln -s "$RELEASE_DIR" "$TMP_LINK"
PREVIEW_TMP_LINK="$TMP_LINK" PREVIEW_CURRENT_LINK="$CURRENT_LINK" \
  "$REPO_ROOT/.venv/bin/python" -c \
  'import os; os.replace(os.environ["PREVIEW_TMP_LINK"], os.environ["PREVIEW_CURRENT_LINK"])'

echo "preview release ready: $CURRENT_LINK"
echo "start frontend: cd $CURRENT_LINK/frontend && npm start -- --hostname 127.0.0.1 --port <port>"
echo "then start proxy with scripts/local_preview_proxy.py after both upstreams are healthy"
