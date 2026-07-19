"""
verify_league.py — 爬某联赛某赛季(已完赛场次),写入 data/verify_leagues.db。

只做验证用,复用 ingest_league.py 的 enumerate_fixtures()(纯函数,不碰 DB)
枚举赛季场次。三个抗崩溃机制:
    1. 分批写库:每 20 场 commit 一次(先把 20 场都抓完解析好,再统一写入 +
       commit——不是"边抓边写、攒够 20 场再提交",目的是不让一个共享连接的
       未提交事务跨越多场慢速网络请求,见 ingest_verify_match.py 顶部注释)。
    2. 断点续爬:开跑前先查 verify_leagues.db 里该 league_id 已有哪些
       match_id,跳过已入库的。
    3. 请求间隔:沿用 fotmob_client 现有 sleep 机制,不调短。

用法:
    python backend/verify/verify_league.py --league-id 87 --season 2025/2026
"""

import argparse
import os
import random
import sys
import time

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)
# ingest_league.py 自己 `from ingest_match import ...`(裸模块名),平时靠
# "作为入口脚本运行时 Python 自动把脚本所在目录塞进 sys.path" 这件事才能
# import 成功;这里是从外部把它当库导入,必须手动把 backend/ingest/ 也放
# 进 sys.path,否则 `ingest_match` 这个裸名字找不到。
sys.path.insert(0, os.path.join(_BACKEND_DIR, "ingest"))

from fotmob_client import FotMobClient
from ingest_league import enumerate_fixtures, _utc_to_date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_verify_match import fetch_match_data, write_match_data
from db_verify import get_verify_connection

BATCH_SIZE = 20


def _existing_match_ids(league_id: int) -> set:
    conn = get_verify_connection()
    try:
        rows = conn.execute(
            "SELECT Match_ID FROM dim_match WHERE League_ID = ?", (league_id,)
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def run(
    league_id: int,
    season: str,
    sleep: float = 0.3,
    sleep_jitter: float = 0.5,
    skip_existing: bool = True,
) -> None:
    client = FotMobClient()

    fixtures = enumerate_fixtures(client, league_id, season)
    fixtures = [f for f in fixtures if f["finished"] and not f["cancelled"]]
    fixtures.sort(key=lambda f: (f["utc"] or ""))

    if skip_existing:
        done = _existing_match_ids(league_id)
        before = len(fixtures)
        fixtures = [f for f in fixtures if f["match_id"] not in done]
        print(f"[league_id={league_id} season={season}] 跳过已落库场次: {before - len(fixtures)}/{before}")

    total = len(fixtures)
    print(f"[league_id={league_id} season={season}] 待落库已完赛场次: {total}")

    ok, fail = [], []
    pending_batch = []  # [(match_id, data), ...] 攒够 BATCH_SIZE 再统一写入 + commit

    def flush_batch():
        if not pending_batch:
            return
        conn = get_verify_connection()
        try:
            for mid, data in pending_batch:
                write_match_data(conn, mid, data)
            conn.commit()
        finally:
            conn.close()
        pending_batch.clear()

    for i, f in enumerate(fixtures, 1):
        mid = f["match_id"]
        date = _utc_to_date(f.get("utc"))
        try:
            data = fetch_match_data(mid, league_id=league_id, date=date, season=season)
            pending_batch.append((mid, data))
            ok.append(mid)
        except Exception as e:
            print(f"[league_id={league_id}] match_id={mid} 失败: {e}")
            fail.append((mid, str(e)))

        if len(pending_batch) >= BATCH_SIZE or i == total:
            flush_batch()
            print(f"[league_id={league_id} season={season}] 已提交至 {i}/{total} "
                  f"(成功 {len(ok)}, 失败 {len(fail)})")

        if i < total:
            time.sleep(sleep + random.uniform(0, sleep_jitter))

    flush_batch()  # 保险:万一循环末尾漏了

    print(f"=== [league_id={league_id} season={season}] 完成: {len(ok)}/{total} 成功, {len(fail)} 失败 ===")
    if fail:
        print(f"[league_id={league_id} season={season}] 失败详情: {fail}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-id", type=int, required=True)
    parser.add_argument("--season", type=str, default="2025/2026")
    parser.add_argument("--sleep", type=float, default=0.3)
    parser.add_argument("--sleep-jitter", type=float, default=0.5)
    parser.add_argument(
        "--skip-existing", action=argparse.BooleanOptionalAction, default=True
    )
    args = parser.parse_args()
    run(args.league_id, args.season, args.sleep, args.sleep_jitter, args.skip_existing)
