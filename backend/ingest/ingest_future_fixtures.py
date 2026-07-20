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
    库里目前只有 'Finish' 一个值(全部完赛场次),FotMob 赛程 JSON 的 status
    只有 started/finished/cancelled 三个布尔字段,没有 match_details() 那种
    reason.short 文本可以直接复用。所以这里新增三个语义不重复的值(不是
    随手新造,是真的没有可复用的旧枚举):
        finished  -> 'Finish'    (与现有完赛语义完全一致,复用同一个值)
        cancelled -> 'Cancelled'
        started 且未 finished -> 'InPlay'
        都不是    -> 'NotStarted'
    本脚本只写 'Finish' 以外的行(已完赛的交给现有流程按完整比赛处理)。

幂等:按 Match_ID 用 INSERT OR REPLACE(upsert),风格与 ingest_match.py 对
dim_match 的写法一致,可重复爬。

用法:
    python backend/ingest/ingest_future_fixtures.py --league-id 47 --season 2026/2027
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection
from db.util import normalize_utc_iso
from fotmob_client import FotMobClient
from ingest_match import _upsert
from schema import DIM_MATCH_COLUMNS


def _status_from_fixture(status_obj: dict) -> str:
    if status_obj.get("finished"):
        return "Finish"
    if status_obj.get("cancelled"):
        return "Cancelled"
    if status_obj.get("started"):
        return "InPlay"
    return "NotStarted"


def fetch_fixture_rows(client: FotMobClient, league_id: int, season: str) -> list:
    data = client.league_matches(league_id, season)
    raw = (data.get("fixtures", {}) or {}).get("allMatches") or []

    rows = []
    for m in raw:
        status_obj = m.get("status", {}) or {}
        status = _status_from_fixture(status_obj)
        if status == "Finish":
            continue  # 已完赛场次交给现有流程(ingest_league.py)按完整比赛处理

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
    rows = fetch_fixture_rows(client, args.league_id, args.season)
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
