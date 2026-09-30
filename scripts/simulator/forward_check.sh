#!/usr/bin/env bash
# 模拟器前瞻复检(样本外;每月跑一次)。口径:docs/simulator-model.md「v0.3 前瞻复检」。
#
# 1. 服务器(只读数据库):按比赛日生成开赛时点参数快照 + 赛果 + Bet365 1x2 收盘(与 Phase 2 同一脚本同一口径);
# 2. 拉回本地 .local-data(不进 git);
# 3. 本地用生产校准文件 calibration_v0.3.json(不做任何拟合)跑路径 A(w=1)与路径 B(w=0)的全部闸门。
#
# 用法(在开发机仓库根目录):
#   bash scripts/simulator/forward_check.sh [赛季,默认 2026/2027]
# 服务器上的脚本版本取 /opt/allwin/source 当前检出;本脚本不改动服务器上的 git 状态。
set -euo pipefail

SEASON="${1:-2026/2027}"
TAG="forward_${SEASON/\//-}"
HOST="${SIM_HOST:-vip-lightsail}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LOCAL="$REPO/.local-data/simulator/$TAG"
REMOTE="/opt/allwin/shared/exports/simulator/$TAG"
CAL="$REPO/scripts/simulator/calibration_v0.3.json"
STAMP="$(date +%Y%m%d)"

echo "== 服务器快照 $SEASON($HOST)"
ssh "$HOST" "cd /opt/allwin/source && echo \"source HEAD: \$(git log --oneline -1)\" \
  && sudo -u allwin mkdir -p $REMOTE \
  && sudo -u allwin nice -n 19 python3 scripts/simulator/backtest_snapshots.py --season $SEASON \
       --data-dir /opt/allwin/shared/data --out-dir $REMOTE"

echo "== 拉回 $LOCAL"
mkdir -p "$LOCAL/results"
rsync -a --delete --exclude results "$HOST:$REMOTE/" "$LOCAL/"

echo "== 本地闸门"
node "$REPO/scripts/simulator/build_backtest.mjs"
LOG="$LOCAL/results/forward_$STAMP.log"
: > "$LOG"
for P in A B; do
  echo "===== 路径 $P =====" | tee -a "$LOG"
  node "$REPO/.local-data/simulator/backtest/run.mjs" forward --dir "$LOCAL" --cal-file "$CAL" --path "$P" | tee -a "$LOG"
  cp "$LOCAL/results/forward_$P.json" "$LOCAL/results/forward_${P}_$STAMP.json"
done
echo "== 结果:$LOG"
