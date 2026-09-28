#!/usr/bin/env python3
"""Phase 1 补充核验(只读):
1) Crown AH snap 内 initial 值是否恒定(分季占比 + 前 10 场明细),initial vs 首条 snap 比对表,
   首条 snap 距开球分位数;
2) Malaga vs Deportivo 该时间戳下 Bet365 原始 payload 全文 + 当前规则选中的线。

用法同 phase1_target.py。产物:<out-dir>/phase1b_initial.csv(每场 initial 众数与是否恒定)。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    COMPANIES, LEAGUE_NAME, PRIMARY, SEASONS, load_ah_timelines, load_matches, load_xref,
    open_ro, parse_utc, pick_close_main, quantiles,
)

MALAGA_MID = None  # 按队名定位,不硬编码 id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)

    core = open_ro(data_dir / "allwin.db")
    odds = open_ro(data_dir / "odds.db")
    matches = load_matches(core)
    ok_xref, _ = load_xref(odds, {m["Match_ID"] for m in matches})
    matches = [m for m in matches if m["Match_ID"] in ok_xref]
    pmid_to_mid = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items()}
    tl = load_ah_timelines(odds, pmid_to_mid, market="ah")

    # ---------------------------------------------------------------- 1) initial 恒定性
    print("=" * 12, "1) Crown AH snap 内 initial 值恒定性", "=" * 12)
    rows_out = []
    per_season = defaultdict(lambda: dict(n=0, nonconst=0, details=[], first_hours=[],
                                          ini_eq_first=0, ini_ne_first=0))
    for m in matches:
        t = tl.get((m["Match_ID"], PRIMARY), [])
        if not t:
            continue
        ps = per_season[m["Season"]]
        ps["n"] += 1
        ko = parse_utc(m["kickoff_at_utc"])
        first = t[0]
        ps["first_hours"].append((ko - parse_utc(first["observed_at"])).total_seconds() / 3600.0)
        triples = [(s["init_line"], s["init_home"], s["init_away"]) for s in t if s["init_line"] is not None]
        lines = Counter(s["init_line"] for s in t if s["init_line"] is not None)
        distinct_lines = sorted(lines)
        mode_line, mode_cnt = lines.most_common(1)[0]
        const = len(distinct_lines) == 1
        # 众数 initial 的 home/away 取该 line 下最常见的三元组
        trip_c = Counter(tr for tr in triples if tr[0] == mode_line)
        mode_trip = trip_c.most_common(1)[0][0]
        if not const:
            ps["nonconst"] += 1
            ps["details"].append((m, distinct_lines, lines, len(t)))
        if abs(mode_line - first["line"]) < 1e-9:
            ps["ini_eq_first"] += 1
        else:
            ps["ini_ne_first"] += 1
        rows_out.append(dict(
            match_id=m["Match_ID"], season=m["Season"], league=LEAGUE_NAME[m["League_ID"]],
            home_team=m["Home_Team_Name"], away_team=m["Away_Team_Name"],
            n_snaps=len(t), n_distinct_initial_line=len(distinct_lines),
            initial_constant=int(const), initial_line=mode_line, initial_home=mode_trip[1],
            initial_away=mode_trip[2], initial_mode_share=round(mode_cnt / len(triples), 3),
            initial_distinct_lines="|".join(f"{v:+.2f}" for v in distinct_lines),
            first_snap_at=first["observed_at"], first_snap_line=first["line"],
        ))
    for season in SEASONS:
        ps = per_season[season]
        print(f"  {season}: 有 Crown AH snap 的场次={ps['n']}  initial.line 不恒定的场次={ps['nonconst']} "
              f"({100.0*ps['nonconst']/ps['n']:.2f}%)")
        for m, dl, lines, nsn in ps["details"][:10]:
            print(f"     match {m['Match_ID']} {LEAGUE_NAME[m['League_ID']]} {m['Home_Team_Name']} vs "
                  f"{m['Away_Team_Name']} kickoff={m['kickoff_at_utc']} snaps={nsn} "
                  f"initial.line 取值分布={ {f'{k:+.2f}': v for k, v in sorted(lines.items())} }")
    print("\n  initial(众数) vs 首条 snap.line 比对表:")
    print(f"  {'赛季':10s} {'场次':>5s} {'相同':>5s} {'不同':>5s} {'不同%':>6s}")
    for season in SEASONS:
        ps = per_season[season]
        print(f"  {season:10s} {ps['n']:5d} {ps['ini_eq_first']:5d} {ps['ini_ne_first']:5d} "
              f"{100.0*ps['ini_ne_first']/ps['n']:6.1f}")
    print("\n  首条 Crown snap 距 kickoff 小时数分位数:")
    print(f"  {'赛季':10s} {'p0':>7s} {'p10':>7s} {'p25':>7s} {'p50':>7s} {'p75':>7s} {'p90':>7s} {'p100':>7s}")
    for season in SEASONS:
        q = quantiles(per_season[season]["first_hours"])
        print(f"  {season:10s} " + " ".join(f"{v:7.1f}" for v in q))
    print("\n  26/27 首条 snap.line ≠ initial 的场次:首条 snap 距开球分位数(验证是否为 168h 候选池截断):")
    ne_hours = []
    for m in matches:
        if m["Season"] != "2026/2027":
            continue
        t = tl.get((m["Match_ID"], PRIMARY), [])
        if not t or t[0]["init_line"] is None:
            continue
        if abs(t[0]["init_line"] - t[0]["line"]) > 1e-9:
            ne_hours.append((parse_utc(m["kickoff_at_utc"]) - parse_utc(t[0]["observed_at"])).total_seconds() / 3600.0)
    q = quantiles(ne_hours)
    print(f"  n={len(ne_hours)} p0/p10/p25/p50/p75/p90/p100 = " + " / ".join(f"{v:.1f}" for v in q))

    csv_path = out_dir / "phase1b_initial.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)
    print(f"  [written] {csv_path} ({len(rows_out)} 行)")

    # ---------------------------------------------------------------- 2) Malaga vs Deportivo
    print("\n" + "=" * 12, "2) Malaga vs Deportivo A Coruña:Bet365 原始 payload", "=" * 12)
    target = [m for m in matches if m["Season"] == "2026/2027" and "Malaga" in m["Home_Team_Name"]
              and "Deportivo" in m["Away_Team_Name"]]
    if len(target) != 1:
        print(f"  定位到 {len(target)} 场,无法继续:{[t['Match_ID'] for t in target]}")
        return 1
    m = target[0]
    x = ok_xref[m["Match_ID"]]
    ko = parse_utc(m["kickoff_at_utc"])
    print(f"  match {m['Match_ID']} {m['Home_Team_Name']} vs {m['Away_Team_Name']} {m['home_score']}-{m['away_score']} "
          f"kickoff={m['kickoff_at_utc']} provider_match_id={x['provider_match_id']}")
    n_rows = odds.execute(
        "SELECT COUNT(*) FROM bronze_ng_odds_snap WHERE provider_match_id=? AND market='ah'",
        (str(x["provider_match_id"]),)).fetchone()[0]
    print(f"  该场 AH 市场 bronze_ng_odds_snap 总行数(全公司)={n_rows}")
    print("  --- 该场 Bet365(8/281)与 Crown(3)全部 AH 行(observed_at < kickoff),payload_json 原文 ---")
    rows = odds.execute(
        """SELECT id, company_id, company_name, market, observed_at, source_updated_at, poll_run_id,
                  market_phase, payload_hash, payload_json
             FROM bronze_ng_odds_snap
            WHERE provider_match_id=? AND market='ah' AND company_id IN ('8','281','3')
            ORDER BY company_id, observed_at, id""",
        (str(x["provider_match_id"]),)).fetchall()
    for r in rows:
        print(f"    id={r['id']} cid={r['company_id']}({r['company_name']}) observed_at={r['observed_at']} "
              f"source_updated_at={r['source_updated_at']} poll_run={r['poll_run_id']} phase={r['market_phase']} "
              f"hash={str(r['payload_hash'])[:12]}")
        print(f"       payload={r['payload_json']}")
    for ck in ("Bet365", "Crown"):
        t = tl.get((m["Match_ID"], ck), [])
        s, gap, reason = pick_close_main(t, ko)
        print(f"  当前规则({ck},主定义 ≤kickoff−10min 最后一条,gap≤120min)选中: "
              f"{None if s is None else (s['observed_at'], s['line'], s['home'], s['away'])} gap={gap} reason={reason}")
    print("  --- 当前规则的主盘口选择:每条 snap 的 payload 只含一个 latest.line,没有多线可选;"
          "若上面 Bet365 各行的 latest.line 在开球前一直是 1.75,则为来源侧数据,不是规则选错 ---")
    print("  --- 同一场 Bet365 在 OU / 1x2 市场收盘前最后一条(旁证该场 Bet365 数据是否整体异常)---")
    for mk in ("ou", "1x2"):
        r = odds.execute(
            """SELECT observed_at, payload_json FROM bronze_ng_odds_snap
                WHERE provider_match_id=? AND market=? AND company_id IN ('8','281') AND observed_at<?
                ORDER BY observed_at DESC, id DESC LIMIT 1""",
            (str(x["provider_match_id"]), mk, m["kickoff_at_utc"])).fetchone()
        print(f"    {mk}: {None if r is None else (r['observed_at'], r['payload_json'])}")
    print("  --- 该场 Macauslot(1/80) AH 开球前最后一条 ---")
    r = odds.execute(
        """SELECT company_id, observed_at, payload_json FROM bronze_ng_odds_snap
            WHERE provider_match_id=? AND market='ah' AND company_id IN ('1','80') AND observed_at<?
            ORDER BY observed_at DESC, id DESC LIMIT 1""",
        (str(x["provider_match_id"]), m["kickoff_at_utc"])).fetchone()
    print(f"    {None if r is None else (r['company_id'], r['observed_at'], r['payload_json'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
