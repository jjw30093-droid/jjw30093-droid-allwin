"""backend/queries/player_form.py 测试(数据 tab 模块四:关键球员占比 + 模块五:门将对位)。"""

from __future__ import annotations

import pytest

from backend.db.connections import connect_rw
from backend.queries import player_form as q
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
TEAM = 1001


def _player_row(conn, match_id, player_id, name, *, minutes=90, is_gk=0, **fields):
    cols = ["Match_ID", "Player_ID", "Team_ID", "is_goalkeeper", "minutes_played", "player_name"]
    vals = [match_id, player_id, TEAM, is_gk, minutes, name]
    for k, v in fields.items():
        cols.append(f'"{k}"' if k == "matchstats.headers.tackles" else k)
        vals.append(v)
    placeholders = ",".join("?" * len(vals))
    conn.execute(
        f"INSERT INTO fact_player_match_stats ({','.join(cols)}) VALUES ({placeholders})",
        vals,
    )


@pytest.fixture
def five_matches(data_dir):
    """队 1001 近 5 场,球员出场次数不均(验证 ≥3 场门槛),含一名轮换门将。"""
    conn = connect_rw("core")
    seed_core_schema(conn)
    match_ids = []
    for i in range(5):
        mid = 9500 + i
        match_ids.append(mid)
        insert_match(conn, mid, league_id=LEAGUE, season="2025/2026",
                     date=f"2026-01-{10+i:02d}", home_id=TEAM, away_id=2000 + i,
                     home="队A", away=f"对手{i}", status="Finish", home_score=1, away_score=0)
        # 主力过人手:5 场全勤,每场 2 次过人
        _player_row(conn, mid, "p_main", "主力过人手", minutes=90, dribbles_succeeded=2)
        # 替补:只打 2 场(不达 3 场门槛),但单场效率很高——不能因此上榜
        if i < 2:
            _player_row(conn, mid, "p_sub", "高效替补", minutes=20, dribbles_succeeded=5)
    conn.commit()
    conn.close()
    yield match_ids


