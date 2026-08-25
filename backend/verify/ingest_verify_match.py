"""
ingest_verify_match.py — 抓取单场比赛详情,写入 data/verify_leagues.db(验证专用)。

和 backend/ingest/ingest_match.py 是同一套解析逻辑(同一个 FotMobClient,同一批
parse_* 方法、同一批 schema.py 列定义),唯一区别是落库目标——这里写
verify_leagues.db,不写生产的 allwin.db,不复用 ingest_match.py 里硬编码
get_connection() 的那个函数体,避免为了"能传自定义连接"去改动生产代码。

🔴 fetch(网络,慢,单场几秒到偶尔十几分钟不等)和 write(本地 DB 写入,快)
拆成两个函数,是为了支持"每 20 场 commit 一次"又不产生跨进程锁等待风险:
本次 4 联赛并行爬,如果用一个共享连接跨 20 场"边抓边写"再统一 commit,这个
事务会从第 1 场的第一次 write 开始一直开到第 20 场 commit 为止——期间任何
一场抓取变慢(实测出现过单请求超时十几分钟),同一份 verify_leagues.db 上
其他联赛进程的写入都会被这个未提交事务卡住,busy_timeout(30s)撑不了那么
久,会直接报 "database is locked"。拆开后,fetch_match_data() 完全不碰
连接,write_match_data() 只做本地写入(毫秒级),批量 commit 只需要扛住
"20 场本地写入"的时长,不需要扛住"20 场网络抓取"的时长。

用法(通常由 verify_league.py 调用,不单独跑):
    from ingest_verify_match import fetch_match_data, write_match_data
    data = fetch_match_data(match_id, league_id=87, date="2025-08-16", season="2025/2026")
    write_match_data(conn, match_id, data)  # conn 由调用方管理 commit 时机
"""

import json
import os
import sys
from typing import Optional, Union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fotmob_client import FotMobClient
from ingest.ingest_match import _upsert, _insert_many, _rows_with_extra_json, _col_names
from schema import (
    DIM_MATCH_COLUMNS,
    DIM_PLAYER_COLUMNS,
    SHOTMAP_COLUMNS,
    PLAYER_STATS_COLUMNS,
    TEAM_STATS_CORE_COLUMNS,
    MATCH_EVENTS_CORE_COLUMNS,
    MATCH_LINEUP_CORE_COLUMNS,
    MOMENTUM_COLUMNS,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_verify import get_verify_connection


def fetch_match_data(
    match_id: Union[int, str],
    league_id: Optional[int] = None,
    date: Optional[str] = None,
    season: Optional[str] = None,
) -> dict:
    """只做网络抓取 + 解析,完全不碰 DB 连接。"""
    client = FotMobClient()
    page_props = client.match_details(match_id)

    dim = client.parse_match_dim(page_props, match_id, league_id=league_id, date=date)
    # parse_match_dim 不再输出 Season(2026-08-25,CLAUDE.md §6.3)。验证库
    # (verify_leagues.db)没有赛程同步这一层,Season 由调用方(verify_league.py
    # 的 --season,required)显式注入——这是验证专用库的孤立约定,不回流生产。
    dim["Season"] = season

    required = ("Home_Team_ID", "Away_Team_ID", "Date", "status")
    missing = [k for k in required if dim.get(k) is None]
    if missing:
        raise ValueError(f"match_id={match_id} 关键字段缺失 {missing},拒绝写入")

    return {
        "dim": dim,
        "shots": client.parse_shotmap_records(page_props, match_id),
        "team_stats": client.parse_team_stats_records(page_props, match_id),
        "player_stats": client.parse_player_stats_records(page_props, match_id),
        "events": client.parse_match_events(page_props, match_id),
        "lineup_rows": client.parse_lineup_records(page_props, match_id),
        "momentum_rows": client.parse_momentum_records(page_props, match_id),
    }


def write_match_data(conn, match_id: Union[int, str], data: dict) -> None:
    """只做本地写入,不 commit——commit 时机由调用方(批量)决定。"""
    _upsert(conn, "dim_match", DIM_MATCH_COLUMNS, data["dim"])

    for p in data["player_stats"]:
        _upsert(
            conn,
            "dim_player",
            DIM_PLAYER_COLUMNS,
            {"Player_ID": p.get("Player_ID"), "Player_Name": p.get("player_name")},
        )

    conn.execute("DELETE FROM fact_shotmap WHERE Match_ID = ?", (match_id,))
    _insert_many(conn, "fact_shotmap", SHOTMAP_COLUMNS, data["shots"])

    conn.execute("DELETE FROM fact_player_match_stats WHERE Match_ID = ?", (match_id,))
    _insert_many(conn, "fact_player_match_stats", PLAYER_STATS_COLUMNS, data["player_stats"])

    conn.execute("DELETE FROM fact_team_match_stats WHERE Match_ID = ?", (match_id,))
    core_names = set(_col_names(TEAM_STATS_CORE_COLUMNS))
    team_rows = []
    for rec in data["team_stats"]:
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
        _rows_with_extra_json(data["events"]),
    )

    conn.execute("DELETE FROM fact_match_lineup WHERE Match_ID = ?", (match_id,))
    _insert_many(
        conn,
        "fact_match_lineup",
        MATCH_LINEUP_CORE_COLUMNS + [("extra_json", "TEXT")],
        _rows_with_extra_json(data["lineup_rows"]),
    )

    conn.execute("DELETE FROM fact_match_momentum WHERE Match_ID = ?", (match_id,))
    _insert_many(conn, "fact_match_momentum", MOMENTUM_COLUMNS, data["momentum_rows"])


def ingest_verify_match(
    match_id: Union[int, str],
    league_id: Optional[int] = None,
    date: Optional[str] = None,
    season: Optional[str] = None,
) -> None:
    """单场抓取 + 写入 + 自己 commit——保留给不需要批量控制的场景用
    (如冒烟测试),内部就是 fetch_match_data + write_match_data 的组合。"""
    data = fetch_match_data(match_id, league_id=league_id, date=date, season=season)
    conn = get_verify_connection()
    try:
        write_match_data(conn, match_id, data)
        conn.commit()
    finally:
        conn.close()
