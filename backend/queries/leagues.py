"""联赛元数据与访问门禁(league:epl 免费,league:top5 为 Pro,league:lottery
为中国竞彩常见非五大联赛——如瑞典超——free 起即可访问,见 platform 0004 迁移)。

单一真源:新增联赛只改这一处字典,不得把 league id 散落写进其它业务分支
(路由/前端/采集脚本一律从这里或真实数据库读取)。"""

from __future__ import annotations

import sqlite3

LEAGUE_META = {
    47: {"code": "epl", "name_zh": "英超", "name_en": "Premier League", "entitlement": "league:epl"},
    87: {"code": "laliga", "name_zh": "西甲", "name_en": "La Liga", "entitlement": "league:top5"},
    55: {"code": "seriea", "name_zh": "意甲", "name_en": "Serie A", "entitlement": "league:top5"},
    54: {"code": "bundesliga", "name_zh": "德甲", "name_en": "Bundesliga", "entitlement": "league:top5"},
    53: {"code": "ligue1", "name_zh": "法甲", "name_en": "Ligue 1", "entitlement": "league:top5"},
    # FotMob league id 67 = Allsvenskan(瑞典,自然年赛季,真实核对见
    # docs/current-state.md:2026-07-21 real probe,country=SWE)。
    67: {"code": "allsvenskan", "name_zh": "瑞典超", "name_en": "Allsvenskan", "entitlement": "league:lottery"},
    # FotMob 59 was revalidated against the live 2026 response for the active-
    # league content MVP. Like Allsvenskan, this is a lottery-relevant free
    # league, not an expansion of the top-five entitlement.
    59: {"code": "eliteserien", "name_zh": "挪威超", "name_en": "Eliteserien", "entitlement": "league:lottery"},
    # 2026-08-07 接入:J1/K1/澳超(FotMob 223/9080/113,id 已经真实 ingest
    # 2,050 场逐场数据核对)。三者同为中国竞彩常见联赛,与挪超/瑞超同档
    # league:lottery,不并入 top5 付费墙。中文名按竞彩/主流媒体惯用简称。
    223: {"code": "j1league", "name_zh": "日职联", "name_en": "J1 League", "entitlement": "league:lottery"},
    9080: {"code": "kleague1", "name_zh": "韩K联", "name_en": "K League 1", "entitlement": "league:lottery"},
    113: {"code": "aleague", "name_zh": "澳超", "name_en": "A-League", "entitlement": "league:lottery"},
}


def accessible_league_ids(entitlements: frozenset) -> set[int]:
    return {
        lid for lid, meta in LEAGUE_META.items() if meta["entitlement"] in entitlements
    }


def league_data_profiles(conn: sqlite3.Connection) -> dict[int, dict]:
    """Return data availability from core facts, never from configuration alone."""

    profiles: dict[int, dict] = {
        league_id: {
            "current_season": None,
            "data_status": "NOT_SYNCED",
            "data_updated_at": None,
        }
        for league_id in LEAGUE_META
    }
    seasons: dict[int, set[str]] = {league_id: set() for league_id in LEAGUE_META}
    for table in ("dim_match", "fact_league_table"):
        try:
            rows = conn.execute(
                f"""SELECT League_ID, Season
                      FROM {table}
                     WHERE League_ID IN ({','.join('?' for _ in LEAGUE_META)})
                     GROUP BY League_ID, Season""",
                tuple(LEAGUE_META),
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            if row["Season"]:
                seasons[int(row["League_ID"])].add(str(row["Season"]))
    for league_id, values in seasons.items():
        if values:
            profiles[league_id]["current_season"] = sorted(values)[-1]
            profiles[league_id]["data_status"] = "AVAILABLE"
    return profiles