class TestTeamKeyPlayers:
    def test_three_blocks_returned(self, five_matches):
        conn = connect_rw("core")
        blocks = q.team_key_players(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        assert [b["id"] for b in blocks] == ["dribbles", "creating", "defending"]

    def test_below_threshold_player_excluded_from_ranking_but_counted_in_denominator(self, five_matches):
        """替补只打 2 场,不能上榜;但他的过人次数仍必须算进全队分母,
        否则主力的占比会被人为抬高(分母只能是"全队真实总量")。"""
        conn = connect_rw("core")
        blocks = q.team_key_players(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        dribbles = blocks[0]
        names = [r["name"] for r in dribbles["rows"]]
        assert "高效替补" not in names
        assert names == ["主力过人手"]
        # 全队总过人 = 主力 5*2=10 + 替补 2*5=10 = 20;主力占比 10/20=50%,
        # 不是把替补排除在分母外算出的 100%
        assert dribbles["rows"][0]["pct"] == 50.0
        assert dribbles["rows"][0]["count"] == 10
        assert dribbles["rows"][0]["appearances"] == 5
        assert dribbles["rows"][0]["minutes"] == 450

    def test_no_qualifying_player_gives_empty_rows(self, data_dir):
        """全队没人打满 3 场——诚实空榜,不是"这队没有这类球员"。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 9600, league_id=LEAGUE, date="2026-01-10",
                     home_id=TEAM, away_id=2000, status="Finish", home_score=0, away_score=0)
        _player_row(conn, 9600, "p1", "球员一", minutes=90, dribbles_succeeded=3)
        conn.commit()
        blocks = q.team_key_players(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        assert blocks[0]["rows"] == []

    def test_no_recent_matches_gives_empty_blocks(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        blocks = q.team_key_players(conn, 9999, LEAGUE, "2026-01-20", window=5)
        assert all(b["rows"] == [] for b in blocks)

    def test_defending_sums_tackles_and_interceptions(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i in range(3):
            mid = 9700 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=0, away_score=0)
            _player_row(conn, mid, "p_def", "防守悍将", minutes=90,
                        **{"matchstats.headers.tackles": 2}, interceptions=1)
        conn.commit()
        blocks = q.team_key_players(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        defending = next(b for b in blocks if b["id"] == "defending")
        assert defending["rows"][0]["count"] == 9  # (2+1)*3
        assert defending["rows"][0]["pct"] == 100.0

    def test_zero_minutes_row_not_counted_as_appearance(self, data_dir):
        """minutes_played=0(未上场,只是在名单里)不应算作出场。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i in range(3):
            mid = 9800 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=0, away_score=0)
            _player_row(conn, mid, "p_bench", "替补席", minutes=0, dribbles_succeeded=0)
        conn.commit()
        blocks = q.team_key_players(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        assert blocks[0]["rows"] == []


class TestTeamGoalkeepers:
    def test_rotation_ordered_by_appearances_desc(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i in range(5):
            mid = 9900 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=0, away_score=0)
            keeper = "p_gk1" if i < 3 else "p_gk2"
            name = "主力门将" if i < 3 else "替补门将"
            _player_row(conn, mid, keeper, name, minutes=90, is_gk=1,
                        saves=3, expected_goals_on_target_faced=1.5, goals_conceded=1,
                        goals_prevented=0.5)
        conn.commit()
        out = q.team_goalkeepers(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        assert [r["name"] for r in out] == ["主力门将", "替补门将"]
        assert out[0]["appearances"] == 3
        assert out[0]["saves"] == 9
        assert out[0]["xgot_faced"] == 4.5
        assert out[0]["goals_prevented"] == 1.5

    def test_partial_goals_prevented_coverage_gives_none_not_partial_sum(self, data_dir):
        """门将 3 场里只有 2 场有 goals_prevented——求和会悄悄漏掉第 3 场,
        必须诚实给 None,不能返回一个看似完整实则漏算的数字。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i in range(3):
            mid = 10000 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=0, away_score=0)
            gp = 0.5 if i < 2 else None
            _player_row(conn, mid, "p_gk", "门将", minutes=90, is_gk=1,
                        saves=2, goals_conceded=1, goals_prevented=gp)
        conn.commit()
        out = q.team_goalkeepers(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        assert out[0]["appearances"] == 3
        assert out[0]["goals_prevented"] is None

    def test_no_goalkeeper_appearances_gives_empty_list(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        assert q.team_goalkeepers(conn, 9999, LEAGUE, "2026-01-20") == []

    def test_full_xgot_coverage_flags_complete_and_confirms_real_number(self, data_dir):
        """门将 3 场都有 expected_goals_on_target_faced —— xgot_faced_complete
        必须为 True,前端才能放心用它现算"阻止进球"估算。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i in range(3):
            mid = 10200 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=0, away_score=0)
            _player_row(conn, mid, "p_gk", "门将", minutes=90, is_gk=1,
                        saves=2, goals_conceded=1, expected_goals_on_target_faced=1.5)
        conn.commit()
        out = q.team_goalkeepers(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        assert out[0]["appearances"] == 3
        assert out[0]["xgot_faced"] == 4.5
        assert out[0]["xgot_faced_complete"] is True

    def test_partial_xgot_coverage_flags_incomplete_not_silently_treated_as_full(self, data_dir):
        """门将 3 场里只有 2 场有 expected_goals_on_target_faced —— 缺失场次
        不能被当 0 参与求和后还宣称"完整"(旧实现的 COALESCE(...,0) 数值上
        碰巧和"只加已知场次"结果相同,但完全没有区分"这是不是全部场次的
        真实合计"——如果前端拿这个数直接现算阻止进球,会得到一个看似精确、
        实则用了不完整分母的估算)。必须显式标 xgot_faced_complete=False。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i in range(3):
            mid = 10300 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=0, away_score=0)
            xgot = 1.5 if i < 2 else None
            _player_row(conn, mid, "p_gk", "门将", minutes=90, is_gk=1,
                        saves=2, goals_conceded=1, expected_goals_on_target_faced=xgot)
        conn.commit()
        out = q.team_goalkeepers(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        assert out[0]["appearances"] == 3
        assert out[0]["xgot_faced"] == 3.0  # 只累加两场已知值,不是三场里把缺的一场当 0
        assert out[0]["xgot_faced_complete"] is False

    def test_no_xgot_data_at_all_gives_none_not_zero(self, data_dir):
        """三场都没有 expected_goals_on_target_faced —— 必须是 None(没有数据),
        不能是 0(0 是"确实面对了 0 次射正"的合法值,两者含义完全不同)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i in range(3):
            mid = 10400 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=0, away_score=0)
            _player_row(conn, mid, "p_gk", "门将", minutes=90, is_gk=1, saves=2, goals_conceded=1)
        conn.commit()
        out = q.team_goalkeepers(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        assert out[0]["xgot_faced"] is None
        assert out[0]["xgot_faced_complete"] is False


class TestPlayerNameI18n:
    """数据库 dim_player_i18n 里已经有真实中文译名(2026-08 全量核对:四大
    联赛出场球员 100% 覆盖),但 team_key_players/team_goalkeepers 此前直接
    用 fact_player_match_stats.player_name(来源英文名),从不查 i18n 表——
    match_report.py/league_stats.py 早就在用同一张表(`_player_i18n_map`
    回退链:短中文名 > 全中文名 > 来源英文名),这里必须对齐,不能维护
    第二套"忘记查译名"的展示逻辑。"""

    def test_key_players_uses_chinese_name_when_i18n_exists(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.execute(
            "INSERT INTO dim_player_i18n (Player_ID, name_en, name_zh, name_zh_short)"
            " VALUES ('p_dribbler', 'Antoine Griezmann', '安托万·格里兹曼', '格里兹曼')"
        )
        for i in range(3):
            mid = 10500 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=1, away_score=0)
            _player_row(conn, mid, "p_dribbler", "Antoine Griezmann", minutes=90, dribbles_succeeded=2)
        conn.commit()
        out = q.team_key_players(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        dribbles_block = next(b for b in out if b["id"] == "dribbles")
        assert dribbles_block["rows"][0]["name"] == "格里兹曼"  # 短中文名优先

    def test_key_players_falls_back_to_full_zh_name_when_no_short_name(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.execute(
            "INSERT INTO dim_player_i18n (Player_ID, name_en, name_zh, name_zh_short)"
            " VALUES ('p_dribbler2', 'Some Player', '中文全名', NULL)"
        )
        for i in range(3):
            mid = 10510 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=1, away_score=0)
            _player_row(conn, mid, "p_dribbler2", "Some Player", minutes=90, dribbles_succeeded=2)
        conn.commit()
        out = q.team_key_players(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        dribbles_block = next(b for b in out if b["id"] == "dribbles")
        assert dribbles_block["rows"][0]["name"] == "中文全名"

    def test_key_players_falls_back_to_source_name_when_no_i18n_row(self, data_dir):
        """没有 i18n 记录时(尚未翻译的球员)如实显示来源英文名,不报错、
        不显示空白——绝不能因为查不到译名就整行消失。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i in range(3):
            mid = 10520 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=1, away_score=0)
            _player_row(conn, mid, "p_untranslated", "Untranslated Player", minutes=90, dribbles_succeeded=2)
        conn.commit()
        out = q.team_key_players(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        dribbles_block = next(b for b in out if b["id"] == "dribbles")
        assert dribbles_block["rows"][0]["name"] == "Untranslated Player"

    def test_goalkeepers_uses_chinese_name_when_i18n_exists(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.execute(
            "INSERT INTO dim_player_i18n (Player_ID, name_en, name_zh, name_zh_short)"
            " VALUES ('p_gk_zh', 'Jan Oblak', '扬·奥布拉克', '奥布拉克')"
        )
        for i in range(3):
            mid = 10530 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2026-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, status="Finish", home_score=0, away_score=0)
            _player_row(conn, mid, "p_gk_zh", "Jan Oblak", minutes=90, is_gk=1, saves=3, goals_conceded=0)
        conn.commit()
        out = q.team_goalkeepers(conn, TEAM, LEAGUE, "2026-01-20", window=5)
        assert out[0]["name"] == "奥布拉克"
