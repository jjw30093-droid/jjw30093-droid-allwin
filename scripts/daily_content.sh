#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

: "${ALLWIN_DATA_DIR:=$PROJECT_ROOT/runtime/data}"
: "${ALLWIN_ARTIFACT_DIR:=$PROJECT_ROOT/runtime/artifacts}"
: "${ALLWIN_STUDIO_OUTPUT_DIR:=$PROJECT_ROOT/runtime/studio}"
: "${ALLWIN_CONTENT_HORIZON_DAYS:=7}"
: "${ALLWIN_MAX_REQUEST_ATTEMPTS:=20}"
: "${ALLWIN_API_BASE:=http://127.0.0.1:8000}"

export ALLWIN_DATA_DIR
export ALLWIN_ARTIFACT_DIR
export ALLWIN_STUDIO_OUTPUT_DIR
export ALLWIN_CONTENT_HORIZON_DAYS
export ALLWIN_MAX_REQUEST_ATTEMPTS
export ALLWIN_API_BASE

cd "$PROJECT_ROOT"
exec "$PROJECT_ROOT/.venv/bin/python" -m backend.cli.daily_content "$@"
