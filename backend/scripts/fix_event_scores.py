"""
fix_event_scores.py — 一次性迁移脚本：修正 fact_match_events 里 Goal 事件的
home_score/away_score。

背景：旧版 parse_match_events 直接存了 FotMob 原始 homeScore/awayScore 字段，
但这两个字段在 Goal 事件上记录的是"进球发生前"的比分，真正的"进球后"比分在
FotMob 原始事件的 newScore 字段（[home, away]），旧代码没有映射进核心列，但
原样保留在了 extra_json 里——因此这里不需要重新请求网络，直接用已入库的
extra_json 反推更正即可。

用法:
    python backend/scripts/fix_event_scores.py [--dry-run]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只统计不实际写入")
    args = parser.parse_args()

    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT Match_ID, event_index, home_score, away_score, extra_json
            FROM fact_match_events WHERE event_type='Goal'
        """).fetchall()

        to_fix = []
        no_new_score = 0
        already_correct = 0
        for match_id, event_index, home_score, away_score, extra_json in rows:
            extra = json.loads(extra_json or "{}")
            new_score = extra.get("newScore")
            if not (isinstance(new_score, list) and len(new_score) == 2):
                no_new_score += 1
                continue
            new_h, new_a = new_score[0], new_score[1]
            if new_h == home_score and new_a == away_score:
                already_correct += 1
                continue
            to_fix.append((new_h, new_a, match_id, event_index))

        print(f"Goal 事件总数: {len(rows)}")
        print(f"extra_json 里没有 newScore 的行数(跳过,无法修正): {no_new_score}")
        print(f"本来就正确的行数: {already_correct}")
        print(f"需要修正的行数: {len(to_fix)}")

        if args.dry_run:
            print("(--dry-run，未实际写入)")
            return

        conn.executemany("""
            UPDATE fact_match_events SET home_score=?, away_score=?
            WHERE Match_ID=? AND event_index=?
        """, to_fix)
        conn.commit()
        print(f"已修正 {len(to_fix)} 行。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
