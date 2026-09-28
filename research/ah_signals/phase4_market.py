#!/usr/bin/env python3
"""Phase 4 — 市场族(M)与时点族(T),按闸门顺序执行(PREREG_phase4;只读;只用 25/26)。

闸门 1:特征构造 + 覆盖报告(缺失原因分解)
闸门 2:截断测试(全量、按比赛日分组、浮点严格相等)
闸门 3:恒等式 margin_T24 = margin + dline_raw 逐场验证 + 随机 5 场打印
任一闸门不通过 → 退出码非 0,不跑回归。
通过后:M 族 / T-a / T-b 主检验(各自独立 BH q=0.10)、稳健性 5 条、功效表、M5 分箱、M6×Δline_60。

用法:
    nice -n 19 python3 research/ah_signals/phase4_market.py --data-dir ... --out-dir ...
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    BET365_EXCLUDE_MIDS, LEAGUE_NAME, LEAGUES, load_matches, load_xref, mean_ci95, open_ro, parse_round,
    pearson, seeded_sample, settle_home,
)
from features import (  # noqa: E402
    C_LINEUP_FEATURES, C_TEAM_FEATURES, MAIN_WINDOW, STAT_FEATURES, load_lineups, load_player_rows,
)
from features_market import M_FEATURES, TICK, build_market_features, implied_two, load_timelines  # noqa: E402
from phase3_univariate import EXPECTED as C_EXPECTED, Q, bh, fit, pval, sd, sign  # noqa: E402

M_EXPECTED = {"M1_open_to_close": "±", "M2_t24_to_close": "±", "M3_water_move": "+", "M4a_line_gap": "±",
              "M4b_prob_gap": "±", "M5_poisson_vs_1x2": "±", "M6_lineup_surprise": "+"}
C_FEATS = [f for f in C_TEAM_FEATURES + C_LINEUP_FEATURES if f not in ("C4_final_third_passes", "C13_log_market_value")]
TRUNC_KEYS_SKIP = ("crown_close_at",)


def pct(a, b):
    return 100.0 * a / b if b else math.nan


def key_main(f):
    return f"diff__{f}__w{MAIN_WINDOW}" if f in STAT_FEATURES else f"diff__{f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)
    summary: dict = {}

    core, odds = open_ro(data_dir / "allwin.db"), open_ro(data_dir / "odds.db")
    matches = [m for m in load_matches(core) if m["Season"] == "2025/2026"]      # 26/27 不载入
    ok_xref, _ = load_xref(odds, {m["Match_ID"] for m in matches})
    matches = [m for m in matches if m["Match_ID"] in ok_xref]
    mids = {m["Match_ID"] for m in matches}
    minfo = {m["Match_ID"]: m for m in matches}
    pmid_to_mid = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items()}
    tl = load_timelines(odds, pmid_to_mid)
    lineups = load_lineups(core, mids)
    player_rows = load_player_rows(core, mids)
    n_snaps = sum(len(v) for v in tl.values())
    print(f"25/26 样本比赛={len(matches)}(26/27 未载入);赔率 snap(过滤水位异常后)={n_snaps};lineup 队场={len(lineups)};player 行={len(player_rows)}")

    # 与 Phase 1 收盘线对账
    p1 = {}
    with open(out_dir / "phase1_target.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            p1[int(r["match_id"])] = r
    feats2 = {}
    with open(out_dir / "phase2_features.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            feats2[int(r["match_id"])] = {k: (float(v) if v not in ("", None) else None) for k, v in r.items()
                                          if k.startswith("diff__")}

    # ================================================================ 闸门 1
    print("\n" + "=" * 12, "闸门 1:特征构造 + 覆盖报告", "=" * 12)
    t0 = time.perf_counter()
    mf = build_market_features(matches, tl, lineups, player_rows)
    t_full = time.perf_counter() - t0
    print(f"  构造耗时 {t_full:.1f}s")
    n_diff = 0
    for m in matches:
        a = mf[m["Match_ID"]]["crown_close_line"]
        b = p1[m["Match_ID"]]["crown_close_line"]
        b = float(b) if b else None
        if not (a == b or (a is None and b is None)):
            n_diff += 1
    print(f"  Crown 收盘线与 Phase 1 目标表逐场对账(本阶段多了水位异常过滤):不同={n_diff}")
    elig = [m for m in matches if (parse_round(m["Match_Round"]) or 0) > 3 and mf[m["Match_ID"]]["margin"] is not None]
    print(f"  分析样本(Match_Round>3 且有 Crown 收盘线)N={len(elig)}")
    print(f"\n  {'特征/目标':22s} {'可用N':>5s} {'缺失':>5s}  缺失原因分解")
    for f in M_FEATURES + ("I_move", "dline_60"):
        av = sum(1 for m in elig if mf[m["Match_ID"]].get(f) is not None)
        reasons = Counter()
        rk = "_reason__" + f.split("_")[0] if f in M_FEATURES else None
        for m in elig:
            if mf[m["Match_ID"]].get(f) is None:
                reasons[mf[m["Match_ID"]].get(rk, "缺失") if rk else "缺失"] += 1
        print(f"  {f:22s} {av:5d} {len(elig)-av:5d}  {dict(reasons) if reasons else ''}")
    for f in ("line_T24", "margin_T24", "dline", "b365_t24_line", "margin_b365", "dline_b365"):
        av = sum(1 for m in elig if mf[m["Match_ID"]].get(f) is not None)
        print(f"  {f:22s} {av:5d} {len(elig)-av:5d}")
    m5_bad = sum(1 for m in elig if mf[m["Match_ID"]].get("_reason__M5") == "ok(拟合不佳>0.02)")
    print(f"  M5 拟合残差>0.02 的场次={m5_bad}(仍保留)")
    m3_moved = sum(1 for m in elig if mf[m["Match_ID"]].get("I_move") == 1.0)
    print(f"  M3:T-24h→收盘线有变动的场次={m3_moved}(Δp 记 0,I_move=1)")
    summary["gate1"] = dict(n=len(elig), close_diff=n_diff)

    # ================================================================ 闸门 2
    print("\n" + "=" * 12, "闸门 2:截断测试(按比赛日分组、全量、浮点严格相等)", "=" * 12)
    ms_sorted = sorted(matches, key=lambda m: (m["kickoff_at_utc"], m["Match_ID"]))
    days = sorted({m["kickoff_at_utc"][:10] for m in ms_sorted})
    print(f"  比赛日={len(days)};单次全量 {t_full:.1f}s → 预计 {t_full*len(days)/2/60:.0f} 分钟")
    t1 = time.perf_counter()
    n_fail = n_fields = n_tested = 0
    fails = []
    next_pct = 10
    for i, d in enumerate(days):
        sub = [x for x in ms_sorted if x["kickoff_at_utc"][:10] <= d]
        sub_mids = {x["Match_ID"] for x in sub}
        tl_sub = {k: v for k, v in tl.items() if k[0] in sub_mids}
        lu_sub = {k: v for k, v in lineups.items() if k[0] in sub_mids}
        pr_sub = [r for r in player_rows if r["Match_ID"] in sub_mids]
        f_sub = build_market_features(sub, tl_sub, lu_sub, pr_sub)
        for x in sub:
            if x["kickoff_at_utc"][:10] != d:
                continue
            n_tested += 1
            a_all, b_all = mf[x["Match_ID"]], f_sub[x["Match_ID"]]
            for k, a in a_all.items():
                if k in TRUNC_KEYS_SKIP:
                    continue
                n_fields += 1
                b = b_all.get(k, "<absent>")
                if not (a == b or (a is None and b is None)):
                    n_fail += 1
                    if len(fails) < 20:
                        fails.append((x["Match_ID"], k, a, b))
        done = 100.0 * (i + 1) / len(days)
        if done >= next_pct or i + 1 == len(days):
            print(f"    进度 {done:5.1f}%  比赛日 {i+1}/{len(days)}  已测场次={n_tested}  不等字段累计={n_fail}  已用时 {time.perf_counter()-t1:.0f}s", flush=True)
            while next_pct <= done:
                next_pct += 10
    print(f"  结果:测试场次={n_tested} 比对字段总数={n_fields} 不等={n_fail} 用时 {time.perf_counter()-t1:.0f}s")
    for mid, k, a, b in fails:
        print(f"    !! match {mid} {k}: full={a} truncated={b}")
    summary["gate2"] = dict(n=n_tested, fields=n_fields, fail=n_fail)
    if n_fail:
        print("  !! 闸门 2 不通过,停止。")
        return 2

    # ================================================================ 闸门 3
    print("\n" + "=" * 12, "闸门 3:恒等式 margin_T24 = margin + dline_raw(逐场,浮点严格相等)", "=" * 12)
    n_chk = n_bad = 0
    for m in elig:
        f = mf[m["Match_ID"]]
        if f["margin_T24"] is None or f["dline_raw"] is None:
            continue
        n_chk += 1
        if f["margin_T24"] != f["margin"] + f["dline_raw"]:
            n_bad += 1
    print(f"  检验场次={n_chk}  不成立={n_bad}")
    print("  随机 5 场(seed=20260928):")
    for m in seeded_sample([m for m in elig if mf[m["Match_ID"]]["margin_T24"] is not None], 5):
        f = mf[m["Match_ID"]]
        print(f"    {m['Home_Team_Name']} vs {m['Away_Team_Name']} {m['home_score']}-{m['away_score']}  "
              f"line_T24={f['line_T24']:+.2f} closing_line={f['crown_close_line']:+.2f} Δline={f['dline']:+.0f}档 "
              f"margin={f['margin']:+.2f} margin_T24={f['margin_T24']:+.2f}")
    summary["gate3"] = dict(n=n_chk, bad=n_bad)
    if n_bad:
        print("  !! 闸门 3 不通过,停止。")
        return 3

    # ================================================================ 回归数据
    rows = []
    for m in elig:
        f = dict(mf[m["Match_ID"]])
        f.update(feats2.get(m["Match_ID"], {}))
        f.update(match_id=m["Match_ID"], league_id=m["League_ID"], round=parse_round(m["Match_Round"]))
        f["closing_line"] = f["crown_close_line"]
        f["closing_line_sq"] = f["closing_line"] ** 2
        if m["Match_ID"] in BET365_EXCLUDE_MIDS:
            f["margin_b365"] = None; f["M4a_line_gap"] = None; f["M4b_prob_gap"] = None
        rows.append(f)

    def run_family(name, feats, ycol, ctrl, expected, extra_ctrl=None, b365=None):
        """b365: dict(ycol, ctrl) 用于稳健性条件 3;extra_ctrl: {feature: [额外控制项]}。"""
        res = {}
        for f in feats:
            xs = [ctrl] + (extra_ctrl.get(f, []) if extra_ctrl else []) + [f]
            r = fit(rows, xs, ycol=ycol)
            if r is None:
                res[f] = dict(n=0); continue
            idx = len(xs)
            coef, se = r["coef"][idx], r["se"][idx]
            t = coef / se if se > 0 else float("nan")
            fv = [x[f] for x in rows if x[f] is not None and x[ycol] is not None and x[ctrl] is not None]
            cv = [x[ctrl] for x in rows if x[f] is not None and x[ycol] is not None and x[ctrl] is not None]
            s_f = sd(fv)
            lo, hi = coef - 1.96 * se, coef + 1.96 * se
            res[f] = dict(n=r["n"], coef=coef, se=se, t=t, p=pval(t), sd_f=s_f, eff=coef * s_f,
                          eff_ci=(lo * s_f, hi * s_f), eff_up=max(abs(lo), abs(hi)) * s_f, corr_ctrl=pearson(fv, cv))
        qs = bh({f: r["p"] for f, r in res.items() if r.get("n")})
        for f, q in qs.items():
            res[f]["q"] = q
        # 稳健性
        for f, r in res.items():
            if not r.get("n"):
                continue
            s0 = sign(r["coef"])
            cond = {}
            xs_extra = extra_ctrl.get(f, []) if extra_ctrl else []
            base = f.split("__")[1] if f.startswith("diff__") else None
            if base in STAT_FEATURES:
                c5 = fit(rows, [ctrl] + xs_extra + [f"diff__{base}__w5"], ycol=ycol)
                c10 = fit(rows, [ctrl] + xs_extra + [f"diff__{base}__w10"], ycol=ycol)
                ok1 = c5 is not None and c10 is not None and sign(c5["coef"][-1]) == s0 and sign(c10["coef"][-1]) == s0
                cond["1_w5_w10"] = (ok1, f"w5={c5['coef'][-1]:+.4f} w10={c10['coef'][-1]:+.4f}" if c5 and c10 else "n/a")
                ca = fit(rows, [ctrl] + xs_extra + [f"diff__{base}__adj"], ycol=ycol)
                cond["2_adj"] = (ca is not None and sign(ca["coef"][-1]) == s0, f"adj={ca['coef'][-1]:+.4f}" if ca else "n/a")
            else:
                cond["1_w5_w10"] = (None, "n/a"); cond["2_adj"] = (None, "n/a")
            if b365 is None or f in ("M4a_line_gap", "M4b_prob_gap", "M5_poisson_vs_1x2"):
                cond["3_bet365"] = (None, "n/a")
            else:
                cb = fit(rows, [b365["ctrl"]] + xs_extra + [f], ycol=b365["ycol"])
                cond["3_bet365"] = (cb is not None and sign(cb["coef"][-1]) == s0, f"b365={cb['coef'][-1]:+.4f} N={cb['n']}" if cb else "n/a")
            lg = {}
            for l in LEAGUES:
                cl_ = fit([x for x in rows if x["league_id"] == l], [ctrl] + xs_extra + [f], ycol=ycol)
                lg[l] = None if cl_ is None else cl_["coef"][-1]
            agree = sum(1 for v in lg.values() if v is not None and sign(v) == s0)
            cond["4_leagues"] = (agree >= 4, " ".join(f"{LEAGUE_NAME[l]}={('n/a' if v is None else f'{v:+.3f}')}" for l, v in lg.items()) + f" 同号={agree}/5")
            h1 = fit([x for x in rows if x["round"] <= 19], [ctrl] + xs_extra + [f], ycol=ycol)
            h2 = fit([x for x in rows if x["round"] > 19], [ctrl] + xs_extra + [f], ycol=ycol)
            cond["5_halves"] = (h1 is not None and h2 is not None and sign(h1["coef"][-1]) == s0 and sign(h2["coef"][-1]) == s0,
                                f"H1={h1['coef'][-1]:+.4f} H2={h2['coef'][-1]:+.4f}" if h1 and h2 else "n/a")
            r["cond"] = cond
            exp = expected[f]
            r["dir"] = "n/a(双侧)" if exp == "±" else ("n/a(预期0)" if exp == "0" else ("一致" if (s0 > 0) == (exp == "+") else "相反"))
        # 打印
        ysd = sd([x[ycol] for x in rows if x[ycol] is not None])
        print("\n" + "=" * 12, f"{name}:{ycol} ~ {ctrl} + feature(HC1;BH q={Q};目标 SD={ysd:.3f})", "=" * 12)
        print(f"  {'feature':24s} {'N':>5s} {'coef':>9s} {'SE':>8s} {'t':>7s} {'p':>8s} {'q':>8s} {'目标/SD':>8s} {'95%CI':>18s} {'上限':>6s} {'corr(ctrl)':>10s} {'方向':>9s} 过BH")
        for f, r in res.items():
            if not r.get("n"):
                print(f"  {f:24s} 不可算"); continue
            print(f"  {f:24s} {r['n']:5d} {r['coef']:+9.4f} {r['se']:8.4f} {r['t']:+7.2f} {r['p']:8.4f} {r['q']:8.4f} "
                  f"{r['eff']:+8.3f} [{r['eff_ci'][0]:+.3f},{r['eff_ci'][1]:+.3f}] {r['eff_up']:6.3f} {r['corr_ctrl']:+10.3f} {r['dir']:>9s} {'是' if r['q'] <= Q else '否'}")
        print("  稳健性条件:")
        for f, r in res.items():
            if not r.get("n"):
                continue
            passed = [v[0] for v in r["cond"].values() if v[0] is not None]
            print(f"  {f}: 通过 {sum(passed)}/{len(passed)}(适用条数)")
            for cn, (ok, detail) in r["cond"].items():
                print(f"      {cn:10s} {'—' if ok is None else ('通过' if ok else '未通过'):4s} {detail}")
        # 功效
        n_fam = len([1 for r in res.values() if r.get("n")])
        za, z80 = NormalDist().inv_cdf(0.975), 0.8416
        zb = NormalDist().inv_cdf(1 - Q / max(n_fam, 1) / 2)
        print(f"  功效(目标 SD={ysd:.3f};BH 最严档 α={Q}/{n_fam}={Q/max(n_fam,1):.4f}):")
        for f, r in res.items():
            if r.get("n"):
                n = r["n"]
                print(f"    {f:24s} N={n:5d}  r_min(α=.05)={(za+z80)/math.sqrt(n):.3f} (≈{(za+z80)/math.sqrt(n)*ysd:.3f} 目标/SD)  "
                      f"r_min(BH最严)={(zb+z80)/math.sqrt(n):.3f} (≈{(zb+z80)/math.sqrt(n)*ysd:.3f})")
        return res

    # ================================================================ M 族
    resM = run_family("M 族主检验", list(M_FEATURES), "margin", "closing_line", M_EXPECTED,
                      extra_ctrl={"M3_water_move": ["I_move"], "M5_poisson_vs_1x2": ["closing_line_sq"]},
                      b365=dict(ycol="margin_b365", ctrl="b365_close_line"))
    # M5 分箱(描述性)
    print("\n  M5 对 closing_line 的分箱均值(描述性;每 0.25 档一箱,N<30 合并到相邻箱):")
    bins = defaultdict(list)
    for x in rows:
        if x["M5_poisson_vs_1x2"] is not None:
            bins[round(x["closing_line"] / TICK)].append(x["M5_poisson_vs_1x2"])
    keys = sorted(bins)
    merged = []
    cur, cur_keys = [], []
    for k in keys:
        cur += bins[k]; cur_keys.append(k)
        if len(cur) >= 30:
            merged.append((cur_keys, cur)); cur, cur_keys = [], []
    if cur:
        if merged:
            merged[-1] = (merged[-1][0] + cur_keys, merged[-1][1] + cur)
        else:
            merged.append((cur_keys, cur))
    for ks, vals in merged:
        mu, lo, hi, n = mean_ci95(vals)
        lab = f"{ks[0]*TICK:+.2f}" if len(ks) == 1 else f"{ks[0]*TICK:+.2f}..{ks[-1]*TICK:+.2f}"
        print(f"    line {lab:>14s}  N={n:4d}  M5 mean={mu:+.4f} [{lo:+.4f},{hi:+.4f}]")
    # M6 × Δline_60(描述性)
    xs = [(x["M6_lineup_surprise"], x["dline_60"]) for x in rows if x["M6_lineup_surprise"] is not None and x["dline_60"] is not None]
    print(f"\n  M6 × Δline_60(描述性):corr={pearson([a for a,_ in xs],[b for _,b in xs]):+.4f} N={len(xs)}")
    for lab, cond in (("M6 < −0.1", lambda v: v < -0.1), ("−0.1 ≤ M6 ≤ 0.1", lambda v: -0.1 <= v <= 0.1), ("M6 > 0.1", lambda v: v > 0.1)):
        vals = [b for a, b in xs if cond(a)]
        mu, lo, hi, n = mean_ci95(vals)
        print(f"    {lab:18s} N={n:4d}  Δline_60 mean={mu:+.4f}档 [{lo:+.4f},{hi:+.4f}]{'  样本不足,不可靠' if n < 100 else ''}")

    # ================================================================ T 族
    print("\n(注:Phase 3 结果已知,但 T 族目标变量不同)")
    c_keys = [key_main(f) for f in C_FEATS]
    expT = {key_main(f): C_EXPECTED[f] for f in C_FEATS}
    resTa = run_family("T-a", c_keys, "margin_T24", "line_T24", expT, b365=dict(ycol="margin_b365_T24", ctrl="b365_t24_line"))
    resTb = run_family("T-b", c_keys, "dline", "line_T24", expT, b365=dict(ycol="dline_b365", ctrl="b365_t24_line"))

    # T-b 显著特征的描述性 ROI / CLV
    sig = [f for f, r in resTb.items() if r.get("n") and r["q"] <= Q]
    print("\n" + "=" * 12, "T-b 通过 BH 的特征:T-24h 下注描述性 ROI / CLV(不进 FDR)", "=" * 12)
    if not sig:
        print("  无特征通过 BH,不做。")
    for f in sig:
        s_coef = sign(resTb[f]["coef"])
        bets = []
        for x in rows:
            v = x.get(f)
            if v is None or x["line_T24"] is None or x["t24_home_w"] is None:
                continue
            side = sign(v) * s_coef
            if side == 0:
                continue
            raw = x["margin_T24"] + x["line_T24"]
            st = settle_home(int(round(raw)), x["line_T24"]) * side
            w_t24 = x["t24_home_w"] if side > 0 else x["t24_away_w"]
            w_close = x["close_home_w"] if side > 0 else x["close_away_w"]
            st_close = settle_home(int(round(raw)), x["closing_line"]) * side
            clv_line = side * x["dline"]
            same = abs(x["dline"]) < 1e-9
            p_t24 = implied_two(x["t24_home_w"], x["t24_away_w"]); p_close = implied_two(x["close_home_w"], x["close_away_w"])
            if side < 0:
                p_t24, p_close = 1 - p_t24, 1 - p_close
            bets.append(dict(st=st, w=w_t24, st_close=st_close, w_close=w_close, clv_line=clv_line, clv_p=(p_close - p_t24) if same else None))
        def roi(items, key_st, key_w):
            return sum((b[key_st] * b[key_w]) if b[key_st] > 0 else b[key_st] for b in items) / len(items) * 100
        n = len(bets)
        wins = sum(1 for b in bets if b["st"] > 0); losses = sum(1 for b in bets if b["st"] < 0)
        r1 = roi(bets, "st", "w"); r2 = roi(bets, "st_close", "w_close")
        avg_w = sum(b["w"] for b in bets) / n; avg_wc = sum(b["w_close"] for b in bets) / n
        clv_l = mean_ci95([b["clv_line"] for b in bets]); clv_p = mean_ci95([b["clv_p"] for b in bets if b["clv_p"] is not None])
        print(f"  {f}: 下注 N={n} 赢/输(含半)={wins}/{losses} 命中率(赢/(赢+输))={pct(wins, wins+losses):.1f}%")
        print(f"     (i) T-24h 线+T-24h 水位:ROI={r1:+.2f}%  平均水位={avg_w:.3f} 保本胜率={100/(1+avg_w):.1f}%")
        print(f"     (ii) 收盘线+Crown 实际收盘水位:ROI={r2:+.2f}%  平均水位={avg_wc:.3f} 保本胜率={100/(1+avg_wc):.1f}%")
        print(f"     CLV_line mean={clv_l[0]:+.3f}档 [{clv_l[1]:+.3f},{clv_l[2]:+.3f}] N={clv_l[3]};CLV_p(同线) mean={clv_p[0]:+.4f} [{clv_p[1]:+.4f},{clv_p[2]:+.4f}] N={clv_p[3]}")

    (out_dir / "phase4_results.json").write_text(json.dumps(dict(summary=summary, M=resM, Ta=resTa, Tb=resTb), ensure_ascii=False, indent=1, default=str))
    cols = ["match_id"] + [k for k in next(iter(mf.values())).keys()]
    with open(out_dir / "phase4_market_features.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(cols)
        for m in ms_sorted:
            w.writerow([m["Match_ID"]] + [mf[m["Match_ID"]].get(c) for c in cols[1:]])
    print(f"\n[written] {out_dir/'phase4_results.json'}, {out_dir/'phase4_market_features.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
