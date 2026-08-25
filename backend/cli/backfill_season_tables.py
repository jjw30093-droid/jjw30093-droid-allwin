"""按联赛+赛季回填季级表(fact_league_table / fact_season_player_stats)。

背景:季级表此前只在英超(47)的整季 ingest 里落过库;五大联赛其余四家
(53/54/55/87)的逐场比赛数据早已在 dim_match/fact_*,但积分榜与球员榜
从未回填,导致 /api/v1/leagues/{id}/standings 与球员/球队榜页对这些联赛
恒为空。本 CLI 复用 ingest_league.py 的既有构件做有边界的补采:

    python -m backend.cli.backfill_season_tables --league-id 87
    python -m backend.cli.backfill_season_tables --league-id 87 \
        --seasons 2020/2021,2021/2022 --players-for 2025/2026

- 每个赛季恰好 1 次 league_matches() 请求(积分榜随响应返回,解析零请求);
- 球员榜维度抓取(~37 次/赛季)开销大,只对 --players-for 显式列出的赛季执行;
- 写入沿用 ingest_season_tables 的 DELETE+INSERT 幂等风格,但加了 fail-loud
  保护:解析结果为空时不执行 DELETE(不能用一次坏响应清掉已有数据);
- 响应身份校验:details.id 必须等于请求的 league_id,selectedSeason 必须等于
  请求的赛季——不一致直接判该赛季失败,不落库。
"""

import argparse
import os
import sys
import time
from urllib.parse import quote

# ingest_* 是 script 风格模块(内部 `from db import ...`),不可直接包导入;
# 沿用 ingest_league.py 头部同款 sys.path 桥接,不复制其函数体。
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_BACKEND_DIR, os.path.join(_BACKEND_DIR, "ingest")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ingest_league import _league_matches_with_retry  # noqa: E402
from ingest_match import _insert_many, _rows_with_extra_json  # noqa: E402

from backend.db import get_connection  # noqa: E402
from backend.fotmob_client import FotMobClient  # noqa: E402
from backend.schema import (  # noqa: E402
    LEAGUE_TABLE_CORE_COLUMNS,
    SEASON_PLAYER_STATS_CORE_COLUMNS,
)


def _dim_match_seasons(conn, league_id: int) -> list:
    rows = conn.execute(
        "SELECT DISTINCT Season FROM dim_match WHERE League_ID = ? ORDER BY Season",
        (league_id,),
    ).fetchall()
    return [r[0] for r in rows]


def _verify_identity(data: dict, league_id: int, season: str) -> str | None:
    """返回 None 表示通过;否则返回失败原因(不落库)。

    2026-08-25 起委托给统一实现(backend/ingest/season_identity.py,
    CLAUDE.md §6.3):本脚本按赛季循环、宁可跳过单个赛季也要继续,所以保留
    "返回原因字符串"的调用形状,只把判定本体收敛到唯一出处。
    """
    from backend.ingest.season_identity import SeasonIdentityError, verify_season_echo

    try:
        verify_season_echo(data, league_id, season)
    except SeasonIdentityError as e:
        return str(e)
    return None


def backfill_one_season(
    client: FotMobClient,
    conn,
    league_id: int,
    season: str,
    with_players: bool,
) -> dict:
    result = {"league_id": league_id, "season": season, "requests": 0,
              "table_rows": 0, "player_rows": 0, "status": "FAILED", "reason": None}

    data = _league_matches_with_retry(client, league_id, quote(season, safe=""))
    result["requests"] += 1

    reason = _verify_identity(data, league_id, season)
    if reason:
        result["reason"] = f"identity: {reason}"
        return result

    table_rows = client.parse_league_table(data, league_id, season)
    if not table_rows:
        # 空结果不 DELETE:不能用一次坏响应/结构漂移清掉已有数据
        result["reason"] = "parse_league_table 返回 0 行,拒绝落库"
        return result

    conn.execute(
        "DELETE FROM fact_league_table WHERE League_ID=? AND Season=?",
        (league_id, season),
    )
    _insert_many(
        conn,
        "fact_league_table",
        LEAGUE_TABLE_CORE_COLUMNS + [("extra_json", "TEXT")],
        _rows_with_extra_json(table_rows),
    )
    result["table_rows"] = len(table_rows)

    if with_players:
        # parse_season_player_stats 内部逐维度 fetchAllUrl(~37 次),单维度失败
        # 记 warning 跳过,不拖垮整季
        stat_defs = (data.get("stats") or {}).get("players") or []
        player_rows = client.parse_season_player_stats(data, league_id, season)
        result["requests"] += len(stat_defs)
        if player_rows:
            conn.execute(
                "DELETE FROM fact_season_player_stats WHERE League_ID=? AND Season=?",
                (league_id, season),
            )
            _insert_many(
                conn,
                "fact_season_player_stats",
                SEASON_PLAYER_STATS_CORE_COLUMNS + [("extra_json", "TEXT")],
                _rows_with_extra_json(player_rows),
            )
            result["player_rows"] = len(player_rows)
        else:
            result["reason"] = "球员榜解析 0 行(积分榜已正常落库)"

    conn.commit()
    result["status"] = "OK"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league-id", type=int, required=True)
    ap.add_argument("--seasons", type=str, default=None,
                    help="逗号分隔;缺省=该联赛在 dim_match 里出现过的全部赛季")
    ap.add_argument("--players-for", type=str, default="",
                    help="逗号分隔;只有列出的赛季才回填球员榜(每季约 37 次额外请求)")
    ap.add_argument("--sleep", type=float, default=0.5, help="赛季之间的间隔秒数")
    args = ap.parse_args()

    conn = get_connection()
    try:
        seasons = (
            [s.strip() for s in args.seasons.split(",") if s.strip()]
            if args.seasons
            else _dim_match_seasons(conn, args.league_id)
        )
        players_for = {s.strip() for s in args.players_for.split(",") if s.strip()}
        if not seasons:
            print(f"league_id={args.league_id} 在 dim_match 无任何赛季,无事可做")
            return 1

        client = FotMobClient()
        results = []
        for i, season in enumerate(seasons):
            r = backfill_one_season(
                client, conn, args.league_id, season, with_players=season in players_for
            )
            results.append(r)
            print(f"[{r['status']}] L{r['league_id']} {r['season']}: "
                  f"table={r['table_rows']} players={r['player_rows']} "
                  f"requests={r['requests']}"
                  + (f" reason={r['reason']}" if r["reason"] else ""))
            if i < len(seasons) - 1:
                time.sleep(args.sleep)

        total_req = sum(r["requests"] for r in results)
        failed = [r for r in results if r["status"] != "OK"]
        print(f"\n合计: {len(results)} 赛季, {total_req} 次真实请求, {len(failed)} 失败")
        return 1 if failed else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
