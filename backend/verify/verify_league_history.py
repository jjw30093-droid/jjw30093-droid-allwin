"""
verify_league_history.py — 对某联赛顺序爬多个历史赛季,写入 data/verify_leagues.db。

每季内部复用 verify_league.py 的 run()(断点续爬 + 每 20 场批量 commit 已内置)。
一个联赛一个进程,内部按赛季顺序跑,4 个联赛各自独立进程并行。

用法:
    python backend/verify/verify_league_history.py --league-id 87 \
        --seasons 2020/2021,2021/2022,2022/2023,2023/2024,2024/2025
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_league import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-id", type=int, required=True)
    parser.add_argument("--seasons", type=str, required=True, help="逗号分隔,如 2020/2021,2021/2022")
    parser.add_argument("--sleep", type=float, default=0.3)
    parser.add_argument("--sleep-jitter", type=float, default=0.5)
    args = parser.parse_args()

    seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]
    print(f"[league_id={args.league_id}] 待爬赛季: {seasons}")

    for season in seasons:
        print(f"\n########## [league_id={args.league_id}] season={season} 开始 ##########")
        run(args.league_id, season, args.sleep, args.sleep_jitter, skip_existing=True)

    print(f"\n====== [league_id={args.league_id}] 全部 {len(seasons)} 个历史赛季处理完毕 ======")


if __name__ == "__main__":
    main()
