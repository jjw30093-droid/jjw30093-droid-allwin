"""
compare_report_history.py — 4 大联赛(verify_leagues.db)历史 6 个赛季
(2020/2021~2025/2026)逐赛季关键字段覆盖率体检,英超(allwin.db,只读)同期
数据作为基准对照,专查早期赛季有没有 xG 等关键字段缺失。

只读两个库,不写任何东西。

用法:
    python backend/verify/compare_report_history.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_connection as get_allwin_connection
from db_verify import get_verify_connection

SEASONS = ["2020/2021", "2021/2022", "2022/2023", "2023/2024", "2024/2025", "2025/2026"]

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


def match_count(conn, league_id: int, season: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM dim_match WHERE League_ID = ? AND Season = ? AND status = 'Finish'",
        (league_id, season),
    ).fetchone()
    return row[0]


def team_stats_by_match(conn, league_id: int, season: str) -> dict:
    rows = conn.execute(
        """
        SELECT fts.Match_ID, fts.extra_json
        FROM fact_team_match_stats fts
        JOIN dim_match dm ON fts.Match_ID = dm.Match_ID
        WHERE dm.League_ID = ? AND dm.Season = ? AND dm.status = 'Finish' AND fts.Period = 'All'
        """,
        (league_id, season),
    ).fetchall()
    by_match = {}
    for match_id, extra_json in rows:
        by_match.setdefault(match_id, []).append(json.loads(extra_json) if extra_json else {})
    return by_match


def events_minute_coverage(conn, league_id: int, season: str) -> tuple:
    total = match_count(conn, league_id, season)
    rows = conn.execute(
        """
        SELECT ev.Match_ID, ev.minute
        FROM fact_match_events ev
        JOIN dim_match dm ON ev.Match_ID = dm.Match_ID
        WHERE dm.League_ID = ? AND dm.Season = ? AND dm.status = 'Finish'
        """,
        (league_id, season),
    ).fetchall()
    matches_with_events = len(set(r[0] for r in rows))
    by_match_minute_ok = {}
    for match_id, minute in rows:
        by_match_minute_ok.setdefault(match_id, True)
        if minute is None:
            by_match_minute_ok[match_id] = False
    minute_ok = sum(1 for v in by_match_minute_ok.values() if v)
    return matches_with_events, minute_ok, total


def field_coverage(by_match: dict, field: str) -> tuple:
    total = len(by_match)
    covered = sum(
        1
        for rows in by_match.values()
        if len(rows) == 2 and all(r.get(field) is not None for r in rows)
    )
    pct = round(100.0 * covered / total, 1) if total else None
    return covered, total, pct


def main() -> None:
    print("=== 逐赛季场次(2020/2021~2025/2026,status='Finish') ===")
    header = f"{'联赛':<6}" + "".join(f"{s:>12}" for s in SEASONS)
    print(header)
    for league_id, label, source in LEAGUES:
        conn = _conn_for(source)
        try:
            counts = [match_count(conn, league_id, s) for s in SEASONS]
        finally:
            conn.close()
        print(f"{label:<6}" + "".join(f"{c:>12}" for c in counts))

    print()
    print("=== 逐赛季 · 逐字段覆盖率(该场两条 team-stats 行都非 NULL 才算覆盖) ===")

    flagged_gaps = []

    for field in KEY_FIELDS:
        print(f"\n--- 字段: {field} ---")
        print(f"{'联赛':<6}" + "".join(f"{s:>12}" for s in SEASONS))
        for league_id, label, source in LEAGUES:
            conn = _conn_for(source)
            try:
                pcts = []
                for season in SEASONS:
                    by_match = team_stats_by_match(conn, league_id, season)
                    _, _, pct = field_coverage(by_match, field)
                    pcts.append(pct)
                    if pct is not None and pct < 100.0:
                        flagged_gaps.append((label, season, field, pct))
            finally:
                conn.close()
            row = f"{label:<6}"
            for pct in pcts:
                row += f"{('%.1f%%' % pct) if pct is not None else '(无场次)':>12}"
            print(row)

    print()
    print("=== fact_match_events(minute 字段)逐赛季覆盖率 ===")
    print(f"{'联赛':<6}" + "".join(f"{s:>12}" for s in SEASONS))
    for league_id, label, source in LEAGUES:
        conn = _conn_for(source)
        try:
            row = f"{label:<6}"
            for season in SEASONS:
                with_events, minute_ok, total = events_minute_coverage(conn, league_id, season)
                pct = round(100.0 * minute_ok / total, 1) if total else None
                row += f"{('%.1f%%' % pct) if pct is not None else '(无场次)':>12}"
        finally:
            conn.close()
        print(row)

    print()
    print("=== 🔴 早期赛季字段缺失体检结论(覆盖率 < 100% 的(联赛,赛季,字段)组合) ===")
    if flagged_gaps:
        for label, season, field, pct in flagged_gaps:
            print(f"  {label} {season} {field}: {pct}%")
    else:
        print("  (无——4 联赛 6 个赛季、6 个关键字段,覆盖率全部 100%,早期赛季没有 xG 等字段缺失)")


if __name__ == "__main__":
    main()
