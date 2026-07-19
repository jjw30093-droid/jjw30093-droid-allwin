"""
merge_into_allwin.py — 把 verify_leagues.db 里的西甲/意甲/德甲/法甲增量合并进
allwin.db(生产库)。

🔴 纯增量,绝不碰 allwin.db 里 League_ID=47(英超)的任何一行:
    - dim_match / dim_player 用 INSERT OR IGNORE(主键天然防重复,已存在的
      行原样保留,不 UPDATE)。
    - fact_shotmap / fact_player_match_stats / fact_team_match_stats /
      fact_match_events / fact_match_lineup 没有能防重复的主键,用
      "先删后插"保证幂等,但 DELETE/INSERT 的 WHERE 范围严格限定在
      "verify_leagues.db 里这 4 个联赛的 Match_ID 集合"——这个集合通过
      子查询 `SELECT Match_ID FROM vdb.dim_match WHERE League_ID IN (...)`
      现算,不可能包含英超的 Match_ID(FotMob match_id 全局唯一,且
      verify_leagues.db 里已确认 League_ID=47 的行数是 0)。
    - 整个合并在一个事务里,任何一步失败整体回滚,不留半合并状态。

用法:
    python backend/verify/merge_into_allwin.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import DB_PATH, get_connection
from db_verify import VERIFY_DB_PATH

NEW_LEAGUE_IDS = (87, 55, 54, 53)

MATCH_SCOPED_TABLES = [
    "fact_shotmap",
    "fact_player_match_stats",
    "fact_team_match_stats",
    "fact_match_events",
    "fact_match_lineup",
]

MATCH_ID_SUBQUERY = (
    f"SELECT Match_ID FROM vdb.dim_match WHERE League_ID IN {NEW_LEAGUE_IDS}"
)


def main() -> None:
    conn = get_connection()
    try:
        conn.execute(f"ATTACH DATABASE '{VERIFY_DB_PATH}' AS vdb")

        # 🔴 安全前置:verify_leagues.db 里不能有英超(League_ID=47)的行,
        # 否则直接停止,不合并。
        contamination = conn.execute(
            "SELECT COUNT(*) FROM vdb.dim_match WHERE League_ID = 47"
        ).fetchone()[0]
        if contamination > 0:
            raise RuntimeError(
                f"verify_leagues.db 里发现 {contamination} 行 League_ID=47,"
                f"疑似英超数据混入验证库,停止合并"
            )
        print(f"安全前置确认: verify_leagues.db 里 League_ID=47 行数 = {contamination}(应为 0)")

        new_match_count = conn.execute(
            f"SELECT COUNT(*) FROM vdb.dim_match WHERE League_ID IN {NEW_LEAGUE_IDS}"
        ).fetchone()[0]
        print(f"待合并的 4 联赛场次: {new_match_count}")

        conn.execute("BEGIN")

        # dim_match: INSERT OR IGNORE,主键(Match_ID)天然防重复,已存在的
        # 行不会被覆盖(不是 UPDATE)。
        before = conn.execute("SELECT COUNT(*) FROM dim_match").fetchone()[0]
        conn.execute(
            f"""
            INSERT OR IGNORE INTO dim_match
            SELECT * FROM vdb.dim_match WHERE League_ID IN {NEW_LEAGUE_IDS}
            """
        )
        after = conn.execute("SELECT COUNT(*) FROM dim_match").fetchone()[0]
        print(f"dim_match: {before} -> {after}(新增 {after - before})")

        # dim_player: 同理 INSERT OR IGNORE,Player_ID 可能和英超球员重叠
        # (转会球员),已存在的直接跳过,不覆盖。
        before = conn.execute("SELECT COUNT(*) FROM dim_player").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO dim_player SELECT * FROM vdb.dim_player")
        after = conn.execute("SELECT COUNT(*) FROM dim_player").fetchone()[0]
        print(f"dim_player: {before} -> {after}(新增 {after - before})")

        # 剩下几张表没有能防重复的主键,先删(严格限定在新联赛 Match_ID
        # 集合内)再整体插入,保证可重跑不产生重复行。
        for table in MATCH_SCOPED_TABLES:
            before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.execute(
                f"DELETE FROM {table} WHERE Match_ID IN ({MATCH_ID_SUBQUERY})"
            )
            conn.execute(
                f"""
                INSERT INTO {table}
                SELECT * FROM vdb.{table}
                WHERE Match_ID IN ({MATCH_ID_SUBQUERY})
                """
            )
            after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}: {before} -> {after}(新增 {after - before})")

        conn.commit()
        print("\n=== 合并事务已提交 ===")
    except Exception:
        conn.rollback()
        print("\n=== 合并失败,已整体回滚,allwin.db 未受影响 ===")
        raise
    finally:
        conn.execute("DETACH DATABASE vdb")
        conn.close()


if __name__ == "__main__":
    main()
