"""
init_db.py — 建本地 SQLite 数据库(data/allwin.db)及全部表结构。
重复运行安全:所有表用 CREATE TABLE IF NOT EXISTS。
"""

from db import DB_PATH, get_connection
from schema import (
    DIM_MATCH_COLUMNS,
    DIM_PLAYER_COLUMNS,
    SHOTMAP_COLUMNS,
    PLAYER_STATS_COLUMNS,
    TEAM_STATS_CORE_COLUMNS,
    LEAGUE_TABLE_CORE_COLUMNS,
    MATCH_EVENTS_CORE_COLUMNS,
    MATCH_LINEUP_CORE_COLUMNS,
    SEASON_PLAYER_STATS_CORE_COLUMNS,
    DIM_TEAM_I18N_COLUMNS,
    DIM_PLAYER_I18N_COLUMNS,
    SILVER_TEAM_SEASON_STATS_COLUMNS,
    SILVER_LEAGUE_SEASON_SUMMARY_COLUMNS,
    SILVER_OVER_UNDER_THRESHOLDS_COLUMNS,
    SILVER_SCORE_DISTRIBUTION_COLUMNS,
    SILVER_GOAL_MINUTE_BUCKETS_COLUMNS,
    _quote,
)


def _create_table(conn, table_name: str, columns: list, extra_sql: str = "") -> None:
    cols_sql = ", ".join(f"{_quote(name)} {sql_type}" for name, sql_type in columns)
    if extra_sql:
        cols_sql += f", {extra_sql}"
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({cols_sql})")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        _create_table(conn, "dim_match", DIM_MATCH_COLUMNS)
        _create_table(conn, "dim_player", DIM_PLAYER_COLUMNS)
        _create_table(conn, "fact_shotmap", SHOTMAP_COLUMNS)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_shotmap_match "
            "ON fact_shotmap (Match_ID)"
        )
        _create_table(conn, "fact_player_match_stats", PLAYER_STATS_COLUMNS)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_player_match_stats_match "
            "ON fact_player_match_stats (Match_ID)"
        )
        _create_table(
            conn,
            "fact_team_match_stats",
            TEAM_STATS_CORE_COLUMNS,
            extra_sql="extra_json TEXT",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_team_match_stats_match "
            "ON fact_team_match_stats (Match_ID)"
        )

        _create_table(
            conn,
            "fact_league_table",
            LEAGUE_TABLE_CORE_COLUMNS,
            extra_sql="extra_json TEXT",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_league_table_season "
            "ON fact_league_table (League_ID, Season, table_type)"
        )

        _create_table(
            conn,
            "fact_match_events",
            MATCH_EVENTS_CORE_COLUMNS,
            extra_sql="extra_json TEXT",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_match_events_match "
            "ON fact_match_events (Match_ID)"
        )

        _create_table(
            conn,
            "fact_match_lineup",
            MATCH_LINEUP_CORE_COLUMNS,
            extra_sql="extra_json TEXT",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_match_lineup_match "
            "ON fact_match_lineup (Match_ID)"
        )

        _create_table(
            conn,
            "fact_season_player_stats",
            SEASON_PLAYER_STATS_CORE_COLUMNS,
            extra_sql="extra_json TEXT",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_season_player_stats_season "
            "ON fact_season_player_stats (League_ID, Season, stat_name)"
        )

        _create_table(conn, "dim_team_i18n", DIM_TEAM_I18N_COLUMNS)
        _create_table(conn, "dim_player_i18n", DIM_PLAYER_I18N_COLUMNS)

        _create_table(conn, "silver_team_season_stats", SILVER_TEAM_SEASON_STATS_COLUMNS)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_silver_team_season_stats_season "
            "ON silver_team_season_stats (League_ID, Season)"
        )

        _create_table(conn, "silver_league_season_summary", SILVER_LEAGUE_SEASON_SUMMARY_COLUMNS)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_silver_league_season_summary_season "
            "ON silver_league_season_summary (League_ID, Season)"
        )

        _create_table(conn, "silver_over_under_thresholds", SILVER_OVER_UNDER_THRESHOLDS_COLUMNS)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_silver_over_under_thresholds_season "
            "ON silver_over_under_thresholds (League_ID, Season)"
        )

        _create_table(conn, "silver_score_distribution", SILVER_SCORE_DISTRIBUTION_COLUMNS)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_silver_score_distribution_season "
            "ON silver_score_distribution (League_ID, Season)"
        )

        _create_table(conn, "silver_goal_minute_buckets", SILVER_GOAL_MINUTE_BUCKETS_COLUMNS)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_silver_goal_minute_buckets_season "
            "ON silver_goal_minute_buckets (League_ID, Season)"
        )

        conn.commit()
    finally:
        conn.close()
    print(f"数据库已就绪: {DB_PATH}")


if __name__ == "__main__":
    init_db()
