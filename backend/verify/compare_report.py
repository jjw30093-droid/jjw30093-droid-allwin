"""
compare_report.py — 4 大联赛(verify_leagues.db)vs 英超(allwin.db,只读)
2025/2026 单赛季对照:场次 / 关键字段覆盖率 / schema key 差异。

只读两个库,不写任何东西。

用法:
    python backend/verify/compare_report.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_connection as get_allwin_connection
from db_verify import get_verify_connection

SEASON = "2025/2026"

LEAGUES = [
    (47, "英超", "allwin"),
    (87, "西甲", "verify"),
    (55, "意甲", "verify"),
    (54, "德甲", "verify"),
    (53, "法甲", "verify"),
]

KEY_FIELDS = [
    "expected_goals",
    "total_shots",
    "ShotsOnTarget",
    "BallPossesion",
    "corners",
    "yellow_cards",
]


def _conn_for(source: str):
    return get_allwin_connection() if source == "allwin" else get_verify_connection()


def match_count(conn, league_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM dim_match WHERE League_ID = ? AND Season = ? AND status = 'Finish'",
        (league_id, SEASON),
    ).fetchone()
    return row[0]


def team_stats_by_match(conn, league_id: int) -> dict:
    """{match_id: [extra_json_dict, extra_json_dict]}(Period='All',每场应有 2 行)。"""
    rows = conn.execute(
        """
        SELECT fts.Match_ID, fts.extra_json
        FROM fact_team_match_stats fts
        JOIN dim_match dm ON fts.Match_ID = dm.Match_ID
        WHERE dm.League_ID = ? AND dm.Season = ? AND dm.status = 'Finish' AND fts.Period = 'All'
        """,
        (league_id, SEASON),
    ).fetchall()
    by_match = {}
    for match_id, extra_json in rows:
        by_match.setdefault(match_id, []).append(json.loads(extra_json) if extra_json else {})
    return by_match


def events_coverage(conn, league_id: int) -> tuple:
    """(有 fact_match_events 记录的完赛场次数, 总完赛场次数, minute 全非空的场次数)"""
    total = match_count(conn, league_id)
    rows = conn.execute(
        """
        SELECT ev.Match_ID, ev.event_type, ev.minute
        FROM fact_match_events ev
        JOIN dim_match dm ON ev.Match_ID = dm.Match_ID
        WHERE dm.League_ID = ? AND dm.Season = ? AND dm.status = 'Finish'
        """,
        (league_id, SEASON),
    ).fetchall()
    by_match_event_type = {}
    by_match_minute_null = {}
    for match_id, event_type, minute in rows:
        by_match_event_type.setdefault(match_id, 0)
        if event_type is not None:
            by_match_event_type[match_id] += 1
        by_match_minute_null.setdefault(match_id, True)
        if minute is None:
            by_match_minute_null[match_id] = False

    matches_with_events = len(by_match_event_type)
    matches_all_minute_present = sum(1 for v in by_match_minute_null.values() if v)
    return matches_with_events, total, matches_all_minute_present


def main() -> None:
    print("=== 场次对照(2025/2026,只统计 status='Finish') ===")
    print(f"{'联赛':<6}{'league_id':>10}{'场次':>8}")
    league_matches = {}
    for league_id, label, source in LEAGUES:
        conn = _conn_for(source)
        try:
            n = match_count(conn, league_id)
        finally:
            conn.close()
        league_matches[label] = n
        print(f"{label:<6}{league_id:>10}{n:>8}")

    print()
    print("=== 关键字段覆盖率(按场次:该场两条 team-stats 行都非 NULL 才算覆盖) ===")
    header = f"{'字段':<20}" + "".join(f"{lbl:>10}" for _, lbl, _ in LEAGUES)
    print(header)

    coverage_by_league = {}
    stats_data = {}
    for league_id, label, source in LEAGUES:
        conn = _conn_for(source)
        try:
            stats_data[label] = team_stats_by_match(conn, league_id)
        finally:
            conn.close()

    for field in KEY_FIELDS:
        row_str = f"{field:<20}"
        for league_id, label, source in LEAGUES:
            by_match = stats_data[label]
            total = len(by_match)
            covered = sum(
                1
                for rows in by_match.values()
                if len(rows) == 2 and all(r.get(field) is not None for r in rows)
            )
            pct = round(100.0 * covered / total, 1) if total else 0.0
            coverage_by_league.setdefault(label, {})[field] = pct
            row_str += f"{pct:>9}%"
        print(row_str)

    print()
    print("=== fact_match_events 覆盖率 ===")
    print(f"{'联赛':<6}{'有事件记录场次/总场次':>22}{'minute 全非空场次':>18}")
    for league_id, label, source in LEAGUES:
        conn = _conn_for(source)
        try:
            with_events, total, minute_ok = events_coverage(conn, league_id)
        finally:
            conn.close()
        print(f"{label:<6}{with_events:>10}/{total:<10}{minute_ok:>10}/{total:<10}")

    print()
    print("=== extra_json key 集合差异(vs 英超) ===")
    epl_keys = set()
    for rows in stats_data["英超"].values():
        for r in rows:
            epl_keys.update(r.keys())
    print(f"英超 key 总数: {len(epl_keys)}")
    for league_id, label, source in LEAGUES:
        if label == "英超":
            continue
        keys = set()
        for rows in stats_data[label].values():
            for r in rows:
                keys.update(r.keys())
        missing = epl_keys - keys
        extra = keys - epl_keys
        print(f"\n{label}(key 总数 {len(keys)}):")
        print(f"  英超有、{label}没有: {sorted(missing) if missing else '(无)'}")
        print(f"  {label}有、英超没有: {sorted(extra) if extra else '(无)'}")

    print()
    print("=== 明显低于英超的字段覆盖率(差 >5 个百分点)提醒 ===")
    epl_cov = coverage_by_league["英超"]
    flagged = False
    for league_id, label, source in LEAGUES:
        if label == "英超":
            continue
        for field in KEY_FIELDS:
            diff = epl_cov[field] - coverage_by_league[label][field]
            if diff > 5:
                flagged = True
                print(f"  {label}.{field}: {coverage_by_league[label][field]}% vs 英超 {epl_cov[field]}%(低 {diff:.1f} 个百分点)")
    if not flagged:
        print("  (无——4 联赛关键字段覆盖率都在英超 5 个百分点以内)")


if __name__ == "__main__":
    main()
