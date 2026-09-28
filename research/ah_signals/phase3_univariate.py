#!/usr/bin/env python3
"""Phase 3 — 单变量检验(PREREG §4),只用 25/26(冷启动剔除后),26/27 完全不参与。

输入(仓库外 out-dir):phase1_target.csv(目标与收盘线)、phase2_features.csv(特征,不含目标)。
纯 Python OLS + HC1 稳健 SE(服务器无 numpy);p 值用正态近似(df≈1600,与 t 分布差异可忽略)。

主检验:margin ~ 1 + closing_line + feature_diff
次检验(C 族):margin ~ 1 + closing_line + B_composite + feature_diff
BH q=0.10,B 族与 C 族各自独立。5 条稳健性条件、方向一致性、效应上限一并报告。
敏感性(5/10 窗口、对手调整、仅上半场)单独成表,不进 FDR。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BET365_EXCLUDE_MIDS, LEAGUE_NAME, LEAGUES, pearson  # noqa: E402
from features import B_FEATURES, C_LINEUP_FEATURES, C_TEAM_FEATURES, MAIN_WINDOW, STAT_FEATURES  # noqa: E402

Q = 0.10
EXPECTED = {  # PREREG §3.4/3.5 预期方向;"0"=预期无增量;"±"=双侧不预设
    "B1_npxgd": "+", "B2_xgot_diff": "+", "B3_att_luck": "-", "B4_def_luck": "+",
    "B5_big_chance_diff": "+", "B6_sib_diff": "+",
    "C1_field_tilt": "+", "C2_ppda": "-", "C3_tob_diff": "+", "C4_final_third_passes": "+",
    "C5_cross_share": "±", "C6_long_share": "±", "C7_duel_rate": "+", "C8_corner_diff": "+",
    "C9_rolling_rating": "+", "C10_xi_jaccard": "+", "C11_rest_days": "+", "C12_possession": "0",
    "C13_log_market_value": "+",
}
B_SIGN = {"B1_npxgd": 1, "B2_xgot_diff": 1, "B3_att_luck": -1, "B4_def_luck": 1, "B5_big_chance_diff": 1, "B6_sib_diff": 1}
ND = NormalDist()


# ------------------------------------------------------------------ 线性代数(k ≤ 4)
def solve(A, b):
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            raise ZeroDivisionError("singular")
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def inverse(A):
    n = len(A)
    cols = []
    for j in range(n):
        e = [1.0 if i == j else 0.0 for i in range(n)]
        cols.append(solve(A, e))
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def ols_hc1(X: list[list[float]], y: list[float]):
    """返回 dict(coef, se, n, k)。X 每行含常数项。HC1 = n/(n-k) 白色稳健协方差。"""
    n, k = len(X), len(X[0])
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    beta = solve(XtX, Xty)
    inv = inverse(XtX)
    resid = [y[i] - sum(X[i][a] * beta[a] for a in range(k)) for i in range(n)]
    meat = [[sum(X[i][a] * X[i][b] * resid[i] ** 2 for i in range(n)) for b in range(k)] for a in range(k)]
    tmp = [[sum(inv[a][c] * meat[c][b] for c in range(k)) for b in range(k)] for a in range(k)]
    cov = [[sum(tmp[a][c] * inv[c][b] for c in range(k)) * n / (n - k) for b in range(k)] for a in range(k)]
    se = [math.sqrt(max(cov[a][a], 0.0)) for a in range(k)]
    return dict(coef=beta, se=se, n=n, k=k)


def fit(rows, xcols, ycol="margin"):
    X, y = [], []
    for r in rows:
        vals = [r.get(c) for c in xcols]
        if any(v is None for v in vals) or r.get(ycol) is None:
            continue
        X.append([1.0] + [float(v) for v in vals])
        y.append(float(r[ycol]))
    if len(X) < len(xcols) + 5:
        return None
    return ols_hc1(X, y)


def pval(t):
    return 2.0 * (1.0 - ND.cdf(abs(t)))


def bh(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    q = {}
    prev = 1.0
    for rank in range(m, 0, -1):
        name, p = items[rank - 1]
        val = min(prev, p * m / rank)
        q[name] = val
        prev = val
    return q


def sd(vals):
    n = len(vals)
    mu = sum(vals) / n
    return math.sqrt(sum((v - mu) ** 2 for v in vals) / (n - 1))


def sign(x):
    return 0 if x is None else (1 if x > 0 else -1 if x < 0 else 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--removed", nargs="*", default=["C13_log_market_value"])
    args = ap.parse_args()
    out_dir = Path(args.out_dir)

    # ---------------------------------------------------------------- 载入与样本隔离
    target = {}
    with open(out_dir / "phase1_target.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            target[int(r["match_id"])] = r
    rows = []
    with open(out_dir / "phase2_features.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            mid = int(r["match_id"])
            t = target.get(mid)
            if t is None or r["season"] != "2025/2026":
                continue                      # 26/27 完全不参与 Phase 3
            rnd = int(r["round"]) if r["round"] else None
            if rnd is None or rnd <= 3:
                continue                      # 冷启动剔除
            if not t["margin"]:
                continue                      # 无 Crown 收盘线
            d = {k: (float(v) if v not in ("", None) else None) for k, v in r.items() if k not in ("match_id", "season", "league_id", "round")}
            d.update(match_id=mid, league_id=int(r["league_id"]), round=rnd,
                     margin=float(t["margin"]), closing_line=float(t["crown_close_line"]),
                     raw_margin=int(t["home_score"]) - int(t["away_score"]),
                     bet365_line=float(t["bet365_close_line"]) if t["bet365_close_line"] else None)
            d["margin_b365"] = None if d["bet365_line"] is None or mid in BET365_EXCLUDE_MIDS else d["raw_margin"] - d["bet365_line"]
            rows.append(d)
    print(f"Phase 3 样本:25/26、Match_Round>3、有 Crown 收盘线 → N={len(rows)}"
          f"(26/27 未载入;Bet365 稳健性另剔除 {sorted(BET365_EXCLUDE_MIDS)})")
    print(f"  各联赛 N: " + ", ".join(f"{LEAGUE_NAME[l]}={sum(1 for r in rows if r['league_id']==l)}" for l in LEAGUES))
    print(f"  上半季(轮≤19) N={sum(1 for r in rows if r['round']<=19)}  下半季 N={sum(1 for r in rows if r['round']>19)}")

    def key_main(f):
        return f"diff__{f}__w{MAIN_WINDOW}" if f in STAT_FEATURES else f"diff__{f}"

    # B_composite(在分析样本上标准化,符号按先验对齐,等权)
    zstats = {}
    for b in B_FEATURES:
        vals = [r[key_main(b)] for r in rows if r[key_main(b)] is not None]
        zstats[b] = (sum(vals) / len(vals), sd(vals))
    for r in rows:
        zs = []
        for b in B_FEATURES:
            v = r[key_main(b)]
            if v is None:
                zs = None
                break
            zs.append(B_SIGN[b] * (v - zstats[b][0]) / zstats[b][1])
        r["B_composite"] = None if zs is None else sum(zs) / len(zs)
    print(f"  B_composite 可用 N={sum(1 for r in rows if r['B_composite'] is not None)}")

    c_feats = [f for f in C_TEAM_FEATURES + C_LINEUP_FEATURES if f not in set(args.removed)]
    print(f"  B 族 {len(B_FEATURES)} 个;C 族 {len(c_feats)} 个(移出/删除:{args.removed})")

    # ---------------------------------------------------------------- 主检验
    results = {}
    for fam, feats in (("B", list(B_FEATURES)), ("C", c_feats)):
        for f in feats:
            k = key_main(f)
            res = fit(rows, ["closing_line", k])
            if res is None:
                results[f] = dict(family=fam, n=0)
                continue
            coef, se = res["coef"][2], res["se"][2]
            t = coef / se if se > 0 else float("nan")
            fv = [r[k] for r in rows if r[k] is not None and r["margin"] is not None]
            cl = [r["closing_line"] for r in rows if r[k] is not None and r["margin"] is not None]
            s_f = sd(fv)
            lo, hi = coef - 1.96 * se, coef + 1.96 * se
            results[f] = dict(family=fam, n=res["n"], coef=coef, se=se, t=t, p=pval(t), ci=(lo, hi),
                              sd_f=s_f, eff_sd=coef * s_f, eff_ci_sd=(lo * s_f, hi * s_f),
                              eff_upper_sd=max(abs(lo), abs(hi)) * s_f,
                              corr_line=pearson(fv, cl), coef_line=res["coef"][1])
    for fam in ("B", "C"):
        qs = bh({f: r["p"] for f, r in results.items() if r["family"] == fam and r.get("n")})
        for f, q in qs.items():
            results[f]["q"] = q

    # ---------------------------------------------------------------- 稳健性条件
    for f, r in results.items():
        if not r.get("n"):
            continue
        s0 = sign(r["coef"])
        cond = {}
        if f in STAT_FEATURES:
            c5 = fit(rows, ["closing_line", f"diff__{f}__w5"])
            c10 = fit(rows, ["closing_line", f"diff__{f}__w10"])
            cond["1_w5_w10"] = (c5 is not None and c10 is not None and sign(c5["coef"][2]) == s0 and sign(c10["coef"][2]) == s0,
                                f"w5={c5['coef'][2]:+.4f} w10={c10['coef'][2]:+.4f}" if c5 and c10 else "n/a")
            ca = fit(rows, ["closing_line", f"diff__{f}__adj"])
            cond["2_adj"] = (ca is not None and sign(ca["coef"][2]) == s0, f"adj={ca['coef'][2]:+.4f}" if ca else "n/a")
        else:
            cond["1_w5_w10"] = (None, "非窗口特征,不适用")
            cond["2_adj"] = (None, "非窗口特征,不适用")
        k = key_main(f)
        cb = fit(rows, ["bet365_line", k], ycol="margin_b365")
        cond["3_bet365"] = (cb is not None and sign(cb["coef"][2]) == s0, f"b365={cb['coef'][2]:+.4f} N={cb['n']}" if cb else "n/a")
        lg = {}
        for l in LEAGUES:
            cl_ = fit([x for x in rows if x["league_id"] == l], ["closing_line", k])
            lg[l] = None if cl_ is None else cl_["coef"][2]
        agree = sum(1 for v in lg.values() if v is not None and sign(v) == s0)
        cond["4_leagues"] = (agree >= 4, " ".join(f"{LEAGUE_NAME[l]}={('n/a' if v is None else f'{v:+.3f}')}" for l, v in lg.items()) + f" 同号={agree}/5")
        h1 = fit([x for x in rows if x["round"] <= 19], ["closing_line", k])
        h2 = fit([x for x in rows if x["round"] > 19], ["closing_line", k])
        cond["5_halves"] = (h1 is not None and h2 is not None and sign(h1["coef"][2]) == s0 and sign(h2["coef"][2]) == s0,
                            f"H1={h1['coef'][2]:+.4f} H2={h2['coef'][2]:+.4f}" if h1 and h2 else "n/a")
        r["cond"] = cond
        exp = EXPECTED[f]
        r["dir_ok"] = "n/a(双侧)" if exp == "±" else ("n/a(预期0)" if exp == "0" else ("一致" if (s0 > 0) == (exp == "+") else "相反"))

    # ---------------------------------------------------------------- 次检验(C)
    for f in c_feats:
        r = results[f]
        if not r.get("n"):
            continue
        res = fit(rows, ["closing_line", "B_composite", key_main(f)])
        if res is None:
            r["sec"] = None
            continue
        coef, se = res["coef"][3], res["se"][3]
        t = coef / se if se > 0 else float("nan")
        r["sec"] = dict(n=res["n"], coef=coef, se=se, t=t, p=pval(t), eff_sd=coef * r["sd_f"],
                        ci_sd=((coef - 1.96 * se) * r["sd_f"], (coef + 1.96 * se) * r["sd_f"]),
                        coef_bcomp=res["coef"][2], se_bcomp=res["se"][2])

    # ---------------------------------------------------------------- 输出
    def show(fam):
        print("\n" + "=" * 12, f"{fam} 族主检验:margin ~ closing_line + feature(HC1;BH q={Q},族内独立)", "=" * 12)
        print(f"  {'feature':24s} {'N':>5s} {'coef':>9s} {'SE':>8s} {'t':>7s} {'p':>8s} {'q':>8s} "
              f"{'球/SD':>7s} {'95%CI(球/SD)':>18s} {'上限':>6s} {'corr(line)':>10s} {'方向':>9s} 过BH")
        for f, r in results.items():
            if r["family"] != fam:
                continue
            if not r.get("n"):
                print(f"  {f:24s} 不可算"); continue
            print(f"  {f:24s} {r['n']:5d} {r['coef']:+9.4f} {r['se']:8.4f} {r['t']:+7.2f} {r['p']:8.4f} {r['q']:8.4f} "
                  f"{r['eff_sd']:+7.3f} [{r['eff_ci_sd'][0]:+.3f},{r['eff_ci_sd'][1]:+.3f}] {r['eff_upper_sd']:6.3f} "
                  f"{r['corr_line']:+10.3f} {r['dir_ok']:>9s} {'是' if r['q'] <= Q else '否'}")
        print("\n  稳健性条件(逐条;通过=系数同号):")
        for f, r in results.items():
            if r["family"] != fam or not r.get("n"):
                continue
            passed = [v[0] for v in r["cond"].values() if v[0] is not None]
            print(f"  {f}: 通过 {sum(passed)}/{len(passed)}(适用条数)")
            for name, (ok, detail) in r["cond"].items():
                print(f"      {name:10s} {'—' if ok is None else ('通过' if ok else '未通过'):4s} {detail}")
    show("B")
    show("C")
    print("\n" + "=" * 12, "C 族次检验:margin ~ closing_line + B_composite + feature(xG 之外的增量)", "=" * 12)
    print(f"  {'feature':24s} {'N':>5s} {'coef':>9s} {'SE':>8s} {'t':>7s} {'p':>8s} {'球/SD':>7s} {'95%CI(球/SD)':>18s} {'B_comp系数':>10s}")
    for f in c_feats:
        r = results[f]
        s_ = r.get("sec")
        if not s_:
            print(f"  {f:24s} 不可算"); continue
        print(f"  {f:24s} {s_['n']:5d} {s_['coef']:+9.4f} {s_['se']:8.4f} {s_['t']:+7.2f} {s_['p']:8.4f} {s_['eff_sd']:+7.3f} "
              f"[{s_['ci_sd'][0]:+.3f},{s_['ci_sd'][1]:+.3f}] {s_['coef_bcomp']:+10.4f}")
    rb = fit(rows, ["closing_line", "B_composite"])
    if rb:
        t = rb["coef"][2] / rb["se"][2]
        print(f"  (参考)margin ~ closing_line + B_composite:N={rb['n']} coef={rb['coef'][2]:+.4f} SE={rb['se'][2]:.4f} t={t:+.2f} p={pval(t):.4f}")

    print("\n" + "=" * 12, "敏感性(不进 FDR):5/10 场窗口、对手调整、仅上半场", "=" * 12)
    print(f"  {'feature':24s} " + " ".join(f"{v:>26s}" for v in ("w5 coef(SE) p N", "w10 coef(SE) p N", "adj coef(SE) p N", "fh coef(SE) p N")))
    sens = {}
    for f in list(B_FEATURES) + c_feats:
        if f not in STAT_FEATURES:
            continue
        cells = []
        sens[f] = {}
        for v in ("w5", "w10", "adj", "fh"):
            if v == "fh" and f in ("B3_att_luck", "B4_def_luck"):
                cells.append(f"{'不可算(FH Goals 全NULL)':>26s}"); sens[f][v] = None; continue
            res = fit(rows, ["closing_line", f"diff__{f}__{v}"])
            if res is None:
                cells.append(f"{'n/a':>26s}"); sens[f][v] = None; continue
            c, s = res["coef"][2], res["se"][2]
            p = pval(c / s) if s > 0 else float("nan")
            cells.append(f"{c:+.4f}({s:.4f}) {p:.3f} {res['n']:d}".rjust(26))
            sens[f][v] = dict(coef=c, se=s, p=p, n=res["n"])
        print(f"  {f:24s} " + " ".join(cells))

    (out_dir / "phase3_results.json").write_text(json.dumps(dict(n=len(rows), results=results, sensitivity=sens),
                                                            ensure_ascii=False, indent=1, default=str))
    print(f"\n[written] {out_dir/'phase3_results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
