"""联赛元数据(与访问门禁历史 —— 2026-08-16 起已彻底解耦)。

2026-08-16 产品权限口径修正(经用户批准):除"每日精选"外,网站所有比赛
内容全部免费,包括匿名用户;登录与内容分层彻底解耦,不再有任何联赛级
访问门禁。每个联赛条目上的 `entitlement` 字段(league:epl/top5/lottery/
european_cup)现在只是**描述性分类元数据**(竞彩语境归类、报表/CLI 统计用,
如 backend/cli/odds_coverage_report.py),不再驱动任何 403/401 门禁或
"是否需要登录"的判断——`accessible_league_ids()`/`anonymous_cacheable_league_ids()`
恒返回全部联赛 id。

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
    # 2026-08-10 数据管道重建接入(Phase 0 真实网络探测确证 details.id,
    # runtime/research/pipeline-v2-probe/summary.json)。均为中国竞彩常见赛事,同档
    # league:lottery(匿名可浏览,匿名仍只见最高一项概率)。season_kind 供 T+7 赛程
    # 同步的赛季解析使用:cross_year=跨年串(2026/2027),calendar=自然年(2026)。
    # 欧战三项资格赛天然不在 FotMob id 42/73/10216 的返回里,round='playoff' 保留。
    48: {"code": "championship", "name_zh": "英冠", "name_en": "Championship", "entitlement": "league:lottery", "season_kind": "cross_year"},
    57: {"code": "eredivisie", "name_zh": "荷甲", "name_en": "Eredivisie", "entitlement": "league:lottery", "season_kind": "cross_year"},
    61: {"code": "primeira", "name_zh": "葡超", "name_en": "Liga Portugal", "entitlement": "league:lottery", "season_kind": "cross_year"},
    268: {"code": "brasileirao", "name_zh": "巴甲", "name_en": "Brasileirão Série A", "entitlement": "league:lottery", "season_kind": "calendar"},
    # 2026-08-11 权限矩阵互换(用户拍板):欧战三项从 league:lottery 改挂
    # league:european_cup,与五大联赛同批进入免费面(见 migrations/platform/0012)。
    # 实测这三项联赛 dim_match 里 0 行数据(见 docs/current-state.md),开放免费
    # 是提前占位,不是当前有内容;联赛入口按 data_status 而非 accessible 控制点击。
    42: {"code": "ucl", "name_zh": "欧冠", "name_en": "Champions League", "entitlement": "league:european_cup", "season_kind": "cross_year"},
    73: {"code": "uel", "name_zh": "欧联", "name_en": "Europa League", "entitlement": "league:european_cup", "season_kind": "cross_year"},
    10216: {"code": "uecl", "name_zh": "欧协联", "name_en": "Conference League", "entitlement": "league:european_cup", "season_kind": "cross_year"},
}

def accessible_league_ids(entitlements: frozenset | None = None) -> set[int]:
    """全部已收录联赛的 id 集合。

    2026-08-16 起(除"每日精选"外全站比赛内容全部免费,包括匿名):访问权
    不再由 entitlement 判定,恒返回全部联赛。`entitlements` 参数保留只为
    兼容既有调用签名(路由层、seed_e2e.py 等仍以位置参数传入调用方当前
    entitlement 集合),不再参与任何过滤计算。
    """
    return set(LEAGUE_META.keys())


def anonymous_cacheable_league_ids() -> frozenset[int]:
    """匿名即可完整浏览的联赛 id 集合。

    2026-08-16 起恒为全部联赛:所有联赛的公开数据端点对匿名与登录请求返回
    一致内容、不随身份变化 → 全部可进公共缓存(CLAUDE.md §10.2:公共缓存
    不与用户身份混用)。取代散落各处的 `league_id == 47` 硬编码判断。
    """
    return frozenset(LEAGUE_META.keys())


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
