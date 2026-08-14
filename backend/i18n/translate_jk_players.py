"""
translate_jk_players.py — J1联赛(223)/K联赛(9080)球员中文全名直译(qwen-mt-plus)。

复用 translate_players.py 的 translate_one() 直译+重试逻辑,范围收窄到这两个
联赛真实出场过的球员(fact_player_match_stats JOIN dim_match)。

带截止时间(--deadline "HH:MM"):到点后不再提交新的翻译请求,已提交的当批
自然收尾,不强杀连接以免写坏正在进行的 UPDATE。

用法:
    python -m backend.i18n.translate_jk_players --deadline 07:00 [--limit N]
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"
))

import dashscope
from db import get_connection

from i18n.translate_players import translate_one

dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]

LEAGUE_IDS = (223, 9080)  # J1 League / K League 1


def _pending_players(limit: int | None) -> list[tuple[str, str]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT s.Player_ID, MIN(p.Player_Name)
            FROM fact_player_match_stats s
            JOIN dim_match m ON m.Match_ID = s.Match_ID
            JOIN dim_player p ON p.Player_ID = s.Player_ID
            WHERE m.League_ID IN ({",".join("?" for _ in LEAGUE_IDS)})
              AND p.Player_Name IS NOT NULL
            GROUP BY s.Player_ID
            """,
            LEAGUE_IDS,
        ).fetchall()
        done = {
            r[0]
            for r in conn.execute(
                "SELECT Player_ID FROM dim_player_i18n WHERE name_zh IS NOT NULL"
            ).fetchall()
        }
    finally:
        conn.close()
    pending = [(pid, name) for pid, name in rows if pid not in done]
    if limit:
        pending = pending[:limit]
    return pending


def _parse_deadline(hhmm: str | None) -> datetime | None:
    if not hhmm:
        return None
    h, m = hhmm.split(":")
    now = datetime.now()
    return now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--deadline", type=str, default=None, help="本地时间 HH:MM,过点不再提交新请求")
    args = parser.parse_args()

    deadline = _parse_deadline(args.deadline)
    pending = _pending_players(args.limit)
    total = len(pending)
    print(f"J1/K League 待直译球员: {total}" + (f",截止 {args.deadline}" if deadline else ""))

    ok, failed, stopped_early = 0, [], False
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {}
        it = iter(pending)
        # 先提交一批,之后每完成一个再补提交一个——这样能在截止前随时停止入队,
        # 而不是一次性把全部 total 个任务都扔进线程池(那样到点也拦不住)。
        for _ in range(min(6, total)):
            try:
                pid, name = next(it)
            except StopIteration:
                break
            futures[pool.submit(translate_one, name)] = (pid, name)

        submitted = len(futures)
        while futures:
            done_set, _ = __import__("concurrent.futures", fromlist=["wait"]).wait(
                futures, return_when="FIRST_COMPLETED"
            )
            for fut in done_set:
                pid, name = futures.pop(fut)
                result = fut.result()
                conn = get_connection()
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO dim_player_i18n
                           (Player_ID, name_en, name_zh, name_zh_short, source, model, confidence, needs_review, updated_at)
                           VALUES (?, ?, ?, ?, 'qwen-mt-plus', 'qwen-mt-plus', NULL, ?, datetime('now'))""",
                        (pid, name, result.get("name_zh"), result.get("name_zh_short"), result.get("needs_review", 1)),
                    )
                    conn.commit()
                finally:
                    conn.close()
                if result.get("name_zh"):
                    ok += 1
                else:
                    failed.append((pid, name, result.get("error")))
                if (ok + len(failed)) % 100 == 0:
                    print(f"进度 {ok + len(failed)}/{total}(成功 {ok})")

                if deadline and datetime.now() >= deadline:
                    if not stopped_early:
                        print(f"到达截止时间 {args.deadline},停止提交新任务(已提交 {submitted})")
                        stopped_early = True
                    continue
                try:
                    npid, nname = next(it)
                except StopIteration:
                    continue
                futures[pool.submit(translate_one, nname)] = (npid, nname)
                submitted += 1

    print(f"=== 完成: {ok}/{submitted} 成功, {len(failed)} 失败(总待处理 {total}"
          + (", 因截止时间提前停止" if stopped_early else "") + ") ===")
    if failed:
        print("失败样例(前10):", failed[:10])


if __name__ == "__main__":
    main()
