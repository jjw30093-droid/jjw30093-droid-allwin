#!/usr/bin/env python3
"""Phase 0 — 数据审计(只读)。

用法(服务器上,仓库根目录):
    nice -n 19 python3 research/ah_signals/phase0_audit.py \
        --data-dir /opt/allwin/shared/data --out-dir ~/research_out/ah_signals

全部查询走 sqlite3 `file:...?mode=ro` URI,零写入生产库;输出只写到 --out-dir
(仓库以外)。stdout 是审计原始输出。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from common import (  # noqa: E402
    COMPANIES,
    LEAGUES,
    LEAGUE_NAME,
    MARKETS,
    SEASONS,
    open_ro,
    parse_utc,
    quantiles,
)


def schema(con: sqlite3.Connection, table: str) -> list[tuple]:
    return con.execute(f"PRAGMA table_xinfo({table})").fetchall()


def count(con: sqlite3.Connection, sql: str, params=()) -> int:
    return con.execute(sql, params).fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    core = open_ro(data_dir / "allwin.db")
    odds = open_ro(data_dir / "odds.db")
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}

    league_in = ",".join(str(x) for x in LEAGUES)

    # ---------------------------------------------------------------- 1. schema
    print("=" * 12, "1. schema + 行数", "=" * 12)
    for con, name in ((core, "dim_match"), (core, "fact_team_match_stats"),
                      (core, "fact_player_match_stats"), (core, "fact_match_lineup"),
                      (odds, "bronze_ng_odds_snap"), (odds, "dim_match_xref")):
        cols = schema(con, name)
        n = count(con, f"SELECT COUNT(*) FROM {name}")
        print(f"\n[{name}] 行数={n} 列数={len(cols)}")
        for c in cols:
            # cid, name, type, notnull, dflt, pk, hidden
            hidden = " (hidden/generated)" if c[6] else ""
            print(f"   {c[1]:40s} {c[2] or '':10s}{hidden}")
        report[f"schema.{name}"] = {"rows": n, "columns": [c[1] for c in cols]}

    # --------------------------------------------- 2. 完赛/开球/统计覆盖 by season×league
    print("\n" + "=" * 12, "2. 分赛季×联赛:完赛 / 精确开球 / 队级统计 / 球员统计", "=" * 12)
    print(f"{'season':10s} {'league':6s} {'sched':>6s} {'fin':>5s} {'exactKO':>8s} "
          f"{'team2':>6s} {'plyr2':>6s} {'rating%':>8s}")
    cov = {}
    for season in SEASONS:
        for lid in LEAGUES:
            sched = count(core, "SELECT COUNT(*) FROM dim_match WHERE League_ID=? AND Season=?",
                          (lid, season))
            fin = count(core, "SELECT COUNT(*) FROM dim_match WHERE League_ID=? AND Season=? "
                              "AND status='Finish'", (lid, season))
            exact = count(core, "SELECT COUNT(*) FROM dim_match WHERE League_ID=? AND Season=? "
                                "AND status='Finish' AND kickoff_precision='exact' "
                                "AND kickoff_at_utc IS NOT NULL", (lid, season))
            team2 = count(core, """SELECT COUNT(*) FROM dim_match dm
                WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish'
                  AND (SELECT COUNT(DISTINCT Team_ID) FROM fact_team_match_stats
                        WHERE Match_ID=dm.Match_ID AND Period='All'
                          AND Team_ID IN (dm.Home_Team_ID, dm.Away_Team_ID))=2""",
                          (lid, season))
            plyr2 = count(core, """SELECT COUNT(*) FROM dim_match dm
                WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish'
                  AND (SELECT COUNT(DISTINCT Team_ID) FROM fact_player_match_stats
                        WHERE Match_ID=dm.Match_ID AND minutes_played>0
                          AND Team_ID IN (dm.Home_Team_ID, dm.Away_Team_ID))=2""",
                          (lid, season))
            r_tot, r_nn = core.execute("""SELECT COUNT(*), SUM(rating_title IS NOT NULL)
                FROM fact_player_match_stats p JOIN dim_match dm ON dm.Match_ID=p.Match_ID
                WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish'
                  AND p.minutes_played>0""", (lid, season)).fetchone()
            rating_pct = 100.0 * (r_nn or 0) / r_tot if r_tot else float("nan")
            print(f"{season:10s} {LEAGUE_NAME[lid]:6s} {sched:6d} {fin:5d} {exact:8d} "
                  f"{team2:6d} {plyr2:6d} {rating_pct:7.1f}%")
            cov[(season, lid)] = dict(sched=sched, fin=fin, exact=exact, team2=team2,
                                      plyr2=plyr2, rating_pct=rating_pct)
        tot = {k: sum(v[k] for (s, _), v in cov.items() if s == season)
               for k in ("sched", "fin", "exact", "team2", "plyr2")}
        print(f"{season:10s} {'合计':6s} {tot['sched']:6d} {tot['fin']:5d} {tot['exact']:8d} "
              f"{tot['team2']:6d} {tot['plyr2']:6d}")
    report["coverage"] = {f"{s}|{l}": v for (s, l), v in cov.items()}

    # ---------------------------------------------------- 3. Period 取值(半场?)
    print("\n" + "=" * 12, "3. fact_team_match_stats.Period 取值(五大联赛两赛季)", "=" * 12)
    rows = core.execute(f"""SELECT t.Period, COUNT(*) FROM fact_team_match_stats t
        JOIN dim_match dm ON dm.Match_ID=t.Match_ID
        WHERE dm.League_ID IN ({league_in}) AND dm.Season IN (?,?) AND dm.status='Finish'
        GROUP BY t.Period ORDER BY COUNT(*) DESC""", SEASONS).fetchall()
    for p, n in rows:
        print(f"   Period={p!r}: {n} 行")
    report["team_stats_periods"] = {str(p): n for p, n in rows}

    # ------------------------------------------------- 4. xref 覆盖 + 反转标记
    print("\n" + "=" * 12, "4. dim_match_xref(nowgoal)覆盖与 review_status / 主客反转", "=" * 12)
    xref_cols = [c[1] for c in schema(odds, "dim_match_xref")]
    invert_col = next((c for c in xref_cols if "invert" in c.lower()), None)
    print(f"   反转标记列名: {invert_col!r}")
    matches_all = core.execute(f"""SELECT Match_ID, League_ID, Season, kickoff_at_utc
        FROM dim_match WHERE League_ID IN ({league_in}) AND Season IN (?,?) AND status='Finish'
          AND kickoff_precision='exact' AND kickoff_at_utc IS NOT NULL""", SEASONS).fetchall()
    mid_info = {m[0]: m for m in matches_all}
    xref_rows = odds.execute(
        f"""SELECT fotmob_match_id, provider_match_id, review_status
                   {', ' + invert_col if invert_col else ''}
              FROM dim_match_xref WHERE provider='nowgoal'"""
    ).fetchall()
    xref_by_mid: dict[int, list] = defaultdict(list)
    for r in xref_rows:
        if r[0] in mid_info:
            xref_by_mid[int(r[0])].append(r)
    status_ct = defaultdict(int)
    multi = 0
    inverted = 0
    for mid, rs in xref_by_mid.items():
        if len(rs) > 1:
            multi += 1
        for r in rs:
            status_ct[r[2]] += 1
            if invert_col and r[3]:
                inverted += 1
    print(f"   目标完赛比赛(精确开球)总数: {len(matches_all)}")
    print(f"   有 nowgoal xref 的比赛: {len(xref_by_mid)}  (无 xref: {len(matches_all)-len(xref_by_mid)})")
    print(f"   同一比赛多条 xref: {multi}")
    print(f"   review_status 分布: {dict(status_ct)}")
    print(f"   {invert_col}=真 的 xref 条数: {inverted}")
    report["xref"] = dict(total=len(matches_all), with_xref=len(xref_by_mid), multi=multi,
                          status=dict(status_ct), inverted=inverted, invert_col=invert_col)

    # ---------------------------------- 5. 三家 × 市场 逐场覆盖率、snap 数、最后snap距开球
    print("\n" + "=" * 12, "5. 公司 × 市场:逐场覆盖率(开球前有 ≥1 条 snap)", "=" * 12)
    # 拉出目标比赛所有 pre-kickoff snaps(按 observed_at < kickoff 判定,并记录 market_phase)
    ok_xref = {mid: [r[1] for r in rs if r[2] in ("auto_ok", "confirmed")]
               for mid, rs in xref_by_mid.items()}
    pmid_to_mid = {}
    for mid, pmids in ok_xref.items():
        for p in pmids:
            pmid_to_mid[str(p)] = mid
    snaps = odds.execute(
        """SELECT provider_match_id, market, company_id, observed_at, market_phase
             FROM bronze_ng_odds_snap
            WHERE market IN ('ah','ou','1x2')"""
    ).fetchall()
    # per (mid, company_key, market) -> list of (observed_at, phase)
    per = defaultdict(list)
    phase_mismatch = 0
    phase_total = 0
    for pmid, market, cid, obs, phase in snaps:
        mid = pmid_to_mid.get(str(pmid))
        if mid is None:
            continue
        ck = COMPANIES.get(str(cid))
        if ck is None:
            continue
        ko = mid_info[mid][3]
        pre_by_ts = obs < ko  # ISO-8601 Z 字符串,同格式可直接比较
        phase_total += 1
        if pre_by_ts != (phase == "pre_match"):
            phase_mismatch += 1
        if pre_by_ts:
            per[(mid, ck, market)].append(obs)
    print(f"   pre-kickoff 判定交叉检验: 'observed_at<kickoff' 与 market_phase='pre_match' "
          f"不一致的 snap 数 = {phase_mismatch} / {phase_total}")
    report["phase_mismatch"] = dict(mismatch=phase_mismatch, total=phase_total)

    print(f"\n{'season':10s} {'company':10s} {'market':7s} {'n_fin':>6s} {'covered':>8s} {'cov%':>7s}")
    cov_tbl = {}
    for season in SEASONS:
        season_mids = [m[0] for m in matches_all if m[2] == season]
        for ck in ("Macauslot", "Bet365", "Crown"):
            for market in MARKETS:
                covered = sum(1 for mid in season_mids if per.get((mid, ck, market)))
                pct = 100.0 * covered / len(season_mids) if season_mids else float("nan")
                print(f"{season:10s} {ck:10s} {market:7s} {len(season_mids):6d} {covered:8d} {pct:6.1f}%")
                cov_tbl[f"{season}|{ck}|{market}"] = dict(n=len(season_mids), covered=covered, pct=pct)
        # 任一家
        for market in MARKETS:
            covered = sum(1 for mid in season_mids
                          if any(per.get((mid, ck, market)) for ck in ("Macauslot", "Bet365", "Crown")))
            pct = 100.0 * covered / len(season_mids) if season_mids else float("nan")
            print(f"{season:10s} {'ANY3':10s} {market:7s} {len(season_mids):6d} {covered:8d} {pct:6.1f}%")
            cov_tbl[f"{season}|ANY3|{market}"] = dict(n=len(season_mids), covered=covered, pct=pct)
    report["market_coverage"] = cov_tbl

    # 分联赛 AH 覆盖(主口径候选各家)
    print("\n   分联赛 AH 逐场覆盖率:")
    print(f"{'season':10s} {'league':6s} {'Macauslot':>10s} {'Bet365':>8s} {'Crown':>8s}")
    for season in SEASONS:
        for lid in LEAGUES:
            mids = [m[0] for m in matches_all if m[2] == season and m[1] == lid]
            vals = []
            for ck in ("Macauslot", "Bet365", "Crown"):
                c = sum(1 for mid in mids if per.get((mid, ck, "ah")))
                vals.append(100.0 * c / len(mids) if mids else float("nan"))
            print(f"{season:10s} {LEAGUE_NAME[lid]:6s} {vals[0]:9.1f}% {vals[1]:7.1f}% {vals[2]:7.1f}%")

    # ---------------------------------------- 6. snap 数量分布 / 最后 snap 距开球分布
    print("\n" + "=" * 12, "6. 每场开球前 snap 数量分布(AH/OU)", "=" * 12)
    print(f"{'season':10s} {'company':10s} {'mkt':4s} {'n':>5s} | min p10 p25 p50 p75 p90 max")
    dist = {}
    for season in SEASONS:
        season_mids = [m[0] for m in matches_all if m[2] == season]
        for ck in ("Macauslot", "Bet365", "Crown"):
            for market in ("ah", "ou"):
                counts = [len(per[(mid, ck, market)]) for mid in season_mids if per.get((mid, ck, market))]
                q = quantiles(counts)
                print(f"{season:10s} {ck:10s} {market:4s} {len(counts):5d} | " +
                      " ".join(f"{v:.0f}" for v in q))
                dist[f"snaps|{season}|{ck}|{market}"] = q

    print("\n" + "=" * 12, "7. 开球前最后一条 snap 距开球的时间(分钟)分布(AH)", "=" * 12)
    print(f"{'season':10s} {'company':10s} {'n':>5s} | min p10 p25 p50 p75 p90 max   (分钟)")
    for season in SEASONS:
        season_mids = [m[0] for m in matches_all if m[2] == season]
        for ck in ("Macauslot", "Bet365", "Crown"):
            gaps = []
            for mid in season_mids:
                obs = per.get((mid, ck, "ah"))
                if not obs:
                    continue
                last = max(obs)
                ko = parse_utc(mid_info[mid][3])
                gaps.append((ko - parse_utc(last)).total_seconds() / 60.0)
            q = quantiles(gaps)
            print(f"{season:10s} {ck:10s} {len(gaps):5d} | " + " ".join(f"{v:.0f}" for v in q))
            dist[f"lastgap_min|{season}|{ck}|ah"] = q
    report["distributions"] = dist

    # ------------------------------------------- 7b. payload 形状(嵌套 vs 扁平)
    # 库里存在两种真实形状(backend/queries/odds.py 模块 docstring):实时轮询与
    # 2026-09-22 全量历史回填写嵌套 {"initial","latest"};更早的 JSONL 分片回填写
    # 扁平 {home,line,away}。扁平行没有 initial,开盘线只能取该序列最早一行。
    print("\n" + "=" * 12, "7b. payload 形状:嵌套(initial/latest) vs 扁平,目标比赛 AH/OU", "=" * 12)
    shape_rows = odds.execute(
        """SELECT provider_match_id, market, company_id,
                  CASE WHEN json_type(payload_json,'$.initial') IS NOT NULL
                         OR json_type(payload_json,'$.latest') IS NOT NULL
                       THEN 'nested' ELSE 'flat' END AS shape,
                  COUNT(*)
             FROM bronze_ng_odds_snap
            WHERE market IN ('ah','ou','1x2')
            GROUP BY provider_match_id, market, company_id, shape"""
    ).fetchall()
    shape_ct = defaultdict(int)          # (season, ck, market, shape) -> rows
    nested_match = defaultdict(set)      # (season, ck, market) -> mids with >=1 nested
    flat_only_match = defaultdict(set)   # (season, ck, market) -> mids with flat but no nested
    tmp_has_nested = defaultdict(set)
    tmp_has_flat = defaultdict(set)
    for pmid, market, cid, shape, n in shape_rows:
        mid = pmid_to_mid.get(str(pmid))
        if mid is None:
            continue
        ck = COMPANIES.get(str(cid))
        if ck is None:
            continue
        season = mid_info[mid][2]
        shape_ct[(season, ck, market, shape)] += n
        (tmp_has_nested if shape == "nested" else tmp_has_flat)[(season, ck, market)].add(mid)
    print(f"{'season':10s} {'company':10s} {'mkt':4s} {'nested_rows':>12s} {'flat_rows':>10s} "
          f"{'m_nested':>9s} {'m_flatonly':>11s}")
    for season in SEASONS:
        for ck in ("Macauslot", "Bet365", "Crown"):
            for market in MARKETS:
                nr = shape_ct.get((season, ck, market, "nested"), 0)
                fr = shape_ct.get((season, ck, market, "flat"), 0)
                mn = tmp_has_nested.get((season, ck, market), set())
                mf = tmp_has_flat.get((season, ck, market), set()) - mn
                print(f"{season:10s} {ck:10s} {market:4s} {nr:12d} {fr:10d} {len(mn):9d} {len(mf):11d}")
                dist[f"shape|{season}|{ck}|{market}"] = dict(nested_rows=nr, flat_rows=fr,
                                                            matches_nested=len(mn), matches_flat_only=len(mf))

    # ---------------------------------------------------------- 8. 对照已知盘点
    print("\n" + "=" * 12, "8. 与任务书『已知数据盘点』逐项对照", "=" * 12)
    fin_2526 = sum(v["fin"] for (s, _), v in cov.items() if s == "2025/2026")
    fin_2627 = sum(v["fin"] for (s, _), v in cov.items() if s == "2026/2027")
    exact_all = sum(v["exact"] for v in cov.values())
    fin_all = fin_2526 + fin_2627
    team2_all = sum(v["team2"] for v in cov.values())
    plyr2_all = sum(v["plyr2"] for v in cov.values())
    any_ah_2526 = cov_tbl["2025/2026|ANY3|ah"]["pct"]
    any_ah_2627 = cov_tbl["2026/2027|ANY3|ah"]["pct"]
    checks = [
        ("25/26 完赛 1752", fin_2526, 1752),
        ("26/27 完赛 250", fin_2627, 250),
        ("精确开球覆盖 100%", round(100.0 * exact_all / fin_all, 1), 100.0),
        ("AH 任一家覆盖 25/26 = 100%", round(any_ah_2526, 1), 100.0),
        ("AH 任一家覆盖 26/27 = 96.8%", round(any_ah_2627, 1), 96.8),
        ("队级统计 Period=All 双方覆盖 100%", round(100.0 * team2_all / fin_all, 1), 100.0),
        ("球员统计双方覆盖 100%", round(100.0 * plyr2_all / fin_all, 1), 100.0),
    ]
    for label, got, expect in checks:
        flag = "OK" if got == expect else "!! 不符"
        print(f"   {label:34s} 实际={got}  任务书={expect}  {flag}")
    report["reconcile"] = [dict(label=l, got=g, expect=e) for l, g, e in checks]

    out = out_dir / "phase0_audit.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    print(f"\n[written] {out}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
