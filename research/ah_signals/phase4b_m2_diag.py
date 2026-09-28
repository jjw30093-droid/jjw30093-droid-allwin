#!/usr/bin/env python3
"""M2 假象诊断 —— **未预注册,事后检验,仅用于判断 M2 是否为测量假象**(只用 25/26,只读 CSV)。

1. 水位感知目标:r_home / r_away(按 Crown 收盘线与收盘水位平注 1 单位的实际盈亏,含赢半/输半/走水)
   r_home ~ M2、r_away ~ M2(HC1);M1 同样;margin ~ closing_line + p_home_close_devig + M2。
2. 按 M2 分组(≤−2 / −1 / 0 / +1 / ≥+2 档)的机械效应描述。
3. 反向下注(Δline≠0 场次,押线移动反方向,收盘线+Crown 收盘水位):N、命中率、ROI + bootstrap 95% CI(2000 次)、
   保本胜率;按上下半季、按联赛拆分。
判定规则见 PREREG_phase4.md 修订记录(写死)。
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import LEAGUE_NAME, LEAGUES, mean_ci95, settle_home  # noqa: E402
from features_market import implied_two  # noqa: E402
from phase3_univariate import fit, pval  # noqa: E402

BOOT_N = 2000
BOOT_SEED = 20260928


def payoff(s: float, w: float) -> float:
    return s * w if s > 0 else s


def fnum(v):
    return float(v) if v not in ("", None) else None


def bet_stats(items, label, seed=BOOT_SEED):
    """items: [(payoff, water)]"""
    n = len(items)
    if n == 0:
        print(f"    {label:28s} N=0"); return
    pays = [p for p, _ in items]
    wins = sum(1 for p in pays if p > 0); losses = sum(1 for p in pays if p < 0)
    roi = sum(pays) / n * 100
    rng = random.Random(seed)
    boots = []
    for _ in range(BOOT_N):
        boots.append(sum(pays[rng.randrange(n)] for _ in range(n)) / n * 100)
    boots.sort()
    lo, hi = boots[int(0.025 * BOOT_N)], boots[int(0.975 * BOOT_N) - 1]
    avg_w = sum(w for _, w in items) / n
    print(f"    {label:28s} N={n:5d} 命中(赢/(赢+输))={100*wins/(wins+losses) if wins+losses else float('nan'):5.1f}% "
          f"ROI={roi:+6.2f}% boot95%CI=[{lo:+6.2f},{hi:+6.2f}] 平均水位={avg_w:.3f} 保本胜率={100/(1+avg_w):.1f}%"
          f"{'  样本不足,不可靠' if n < 100 else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    print("*** 未预注册,事后检验,仅用于判断 M2 是否为测量假象;只用 25/26 ***")

    t = {}
    with open(out_dir / "phase1_target.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t[int(r["match_id"])] = r
    rows = []
    with open(out_dir / "phase4_market_features.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            mid = int(r["match_id"])
            tt = t.get(mid)
            if tt is None or tt["season"] != "2025/2026":
                continue
            rnd = int(tt["round"]) if tt["round"] else None
            if rnd is None or rnd <= 3 or not r["margin"]:
                continue
            d = dict(match_id=mid, league_id=int(tt["league_id"]), round=rnd,
                     margin=fnum(r["margin"]), closing_line=fnum(r["crown_close_line"]),
                     M1=fnum(r["M1_open_to_close"]), M2=fnum(r["M2_t24_to_close"]), dline=fnum(r["dline"]),
                     wh=fnum(r["close_home_w"]), wa=fnum(r["close_away_w"]))
            raw = int(tt["home_score"]) - int(tt["away_score"])
            assert abs((raw - d["closing_line"]) - d["margin"]) < 1e-9
            s = settle_home(raw, d["closing_line"])
            d["r_home"] = payoff(s, d["wh"])
            d["r_away"] = payoff(-s, d["wa"])
            d["p_dev"] = implied_two(d["wh"], d["wa"]) - 0.5
            rows.append(d)
    print(f"样本 N={len(rows)}(25/26,Match_Round>3,有 Crown 收盘线与收盘水位)")

    # ---------------------------------------------------------------- 1
    print("\n" + "=" * 12, "1) 水位感知目标(HC1)", "=" * 12)
    print(f"  {'模型':44s} {'N':>5s} {'coef':>9s} {'SE':>8s} {'t':>7s} {'p':>8s}")
    verdict = {}
    for feat in ("M2", "M1"):
        for ycol in ("r_home", "r_away"):
            r = fit(rows, [feat], ycol=ycol)
            c, se = r["coef"][1], r["se"][1]
            tval = c / se
            print(f"  {ycol + ' ~ ' + feat:44s} {r['n']:5d} {c:+9.4f} {se:8.4f} {tval:+7.2f} {pval(tval):8.4f}")
            verdict[(feat, ycol)] = pval(tval)
        r = fit(rows, ["closing_line", "p_dev", feat], ycol="margin")
        c, se = r["coef"][3], r["se"][3]
        tval = c / se
        print(f"  {'margin ~ closing_line + p_dev + ' + feat:44s} {r['n']:5d} {c:+9.4f} {se:8.4f} {tval:+7.2f} {pval(tval):8.4f}"
              f"   (p_dev coef={r['coef'][2]:+.4f} SE={r['se'][2]:.4f})")
        verdict[(feat, "with_pdev")] = pval(tval)
        r0 = fit(rows, ["closing_line", feat], ycol="margin")
        print(f"  {'(参考)margin ~ closing_line + ' + feat:44s} {r0['n']:5d} {r0['coef'][2]:+9.4f} {r0['se'][2]:8.4f} {r0['coef'][2]/r0['se'][2]:+7.2f} {pval(r0['coef'][2]/r0['se'][2]):8.4f}")
    r = fit(rows, ["closing_line", "p_dev"], ycol="margin")
    print(f"  {'(参考)margin ~ closing_line + p_dev':44s} {r['n']:5d} p_dev coef={r['coef'][2]:+.4f} SE={r['se'][2]:.4f} p={pval(r['coef'][2]/r['se'][2]):.4f}")

    # ---------------------------------------------------------------- 2
    print("\n" + "=" * 12, "2) 按 M2 分组的机械效应(描述)", "=" * 12)
    groups = [("≤−2 档", lambda v: v <= -2), ("−1 档", lambda v: v == -1), ("0", lambda v: v == 0),
              ("+1 档", lambda v: v == 1), ("≥+2 档", lambda v: v >= 2)]
    print(f"  {'M2 组':8s} {'N':>5s} {'p_home_close':>13s} {'mean margin':>22s} {'mean r_home':>22s} {'mean r_away':>22s}")
    for lab, cond in groups:
        g = [x for x in rows if cond(x["M2"])]
        if not g:
            print(f"  {lab:8s} {0:5d}"); continue
        p = sum(x["p_dev"] + 0.5 for x in g) / len(g)
        mm = mean_ci95([x["margin"] for x in g]); rh = mean_ci95([x["r_home"] for x in g]); ra = mean_ci95([x["r_away"] for x in g])
        print(f"  {lab:8s} {len(g):5d} {p:13.4f} {mm[0]:+7.3f}[{mm[1]:+.3f},{mm[2]:+.3f}] {rh[0]:+7.4f}[{rh[1]:+.4f},{rh[2]:+.4f}] "
              f"{ra[0]:+7.4f}[{ra[1]:+.4f},{ra[2]:+.4f}]{'  样本不足,不可靠' if len(g) < 100 else ''}")

    # ---------------------------------------------------------------- 3
    print("\n" + "=" * 12, "3) 反向下注(Δline≠0;押线移动反方向;收盘线 + Crown 实际收盘水位;平注 1 单位)", "=" * 12)
    bets = []
    for x in rows:
        if x["dline"] is None or abs(x["dline"]) < 1e-9:
            continue
        side = -1 if x["dline"] > 0 else 1          # 线向主队移动 → 押客;向客队移动 → 押主
        pay = x["r_home"] if side > 0 else x["r_away"]
        w = x["wh"] if side > 0 else x["wa"]
        bets.append((pay, w, x))
    print(f"  Δline≠0 的场次={len(bets)}(其中押客={sum(1 for _,_,x in bets if x['dline']>0)},押主={sum(1 for _,_,x in bets if x['dline']<0)})")
    bet_stats([(p, w) for p, w, _ in bets], "全部")
    bet_stats([(p, w) for p, w, x in bets if x["dline"] > 0], "线向主队移动→押客")
    bet_stats([(p, w) for p, w, x in bets if x["dline"] < 0], "线向客队移动→押主")
    bet_stats([(p, w) for p, w, x in bets if abs(x["dline"]) >= 2], "|Δline|≥2 档")
    print("  按上下半季:")
    bet_stats([(p, w) for p, w, x in bets if x["round"] <= 19], "上半季(轮≤19)")
    bet_stats([(p, w) for p, w, x in bets if x["round"] > 19], "下半季(轮>19)")
    print("  按联赛:")
    for l in LEAGUES:
        bet_stats([(p, w) for p, w, x in bets if x["league_id"] == l], LEAGUE_NAME[l])
    print("  (对照)顺向下注(押线移动方向):")
    bet_stats([(-p if False else (x["r_home"] if x["dline"] > 0 else x["r_away"]), (x["wh"] if x["dline"] > 0 else x["wa"])) for _, _, x in bets], "顺向 全部")

    # ---------------------------------------------------------------- 判定
    print("\n" + "=" * 12, "判定(规则见 PREREG_phase4.md 修订记录)", "=" * 12)
    p_rh, p_ra, p_pd = verdict[("M2", "r_home")], verdict[("M2", "r_away")], verdict[("M2", "with_pdev")]
    both_ns = p_rh > 0.05 and p_ra > 0.05
    print(f"  r_home~M2 p={p_rh:.4f};r_away~M2 p={p_ra:.4f};margin~closing_line+p_dev+M2 的 M2 p={p_pd:.4f}")
    if both_ns or p_pd > 0.05:
        print("  → 判定:M2 为水位假象,不进入 Phase 5/6。")
    else:
        print("  → 判定:M2 作为唯一候选进入 Phase 6 前瞻记录,下注规则按第 3 条写死。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
