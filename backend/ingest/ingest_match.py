"""
ingest_match.py — 抓取单场比赛详情并落库。

用法:
    python backend/ingest/ingest_match.py <match_id> [--league-id 47] [--date 2026-04-11]

幂等策略(按 match 粒度):
    - dim_match / dim_player: INSERT OR REPLACE(upsert)
    - fact_shotmap / fact_player_match_stats / fact_team_match_stats /
      fact_match_events / fact_match_lineup:
      先 DELETE WHERE Match_ID=? 再插入,不依赖行级唯一键
      (shotmap 等原始数据本身没有稳定的行 id)
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection
from fotmob_client import FotMobClient
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


def _col_names(columns: list) -> list:
    return [name for name, _ in columns]


def _upsert(conn, table: str, columns: list, row: dict) -> None:
    names = _col_names(columns)
    placeholders = ", ".join(["?"] * len(names))
    col_sql = ", ".join(_quote(n) for n in names)
    values = [row.get(n) for n in names]
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({col_sql}) VALUES ({placeholders})",
        values,
    )


def _insert_many(conn, table: str, columns: list, rows: list) -> None:
    if not rows:
        return
    names = _col_names(columns)
    placeholders = ", ".join(["?"] * len(names))
    col_sql = ", ".join(_quote(n) for n in names)
    values = [[row.get(n) for n in names] for row in rows]
    conn.executemany(
        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})",
        values,
    )


def _rows_with_extra_json(rows: list) -> list:
    """rows 中每行的 'extra' 字典转成 JSON 字符串存入 'extra_json' 键。"""
    out = []
    for row in rows:
        row = dict(row)
        extra = row.pop("extra", {})
        row["extra_json"] = json.dumps(extra, ensure_ascii=False, default=str)
        out.append(row)
    return out


def ingest_match(
    match_id: int, league_id: int = None, date: str = None, season: str = None
) -> None:
    client = FotMobClient()
    page_props = client.match_details(match_id)

    dim_kwargs = {"league_id": league_id, "date": date}
    if season is not None:
        dim_kwargs["season"] = season
    dim = client.parse_match_dim(page_props, match_id, **dim_kwargs)

    # 数据完整性闸门：这几个字段缺失通常意味着页面结构漂移或抓到了半页数据，
    # 不能静默写入 NULL 污染 dim_match——让它像网络错误一样抛出、计入失败列表，
    # 方便暴露问题并用 --match-ids 重试，而不是留下一条看似正常、实则残缺的行。
    required = ("Home_Team_ID", "Away_Team_ID", "Date", "status")
    missing = [k for k in required if dim.get(k) is None]
    if missing:
        raise ValueError(
            f"match_id={match_id} 关键字段缺失 {missing}，疑似页面结构漂移，拒绝写入"
        )
    shots = client.parse_shotmap_records(page_props, match_id)
    team_stats = client.parse_team_stats_records(page_props, match_id)
    player_stats = client.parse_player_stats_records(page_props, match_id)
    events = client.parse_match_events(page_props, match_id)
    lineup_rows = client.parse_lineup_records(page_props, match_id)

    conn = get_connection()
    try:
        # dim_match / dim_player — upsert
        _upsert(conn, "dim_match", DIM_MATCH_COLUMNS, dim)

        for p in player_stats:
            _upsert(
                conn,
                "dim_player",
                DIM_PLAYER_COLUMNS,
                {"Player_ID": p.get("Player_ID"), "Player_Name": p.get("player_name")},
            )

        # fact_* — 按 match 粒度先清后插
        conn.execute("DELETE FROM fact_shotmap WHERE Match_ID = ?", (match_id,))
        _insert_many(conn, "fact_shotmap", SHOTMAP_COLUMNS, shots)

        conn.execute(
            "DELETE FROM fact_player_match_stats WHERE Match_ID = ?", (match_id,)
        )
        _insert_many(
            conn, "fact_player_match_stats", PLAYER_STATS_COLUMNS, player_stats
        )

        conn.execute(
            "DELETE FROM fact_team_match_stats WHERE Match_ID = ?", (match_id,)
        )
        core_names = set(_col_names(TEAM_STATS_CORE_COLUMNS))
        team_rows = []
        for rec in team_stats:
            core_row = {k: rec.get(k) for k in core_names}
            extra = {k: v for k, v in rec.items() if k not in core_names}
            core_row["extra_json"] = json.dumps(extra, ensure_ascii=False)
            team_rows.append(core_row)
        _insert_many(
            conn,
            "fact_team_match_stats",
            TEAM_STATS_CORE_COLUMNS + [("extra_json", "TEXT")],
            team_rows,
        )

        conn.execute("DELETE FROM fact_match_events WHERE Match_ID = ?", (match_id,))
        _insert_many(
            conn,
            "fact_match_events",
            MATCH_EVENTS_CORE_COLUMNS + [("extra_json", "TEXT")],
            _rows_with_extra_json(events),
        )

        conn.execute("DELETE FROM fact_match_lineup WHERE Match_ID = ?", (match_id,))
        _insert_many(
            conn,
            "fact_match_lineup",
            MATCH_LINEUP_CORE_COLUMNS + [("extra_json", "TEXT")],
            _rows_with_extra_json(lineup_rows),
        )

        conn.commit()
    finally:
        conn.close()

    print(f"match_id={match_id} 落库完成: "
          f"dim_match=1, dim_player={len(player_stats)}, "
          f"fact_shotmap={len(shots)}, "
          f"fact_player_match_stats={len(player_stats)}, "
          f"fact_team_match_stats={len(team_stats)}, "
          f"fact_match_events={len(events)}, "
          f"fact_match_lineup={len(lineup_rows)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("match_id", type=int)
    parser.add_argument("--league-id", type=int, default=None)
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--season", type=str, default=None)
    args = parser.parse_args()

    ingest_match(
        args.match_id, league_id=args.league_id, date=args.date, season=args.season
    )
