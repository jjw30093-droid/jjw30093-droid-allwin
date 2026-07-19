"""联赛元数据与访问门禁(league:epl 免费,league:top5 为 Pro)。"""

LEAGUE_META = {
    47: {"code": "epl", "name_zh": "英超", "name_en": "Premier League", "entitlement": "league:epl"},
    87: {"code": "laliga", "name_zh": "西甲", "name_en": "La Liga", "entitlement": "league:top5"},
    55: {"code": "seriea", "name_zh": "意甲", "name_en": "Serie A", "entitlement": "league:top5"},
    54: {"code": "bundesliga", "name_zh": "德甲", "name_en": "Bundesliga", "entitlement": "league:top5"},
    53: {"code": "ligue1", "name_zh": "法甲", "name_en": "Ligue 1", "entitlement": "league:top5"},
}


def accessible_league_ids(entitlements: frozenset) -> set[int]:
    return {
        lid for lid, meta in LEAGUE_META.items() if meta["entitlement"] in entitlements
    }
