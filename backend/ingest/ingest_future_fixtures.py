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
# 也把本模块所在目录(backend/ingest/)加入 path,使 `from ingest_match import`
# 在"独立脚本运行"和"作为 backend.ingest.* 包导入"(如 cli/sync_fixtures_window)
# 两种上下文下都可解析——原来只有独立脚本(cwd=backend/ingest)能跑通。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_connection
from db.util import normalize_utc_iso
from fotmob_client import FotMobClient, derive_match_status
from ingest_match import _upsert
from schema import DIM_MATCH_COLUMNS

# 赛季身份校验统一收敛进 season_identity.py(2026-08-25,CLAUDE.md §6.3):
# 此前同一套"回声校验"在仓库里有五份互不复用的实现。这里保留同名 re-export,
# 既有 import(cli/sync_fixtures_window、测试)不需要改。优先包路径导入——
# 保证与其它 `backend.ingest.season_identity` 消费方拿到同一个类对象
# (异常 isinstance 跨模块才成立);独立脚本运行时退回 sys.path 导入。
try:
    from backend.ingest.season_identity import (  # noqa: F401 — re-export 兼容
        SeasonIdentityError,
        discover_season as discover_season_identity,
        verify_season_echo as _verify_season_identity,
    )
except ImportError:  # 独立脚本上下文(cwd=backend/ingest)
    from season_identity import (  # noqa: F401
        SeasonIdentityError,
        discover_season as discover_season_identity,
        verify_season_echo as _verify_season_identity,
    )

# 与 parse_match_dim(match_details 详情页)共用同一个判定函数,见上方模块 docstring。
_status_from_fixture = derive_match_status


def fetch_fixture_rows(client: FotMobClient, league_id: int, season: str) -> list:
    data = client.league_matches(league_id, season)
    _verify_season_identity(data, league_id, season)
    return rows_from_payload(data, league_id, season)


def rows_from_payload(data: dict, league_id: int, season: str) -> list:
    """纯解析:league_matches 响应 → dim_match 行(不发网络、不校验身份)。

    2026-08-10 数据管道重建抽出:T+7 赛程同步(cli/sync_fixtures_window.py)走
    赛季发现模式(不预设 season),身份校验由调用方以 discovery 口径单独执行,
    这里只做行解析。fetch_fixture_rows 保持原签名与行为(校验 + 解析)。
    """
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


# 赛程同步"拥有"的列:赛程刷新只应更新这些(比赛身份、开球、状态、轮次)。
# 明确**不含** Referee/Temperature/Wind_Speed(由 poll_fotmob_snapshots
# --write-match-details 写)与 home_score/away_score(由完赛流程写)——
# 用整行 INSERT OR REPLACE 会把它们清成 NULL(前身项目事故 #2 同型缺陷)。
FIXTURE_OWNED_COLUMNS = (
    "Season", "League_ID", "Date",
    "Home_Team_ID", "Away_Team_ID", "Home_Team_Name", "Away_Team_Name",
    "status", "Match_Round",
    "kickoff_at_utc", "kickoff_precision", "kickoff_source",
)


def upsert_fixture_row(conn, row: dict) -> None:
    """按 Match_ID upsert,但**只写赛程拥有的列**——绝不碰裁判/天气/比分。

    新行:插入拥有列 + Match_ID,其余列走表默认(NULL)。
    已有行:ON CONFLICT 只 UPDATE 拥有列,保留其它路径已写入的值。
    """
    cols = ("Match_ID",) + FIXTURE_OWNED_COLUMNS
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)
    update_sql = ", ".join(f"{c}=excluded.{c}" for c in FIXTURE_OWNED_COLUMNS)
    conn.execute(
        f"INSERT INTO dim_match ({col_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT(Match_ID) DO UPDATE SET {update_sql}",
        [row.get(c) for c in cols],
    )


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
