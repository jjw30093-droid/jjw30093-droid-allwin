"""
init_verify_db.py — 建 data/verify_leagues.db 的表结构,只建 Bronze 层
(dim_match / dim_player / fact_shotmap / fact_player_match_stats /
fact_team_match_stats / fact_match_events / fact_match_lineup),
列定义直接复用 backend/schema.py,和 allwin.db 的 Bronze 表结构完全一致,
保证后续字段覆盖率对照是同口径比较。

用法:
    python backend/verify/init_verify_db.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schema import (
    DIM_MATCH_COLUMNS,
    DIM_PLAYER_COLUMNS,
    SHOTMAP_COLUMNS,
    PLAYER_STATS_COLUMNS,
    TEAM_STATS_CORE_COLUMNS,
    MATCH_EVENTS_CORE_COLUMNS,
    MATCH_LINEUP_CORE_COLUMNS,
    _quote,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_verify import VERIFY_DB_PATH, get_verify_connection


def _create_table(conn, table_name: str, columns: list, extra_sql: str = "") -> None:
    cols_sql = ", ".join(f"{_quote(name)} {sql_type}" for name, sql_type in columns)
    if extra_sql:
        cols_sql += f", {extra_sql}"
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({cols_sql})")


def init_verify_db() -> None:
    VERIFY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_verify_connection()
    try:
        _create_table(conn, "dim_match", DIM_MATCH_COLUMNS)
        _create_table(conn, "dim_player", DIM_PLAYER_COLUMNS)
        _create_table(conn, "fact_shotmap", SHOTMAP_COLUMNS)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_v_shotmap_match ON fact_shotmap (Match_ID)")
        _create_table(conn, "fact_player_match_stats", PLAYER_STATS_COLUMNS)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_v_player_stats_match ON fact_player_match_stats (Match_ID)")
        _create_table(conn, "fact_team_match_stats", TEAM_STATS_CORE_COLUMNS, extra_sql="extra_json TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_v_team_stats_match ON fact_team_match_stats (Match_ID)")
        _create_table(conn, "fact_match_events", MATCH_EVENTS_CORE_COLUMNS, extra_sql="extra_json TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_v_events_match ON fact_match_events (Match_ID)")
        _create_table(conn, "fact_match_lineup", MATCH_LINEUP_CORE_COLUMNS, extra_sql="extra_json TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_v_lineup_match ON fact_match_lineup (Match_ID)")
        conn.commit()
    finally:
        conn.close()
    print(f"验证库已就绪: {VERIFY_DB_PATH}")


if __name__ == "__main__":
    init_verify_db()
