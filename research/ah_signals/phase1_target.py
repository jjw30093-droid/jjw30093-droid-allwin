#!/usr/bin/env python3
"""Phase 1 — 目标表 + 市场基线(只读),含任务书 Phase 0 确认后的补充项 3/4/6/7。

用法(服务器上,仓库根目录):
    nice -n 19 python3 research/ah_signals/phase1_target.py \
        --data-dir /opt/allwin/shared/data --out-dir ~/research_out/ah_signals

产物(仓库外):<out-dir>/phase1_target.csv(每场一行)、phase1_summary.json。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    COMPANIES, LEAGUE_NAME, LEAGUES, PRIMARY, PROMOTED_2526, RECO_TO_NUM, SEASONS,
    load_ah_timelines, load_matches, load_xref, mean_ci95, open_ro, parse_round,
    parse_utc, pearson, pick_close_last, pick_close_main, pick_open, pick_t24,
    quantiles, seeded_sample, settle_home,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from backend.commands.reco_settlement_math import resolve_leg_result  # noqa: E402


def tick(x: float) -> int:
    return round(x / 0.25)


def depth_bucket(line: float) -> str:
    a = abs(line)
    if a < 0.125:
        return "0(平手)"
    if a <= 0.5:
        return "0.25~0.5"
    if a <= 1.0:
        return "0.75~1.0"
    if a <= 1.5:
        return "1.25~1.5"
    return "≥1.75"


def direction_bucket(line: float) -> str:
    if line > 1e-9:
        return "主让"
    if line < -1e-9:
        return "客让"
    return "平手"


def group_stats(rows: list[dict]) -> dict:
    n = len(rows)
    margins = [r["margin"] for r in rows]
    m, lo, hi, _ = mean_ci95(margins)
    settles = [r["settle"] for r in rows]
    pure_win = sum(1 for s in settles if s == 1.0)
    decided = sum(1 for s in settles if s != 0.0)
    win_eq = sum(1.0 if s == 1.0 else 0.5 if s == 0.5 else 0.0 for s in settles)
    roi_home = 0.0
    roi_away = 0.0
    for r in rows:
        s = r["settle"]
        hw, aw = r["crown_close_home"], r["crown_close_away"]
        if hw is None or aw is None:
            continue
        roi_home += (s * hw) if s > 0 else s          # s=+1/+0.5 按水位赔付;负值按本金亏
        sa = -s
        roi_away += (sa * aw) if sa > 0 else sa
    return dict(
        n=n, margin_mean=m, ci_lo=lo, ci_hi=hi,
        pure_win_rate=100.0 * pure_win / n if n else math.nan,
        equiv_win_rate=100.0 * win_eq / decided if decided else math.nan,
        roi_home=100.0 * roi_home / n if n else math.nan,
        roi_away=100.0 * roi_away / n if n else math.nan,
        flag="样本不足,不可靠" if n < 100 else "",
    )


def print_group_table(title: str, groups: dict[str, list[dict]]):
    print(f"\n--- {title} ---")
    print(f"{'组':22s} {'N':>5s} {'mean margin':>12s} {'95%CI':>18s} {'纯赢%':>6s} "
          f"{'等效赢%':>7s} {'ROI主%':>7s} {'ROI客%':>7s}  备注")
    for name, rows in groups.items():
        g = group_stats(rows)
        ci = f"[{g['ci_lo']:+.3f},{g['ci_hi']:+.3f}]" if not math.isnan(g["ci_lo"]) else "n/a"
        print(f"{name:22s} {g['n']:5d} {g['margin_mean']:+12.3f} {ci:>18s} {g['pure_win_rate']:6.1f} "
              f"{g['equiv_win_rate']:7.1f} {g['roi_home']:+7.2f} {g['roi_away']:+7.2f}  {g['flag']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    core = open_ro(data_dir / "allwin.db")
    odds = open_ro(data_dir / "odds.db")
    summary: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}

    matches = load_matches(core)
    mids = {m["Match_ID"] for m in matches}
    ok_xref, needs_review = load_xref(odds, mids)
    print(f"目标完赛比赛(精确开球): {len(matches)};xref auto_ok/confirmed: {len(ok_xref)};"
          f"needs_review: {len(needs_review)}")

    # ================================================== 补充项 7:needs_review 明细
    print("\n" + "=" * 12, "补充项7:needs_review 明细(全研究剔除)", "=" * 12)
    minfo = {m["Match_ID"]: m for m in matches}
    for x in needs_review:
        m = minfo[int(x["fotmob_match_id"])]
        print(f"  match {m['Match_ID']} {m['Season']} {LEAGUE_NAME[m['League_ID']]} "
              f"{m['Home_Team_Name']} vs {m['Away_Team_Name']} {m['home_score']}-{m['away_score']} "
              f"kickoff={m['kickoff_at_utc']}")
        print(f"     xref: provider_match_id={x['provider_match_id']} inverted={x['home_away_inverted']} "
              f"confidence={x['confidence']} verified={x['verified']} method={x['method']} "
              f"kickoff_diff_seconds={x['kickoff_diff_seconds']} review_status={x['review_status']} "
              f"created_at={x['created_at']}")
    excluded_nr = len(needs_review)
    summary["needs_review_excluded"] = excluded_nr
    print(f"  → 剔除 {excluded_nr} 场(无法在本研究内确认映射正确性)")

    matches = [m for m in matches if m["Match_ID"] in ok_xref]
    pmid_to_mid = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items()}
    tl = load_ah_timelines(odds, pmid_to_mid, market="ah")

    # ================================================== 补充项 6:升班马
    print("\n" + "=" * 12, "补充项6:升班马", "=" * 12)
    teams = defaultdict(lambda: defaultdict(dict))  # (season, league) -> {tid: name}
    for m in matches:
        teams[(m["Season"], m["League_ID"])][m["Home_Team_ID"]] = m["Home_Team_Name"]
        teams[(m["Season"], m["League_ID"])][m["Away_Team_ID"]] = m["Away_Team_Name"]
    zh = {r[0]: r[1] for r in core.execute("SELECT Team_ID, name_zh FROM dim_team_i18n")}
    promoted: dict[tuple[str, int], set[int]] = {}
    print("  25/26(常量按库内英文名匹配):")
    for lid, names in PROMOTED_2526.items():
        pool = teams[("2025/2026", lid)]
        found = set()
        for name in names:
            hit = [tid for tid, n in pool.items() if n.lower() == name.lower()]
            if not hit:
                hit = [tid for tid, n in pool.items()
                       if name.lower() in n.lower() or n.lower() in name.lower()]
            if len(hit) == 1:
                found.add(hit[0])
                print(f"    {LEAGUE_NAME[lid]} {name!r} → {hit[0]} {pool[hit[0]]!r} ({zh.get(hit[0])})")
            else:
                print(f"    {LEAGUE_NAME[lid]} {name!r} → 未匹配(候选={[pool[t] for t in hit]})  !! 请核对")
        promoted[("2025/2026", lid)] = found
    print("  26/27(样本内推导:26/27 球队集合 − 25/26 球队集合):")
    for lid in LEAGUES:
        s26 = set(teams[("2026/2027", lid)])
        s25 = set(teams[("2025/2026", lid)])
        new = s26 - s25
        promoted[("2026/2027", lid)] = new
        print(f"    {LEAGUE_NAME[lid]}: {len(s26)} 队(已出现在已完赛比赛中) − 25/26 {len(s25)} 队 → 升班马 "
              f"{[(t, teams[('2026/2027', lid)][t], zh.get(t)) for t in sorted(new)]}")
        if len(s26) < len(s25):
            print(f"      (注:26/27 已完赛场次尚未覆盖全部球队时,名单可能不完整)")
    summary["promoted"] = {f"{s}|{l}": sorted(v) for (s, l), v in promoted.items()}

    # ================================================== 补充项 3:initial 语义 / 首条 snap / T-24h
    print("\n" + "=" * 12, "补充项3:Crown 开盘 initial 语义、首条 snap 距开球、T-24h 缺失", "=" * 12)
    per_season = defaultdict(lambda: dict(n=0, init_ne_first=0, init_missing=0, first_hours=[],
                                          t24_missing=0))
    rows_out: list[dict] = []
    for m in matches:
        mid = m["Match_ID"]
        ko = parse_utc(m["kickoff_at_utc"])
        season = m["Season"]
        t_crown = tl.get((mid, PRIMARY), [])
        ini, first = pick_open(t_crown)
        ps = per_season[season]
        if first is not None:
            ps["n"] += 1
            ps["first_hours"].append((ko - parse_utc(first["observed_at"])).total_seconds() / 3600.0)
            if ini is None:
                ps["init_missing"] += 1
            elif abs(ini["line"] - first["line"]) > 1e-9:
                ps["init_ne_first"] += 1
            if pick_t24(t_crown, ko) is None:
                ps["t24_missing"] += 1
    for season in SEASONS:
        ps = per_season[season]
        q = quantiles(ps["first_hours"], probs=(0.1, 0.5, 0.9))
        print(f"  {season}: 有 Crown snap 的场次={ps['n']}  initial 缺失={ps['init_missing']}  "
              f"initial.line≠首条snap.line 的场次={ps['init_ne_first']} "
              f"({100.0*ps['init_ne_first']/ps['n']:.1f}%)")
        print(f"     首条 snap 距 kickoff 小时数 p10/p50/p90 = {q[0]:.1f} / {q[1]:.1f} / {q[2]:.1f}")
        print(f"     T-24h 缺失(首条 snap 晚于 kickoff−24h): {ps['t24_missing']} "
              f"({100.0*ps['t24_missing']/ps['n']:.1f}%)")
        summary[f"open|{season}"] = dict(n=ps["n"], init_missing=ps["init_missing"],
                                          init_ne_first=ps["init_ne_first"],
                                          first_hours_p10_p50_p90=q, t24_missing=ps["t24_missing"])
    print("  抽样对比(每季 5 场):initial vs 第一条 snap")
    for season in SEASONS:
        cands = [m for m in matches if m["Season"] == season and tl.get((m["Match_ID"], PRIMARY))]
        for m in seeded_sample(cands, 5, seed=3):
            t_crown = tl[(m["Match_ID"], PRIMARY)]
            ini, first = pick_open(t_crown)
            ko = parse_utc(m["kickoff_at_utc"])
            hrs = (ko - parse_utc(first["observed_at"])).total_seconds() / 3600.0
            print(f"    [{season}] {m['Match_ID']} {m['Home_Team_Name']} vs {m['Away_Team_Name']} kickoff={m['kickoff_at_utc']}")
            print(f"       initial: line={ini['line'] if ini else None} home={ini['home'] if ini else None} "
                  f"away={ini['away'] if ini else None}")
            print(f"       首条snap: at={first['observed_at']} (kickoff−{hrs:.1f}h) shape={first['shape']} "
                  f"line={first['line']} home={first['home']} away={first['away']}  共 {len(t_crown)} 条")

    # ================================================== Phase 1 目标表
    print("\n" + "=" * 12, "Phase 1:目标表构建(主口径 Crown)", "=" * 12)
    close_missing = Counter()
    close_diff = Counter()
    close_n = Counter()
    settle_mismatch = 0
    settle_compared = 0
    for m in matches:
        mid = m["Match_ID"]
        ko = parse_utc(m["kickoff_at_utc"])
        season = m["Season"]
        lid = m["League_ID"]
        row = dict(
            match_id=mid, league_id=lid, league=LEAGUE_NAME[lid], season=season,
            round=parse_round(m["Match_Round"]), round_raw=m["Match_Round"],
            kickoff_at_utc=m["kickoff_at_utc"],
            home_team_id=m["Home_Team_ID"], away_team_id=m["Away_Team_ID"],
            home_team=m["Home_Team_Name"], away_team=m["Away_Team_Name"],
            home_score=m["home_score"], away_score=m["away_score"],
            promoted_home=int(m["Home_Team_ID"] in promoted.get((season, lid), set())),
            promoted_away=int(m["Away_Team_ID"] in promoted.get((season, lid), set())),
        )
        t_crown = tl.get((mid, PRIMARY), [])
        ini, first = pick_open(t_crown)
        row["crown_open_initial_line"] = ini["line"] if ini else None
        row["crown_open_initial_home"] = ini["home"] if ini else None
        row["crown_open_initial_away"] = ini["away"] if ini else None
        row["crown_first_snap_at"] = first["observed_at"] if first else None
        row["crown_first_snap_line"] = first["line"] if first else None
        row["crown_first_snap_home"] = first["home"] if first else None
        row["crown_first_snap_away"] = first["away"] if first else None
        t24 = pick_t24(t_crown, ko)
        row["crown_t24_at"] = t24["observed_at"] if t24 else None
        row["crown_t24_line"] = t24["line"] if t24 else None
        row["crown_t24_home"] = t24["home"] if t24 else None
        row["crown_t24_away"] = t24["away"] if t24 else None
        cm, gap, reason = pick_close_main(t_crown, ko)
        row["crown_close_at"] = cm["observed_at"] if cm else None
        row["crown_close_line"] = cm["line"] if cm else None
        row["crown_close_home"] = cm["home"] if cm else None
        row["crown_close_away"] = cm["away"] if cm else None
        row["crown_close_gap_min"] = round(gap, 1) if gap is not None else None
        row["crown_close_reason"] = reason
        cl = pick_close_last(t_crown, ko)
        row["crown_close_last_at"] = cl["observed_at"] if cl else None
        row["crown_close_last_line"] = cl["line"] if cl else None
        close_n[season] += 1
        if cm is None:
            close_missing[(season, reason)] += 1
        elif cl is not None and abs(cl["line"] - cm["line"]) > 1e-9:
            close_diff[season] += 1
        for ck, prefix in (("Bet365", "bet365"), ("Macauslot", "macau")):
            t_o = tl.get((mid, ck), [])
            c_o, g_o, r_o = pick_close_main(t_o, ko)
            row[f"{prefix}_close_at"] = c_o["observed_at"] if c_o else None
            row[f"{prefix}_close_line"] = c_o["line"] if c_o else None
            row[f"{prefix}_close_home"] = c_o["home"] if c_o else None
            row[f"{prefix}_close_away"] = c_o["away"] if c_o else None
            row[f"{prefix}_close_reason"] = r_o
        if cm is not None:
            raw = m["home_score"] - m["away_score"]
            row["margin"] = raw - cm["line"]
            row["settle"] = settle_home(raw, cm["line"])
            reco = RECO_TO_NUM[resolve_leg_result("ah", cm["line"], "home",
                                                  home_score=m["home_score"], away_score=m["away_score"])]
            row["settle_reco"] = reco
            settle_compared += 1
            if abs(reco - row["settle"]) > 1e-9:
                settle_mismatch += 1
        else:
            row["margin"] = None
            row["settle"] = None
            row["settle_reco"] = None
        rows_out.append(row)

    for season in SEASONS:
        n = close_n[season]
        miss = {r: c for (s, r), c in close_missing.items() if s == season}
        miss_total = sum(miss.values())
        print(f"  {season}: 场次={n}  收盘线(主定义)缺失={miss_total} {miss}  "
              f"主定义 vs 真实最后一条 line 不同={close_diff[season]} "
              f"({100.0*close_diff[season]/(n-miss_total):.1f}% of 有收盘线)")
        summary[f"close|{season}"] = dict(n=n, missing=miss, diff_main_vs_last=close_diff[season])
    for ck, prefix in (("Bet365", "bet365"), ("Macauslot", "macau")):
        for season in SEASONS:
            rs = [r for r in rows_out if r["season"] == season]
            miss = sum(1 for r in rs if r[f"{prefix}_close_line"] is None)
            print(f"  {ck} 收盘线(主定义)缺失 {season}: {miss}/{len(rs)}")

    # ================================================== 补充项 4:结算断言 + 抽样
    print("\n" + "=" * 12, "补充项4:settle 与 reco_settlement_math 逐场比对", "=" * 12)
    print(f"  比对场次={settle_compared}  不一致={settle_mismatch}")
    summary["settle_check"] = dict(compared=settle_compared, mismatch=settle_mismatch)
    print("  随机抽 5 场(seed=20260928):")
    for r in seeded_sample([r for r in rows_out if r["settle"] is not None], 5):
        print(f"    {r['season']} {r['league']} 主={r['home_team']} 客={r['away_team']} "
              f"比分={r['home_score']}-{r['away_score']} line={r['crown_close_line']:+.2f} "
              f"margin={r['margin']:+.2f} settle={r['settle']:+.1f} (reco={r['settle_reco']:+.1f})")

    # ================================================== 健全性检查
    print("\n" + "=" * 12, "Phase 1:健全性检查", "=" * 12)
    valid = [r for r in rows_out if r["margin"] is not None]
    for season in SEASONS + ("ALL",):
        rs = valid if season == "ALL" else [r for r in valid if r["season"] == season]
        margins = [r["margin"] for r in rs]
        m, lo, hi, n = mean_ci95(margins)
        q = quantiles(margins, probs=(0.05, 0.25, 0.5, 0.75, 0.95))
        raw = [r["home_score"] - r["away_score"] for r in rs]
        lines = [r["crown_close_line"] for r in rs]
        print(f"  [{season}] n={n} margin mean={m:+.4f} 95%CI=[{lo:+.4f},{hi:+.4f}] "
              f"p5/p25/p50/p75/p95={q[0]:+.2f}/{q[1]:+.2f}/{q[2]:+.2f}/{q[3]:+.2f}/{q[4]:+.2f}")
        print(f"          corr(收盘线, 净胜球)={pearson(lines, raw):+.4f}  "
              f"corr(收盘线, margin)={pearson(lines, margins):+.4f}")
        sd = Counter(r["settle"] for r in rs)
        print(f"          settle 分布: " + " ".join(f"{k:+.1f}:{v}" for k, v in sorted(sd.items())))
        summary[f"sanity|{season}"] = dict(n=n, margin_mean=m, ci=[lo, hi],
                                          corr_line_rawmargin=pearson(lines, raw),
                                          settle_dist={str(k): v for k, v in sd.items()})

    print("\n  三家收盘线(各自主定义)逐场一致率与差值分布(档=0.25):")
    for other, prefix in (("Bet365", "bet365"), ("Macauslot", "macau")):
        for season in SEASONS:
            rs = [r for r in rows_out if r["season"] == season and r["crown_close_line"] is not None
                  and r[f"{prefix}_close_line"] is not None]
            if not rs:
                print(f"    Crown vs {other} {season}: 无可比场次")
                continue
            diffs = [tick(r["crown_close_line"] - r[f"{prefix}_close_line"]) for r in rs]
            same = sum(1 for d in diffs if d == 0)
            dist = Counter(diffs)
            print(f"    Crown vs {other} {season}: 可比={len(rs)} 一致={same} ({100.0*same/len(rs):.1f}%) "
                  f"差值档分布={dict(sorted(dist.items()))}")
    print("\n  Crown vs Bet365 收盘线差值最大的 20 场(供人工抽查):")
    cmp_rows = [r for r in rows_out if r["crown_close_line"] is not None and r["bet365_close_line"] is not None]
    cmp_rows.sort(key=lambda r: -abs(r["crown_close_line"] - r["bet365_close_line"]))
    for r in cmp_rows[:20]:
        print(f"    {r['season']} {r['league']} {r['kickoff_at_utc']} {r['home_team']} vs {r['away_team']} "
              f"{r['home_score']}-{r['away_score']}  Crown={r['crown_close_line']:+.2f}@{r['crown_close_at']} "
              f"Bet365={r['bet365_close_line']:+.2f}@{r['bet365_close_at']}")

    # ================================================== 市场偏差体检
    print("\n" + "=" * 12, "Phase 1:市场偏差体检(按 Crown 收盘线/水位;margin 主队视角;ROI=平注1单位)", "=" * 12)
    print_group_table("按赛季", {s: [r for r in valid if r["season"] == s] for s in SEASONS} | {"ALL": valid})
    print_group_table("按联赛(两季合并)", {LEAGUE_NAME[l]: [r for r in valid if r["league_id"] == l] for l in LEAGUES})
    print_group_table("按让球方向(主让/平手/客让)",
                      {k: [r for r in valid if direction_bucket(r["crown_close_line"]) == k]
                       for k in ("主让", "平手", "客让")})
    print_group_table("按让球深度 |line|",
                      {k: [r for r in valid if depth_bucket(r["crown_close_line"]) == k]
                       for k in ("0(平手)", "0.25~0.5", "0.75~1.0", "1.25~1.5", "≥1.75")})
    print_group_table("按升班马",
                      {"主队升班马": [r for r in valid if r["promoted_home"] and not r["promoted_away"]],
                       "客队升班马": [r for r in valid if r["promoted_away"] and not r["promoted_home"]],
                       "双方升班马": [r for r in valid if r["promoted_home"] and r["promoted_away"]],
                       "无升班马": [r for r in valid if not r["promoted_home"] and not r["promoted_away"]]})
    max_round = {}
    for r in valid:
        if r["round"] is not None:
            k = (r["season"], r["league_id"])
            max_round[k] = max(max_round.get(k, 0), r["round"])
    first5 = {s: [r for r in valid if r["season"] == s and r["round"] is not None and r["round"] <= 5]
              for s in SEASONS}
    last5_2526 = [r for r in valid if r["season"] == "2025/2026" and r["round"] is not None
                  and r["round"] >= max_round.get(("2025/2026", r["league_id"]), 0) - 4]
    print_group_table("按赛季阶段(轮次由 Match_Round 解析;26/27 无末 5 轮)",
                      {"25/26 前5轮": first5["2025/2026"], "26/27 前5轮": first5["2026/2027"],
                       "25/26 末5轮": last5_2526})
    no_round = sum(1 for r in valid if r["round"] is None)
    print(f"  (Match_Round 无法解析出轮次的场次: {no_round};各联赛 25/26 最大轮次: "
          f"{ {LEAGUE_NAME[l]: max_round.get(('2025/2026', l)) for l in LEAGUES} })")

    # ================================================== 写出
    csv_path = out_dir / "phase1_target.csv"
    cols = list(rows_out[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows_out)
    (out_dir / "phase1_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    print(f"\n[written] {csv_path} ({len(rows_out)} 行, {len(cols)} 列)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
