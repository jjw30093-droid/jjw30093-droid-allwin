"""预测对外查询:当前可展示快照(draft/legacy_unverified 永不对外)。"""

import sqlite3


def current_public_snapshot(conn: sqlite3.Connection, match_id: int):
    """该场比赛当前有效的公开快照:published/locked/retracted,非被取代,最新一条。"""
    return conn.execute(
        """SELECT * FROM prediction_snapshots
           WHERE match_id=? AND visibility='public'
             AND status IN ('published','locked','retracted')
             AND superseded_by IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        (match_id,),
    ).fetchone()
