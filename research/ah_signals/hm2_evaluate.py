#!/usr/bin/env python3
"""H-M2 评估脚本(PREREG_hm2.md §4)。下注数 <1600 时拒绝运行;只对按 kickoff 排序的前 1600 注评估;
只允许运行一次(hm2_state.json 记 evaluated_at)。启动时做规则冻结哈希自检。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import LEAGUE_NAME  # noqa: E402
from hm2_log import FROZEN_FILES, sha256  # noqa: E402

N_REQUIRED = 1600
ALPHA = 0.05
BOOT_N = 10000
SEED = 20260928
SD_PRIOR = 0.885


def binom_sf(x: int, n: int, p: float) -> float:
    """P(X ≥ x | n, p),对数空间精确求和。"""
    tot = 0.0
    for k in range(x, n + 1):
        tot += math.exp(math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
                        + k * math.log(p) + (n - k) * math.log1p(-p))
    return min(1.0, tot)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    sp = out_dir / "hm2_state.json"
    if not sp.exists():
        print("!! 无 hm2_state.json(记录脚本尚未运行)。"); return 4
    state = json.loads(sp.read_text())
    bad = [f for f in FROZEN_FILES if state["frozen"].get(f) != sha256(HERE / f)]
    if bad:
        print(f"!! 规则冻结自检失败:文件已改动 {bad}。拒绝运行。"); return 4
    if state.get("evaluated_at"):
        print(f"!! 评估已于 {state['evaluated_at']} 完成,不允许重复运行。"); return 5

    bets = []
    with open(out_dir / "hm2_bets.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["reason"] == "bet":
                bets.append(r)
    bets.sort(key=lambda r: (r["kickoff_at_utc"], int(r["match_id"])))
    if len(bets) < N_REQUIRED:
        print(f"累计下注数={len(bets)} < {N_REQUIRED},未到评估点,拒绝运行。"); return 3
    bets = bets[:N_REQUIRED]
    pays = [float(r["payoff"]) for r in bets]
    n = len(pays)
    mean = sum(pays) / n
    sd = math.sqrt(sum((p - mean) ** 2 for p in pays) / (n - 1))
    se = sd / math.sqrt(n)
    t = mean / se
    p_t = 1.0 - NormalDist().cdf(t)   # df=1599,正态近似与 t 分布差异 <1e-4
    rng = random.Random(SEED)
    boots = []
    for _ in range(BOOT_N):
        boots.append(sum(pays[rng.randrange(n)] for _ in range(n)) / n)
    boots.sort()
    p_boot = sum(1 for b in boots if b <= 0) / BOOT_N
    lo, hi = boots[int(0.025 * BOOT_N)], boots[int(0.975 * BOOT_N) - 1]
    print("=" * 12, f"H-M2 评估(前 {n} 注,按 kickoff 排序;主检验:单侧 t 检验 平均盈亏>0,α={ALPHA})", "=" * 12)
    print(f"  平均盈亏(ROI)={100*mean:+.3f}%  SD={sd:.4f}  SE={se:.4f}  t={t:+.3f}  单侧 p(t)={p_t:.4f}")
    print(f"  bootstrap({BOOT_N}):单侧 p={p_boot:.4f}  95%CI=[{100*lo:+.3f}%, {100*hi:+.3f}%]")
    verdict = "H-M2 在前瞻样本中获得支持(α=0.05,单侧)" if p_t <= ALPHA else "H-M2 未获支持"
    print(f"  结论(以 t 检验为准):{verdict}")
    mde = (NormalDist().inv_cdf(1 - ALPHA) + NormalDist().inv_cdf(0.8)) * se
    print(f"  功效(实际 SD):80% 功效可检出最小 ROI={100*mde:+.2f}%(事先按 SD={SD_PRIOR} 算得 {100*(NormalDist().inv_cdf(1-ALPHA)+NormalDist().inv_cdf(0.8))*SD_PRIOR/math.sqrt(N_REQUIRED):+.2f}%);"
          f"真实 ROI=+4% 时功效={NormalDist().cdf(0.04/se - NormalDist().inv_cdf(1-ALPHA)):.2f}")
    # 次要
    dec = [(float(r["payoff"]), float(r["water_used"])) for r in bets if float(r["payoff"]) != 0]
    wins = sum(1 for p, _ in dec if p > 0)
    p0 = sum(1 / (1 + w) for _, w in dec) / len(dec)
    print(f"  次要:命中 {wins}/{len(dec)}={100*wins/len(dec):.2f}% vs 保本胜率 {100*p0:.2f}%  精确二项单侧 p={binom_sf(wins, len(dec), p0):.4f}(走水剔除 {n-len(dec)})")
    by = defaultdict(list)
    for r, p in zip(bets, pays):
        by[("联赛", LEAGUE_NAME.get(int(r["league_id"]), r["league_id"]))].append(p)
        by[("方向", r["side"])].append(p)
    for (kind, k), v in sorted(by.items()):
        print(f"     {kind} {k}: N={len(v)} ROI={100*sum(v)/len(v):+.2f}%{'  样本不足,不可靠' if len(v) < 100 else ''}")
    print(f"  Δline 幅度分布(档): {dict(sorted(Counter(int(r['dline']) for r in bets).items()))}")
    print(f"  不下注原因累计: {state.get('reasons')}")
    state["evaluated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["evaluation"] = dict(n=n, roi=mean, sd=sd, t=t, p_t=p_t, p_boot=p_boot, ci=[lo, hi], verdict=verdict)
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    (out_dir / "hm2_result.json").write_text(json.dumps(state["evaluation"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
