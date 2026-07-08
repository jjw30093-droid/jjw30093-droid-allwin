"""
build_silver.py — 从 Bronze(dim_match / fact_team_match_stats / fact_match_events)
聚合出 5 张 Silver 表(ROADMAP.md Phase 1.2)。

口径(详见 schema.py 里 SILVER_* 常量前的注释):
    - 只聚合 dim_match.status = 'Finish' 的场次(当前 2280/2280 全部满足，
      但仍显式过滤，防止未来出现未完赛/取消的场次)。
    - 大小球/比分分布/胜平负/净胜球全部由 dim_match 的最终比分算，
      不依赖 fact_team_match_stats.extra_json。
    - 场均射门/射正/控球/角球/犯规/黄牌/xG 及其变体，从
      fact_team_match_stats(Period='All')的 extra_json 里 NULL-safe 地取平均，
      缺失值不计入平均(不当 0 算)。
    - avg_touches_opp_box 只在 2024/2025、2025/2026 两个赛季计算，
      其余赛季固定 NULL(FotMob 历史采集限制，覆盖率随赛季分布，非 bug)。
    - 进球时间分桶直接用 fact_match_events.minute 原始值(0-15/16-30/31-45/
      46-60/61-75/76-90)，不加 overload_time——字段体检已确认补时进球的
      minute 恒为 45 或 90，原始分桶已经把补时进球算进正确的一段。

幂等策略:
    按 (League_ID, Season) 先 DELETE 再整体 INSERT，可反复重跑
    (与 ingest_league.py 的 fact_league_table/fact_season_player_stats 同一风格)。

用法:
    python backend/silver/build_silver.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection
from schema import (
    SILVER_TEAM_SEASON_STATS_COLUMNS,
    SILVER_LEAGUE_SEASON_SUMMARY_COLUMNS,
    SILVER_OVER_UNDER_THRESHOLDS_COLUMNS,
    SILVER_SCORE_DISTRIBUTION_COLUMNS,
    SILVER_GOAL_MINUTE_BUCKETS_COLUMNS,
    _quote,
)

OU_THRESHOLDS = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]
TOUCHES_OPP_BOX_SEASONS = {"2024/2025", "2025/2026"}
MINUTE_BUCKETS = [
    ("0-15", 0, 15),
    ("16-30", 16, 30),
    ("31-45", 31, 45),
    ("46-60", 46, 60),
    ("61-75", 61, 75),
    ("76-90", 76, 90),
]

EXTRA_JSON_MEAN_FIELDS = [
    ("avg_total_shots", "total_shots"),
    ("avg_shots_on_target", "ShotsOnTarget"),
    ("avg_possession", "BallPossesion"),
    ("avg_corners", "corners"),
    ("avg_fouls", "fouls"),
    ("avg_yellow_cards", "yellow_cards"),
    ("avg_red_cards", "red_cards"),
    ("avg_expected_goals", "expected_goals"),
    ("avg_expected_goals_non_penalty", "expected_goals_non_penalty"),
    ("avg_expected_goals_open_play", "expected_goals_open_play"),
    ("avg_expected_goals_set_play", "expected_goals_set_play"),
    ("avg_expected_goals_on_target", "expected_goals_on_target"),
]


def _col_names(columns: list) -> list:
    return [name for name, _ in columns if name != "updated_at"]


def _insert_many(conn, table: str, columns: list, rows: list) -> None:
    if not rows:
        return
    names = _col_names(columns)
    placeholders = ", ".join("?" for _ in names)
    cols_sql = ", ".join(_quote(n) for n in names)
    conn.executemany(
        f"INSERT INTO {table} ({cols_sql}, updated_at) VALUES ({placeholders}, datetime('now'))",
        [tuple(row.get(n) for n in names) for row in rows],
    )


def _seasons(conn) -> list:
    rows = conn.execute(
        "SELECT League_ID, Season, COUNT(*) FROM dim_match "
        "WHERE status = 'Finish' GROUP BY League_ID, Season ORDER BY League_ID, Season"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _matches(conn, league_id: int, season: str) -> list:
    rows = conn.execute(
        """
        SELECT Match_ID, Home_Team_ID, Away_Team_ID, home_score, away_score
        FROM dim_match
        WHERE status = 'Finish' AND League_ID = ? AND Season = ?
        """,
        (league_id, season),
    ).fetchall()
    return [
        {
            "Match_ID": r[0],
            "Home_Team_ID": r[1],
            "Away_Team_ID": r[2],
            "home_score": r[3],
            "away_score": r[4],
        }
        for r in rows
    ]


def build_team_season_stats(conn, league_id: int, season: str, matches: list) -> list:
    select_parts = ["fts.Team_ID", "COUNT(*) AS matches_played"]
    for out_name, key in EXTRA_JSON_MEAN_FIELDS:
        select_parts.append(
            f"AVG(CASE WHEN json_extract(extra_json, '$.{key}') IS NOT NULL "
            f"THEN json_extract(extra_json, '$.{key}') END) AS {out_name}"
        )
    if season in TOUCHES_OPP_BOX_SEASONS:
        select_parts.append(
            "AVG(CASE WHEN json_extract(extra_json, '$.touches_opp_box') IS NOT NULL "
            "THEN json_extract(extra_json, '$.touches_opp_box') END) AS avg_touches_opp_box"
        )
    sql = f"""
        SELECT {', '.join(select_parts)}
        FROM fact_team_match_stats fts
        JOIN dim_match dm ON fts.Match_ID = dm.Match_ID
        WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ? AND fts.Period = 'All'
        GROUP BY fts.Team_ID
    """
    rows = conn.execute(sql, (league_id, season)).fetchall()
    col_names = [d[0] for d in conn.execute(sql, (league_id, season)).description]
    stats_by_team = {}
    for r in rows:
        d = dict(zip(col_names, r))
        d.setdefault("avg_touches_opp_box", None)
        stats_by_team[d["Team_ID"]] = d

    clean_sheets = {}
    btts_matches = {}
    for m in matches:
        home, away = m["Home_Team_ID"], m["Away_Team_ID"]
        clean_sheets.setdefault(home, 0)
        clean_sheets.setdefault(away, 0)
        btts_matches.setdefault(home, 0)
        btts_matches.setdefault(away, 0)
        if m["away_score"] == 0:
            clean_sheets[home] += 1
        if m["home_score"] == 0:
            clean_sheets[away] += 1
        if m["home_score"] > 0 and m["away_score"] > 0:
            btts_matches[home] += 1
            btts_matches[away] += 1

    out = []
    for team_id, d in stats_by_team.items():
        matches_played = d["matches_played"]
        row = {
            "League_ID": league_id,
            "Season": season,
            "Team_ID": team_id,
            "matches_played": matches_played,
            "clean_sheets": clean_sheets.get(team_id, 0),
            "btts_matches": btts_matches.get(team_id, 0),
            "btts_pct": round(100.0 * btts_matches.get(team_id, 0) / matches_played, 2)
            if matches_played
            else None,
        }
        for out_name, _ in EXTRA_JSON_MEAN_FIELDS:
            row[out_name] = d.get(out_name)
        row["avg_touches_opp_box"] = d.get("avg_touches_opp_box")
        out.append(row)
    return out


def build_league_season_summary(league_id: int, season: str, matches: list) -> dict:
    total = len(matches)
    home_wins = sum(1 for m in matches if m["home_score"] > m["away_score"])
    draws = sum(1 for m in matches if m["home_score"] == m["away_score"])
    away_wins = sum(1 for m in matches if m["home_score"] < m["away_score"])
    btts = sum(1 for m in matches if m["home_score"] > 0 and m["away_score"] > 0)
    clean_sheet_matches = sum(
        1 for m in matches if m["home_score"] == 0 or m["away_score"] == 0
    )
    avg_home_goals = sum(m["home_score"] for m in matches) / total
    avg_away_goals = sum(m["away_score"] for m in matches) / total
    return {
        "League_ID": league_id,
        "Season": season,
        "total_matches": total,
        "home_win_pct": round(100.0 * home_wins / total, 2),
        "draw_pct": round(100.0 * draws / total, 2),
        "away_win_pct": round(100.0 * away_wins / total, 2),
        "btts_pct": round(100.0 * btts / total, 2),
        "clean_sheet_pct": round(100.0 * clean_sheet_matches / total, 2),
        "avg_total_goals": round(avg_home_goals + avg_away_goals, 4),
        "home_away_goal_diff": round(avg_home_goals - avg_away_goals, 4),
    }


def build_over_under_thresholds(league_id: int, season: str, matches: list) -> list:
    total = len(matches)
    totals = [m["home_score"] + m["away_score"] for m in matches]
    out = []
    for threshold in OU_THRESHOLDS:
        over_count = sum(1 for t in totals if t > threshold)
        under_count = sum(1 for t in totals if t < threshold)
        out.append(
            {
                "League_ID": league_id,
                "Season": season,
                "threshold": threshold,
                "over_count": over_count,
                "under_count": under_count,
                "over_pct": round(100.0 * over_count / total, 2),
                "under_pct": round(100.0 * under_count / total, 2),
            }
        )
    return out


def build_score_distribution(league_id: int, season: str, matches: list) -> list:
    total = len(matches)
    counts = {}
    for m in matches:
        key = (m["home_score"], m["away_score"])
        counts[key] = counts.get(key, 0) + 1
    out = []
    for (home_score, away_score), count in counts.items():
        out.append(
            {
                "League_ID": league_id,
                "Season": season,
                "home_score": home_score,
                "away_score": away_score,
                "match_count": count,
                "pct": round(100.0 * count / total, 2),
            }
        )
    return out


def build_goal_minute_buckets(conn, league_id: int, season: str) -> list:
    rows = conn.execute(
        """
        SELECT ev.minute
        FROM fact_match_events ev
        JOIN dim_match dm ON ev.Match_ID = dm.Match_ID
        WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
          AND ev.event_type = 'Goal'
        """,
        (league_id, season),
    ).fetchall()
    minutes = [r[0] for r in rows]
    total_goals = len(minutes)
    out = []
    for label, lo, hi in MINUTE_BUCKETS:
        goal_count = sum(1 for m in minutes if lo <= m <= hi)
        out.append(
            {
                "League_ID": league_id,
                "Season": season,
                "bucket": label,
                "goal_count": goal_count,
                "pct": round(100.0 * goal_count / total_goals, 2) if total_goals else None,
            }
        )
    return out


def build_silver() -> None:
    conn = get_connection()
    try:
        status_rows = conn.execute(
            "SELECT status, COUNT(*) FROM dim_match GROUP BY status"
        ).fetchall()
        print("dim_match.status 分布:", status_rows)

        seasons = _seasons(conn)
        print(f"待聚合 (League_ID, Season): {seasons}")

        for league_id, season in seasons:
            matches = _matches(conn, league_id, season)

            team_rows = build_team_season_stats(conn, league_id, season, matches)
            conn.execute(
                "DELETE FROM silver_team_season_stats WHERE League_ID = ? AND Season = ?",
                (league_id, season),
            )
            _insert_many(conn, "silver_team_season_stats", SILVER_TEAM_SEASON_STATS_COLUMNS, team_rows)

            summary_row = build_league_season_summary(league_id, season, matches)
            conn.execute(
                "DELETE FROM silver_league_season_summary WHERE League_ID = ? AND Season = ?",
                (league_id, season),
            )
            _insert_many(
                conn, "silver_league_season_summary", SILVER_LEAGUE_SEASON_SUMMARY_COLUMNS, [summary_row]
            )

            ou_rows = build_over_under_thresholds(league_id, season, matches)
            conn.execute(
                "DELETE FROM silver_over_under_thresholds WHERE League_ID = ? AND Season = ?",
                (league_id, season),
            )
            _insert_many(
                conn, "silver_over_under_thresholds", SILVER_OVER_UNDER_THRESHOLDS_COLUMNS, ou_rows
            )

            score_rows = build_score_distribution(league_id, season, matches)
            conn.execute(
                "DELETE FROM silver_score_distribution WHERE League_ID = ? AND Season = ?",
                (league_id, season),
            )
            _insert_many(
                conn, "silver_score_distribution", SILVER_SCORE_DISTRIBUTION_COLUMNS, score_rows
            )

            bucket_rows = build_goal_minute_buckets(conn, league_id, season)
            conn.execute(
                "DELETE FROM silver_goal_minute_buckets WHERE League_ID = ? AND Season = ?",
                (league_id, season),
            )
            _insert_many(
                conn, "silver_goal_minute_buckets", SILVER_GOAL_MINUTE_BUCKETS_COLUMNS, bucket_rows
            )

            conn.commit()
            print(
                f"[{league_id} {season}] team_rows={len(team_rows)} ou_rows={len(ou_rows)} "
                f"score_rows={len(score_rows)} bucket_rows={len(bucket_rows)}"
            )
    finally:
        conn.close()

    print("=== Silver 聚合完成 ===")


if __name__ == "__main__":
    build_silver()
