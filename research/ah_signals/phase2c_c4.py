#!/usr/bin/env python3
"""Phase 2c — C4 验证字段更正(只看字段,不接触任何结果变量),并按 25/26 实际 N 重算功效表。

a. 行完整性:Σ球员 accurate_passes vs 队级 accurate_passes(相对误差 >2% 的队场占比)。
b. passes_into_final_third 的 NULL 语义:分组(minutes_played ≥45 且 accurate_passes ≥10 / 其余)
   统计 NULL 率;第一组 ≤5% → 判 NULL=0,C4 恢复;>5% → 缺数据,C4 维持移出。
c. 25/26(冷启动剔除后)各特征主版本可用 N 与功效表。
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import LEAGUE_NAME, SEASONS, load_matches, load_xref, open_ro, parse_round  # noqa: E402
from features import (  # noqa: E402
    ALL_FEATURES, K, MAIN_WINDOW, STAT_FEATURES, build_features, load_lineups, load_player_rows,
    load_team_stats,
)


def pct(a, b):
    return 100.0 * a / b if b else math.nan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--c5-mode", default="team_proxy")
    args = ap.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)

    core = open_ro(data_dir / "allwin.db")
    odds = open_ro(data_dir / "odds.db")
    matches = load_matches(core)
    ok_xref, _ = load_xref(odds, {m["Match_ID"] for m in matches})
    matches = [m for m in matches if m["Match_ID"] in ok_xref]
    mids = {m["Match_ID"] for m in matches}
    minfo = {m["Match_ID"]: m for m in matches}
    team_stats = load_team_stats(core, mids)
    lineups = load_lineups(core, mids)
    player_rows = load_player_rows(core, mids)
    print(f"样本比赛={len(matches)} 球员行={len(player_rows)}")

    # ---------------------------------------------------------------- a. 行完整性
    print("\n" + "=" * 12, "a. 行完整性:Σ球员 accurate_passes vs 队级 accurate_passes", "=" * 12)
    by_tm = defaultdict(list)
    for r in player_rows:
        by_tm[(r["Match_ID"], r["Team_ID"])].append(r)
    for season in SEASONS + ("ALL",):
        n = bad2 = bad10 = 0
        for (mid, tid), rs in by_tm.items():
            if season != "ALL" and minfo[mid]["Season"] != season:
                continue
            tv = team_stats.get((mid, tid, "All"), {}).get("raw", {}).get(K["acc_passes"])
            if not isinstance(tv, (int, float)) or not tv:
                continue
            psum = sum(float(r["accurate_passes"]) for r in rs if r["accurate_passes"] is not None)
            rel = abs(psum - tv) / tv
            n += 1
            bad2 += rel > 0.02
            bad10 += rel > 0.10
        print(f"  {season}: 可比队场={n}  |err|>2%: {bad2} ({pct(bad2, n):.2f}%)  |err|>10%: {bad10} ({pct(bad10, n):.2f}%)"
              f"  → {'通过(≤2%)' if pct(bad2, n) <= 2 else '不通过'}")

    # ---------------------------------------------------------------- b. NULL 语义
    print("\n" + "=" * 12, "b. passes_into_final_third NULL 语义(按球员行分组)", "=" * 12)
    print(f"  {'赛季':10s} {'组':40s} {'行数':>7s} {'NULL':>7s} {'NULL%':>7s}")
    verdict = {}
    for season in SEASONS + ("ALL",):
        g1, g2 = Counter(), Counter()
        for r in player_rows:
            if season != "ALL" and minfo[r["Match_ID"]]["Season"] != season:
                continue
            mp = r["minutes_played"] or 0
            apv = r["accurate_passes"] or 0
            grp = g1 if (mp >= 45 and apv >= 10) else g2
            grp["n"] += 1
            grp["null"] += r["passes_into_final_third"] is None
        for name, g in (("minutes≥45 且 accurate_passes≥10", g1), ("其余球员", g2)):
            print(f"  {season:10s} {name:40s} {g['n']:7d} {g['null']:7d} {pct(g['null'], g['n']):7.2f}")
        verdict[season] = pct(g1["null"], g1["n"])
    # 第一组内 NULL 行的 accurate_passes / minutes 分布(辅助判断是"没有这类传球"还是缺数据)
    g1_null = [r for r in player_rows if (r["minutes_played"] or 0) >= 45 and (r["accurate_passes"] or 0) >= 10
               and r["passes_into_final_third"] is None]
    if g1_null:
        aps = sorted((r["accurate_passes"] or 0) for r in g1_null)
        print(f"  第一组 NULL 行 {len(g1_null)} 行:accurate_passes p50={aps[len(aps)//2]:.0f} max={aps[-1]:.0f}")
    verdict_all = verdict["ALL"]
    c4_keep = verdict_all <= 5
    print(f"\n  判定:第一组 NULL 率(全样本)={verdict_all:.2f}% → "
          f"{'≤5%:NULL=0,C4 恢复进入 C 族' if c4_keep else '>5%:缺数据,C4 维持移出'}")

    # ---------------------------------------------------------------- c. 25/26 N 与功效
    print("\n" + "=" * 12, "c. 25/26(排除前 3 轮)各特征主版本可用 N 与功效(Phase 3/4 只用 25/26)", "=" * 12)
    feats = build_features(matches, team_stats, lineups, player_rows, c5_mode=args.c5_mode)
    elig = [m for m in matches if m["Season"] == "2025/2026"
            and not ((parse_round(m["Match_Round"]) or 0) <= 3)]
    print(f"  25/26 候选场次={len(elig)}")
    margins = []
    with open(out_dir / "phase1_target.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["margin"] and row["season"] == "2025/2026":
                margins.append(float(row["margin"]))
    mu = sum(margins) / len(margins)
    sd = math.sqrt(sum((x - mu) ** 2 for x in margins) / (len(margins) - 1))
    removed = {"C13_log_market_value"} | (set() if c4_keep else {"C4_final_third_passes"})
    n_c = len([f for f in ALL_FEATURES if f.startswith("C") and f not in removed])
    print(f"  25/26 margin SD={sd:.3f} (N={len(margins)},仅取 margin 列);C 族剩余={n_c} → BH 最严档 α=0.10/{n_c}={0.10/n_c:.4f}")
    za, zb, z80 = NormalDist().inv_cdf(0.975), NormalDist().inv_cdf(1 - 0.10 / n_c / 2), 0.8416
    print(f"  {'feature':28s} {'N':>5s} {'缺失%':>6s}  r_min(α=.05)  球/SD   r_min(BH最严)  球/SD")
    for f in ALL_FEATURES:
        key = f"diff__{f}__w{MAIN_WINDOW}" if f in STAT_FEATURES else f"diff__{f}"
        n = sum(1 for m in elig if feats[m["Match_ID"]].get(key) is not None)
        tag = "  (已移出/删除)" if f in removed else ""
        if n < 2:
            print(f"  {f:28s} {n:5d}{tag}")
            continue
        r05, rbh = (za + z80) / math.sqrt(n), (zb + z80) / math.sqrt(n)
        print(f"  {f:28s} {n:5d} {pct(len(elig)-n, len(elig)):6.1f}  {r05:12.3f} {r05*sd:6.3f}   {rbh:12.3f} {rbh*sd:6.3f}{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
