"""
ingest_future_fixtures.py — 爬某赛季的"未来赛程"(未开赛比赛),只写 dim_match。

数据来源: FotMobClient.league_matches() 的 fixtures.allMatches[],这份 JSON
本身就是渲染赛程页用的,每场比赛自带 home/away 队伍 id/name + 轮次 + 开球
时间 + status(started/finished/cancelled 三个布尔字段),不需要再逐场调用
match_details()(那是给"已发生的比赛"抓统计/事件/阵容用的,未开赛比赛没有
这些东西,也不需要抓)。

只落 dim_match,不碰任何 fact_* 表——未开赛比赛没有比分/统计/事件/阵容,
写空 fact_* 行没有意义,也超出这个脚本的职责边界。等比赛真正踢完后,交给
现有的 ingest_league.py/ingest_match.py 正常流程回填。

status 取值:
    与 fotmob_client.derive_match_status()(match_details 详情页读的是同一个
    status 对象形状:started/finished/cancelled)共用同一份判定,两条 ingest
    路径结构上不可能漂移:
        finished  -> 'Finish'    (与现有完赛语义完全一致,复用同一个值)
        cancelled -> 'Cancelled'
        started 且未 finished -> 'InPlay'
        都不是    -> 'NotStarted'
    本脚本只写 'Finish' 以外的行(已完赛的交给现有流程按完整比赛处理)。

幂等:按 Match_ID 用 INSERT OR REPLACE(upsert),风格与 ingest_match.py 对
dim_match 的写法一致,可重复爬。

赛季身份校验:响应的 details.id/selectedSeason 必须与请求的 league_id/season
完全一致(同 backend/cli/backfill_season_tables.py::_verify_identity),不一致
抛 SeasonIdentityError、不落一行库。防的是来源尚未发布目标赛季时静默返回
另一个赛季的数据,被当成目标赛季写进 dim_match。

用法:
    python backend/ingest/ingest_future_fixtures.py --league-id 47 --season 2026/2027
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection
from db.util import normalize_utc_iso
from fotmob_client import FotMobClient, derive_match_status
from ingest_match import _upsert
from schema import DIM_MATCH_COLUMNS

# 与 parse_match_dim(match_details 详情页)共用同一个判定函数,见上方模块 docstring。
_status_from_fixture = derive_match_status


class SeasonIdentityError(RuntimeError):
    """league_matches() 响应的 details.id/selectedSeason 与请求参数不一致。

    FotMob 在某个联赛尚未发布下一赛季赛程时,`&season=` 请求参数可能被忽略、
    静默返回当前/上一个已发布赛季的数据。不校验的话,这些行会被当成目标赛季
    写进 dim_match(例如把已完赛的 2025/2026 整季误标成 Season='2026/2027'),
    这正是 docs/data-plan.md 记录的"跨联赛/跨赛季污染"风险——必须拒绝写库,
    不能静默降级成"写入了但赛季标错"。
    """


def _verify_season_identity(data: dict, league_id: int, season: str) -> None:
    """与 backend/cli/backfill_season_tables.py::_verify_identity 同一口径
    (details.id 必须等于请求的 league_id,selectedSeason 必须等于请求的 season)。
    不同点:那里按赛季循环、宁可跳过单个赛季也要继续;这里一次调用只处理一个
    (league_id, season),不一致就是这次调用唯一目的失败,直接抛异常。"""
    details = data.get("details") or {}
    observed_id = details.get("id")
    try:
        observed_id = int(observed_id)
    except (TypeError, ValueError):
        raise SeasonIdentityError(
            f"league_matches(league_id={league_id}, season={season!r}) 响应的 "
            f"details.id 无法解析为整数: {observed_id!r}"
        )
    if observed_id != league_id:
        raise SeasonIdentityError(
            f"league_matches(league_id={league_id}, season={season!r}) 响应的 "
            f"details.id={observed_id} 与请求的 league_id 不一致——来源很可能还没有 "
            f"这个赛季的数据,拒绝落库"
        )
    observed_season = details.get("selectedSeason") or details.get("season")
    if observed_season != season:
        raise SeasonIdentityError(
            f"league_matches(league_id={league_id}, season={season!r}) 响应的 "
            f"selectedSeason={observed_season!r} 与请求的 season 不一致——来源很可能还没有 "
            f"这个赛季的数据,拒绝落库"
        )


def fetch_fixture_rows(client: FotMobClient, league_id: int, season: str) -> list:
    data = client.league_matches(league_id, season)
    _verify_season_identity(data, league_id, season)
    raw = (data.get("fixtures", {}) or {}).get("allMatches") or []

    rows = []
    for m in raw:
        status_obj = m.get("status", {}) or {}
        status = _status_from_fixture(status_obj)
        if status == "Finish":
            # 已完赛场次交给现有流程(ingest_league.py)按完整比赛处理。
            # 已知限制:ingest_league 路径不写 kickoff_at_utc,所以已完赛比赛的
            # 精确开球时刻不会经由本模块补上——历史回填由
            # backend/cli/backfill_kickoff_from_fotmob.py 按 (League_ID, Season)
            # 分区批量处理(同一响应、每分区一个请求)。
            continue

        home = m.get("home") or {}
        away = m.get("away") or {}
        utc = status_obj.get("utcTime")
        date = utc[:10] if isinstance(utc, str) and len(utc) >= 10 and utc[4] == "-" and utc[7] == "-" else None
        # 精确开球时刻(CLAUDE.md §6.2.1):保留来源 utcTime 的完整时间;
        # 来源只给日期时为 NULL,不补当天 00:00 伪装精确时间。provenance 同步落库:
        # 有精确时刻 → exact + source 'fotmob:fixtures';只有日期 → date_only,不编造来源。
        kickoff_at_utc = normalize_utc_iso(utc)
        if kickoff_at_utc is not None:
            kickoff_precision, kickoff_source = "exact", "fotmob:fixtures"
        elif date is not None:
            kickoff_precision, kickoff_source = "date_only", None
        else:
            kickoff_precision, kickoff_source = "unknown", None

        rows.append({
            "Match_ID": int(m["id"]),
            "Season": season,
            "League_ID": int(league_id),
            "Date": date,
            "kickoff_at_utc": kickoff_at_utc,
            "kickoff_precision": kickoff_precision,
            "kickoff_source": kickoff_source,
            "Home_Team_ID": int(home["id"]) if home.get("id") is not None else None,
            "Away_Team_ID": int(away["id"]) if away.get("id") is not None else None,
            "Home_Team_Name": home.get("name"),
            "Away_Team_Name": away.get("name"),
            "home_score": None,
            "away_score": None,
            "status": status,
            "Referee": None,
            "Match_Round": m.get("round"),
            "Temperature": None,
            "Wind_Speed": None,
            "Who_Lost_On_Penalties": None,
        })
    return rows


def find_new_teams(conn, rows: list) -> list:
    """列出赛程里出现、但历史(bronze)dim_match 里从未出现过的球队(升班马/
    新面孔),以及它们在 dim_team_i18n 里有没有中文映射——只列出、标记待补,
    不阻断落库。"""
    historical = set()
    for r in conn.execute("SELECT DISTINCT Home_Team_ID FROM dim_match").fetchall():
        historical.add(r[0])
    for r in conn.execute("SELECT DISTINCT Away_Team_ID FROM dim_match").fetchall():
        historical.add(r[0])
    i18n_ids = {r[0] for r in conn.execute("SELECT Team_ID FROM dim_team_i18n").fetchall()}

    fixture_teams = {}
    for r in rows:
        fixture_teams[r["Home_Team_ID"]] = r["Home_Team_Name"]
        fixture_teams[r["Away_Team_ID"]] = r["Away_Team_Name"]

    new_teams = []
    for tid, name in sorted(fixture_teams.items()):
        if tid not in historical:
            new_teams.append({"team_id": tid, "name": name, "has_i18n": tid in i18n_ids})
    return new_teams


def write_rows(conn, rows: list) -> None:
    for row in rows:
        _upsert(conn, "dim_match", DIM_MATCH_COLUMNS, row)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-id", type=int, default=47)
    parser.add_argument("--season", type=str, default="2026/2027")
    args = parser.parse_args()

    client = FotMobClient()
    try:
        rows = fetch_fixture_rows(client, args.league_id, args.season)
    except SeasonIdentityError as e:
        print(f"\n拒绝落库(赛季身份校验未通过): {e}")
        print("如实报告:来源可能尚未发布这个赛季,不重试、不改用其它赛季猜测。")
        raise SystemExit(1)
    print(f"抓到未开赛场次: {len(rows)} (league_id={args.league_id}, season={args.season})")

    conn = get_connection()
    try:
        new_teams = find_new_teams(conn, rows)
        write_rows(conn, rows)
    finally:
        conn.close()

    print(f"写入 dim_match 完成: {len(rows)} 行")

    if new_teams:
        print(f"\n历史 bronze 里从未出现过的新球队({len(new_teams)} 支):")
        for t in new_teams:
            i18n_flag = "有" if t["has_i18n"] else "缺失(待补)"
            print(f"  team_id={t['team_id']}  {t['name']}  中文映射: {i18n_flag}")
    else:
        print("\n没有历史 bronze 里未出现过的新球队。")


if __name__ == "__main__":
    main()
