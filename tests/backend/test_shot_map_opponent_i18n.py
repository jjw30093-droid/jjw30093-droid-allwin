"""recent_shot_map_spec:近期比赛列表里的对手球队名未译中文(2026-08-21)。

背景:team_display_for() 是按需(scoped)查询,只覆盖调用方点名的 team_id。
recent_shot_map_spec 只用目标比赛的两支球队调用它,而 spec["matches"] 里的
"近 N 场"对手球队(如目标球队近期交手过的第三方球队)从未被点名,拿不到
dim_team_i18n.name_zh,只能回退成 FotMob 原始英文名——前端比赛筛选栏("对
Urawa Red Diamonds")因此整排显示英文。这里钉住:target 两队之外、只在
"近期比赛"里出现的对手球队,只要 dim_team_i18n 里有中文名,spec["matches"]
就必须用中文名,不得回退英文。
"""

from __future__ import annotations

import pytest

from backend.db.connections import connect_rw
from backend.queries.matches import recent_shot_map_spec
from tests.backend.coreseed import insert_match, seed_core_schema


@pytest.fixture
def recent_form_with_english_opponent(data_dir):
    """目标比赛 9500(未开赛,阿队 vs 乙队)之前,阿队打了一场同联赛比赛,
    对手是浦和红钻——dim_match 只存了 FotMob 英文名,但 dim_team_i18n 里
    已经有中文译名,和真实生产数据的形状一致(采集时是英文,i18n 表另存)。
    """
    conn = connect_rw("core")
    seed_core_schema(conn)

    # 浦和红钻(1003)只在 dim_team_i18n 有中文名,dim_match 里存的是原始英文
    conn.execute(
        "INSERT OR REPLACE INTO dim_team_i18n VALUES (1003,'Urawa Red Diamonds','浦和红钻','t','')"
    )

    insert_match(
        conn, 8001, league_id=47, date="2026-01-01",
        home_id=1001, away_id=1003, home="阿队", away="Urawa Red Diamonds",
        status="Finish", home_score=2, away_score=1,
    )
    insert_match(
        conn, 9500, league_id=47, date="2026-01-20",
        home_id=1001, away_id=1002, home="阿队", away="乙队",
        status="NotStarted",
    )
    conn.execute(
        "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
        " X_Coord, Y_Coord, xG, xGOT, Situation, Outcome, Shot_Type)"
        " VALUES (8001, 'p1', 1001, 10, 'FirstHalf', 95.0, 40.0, 0.3, NULL,"
        " 'RegularPlay', 'Goal', 'RightFoot')"
    )
    conn.commit()
    conn.close()
    yield


class TestOpponentNameTranslation:
    def test_opponent_only_seen_in_recent_matches_gets_chinese_name(
        self, recent_form_with_english_opponent
    ):
        conn = connect_rw("core")
        spec = recent_shot_map_spec(conn, 9500, window=5)
        assert spec is not None

        match = next(m for m in spec["matches"] if m["match_id"] == 8001)
        assert match["away"]["name"] == "浦和红钻"
        assert match["away"]["name"] != "Urawa Red Diamonds"
        # name_en 字段本来就该是原始英文,不受这次修复影响
        assert match["away"]["name_en"] == "Urawa Red Diamonds"

    def test_target_match_teams_still_translated_as_before(
        self, recent_form_with_english_opponent
    ):
        """回归护栏:补对手不能误伤 target 两队原有的翻译路径。"""
        conn = connect_rw("core")
        spec = recent_shot_map_spec(conn, 9500, window=5)
        names = {t["team_id"]: t["name"] for t in spec["teams"]}
        assert names[1001] == "阿队"
        assert names[1002] == "乙队"
