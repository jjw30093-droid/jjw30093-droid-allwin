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
        conn.commit()
    finally:
        conn.close()
    print(f"数据库已就绪: {DB_PATH}")


if __name__ == "__main__":
    init_db()
