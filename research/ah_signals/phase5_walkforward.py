#!/usr/bin/env python3
"""Phase 5 — 整体增量检验(PREREG §4;站长批准的方案):
模型 = ridge(closing_line + B_composite + 全部 C 族 11 个),alpha 只在每个训练窗口内部 5 折 CV 选择;
基准 = OLS(margin ~ closing_line),同一训练窗口。
25/26:第 4–19 轮训练,第 20–38 轮逐轮滚动(每轮用截至上一轮的数据重训);
26/27:用 25/26 全部(4–38 轮)训练的冻结模型只跑一次,只报方向与 95% CI。
报告:样本外 R²(相对基准的增量,bootstrap CI)、预测值 top 10%/20% 的命中率、按 Crown 实际收盘水位的 ROI、
保本胜率;分联赛。纯 Python(服务器无 numpy)。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import LEAGUE_NAME, LEAGUES, settle_home  # noqa: E402
from features import B_FEATURES, C_LINEUP_FEATURES, C_TEAM_FEATURES, MAIN_WINDOW, STAT_FEATURES  # noqa: E402
from phase3_univariate import B_SIGN, inverse, solve  # noqa: E402

ALPHAS = [0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300, 1000]
K_FOLD = 5
SEED = 20260928
BOOT_N = 2000
C_FEATS = [f for f in C_TEAM_FEATURES + C_LINEUP_FEATURES if f not in ("C4_final_third_passes", "C13_log_market_value")]
TRAIN_START, TEST_START, LAST_ROUND = 4, 20, 38


def key_main(f):
    return f"diff__{f}__w{MAIN_WINDOW}" if f in STAT_FEATURES else f"diff__{f}"


def fnum(v):
    return float(v) if v not in ("", None) else None


# ------------------------------------------------------------------ 线性模型
def ols_line(train):
    """margin ~ 1 + closing_line;返回预测函数。"""
    X = [[1.0, r["closing_line"]] for r in train]
    y = [r["margin"] for r in train]
    XtX = [[sum(a[i] * a[j] for a in X) for j in range(2)] for i in range(2)]
    Xty = [sum(a[i] * yy for a, yy in zip(X, y)) for i in range(2)]
    b = solve(XtX, Xty)
    return lambda r: b[0] + b[1] * r["closing_line"]


def design(rows, cols):
    return [[r[c] for c in cols] for r in rows]


def ridge_fit(X, y, alpha):
    """列标准化 + 截距不惩罚;返回 (mu_x, sd_x, mu_y, beta)。"""
    n, p = len(X), len(X[0])
    mu = [sum(row[j] for row in X) / n for j in range(p)]
    sd = [math.sqrt(sum((row[j] - mu[j]) ** 2 for row in X) / (n - 1)) or 1.0 for j in range(p)]
    Z = [[(row[j] - mu[j]) / sd[j] for j in range(p)] for row in X]
    my = sum(y) / n
    yc = [v - my for v in y]
    A = [[sum(Z[i][a] * Z[i][b] for i in range(n)) + (alpha if a == b else 0.0) for b in range(p)] for a in range(p)]
    bvec = [sum(Z[i][a] * yc[i] for i in range(n)) for a in range(p)]
    beta = solve(A, bvec)
    return mu, sd, my, beta


def ridge_predict(model, row_x):
    mu, sd, my, beta = model
    return my + sum(beta[j] * (row_x[j] - mu[j]) / sd[j] for j in range(len(beta)))


def cv_alpha(X, y, seed):
    idx = list(range(len(X)))
    random.Random(seed).shuffle(idx)
    folds = [idx[k::K_FOLD] for k in range(K_FOLD)]
    best = None
    scores = {}
    for a in ALPHAS:
        sse = 0.0
        for k in range(K_FOLD):
            te = set(folds[k])
            Xtr = [X[i] for i in idx if i not in te]; ytr = [y[i] for i in idx if i not in te]
            m = ridge_fit(Xtr, ytr, a)
            sse += sum((y[i] - ridge_predict(m, X[i])) ** 2 for i in te)
        scores[a] = sse
        if best is None or sse < best[0]:
            best = (sse, a)
    return best[1], scores


def make_b_composite(rows, train):
    """B_composite 的 z 分数只用训练窗口的均值/SD(防泄漏)。"""
    stats = {}
    for b in B_FEATURES:
        vals = [r[key_main(b)] for r in train]
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))
        stats[b] = (mu, sd)
    for r in rows:
        r["B_composite"] = sum(B_SIGN[b] * (r[key_main(b)] - stats[b][0]) / stats[b][1] for b in B_FEATURES) / len(B_FEATURES)


# ------------------------------------------------------------------ 评估
def payoff(s, w):
    return s * w if s > 0 else s


def r2(y, pred):
    my = sum(y) / len(y)
    sst = sum((v - my) ** 2 for v in y)
    sse = sum((a - b) ** 2 for a, b in zip(y, pred))
    return 1 - sse / sst, sse


def evaluate(label, recs, seed=SEED, ci_only=False):
    """recs: [dict(y, base, full, settle, wh, wa, league_id)]"""
    n = len(recs)
    y = [r["y"] for r in recs]
    r2_base, sse_b = r2(y, [r["base"] for r in recs])
    r2_full, sse_f = r2(y, [r["full"] for r in recs])
    inc = 1 - sse_f / sse_b
    rng = random.Random(seed)
    boots = []
    for _ in range(BOOT_N):
        sb = sf = 0.0
        for _ in range(n):
            r = recs[rng.randrange(n)]
            sb += (r["y"] - r["base"]) ** 2; sf += (r["y"] - r["full"]) ** 2
        boots.append(1 - sf / sb)
    boots.sort()
    lo, hi = boots[int(0.025 * BOOT_N)], boots[int(0.975 * BOOT_N) - 1]
    print(f"  [{label}] N={n}  R²(基准 vs 均值)={r2_base:+.4f}  R²(ridge vs 均值)={r2_full:+.4f}  "
          f"增量 1−SSE_ridge/SSE_基准={inc:+.4f}  bootstrap95%CI=[{lo:+.4f},{hi:+.4f}]")
    # 信号 = ridge 预测 − 基准预测(相对收盘线的预期偏差);按 |信号| 取 top
    sig = sorted(recs, key=lambda r: -abs(r["full"] - r["base"]))
    for frac in (0.10, 0.20):
        top = sig[:max(1, int(round(frac * n)))]
        bets = []
        for r in top:
            side = 1 if (r["full"] - r["base"]) > 0 else -1
            s = r["settle"] * side
            w = r["wh"] if side > 0 else r["wa"]
            bets.append((payoff(s, w), w, s))
        wins = sum(1 for p, _, _ in bets if p > 0); losses = sum(1 for p, _, _ in bets if p < 0)
        roi = sum(p for p, _, _ in bets) / len(bets) * 100
        avg_w = sum(w for _, w, _ in bets) / len(bets)
        rb = random.Random(seed + 1)
        bs = []
        for _ in range(BOOT_N):
            bs.append(sum(bets[rb.randrange(len(bets))][0] for _ in range(len(bets))) / len(bets) * 100)
        bs.sort()
        blo, bhi = bs[int(0.025 * BOOT_N)], bs[int(0.975 * BOOT_N) - 1]
        print(f"      top {int(frac*100)}% |信号|:N={len(bets)} 命中(赢/(赢+输))={100*wins/(wins+losses) if wins+losses else float('nan'):5.1f}% "
              f"ROI={roi:+6.2f}% boot95%CI=[{blo:+6.2f},{bhi:+6.2f}] 平均水位={avg_w:.3f} 保本胜率={100/(1+avg_w):.1f}%"
              f"{'  样本不足,不可靠' if len(bets) < 100 else ''}")
    return dict(n=n, r2_base=r2_base, r2_full=r2_full, inc=inc, inc_ci=(lo, hi))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)

    target = {}
    with open(out_dir / "phase1_target.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            target[int(r["match_id"])] = r
    feat_cols = [key_main(b) for b in B_FEATURES] + [key_main(c) for c in C_FEATS]
    data = defaultdict(list)
    drop = defaultdict(int)
    with open(out_dir / "phase2_features.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            mid = int(r["match_id"]); t = target.get(mid)
            if t is None or not t["margin"]:
                drop[r["season"] + ":no_close"] += 1; continue
            rnd = int(r["round"]) if r["round"] else None
            d = {c: fnum(r[c]) for c in feat_cols}
            if any(v is None for v in d.values()):
                drop[r["season"] + ":feature_missing"] += 1; continue
            d.update(match_id=mid, season=r["season"], league_id=int(r["league_id"]), round=rnd,
                     margin=float(t["margin"]), closing_line=float(t["crown_close_line"]),
                     wh=float(t["crown_close_home"]), wa=float(t["crown_close_away"]),
                     settle=float(t["settle"]))
            if r["season"] == "2025/2026" and (rnd is None or rnd < TRAIN_START):
                drop["2025/2026:round<4"] += 1; continue
            data[r["season"]].append(d)
    s25, s26 = data["2025/2026"], data["2026/2027"]
    print(f"25/26 可用 N={len(s25)}(需全部 {len(feat_cols)} 个特征非缺失);26/27 可用 N={len(s26)};剔除={dict(drop)}")
    print(f"  特征:closing_line + B_composite(B1–B6,z 分数只用训练窗口统计) + C 族 {len(C_FEATS)} 个 = ridge {2+len(C_FEATS)} 维;alpha 网格={ALPHAS};{K_FOLD} 折 CV")
    cols = ["closing_line", "B_composite"] + [key_main(c) for c in C_FEATS]

    # ---------------------------------------------------------------- 25/26 walk-forward
    print("\n" + "=" * 12, "25/26 按轮次 walk-forward(训练 4..r−1,测试 r;r=20..38)", "=" * 12)
    print(f"  {'r':>3s} {'N_train':>7s} {'N_test':>6s} {'alpha':>7s}  CV-SSE(最优/最差)")
    recs = []
    per_round = []
    for r_test in range(TEST_START, LAST_ROUND + 1):
        train = [x for x in s25 if x["round"] < r_test]
        test = [x for x in s25 if x["round"] == r_test]
        if not test:
            continue
        make_b_composite(train + test, train)
        base = ols_line(train)
        X, y = design(train, cols), [x["margin"] for x in train]
        alpha, scores = cv_alpha(X, y, SEED + r_test)
        model = ridge_fit(X, y, alpha)
        for x in test:
            recs.append(dict(y=x["margin"], base=base(x), full=ridge_predict(model, [x[c] for c in cols]),
                             settle=x["settle"], wh=x["wh"], wa=x["wa"], league_id=x["league_id"], round=r_test))
        per_round.append((r_test, len(train), len(test), alpha))
        print(f"  {r_test:3d} {len(train):7d} {len(test):6d} {alpha:7g}  {min(scores.values()):.1f}/{max(scores.values()):.1f}")
    print("\n  汇总(样本外,第 20–38 轮合并):")
    res25 = evaluate("25/26 OOS 全部", recs)
    print("  分联赛:")
    res25_lg = {}
    for l in LEAGUES:
        sub = [r for r in recs if r["league_id"] == l]
        if len(sub) >= 20:
            res25_lg[l] = evaluate(f"25/26 OOS {LEAGUE_NAME[l]}", sub)
    print("  分半段(第 20–29 轮 / 第 30–38 轮):")
    evaluate("25/26 OOS 轮 20–29", [r for r in recs if r["round"] <= 29])
    evaluate("25/26 OOS 轮 30–38", [r for r in recs if r["round"] >= 30])

    # ---------------------------------------------------------------- 26/27 冻结
    print("\n" + "=" * 12, "26/27:25/26 全部(4–38 轮)训练的冻结模型,只跑一次(只报方向与 95% CI)", "=" * 12)
    train = list(s25)
    make_b_composite(train + s26, train)
    base = ols_line(train)
    X, y = design(train, cols), [x["margin"] for x in train]
    alpha, scores = cv_alpha(X, y, SEED)
    model = ridge_fit(X, y, alpha)
    print(f"  冻结模型:N_train={len(train)} alpha={alpha}  标准化系数:" +
          " ".join(f"{c.replace('diff__','').replace('__w8','')}={b:+.3f}" for c, b in zip(cols, model[3])))
    recs26 = [dict(y=x["margin"], base=base(x), full=ridge_predict(model, [x[c] for c in cols]),
                   settle=x["settle"], wh=x["wh"], wa=x["wa"], league_id=x["league_id"], round=x["round"]) for x in s26]
    res26 = evaluate("26/27 冻结模型", recs26) if recs26 else None
    if recs26:
        print("  分联赛(26/27,N 小,只看方向):")
        for l in LEAGUES:
            sub = [r for r in recs26 if r["league_id"] == l]
            if len(sub) >= 20:
                evaluate(f"26/27 {LEAGUE_NAME[l]}", sub)

    (out_dir / "phase5_results.json").write_text(json.dumps(dict(
        n25=len(s25), n26=len(s26), per_round=per_round, res25=res25, res25_by_league={LEAGUE_NAME[l]: v for l, v in res25_lg.items()},
        res26=res26, frozen_alpha=alpha, frozen_beta=dict(zip(cols, model[3]))), ensure_ascii=False, indent=1, default=str))
    print(f"\n[written] {out_dir/'phase5_results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
