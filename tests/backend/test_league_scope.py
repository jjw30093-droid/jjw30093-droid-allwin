"""联赛范围守卫(数据管道重建 Phase 1)。

把"扩到 16 项赛事、entitlement 分档、模型不随扩联赛失控、双注册表不漂移"
这几条从口头约定变成可执行断言(前身项目事故 #6 的元教训:规则要配可执行检查)。
"""

from backend.queries.leagues import (
    LEAGUE_META,
    accessible_league_ids,
    anonymous_cacheable_league_ids,
)

VALID_ENTITLEMENTS = {"league:epl", "league:top5", "league:lottery", "league:european_cup"}

# 本轮接入的 7 项赛事(Phase 0 真实探测确证 details.id)
NEW_LEAGUE_IDS = {48, 57, 61, 268, 42, 73, 10216}
# 接入前既有 10 联赛
EXISTING_LEAGUE_IDS = {47, 87, 55, 54, 53, 67, 59, 223, 9080, 113}

# 2026-08-11 权限矩阵互换(用户拍板,见 platform 0012 迁移):欧战三项从
# league:lottery 改挂 league:european_cup,随五大联赛一起进入免费面;
# 其余 4 个新接入联赛(英冠/荷甲/葡超/巴甲)仍是 league:lottery,登录门槛。
NEW_EUROPEAN_CUP_IDS = {42, 73, 10216}
NEW_LOTTERY_IDS = NEW_LEAGUE_IDS - NEW_EUROPEAN_CUP_IDS


def test_registry_has_all_seventeen_leagues():
    assert set(LEAGUE_META) == EXISTING_LEAGUE_IDS | NEW_LEAGUE_IDS
    assert len(LEAGUE_META) == 17


def test_every_entitlement_is_valid():
    for lid, meta in LEAGUE_META.items():
        assert meta["entitlement"] in VALID_ENTITLEMENTS, (lid, meta["entitlement"])


def test_new_leagues_are_lottery_tier():
    for lid in NEW_LOTTERY_IDS:
        assert LEAGUE_META[lid]["entitlement"] == "league:lottery"


def test_european_cup_leagues_are_european_cup_tier():
    for lid in NEW_EUROPEAN_CUP_IDS:
        assert LEAGUE_META[lid]["entitlement"] == "league:european_cup"


def test_new_leagues_declare_season_kind():
    # 2026-08-25 起:全部联赛都声明 season_kind,取值与
    # backend.season_resolver.SeasonKind 枚举词汇一致(旧值 "calendar" 与
    # 枚举 "calendar_year" 不兼容,SeasonKind(...) 会直接抛——那正是这条
    # 测试要钉死的词汇契约)。赛季推导的权威在 dim_league_season_regime
    # 制度表(migrations/core/0011),这里只是展示性元数据。
    from backend.season_resolver import SeasonKind

    assert LEAGUE_META[268]["season_kind"] == "calendar_year"
    for lid in NEW_LEAGUE_IDS - {268}:
        assert LEAGUE_META[lid]["season_kind"] == "cross_year"
    for lid, meta in LEAGUE_META.items():
        SeasonKind(meta["season_kind"])  # 词汇不兼容会抛 ValueError


def test_anonymous_cacheable_is_universal():
    """2026-08-16 产品权限口径修正:除"每日精选"外全站比赛内容全部免费,
    包括匿名——联赛级 entitlement 分类(epl/top5/lottery/european_cup)现在
    只是描述性元数据,不再影响匿名可缓存集合。这条断言正是要推翻
    "league:lottery 联赛不进匿名可缓存集"的旧规则。"""
    anon = anonymous_cacheable_league_ids()
    assert set(LEAGUE_META) == set(anon)
    # league:lottery 联赛现在同样在匿名可缓存集合内
    for lid, meta in LEAGUE_META.items():
        if meta["entitlement"] == "league:lottery":
            assert lid in anon, f"{lid} 应在匿名可缓存集合内(登录与内容分层已解耦)"


def test_accessible_league_ids_is_universal_regardless_of_entitlements():
    """任何 entitlements 输入(即使是空集)都应恒返回全部联赛 id——访问权
    不再由 entitlement 判定,参数只为兼容既有调用签名保留。"""
    empty = accessible_league_ids(frozenset())
    assert empty == set(LEAGUE_META)
    full = accessible_league_ids(frozenset({"league:epl", "league:top5", "league:european_cup"}))
    assert full == set(LEAGUE_META)
    assert 48 in empty  # 原 league:lottery(英冠)现同样恒可访问


def test_content_pipeline_registry_does_not_drift():
    """第二套注册表(content_pipeline.LEAGUES)只允许出现 LEAGUE_META 已知的联赛,
    或历史遗留的 MLS 130——防止两套注册表悄悄漂移(本轮不合并,但钉住不扩大)。"""
    from backend.content_pipeline import LEAGUES

    ids = {c.fotmob_competition_id for c in LEAGUES.values()}
    assert ids <= set(LEAGUE_META) | {130}, f"content_pipeline 出现未登记联赛: {ids - set(LEAGUE_META) - {130}}"


