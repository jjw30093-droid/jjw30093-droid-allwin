#!/usr/bin/env python3
"""OU 基线偏态验证(只读;只用 25/26;读 ou_phase1_target.csv):
每场用 Crown 收盘 OU 线 T 与去水大球概率 p_over(四分之一盘用 EV 中性 p_eff)按单 Poisson 反推 λ_close,
理论值 E[总进球] − T = λ_close − T;报告其均值/分布,并与观察到的 margin_ou 逐场配对比较(差值的均值与 95% CI)。
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ah_signals"))
from common import mean_ci95, quantiles  # noqa: E402
from features_market import implied_two, p_eff  # noqa: E402

MAX_GOALS = 15


def total_dist(lam: float) -> dict[int, float]:
    d = {}
    p = math.exp(-lam)
    for k in range(0, MAX_GOALS + 1):
        d[k] = p
        p *= lam / (k + 1)
    return d


def solve_lambda(line: float, p_over: float) -> float:
    """p_eff(total_dist(λ), line) 关于 λ 单调递增;二分求根。"""
    lo, hi = 0.05, 10.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if p_eff(total_dist(mid), line) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    rows = []
    with open(Path(args.out_dir) / "ou_phase1_target.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    obs, theo, diff, lams, pov = [], [], [], [], []
    for r in rows:
        T = float(r["ou_close_line"]); w_o, w_u = float(r["over_w"]), float(r["under_w"])
        p_over = implied_two(w_o, w_u)
        lam = solve_lambda(T, p_over)
        m_obs = float(r["margin_ou"])
        obs.append(m_obs); theo.append(lam - T); diff.append(m_obs - (lam - T)); lams.append(lam); pov.append(p_over)
    n = len(rows)
    mo = mean_ci95(obs); mt = mean_ci95(theo); md = mean_ci95(diff)
    q = quantiles(theo, probs=(0.05, 0.25, 0.5, 0.75, 0.95))
    print(f"N={n}(25/26 有 Crown OU 收盘线的比赛);λ_close 由 p_eff(Poisson(λ), T) = 去水大球概率 反推(单 Poisson,截断 0..{MAX_GOALS})")
    print(f"  去水大球概率 p_over:mean={sum(pov)/n:.4f}  λ_close:mean={sum(lams)/n:.4f}  收盘线 T:mean={sum(float(r['ou_close_line']) for r in rows)/n:.4f}")
    print(f"  理论 E[总进球]−T = λ_close−T:mean={mt[0]:+.4f} 95%CI=[{mt[1]:+.4f},{mt[2]:+.4f}]  p5/p25/p50/p75/p95={q[0]:+.3f}/{q[1]:+.3f}/{q[2]:+.3f}/{q[3]:+.3f}/{q[4]:+.3f}")
    print(f"  观察 margin_ou:mean={mo[0]:+.4f} 95%CI=[{mo[1]:+.4f},{mo[2]:+.4f}]")
    print(f"  逐场配对差 观察−理论:mean={md[0]:+.4f} 95%CI=[{md[1]:+.4f},{md[2]:+.4f}]  → {'CI 包含 0:正均值可由分布右偏解释' if md[1] <= 0 <= md[2] else 'CI 不包含 0:理论值不能解释观察均值'}")
    # 按盘口档位
    print("  分档位(理论 mean / 观察 mean / 配对差 mean [CI]):")
    for lab, cond in (("≤2.25", lambda T: T <= 2.25), ("2.5", lambda T: T == 2.5), ("2.75", lambda T: T == 2.75), ("≥3", lambda T: T >= 3)):
        idx = [i for i, r in enumerate(rows) if cond(float(r["ou_close_line"]))]
        t_ = mean_ci95([theo[i] for i in idx]); o_ = mean_ci95([obs[i] for i in idx]); d_ = mean_ci95([diff[i] for i in idx])
        print(f"     {lab:6s} N={len(idx):4d}  理论 {t_[0]:+.3f}  观察 {o_[0]:+.3f}  差 {d_[0]:+.3f} [{d_[1]:+.3f},{d_[2]:+.3f}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
