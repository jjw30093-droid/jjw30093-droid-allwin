"""odds.db Silver/Gold 构建 CLI / Worker job(odds_silver_build)。

Bronze 快照 → silver_odds_moves / silver_event_moves → gold_move_cooccurrence。
全部幂等(UNIQUE + INSERT OR IGNORE),重跑不重复。
needs_review / rejected 的映射不产出派生(silver 层过滤)。

用法:.venv/bin/python -m backend.cli.build_odds_silver [--window-seconds 900]
"""

import argparse
import sys

from backend.db.connections import connect_rw
from backend.silver.odds_moves import build_cooccurrence, build_event_moves, build_odds_moves


def run(window_seconds: int = 900) -> dict:
    conn = connect_rw("odds")
    try:
        odds_moves = build_odds_moves(conn)
        event_moves = build_event_moves(conn)
        cooccurrence = build_cooccurrence(conn, window_seconds=window_seconds)
        return {
            "odds_moves_inserted": odds_moves,
            "event_moves_inserted": event_moves,
            "cooccurrence_inserted": cooccurrence,
        }
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="odds.db Silver/Gold 构建(幂等)")
    ap.add_argument("--window-seconds", type=int, default=900, help="时间共现窗口(秒)")
    args = ap.parse_args(argv)
    result = run(window_seconds=args.window_seconds)
    print(f"[build_odds_silver] odds_moves +{result['odds_moves_inserted']},"
          f" event_moves +{result['event_moves_inserted']},"
          f" cooccurrence +{result['cooccurrence_inserted']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
