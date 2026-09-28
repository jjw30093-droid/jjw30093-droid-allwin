#!/usr/bin/env python3
"""Phase 2 — 字段核验(PREREG §6),只读。

范围(PREREG 审定):NULL 三分法、球员汇总 vs 队级交叉核对、C4 间接验证、C5 字段判定、
market_value 时间语义、split-half 持续性、截断测试、按实际 N 的功效表。
**不计算任何特征与 margin / settle / 收盘线的关系。**

用法:
    nice -n 19 python3 research/ah_signals/phase2_verify.py \
        --data-dir /opt/allwin/shared/data --out-dir ~/research_out/ah_signals
产物(仓库外):phase2_features.csv(每场一行,不含目标列)、phase2_summary.json。
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    LEAGUE_NAME, LEAGUES, SEASONS, load_matches, load_xref, open_ro, parse_round, pearson,
    quantiles, seeded_sample,
)
from features import (  # noqa: E402
    ALL_FEATURES, C_LINEUP_FEATURES, K, MAIN_WINDOW, STAT_FEATURES, TEAM_KEYS_USED,
    build_features, load_lineups, load_player_rows, load_team_stats, parse_utc,
)

NULL_MAX = 0.05
TRUNC_TIME_BUDGET_S = 30 * 60


def pct(a, b):
    return 100.0 * a / b if b else math.nan


def eligible(m: dict) -> bool:
    """冷启动可用集合:25/26 Match_Round ≤3 排除;n_prior<3 已在特征里为 None。"""
    r = parse_round(m["Match_Round"])
    return not (m["Season"] == "2025/2026" and r is not None and r <= 3)


def n_avail_table(matches: list[dict], feats: dict, verbose: bool) -> dict:
    elig = [m for m in matches if eligible(m)]
    if verbose:
        print(f"  特征检验候选场次(排除 25/26 前 3 轮)={len(elig)}  "
              f"25/26={sum(1 for m in elig if m['Season']=='2025/2026')} 26/27={sum(1 for m in elig if m['Season']=='2026/2027')}")
        print(f"\n  各特征主版本 diff 的可用 N 与缺失率(候选场次内):")
        print(f"  {'feature':28s} {'N可用':>6s} {'缺失%':>6s}  {'25/26 N':>8s} {'26/27 N':>8s}   adj N   fh N")
    n_avail = {}
    for f in ALL_FEATURES:
        key = f"diff__{f}__w{MAIN_WINDOW}" if f in STAT_FEATURES else f"diff__{f}"
        av = [m for m in elig if feats[m["Match_ID"]].get(key) is not None]
        n25 = sum(1 for m in av if m["Season"] == "2025/2026")
        n_adj = sum(1 for m in elig if feats[m["Match_ID"]].get(f"diff__{f}__adj") is not None) if f in STAT_FEATURES else None
        n_fh = sum(1 for m in elig if feats[m["Match_ID"]].get(f"diff__{f}__fh") is not None) if f"diff__{f}__fh" in feats[elig[0]["Match_ID"]] else None
        n_avail[f] = len(av)
        if verbose:
            print(f"  {f:28s} {len(av):6d} {pct(len(elig)-len(av), len(elig)):6.1f}  {n25:8d} {len(av)-n25:8d}   "
                  f"{'-' if n_adj is None else n_adj:>5} {'-' if n_fh is None else n_fh:>5}")
    return n_avail


def finish(args, out_dir: Path, matches, team_stats, lineups, player_rows, c5_mode, feats, t_full,
           n_avail, removed, summary) -> int:
    """§6.5 截断测试(按比赛日分组)→ §5 功效表 → 写出。"""
    print("\n" + "=" * 12, "§6.5 截断测试(按比赛日分组:删除开球日 > 该日的全部比赛后重算,逐字段浮点严格相等)", "=" * 12)
    ms_sorted = sorted(matches, key=lambda m: (m["kickoff_at_utc"], m["Match_ID"]))
    days = sorted({m["kickoff_at_utc"][:10] for m in ms_sorted})
    est_full = t_full * len(days) / 2.0
    tier = args.trunc_tier
    if tier == "auto":
        tier = "full" if est_full <= TRUNC_TIME_BUDGET_S else "sample"
    if tier == "full":
        target_mids = {m["Match_ID"] for m in ms_sorted}
    else:
        target_mids = {m["Match_ID"] for m in seeded_sample(ms_sorted, 300)}
        for m in ms_sorted:
            r = parse_round(m["Match_Round"])
            if (m["Season"] == "2026/2027" and r is not None and r <= 3) or (m["Season"] == "2025/2026" and r == 4):
                target_mids.add(m["Match_ID"])
    target_days = [d for d in days if any(m["Match_ID"] in target_mids and m["kickoff_at_utc"][:10] == d for m in ms_sorted)]
    print(f"  全量单次 {t_full:.1f}s;比赛日={len(days)} → 全量截断预计 {est_full/60:.0f} 分钟;采用档位={tier};"
          f"测试场次={len(target_mids)} 分布在 {len(target_days)} 个比赛日")
    t1 = time.perf_counter()
    n_fail = n_fields = n_tested = 0
    fails = []
    next_pct = 10
    # 数据已全部在内存(team_stats/lineups/player_rows),每次只按 mid 过滤,不重新读库/解析
    for i, d in enumerate(target_days):
        sub = [x for x in ms_sorted if x["kickoff_at_utc"][:10] <= d]
        sub_mids = {x["Match_ID"] for x in sub}
        ts_sub = {k: v for k, v in team_stats.items() if k[0] in sub_mids}
        lu_sub = {k: v for k, v in lineups.items() if k[0] in sub_mids}
        pr_sub = [r0 for r0 in player_rows if r0["Match_ID"] in sub_mids]
        f_sub_all = build_features(sub, ts_sub, lu_sub, pr_sub, c5_mode=c5_mode)
        for m in sub:
            if m["kickoff_at_utc"][:10] != d or m["Match_ID"] not in target_mids:
                continue
            n_tested += 1
            f_full, f_sub = feats[m["Match_ID"]], f_sub_all[m["Match_ID"]]
            for k in f_full:
                n_fields += 1
                a, b = f_full[k], f_sub.get(k, "<absent>")
                if not (a == b or (a is None and b is None)):
                    n_fail += 1
                    if len(fails) < 20:
                        fails.append((m["Match_ID"], k, a, b))
        done = 100.0 * (i + 1) / len(target_days)
        if done >= next_pct or i + 1 == len(target_days):
            print(f"    进度 {done:5.1f}%  比赛日 {i+1}/{len(target_days)}  已测场次={n_tested}  不等字段累计={n_fail}  "
                  f"已用时 {time.perf_counter()-t1:.0f}s", flush=True)
            while next_pct <= done:
                next_pct += 10
    print(f"  结果:测试场次={n_tested} 比对字段总数={n_fields} 不等={n_fail} 用时 {time.perf_counter()-t1:.0f}s")
    for mid, k, a, b in fails:
        print(f"    !! match {mid} {k}: full={a} truncated={b}")
    summary["truncation"] = dict(tier=tier, n=n_tested, days=len(target_days), fields=n_fields, fail=n_fail)
    if n_fail:
        print("  !! 截断测试失败,停止。")
        return 2

    # ================================================================ 功效(实际 N)
    print("\n" + "=" * 12, "§5 功效(实际 N;margin SD 只从 phase1_target.csv 的 margin 列读取,不与特征关联)", "=" * 12)
    margins = []
    p1 = out_dir / "phase1_target.csv"
    if p1.exists():
        with open(p1, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["margin"]:
                    margins.append(float(row["margin"]))
    sd = math.sqrt(sum((x - sum(margins)/len(margins))**2 for x in margins)/(len(margins)-1)) if len(margins) > 1 else math.nan
    n_c = len([f for f in ALL_FEATURES if f.startswith("C") and f not in removed])
    print(f"  margin SD={sd:.3f} (N={len(margins)});C 族剩余特征数={n_c};最严格 BH 档 α≈0.10/{n_c}={0.10/n_c:.4f}")
    z80 = 0.8416
    from statistics import NormalDist
    for f in ALL_FEATURES:
        n = n_avail[f]
        if n < 2:
            continue
        za = NormalDist().inv_cdf(1 - 0.05 / 2)
        zb = NormalDist().inv_cdf(1 - (0.10 / max(n_c, 1)) / 2)
        r05 = (za + z80) / math.sqrt(n)
        rbh = (zb + z80) / math.sqrt(n)
        print(f"  {f:28s} N={n:5d}  r_min(α=.05)={r05:.3f} (≈{r05*sd:.3f} 球/SD)  r_min(BH最严)={rbh:.3f} (≈{rbh*sd:.3f} 球/SD)")

    # ================================================================ 写出
    cols = ["match_id", "season", "league_id", "round"] + list(next(iter(feats.values())).keys())
    with open(out_dir / "phase2_features.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for m in ms_sorted:
            row = feats[m["Match_ID"]]
            w.writerow([m["Match_ID"], m["Season"], m["League_ID"], parse_round(m["Match_Round"])] + [row.get(c) for c in cols[4:]])
    (out_dir / "phase2_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    print(f"\n[written] {out_dir/'phase2_features.csv'} ({len(feats)} 行, {len(cols)} 列;不含目标列)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--trunc-tier", choices=("auto", "full", "sample"), default="auto")
    ap.add_argument("--only", choices=("truncation",), default=None,
                    help="只跑截断测试+功效表+写出(复用 part1 已完成的子任务结论)")
    ap.add_argument("--c5-mode", choices=("player_total", "team_proxy"), default=None,
                    help="--only truncation 时必须给出(取 part1 的 C5 判定)")
    ap.add_argument("--removed", nargs="*", default=[],
                    help="--only truncation 时给出 part1 已判移出的 C 族特征名")
    args = ap.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {}

    core = open_ro(data_dir / "allwin.db")
    odds = open_ro(data_dir / "odds.db")
    matches = load_matches(core)
    ok_xref, _ = load_xref(odds, {m["Match_ID"] for m in matches})
    matches = [m for m in matches if m["Match_ID"] in ok_xref]
    mids = {m["Match_ID"] for m in matches}
    minfo = {m["Match_ID"]: m for m in matches}
    print(f"研究样本比赛数(xref ok)={len(matches)}")

    team_stats = load_team_stats(core, mids)
    lineups = load_lineups(core, mids)
    player_rows = load_player_rows(core, mids)
    print(f"fact_team_match_stats 行(All/FirstHalf)={len(team_stats)}  lineup 队场={len(lineups)}  "
          f"player 行={len(player_rows)}")

    if args.only == "truncation":
        if not args.c5_mode:
            print("--only truncation 需要 --c5-mode"); return 1
        c5_mode = args.c5_mode
        print(f"\n[only=truncation] 复用 part1 结论:c5_mode={c5_mode} removed={args.removed}")
        t0 = time.perf_counter()
        feats = build_features(matches, team_stats, lineups, player_rows, c5_mode=c5_mode)
        t_full = time.perf_counter() - t0
        print(f"  全量构造耗时 {t_full:.1f}s,比赛数={len(feats)}")
        n_avail = n_avail_table(matches, feats, verbose=False)
        removed = {r: "part1 判定" for r in args.removed}
        summary.update(dict(c5_mode=c5_mode, removed=removed, n_avail=n_avail, only="truncation"))
        return finish(args, out_dir, matches, team_stats, lineups, player_rows, c5_mode, feats, t_full,
                      n_avail, removed, summary)

    # ================================================================ 6.1 NULL 三分法
    print("\n" + "=" * 12, "§6.1 NULL 三分法(队级 extra_json;a=key缺失 b=值null c=值0;分母=队场行数)", "=" * 12)
    rows_by = defaultdict(list)  # (period, season, league) -> [raw]
    goals_null = Counter()
    for (mid, tid, period), d in team_stats.items():
        m = minfo[mid]
        rows_by[(period, m["Season"], m["League_ID"])].append(d["raw"])
        if d["goals"] is None:
            goals_null[(period, m["Season"])] += 1
    # 队场覆盖:每场应有 2 行
    for period in ("All", "FirstHalf"):
        for season in SEASONS:
            expect = 2 * sum(1 for m in matches if m["Season"] == season)
            have = sum(len(v) for (p, s, l), v in rows_by.items() if p == period and s == season)
            print(f"  [{period}] {season}: 队场行数 {have}/{expect}  Goals NULL={goals_null[(period, season)]}")
    null_rate_all = {}   # (key, period) -> 全样本缺失率(a+b)
    null_rate_league = {}  # (key, period, league) -> 缺失率
    for period in ("All", "FirstHalf"):
        print(f"\n  --- Period={period} ---")
        print(f"  {'key':32s} " + " ".join(f"{LEAGUE_NAME[l]:>16s}" for l in LEAGUES) + "   全样本 a/b/c (缺失%)")
        for key in TEAM_KEYS_USED:
            cells = []
            tot = Counter()
            n_tot = 0
            for l in LEAGUES:
                c = Counter()
                n = 0
                for season in SEASONS:
                    for raw in rows_by.get((period, season, l), []):
                        n += 1
                        if key not in raw:
                            c["a"] += 1
                        elif raw[key] is None:
                            c["b"] += 1
                        elif raw[key] == 0:
                            c["c"] += 1
                tot.update(c)
                n_tot += n
                miss = pct(c["a"] + c["b"], n)
                null_rate_league[(key, period, l)] = miss / 100.0
                cells.append(f"{c['a']}/{c['b']}/{c['c']}({miss:4.1f}%)".rjust(16))
            miss_all = pct(tot["a"] + tot["b"], n_tot)
            null_rate_all[(key, period)] = miss_all / 100.0
            flag = "  !!>5%" if miss_all > 5 else ""
            print(f"  {key:32s} " + " ".join(cells) + f"   {tot['a']}/{tot['b']}/{tot['c']} ({miss_all:.1f}%){flag}")
    summary["null_rate_all"] = {f"{k}|{p}": v for (k, p), v in null_rate_all.items()}

    print("\n  --- lineup / player 字段 NULL ---")
    for season in SEASONS:
        smids = {m["Match_ID"] for m in matches if m["Season"] == season}
        starters = [p for (mid, tid), ps in lineups.items() if mid in smids for p in ps if p["is_starter"]]
        n_st = len(starters)
        r_null = sum(1 for p in starters if p["rating"] is None)
        mv_null = sum(1 for p in starters if p["market_value"] is None)
        per_team = Counter((mid, tid) for (mid, tid), ps in lineups.items() if mid in smids
                           for p in ps if p["is_starter"])
        not11 = sum(1 for v in per_team.values() if v != 11)
        print(f"  {season} 首发行={n_st} (队场首发≠11 人的队场数={not11}/{len(per_team)})  rating NULL={r_null} "
              f"({pct(r_null, n_st):.2f}%)  market_value NULL={mv_null} ({pct(mv_null, n_st):.2f}%)")
        prs = [r for r in player_rows if r["Match_ID"] in smids]
        n_pr = len(prs)
        for col in ("minutes_played", "passes_into_final_third", "accurate_passes_total", "accurate_crosses_total"):
            nn = sum(1 for r in prs if r[col] is None)
            print(f"     player.{col}: NULL {nn}/{n_pr} ({pct(nn, n_pr):.1f}%)")
        played = [r for r in prs if r["minutes_played"] is not None and r["minutes_played"] > 0]
        for col in ("passes_into_final_third", "accurate_passes_total", "accurate_crosses_total"):
            nn = sum(1 for r in played if r[col] is None)
            print(f"     player.{col} (仅 minutes>0 行 {len(played)}): NULL {nn} ({pct(nn, len(played)):.1f}%)")
        # 队场粒度:该队该场所有球员 accurate_crosses_total 全 NULL
        by_tm = defaultdict(list)
        for r in played:
            by_tm[(r["Match_ID"], r["Team_ID"])].append(r)
        all_null_ct = sum(1 for rs in by_tm.values() if all(r["accurate_crosses_total"] is None for r in rs))
        all_null_pt = sum(1 for rs in by_tm.values() if all(r["accurate_passes_total"] is None for r in rs))
        all_null_ft = sum(1 for rs in by_tm.values() if all(r["passes_into_final_third"] is None for r in rs))
        print(f"     队场粒度(出场球员全 NULL 的队场数/总队场 {len(by_tm)}): accurate_crosses_total={all_null_ct} "
              f"accurate_passes_total={all_null_pt} passes_into_final_third={all_null_ft}")
        summary[f"crosses_total_all_null|{season}"] = [all_null_ct, len(by_tm)]

    # ================================================================ 6.2 交叉核对
    print("\n" + "=" * 12, "§6.2 球员汇总 vs 队级(NULL 视作 0 求和)", "=" * 12)
    by_tm_all = defaultdict(list)
    for r in player_rows:
        by_tm_all[(r["Match_ID"], r["Team_ID"])].append(r)
    checks = {
        "expected_goals": (K["xg"], "expected_goals"),
        "accurate_passes": (K["acc_passes"], "accurate_passes"),
        "goals": (None, "goals"),
        "C4:accurate_passes_total_vs_team_passes": (K["passes"], "accurate_passes_total"),
    }
    c4_bad_share = {}
    for name, (team_key, pcol) in checks.items():
        rel_errs, n_cmp, n_bad10, n_bad2, n_missing = [], 0, 0, 0, 0
        for (mid, tid), rs in by_tm_all.items():
            ts = team_stats.get((mid, tid, "All"))
            if ts is None:
                continue
            tv = ts["goals"] if team_key is None else (ts["raw"].get(team_key) if team_key in ts["raw"] else None)
            if not isinstance(tv, (int, float)) or tv is None:
                n_missing += 1
                continue
            psum = sum(float(r[pcol]) for r in rs if r[pcol] is not None)
            n_cmp += 1
            rel = abs(psum - float(tv)) / float(tv) if tv else (0.0 if psum == 0 else math.inf)
            rel_errs.append(rel)
            if rel > 0.10:
                n_bad10 += 1
            if rel > 0.02:
                n_bad2 += 1
        q = quantiles(rel_errs, probs=(0.5, 0.9, 0.99, 1.0))
        print(f"  {name}: 可比队场={n_cmp} (队级值缺失 {n_missing})  相对误差 p50/p90/p99/max="
              f"{q[0]:.4f}/{q[1]:.4f}/{q[2]:.4f}/{q[3]:.4f}  |err|>10%: {n_bad10} ({pct(n_bad10, n_cmp):.2f}%)"
              f"  |err|>2%: {n_bad2} ({pct(n_bad2, n_cmp):.2f}%)")
        summary[f"xcheck|{name}"] = dict(n=n_cmp, bad10=n_bad10, bad2=n_bad2, q=q)
        if name.startswith("C4"):
            c4_bad_share["bad2"] = n_bad2
            c4_bad_share["n"] = n_cmp
    mins_sum = []
    for (mid, tid), rs in by_tm_all.items():
        mins_sum.append(sum(float(r["minutes_played"]) for r in rs if r["minutes_played"] is not None))
    q = quantiles(mins_sum, probs=(0.0, 0.01, 0.05, 0.5, 0.95, 0.99, 1.0))
    print(f"  每队每场 Σminutes_played 分布(队场数={len(mins_sum)}) p0/p1/p5/p50/p95/p99/p100 = "
          + " / ".join(f"{v:.0f}" for v in q) + f"   <900 的队场数={sum(1 for v in mins_sum if v < 900)}")
    summary["minutes_sum_q"] = q

    # C5 模式判定:accurate_crosses_total 队场粒度全 NULL 占比 ≤5% 且行级 NULL 率满足才用球员总数
    ct_all_null = sum(summary[f"crosses_total_all_null|{s}"][0] for s in SEASONS)
    ct_n = sum(summary[f"crosses_total_all_null|{s}"][1] for s in SEASONS)
    c5_mode = "player_total" if pct(ct_all_null, ct_n) <= 5 else "team_proxy"
    print(f"\n  C5 判定:球员表存在传中总数列 accurate_crosses_total(迁移 0022);队场粒度全 NULL "
          f"{ct_all_null}/{ct_n} ({pct(ct_all_null, ct_n):.1f}%) → c5_mode={c5_mode}"
          + ("" if c5_mode == "player_total" else "(列存在但覆盖不足,按 PREREG 回退队级代理,交站长确认)"))
    summary["c5_mode"] = c5_mode

    # ================================================================ 6.6 market_value 时间语义
    print("\n" + "=" * 12, "§6.6 fact_match_lineup.market_value 时间语义", "=" * 12)
    mv_by_player = defaultdict(lambda: defaultdict(set))  # pid -> season -> {(date, mv)}
    for (mid, tid), ps in lineups.items():
        m = minfo[mid]
        for p in ps:
            if p["market_value"] is not None:
                mv_by_player[str(p["Player_ID"])][m["Season"]].add((m["kickoff_at_utc"][:10], int(p["market_value"])))
    for season in SEASONS:
        const = varied = 0
        examples = []
        for pid, by_s in mv_by_player.items():
            obs = by_s.get(season)
            if not obs or len({d for d, _ in obs}) < 2:
                continue
            vals = {v for _, v in obs}
            if len(vals) == 1:
                const += 1
            else:
                varied += 1
                if len(examples) < 10:
                    examples.append((pid, sorted(obs)))
        print(f"  {season}: 有 ≥2 个比赛日记录的球员={const + varied}  赛季内恒定={const}  变化={varied} "
              f"({pct(varied, const + varied):.1f}%)")
        for pid, obs in examples:
            # 压缩为按值分段
            segs = []
            for d, v in obs:
                if not segs or segs[-1][1] != v:
                    segs.append([d, v, d])
                else:
                    segs[-1][2] = d
            print(f"     player {pid}: " + " → ".join(f"{v}€ [{a}..{b}]" for a, v, b in segs))
        summary[f"mv|{season}"] = dict(const=const, varied=varied)
    both = same = 0
    for pid, by_s in mv_by_player.items():
        if "2025/2026" in by_s and "2026/2027" in by_s:
            both += 1
            v25 = {v for _, v in by_s["2025/2026"]}
            v26 = {v for _, v in by_s["2026/2027"]}
            if v25 == v26:
                same += 1
    print(f"  两季都有记录的球员={both}  两季取值集合完全相同={same} ({pct(same, both):.1f}%)")
    summary["mv_cross_season"] = dict(both=both, same=same)

    # ================================================================ 特征构造(全量)
    print("\n" + "=" * 12, "特征构造(全量,主窗口 8 / 敏感性 5,10 / 对手调整 / 上半场)", "=" * 12)
    t0 = time.perf_counter()
    feats = build_features(matches, team_stats, lineups, player_rows, c5_mode=c5_mode)
    t_full = time.perf_counter() - t0
    print(f"  全量构造耗时 {t_full:.1f}s,比赛数={len(feats)}")

    n_avail = n_avail_table(matches, feats, verbose=True)
    elig = [m for m in matches if eligible(m)]
    summary["n_avail"] = n_avail
    # C9 缺失明细
    c9_valid = [feats[m["Match_ID"]][f"{side}__C9_valid_players"] for m in elig for side in ("home", "away")]
    c9_valid = [v for v in c9_valid if v is not None]
    print(f"  C9 有效球员数分布(队场,n_prior≥3): " + str(dict(sorted(Counter(c9_valid).items()))))
    # C4 缺失占比(队场,基于 c4_valid)
    print(f"  C4 间接验证:|Σ球员 accurate_passes_total − 队级 passes|>2% 的队场 {c4_bad_share.get('bad2')}/"
          f"{c4_bad_share.get('n')} ({pct(c4_bad_share.get('bad2', 0), c4_bad_share.get('n', 0)):.2f}%)"
          f"{'  !!>5% → 按 §3.5 移出 C4' if pct(c4_bad_share.get('bad2', 0), c4_bad_share.get('n', 0)) > 5 else ''}")

    # 去留:C1/C2/C4/C5/C6/C7 字段缺失率 >5%
    dep = {
        "C1_field_tilt": [K["opp_half"]],
        "C2_ppda": [K["passes"], K["opp_half"], K["tackles"], K["interceptions"], K["fouls"]],
        "C5_cross_share": [K["acc_crosses"], K["acc_passes"]] if c5_mode == "team_proxy" else [],
        "C6_long_share": [K["long_balls"], K["acc_passes"]],
        "C7_duel_rate": [K["gdw"], K["aer"]],
    }
    print("\n  去留规则(只看字段核验):")
    removed = {}
    for f, keys in dep.items():
        bad = [(k, null_rate_all[(k, "All")]) for k in keys if null_rate_all[(k, "All")] > NULL_MAX]
        if bad:
            removed[f] = f"字段缺失率>5%: {bad}"
        print(f"    {f}: 依赖 {keys} → {'移出 ' + removed[f] if f in removed else '保留'}")
    c4_share = pct(c4_bad_share.get("bad2", 0), c4_bad_share.get("n", 0))
    if c4_share > 5:
        removed["C4_final_third_passes"] = f"C4 间接验证缺失占比 {c4_share:.2f}% > 5%"
    print(f"    C4_final_third_passes: {'移出 ' + removed['C4_final_third_passes'] if 'C4_final_third_passes' in removed else '保留'}")
    summary["removed"] = removed
    # 各联赛"覆盖不足"标记
    weak = defaultdict(list)
    for (k, p, l), r in null_rate_league.items():
        if p == "All" and r > NULL_MAX:
            weak[k].append(LEAGUE_NAME[l])
    print(f"  按联赛缺失率>5% 的 key(§4 条件 4 不计该联赛): {dict(weak) if weak else '无'}")

    # C2 × C12 相关(特征之间,允许)
    xs, ys = [], []
    for m in elig:
        a, b = feats[m["Match_ID"]].get(f"diff__C2_ppda__w{MAIN_WINDOW}"), feats[m["Match_ID"]].get(f"diff__C12_possession__w{MAIN_WINDOW}")
        if a is not None and b is not None:
            xs.append(a); ys.append(b)
    r = pearson(xs, ys)
    print(f"  corr(diff C2_ppda w8, diff C12_possession w8) = {r:+.3f} (N={len(xs)})"
          f"{'  → |r|>0.8 标注共线(不移出)' if abs(r) > 0.8 else ''}")
    summary["corr_c2_c12"] = [r, len(xs)]

    # ================================================================ 6.4 split-half
    print("\n" + "=" * 12, "§6.4 split-half 持续性(team-season 内奇/偶轮均值的跨队 Pearson r;只看特征自身)", "=" * 12)
    # 单场值:feats 里的 home__/away__ 是滚动值,这里需要单场值 → 用 build_features 同一个
    # single_match_values 重算;player_agg 按 build 内同样逻辑得到
    from features import single_match_values  # noqa: E402
    single_vals = {}
    pagg = {}
    tmp = defaultdict(lambda: dict(ft=0.0, ap=0.0, ap_any=False, pt=0.0, pt_any=False, ct=0.0, ct_any=False))
    for r0 in player_rows:
        t = tmp[(r0["Match_ID"], r0["Team_ID"])]
        if r0["passes_into_final_third"] is not None:
            t["ft"] += float(r0["passes_into_final_third"])
        if r0["accurate_passes"] is not None:
            t["ap"] += float(r0["accurate_passes"]); t["ap_any"] = True
        if r0["accurate_passes_total"] is not None:
            t["pt"] += float(r0["accurate_passes_total"]); t["pt_any"] = True
        if r0["accurate_crosses_total"] is not None:
            t["ct"] += float(r0["accurate_crosses_total"]); t["ct_any"] = True
    for key, t in tmp.items():
        tp = team_stats.get((key[0], key[1], "All"), {}).get("raw", {}).get(K["acc_passes"])
        valid = False
        if t["ap_any"] and isinstance(tp, (int, float)) and tp:
            valid = abs(t["ap"] - tp) / tp <= 0.02
        pagg[key] = dict(final_third_passes=t["ft"],
                         passes_total=t["pt"] if t["pt_any"] else None,
                         crosses_total=t["ct"] if t["ct_any"] else None, c4_valid=valid)
    for m in matches:
        mid, h, a = m["Match_ID"], m["Home_Team_ID"], m["Away_Team_ID"]
        th, ta = team_stats.get((mid, h, "All")), team_stats.get((mid, a, "All"))
        if th is None or ta is None:
            continue
        single_vals[(mid, h)] = single_match_values(th["goals"], th["raw"], ta["goals"], ta["raw"], pagg.get((mid, h)), c5_mode)
        single_vals[(mid, a)] = single_match_values(ta["goals"], ta["raw"], th["goals"], th["raw"], pagg.get((mid, a)), c5_mode)

    def split_half(getter, season):
        odd, even = defaultdict(list), defaultdict(list)
        for m in matches:
            if m["Season"] != season:
                continue
            r = parse_round(m["Match_Round"])
            if r is None:
                continue
            for side, tid in (("home", m["Home_Team_ID"]), ("away", m["Away_Team_ID"])):
                v = getter(m, side, tid)
                if v is None:
                    continue
                (odd if r % 2 else even)[tid].append(v)
        xs, ys = [], []
        for tid in odd:
            if tid in even and len(odd[tid]) >= 5 and len(even[tid]) >= 5:
                xs.append(sum(odd[tid]) / len(odd[tid])); ys.append(sum(even[tid]) / len(even[tid]))
        return pearson(xs, ys), len(xs)

    print(f"  {'feature':28s} {'25/26 r':>8s} {'N队':>4s}   {'26/27 r':>8s} {'N队':>4s}  备注")
    sh = {}
    for f in ALL_FEATURES:
        if f in STAT_FEATURES:
            getter = lambda m, side, tid, f=f: (single_vals.get((m["Match_ID"], tid)) or {}).get(f)  # noqa: E731
            kind = "单场值"
        else:
            getter = lambda m, side, tid, f=f: feats[m["Match_ID"]].get(f"{side}__{f}")  # noqa: E731
            kind = "赛前滚动值"
        r25, n25 = split_half(getter, "2025/2026")
        r26, n26 = split_half(getter, "2026/2027")
        note = kind + ("; 低持续性(r<0.3)" if not math.isnan(r25) and r25 < 0.3 else "")
        note += "; 26/27 样本不足,不可靠" if n26 < 100 else ""
        print(f"  {f:28s} {r25:+8.3f} {n25:4d}   {r26:+8.3f} {n26:4d}  {note}")
        sh[f] = dict(r25=r25, n25=n25, r26=r26, n26=n26)
    summary["split_half"] = sh

    return finish(args, out_dir, matches, team_stats, lineups, player_rows, c5_mode, feats, t_full,
                  n_avail, removed, summary)


if __name__ == "__main__":
    sys.exit(main())
