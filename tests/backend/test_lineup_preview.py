"""backend/queries/lineup_preview.py 测试(数据 tab 模块一:预计阵容 + 伤停)。"""

import json

import pytest

from backend.db.connections import connect_rw
from backend.queries import lineup_preview as q
from tests.backend.coreseed import seed_core_schema


def _seed_lineup(conn, fotmob_match_id, payload, *, observed_at="2026-08-15T09:00:00Z"):
    conn.execute(
        """INSERT INTO bronze_fm_lineup_snap
           (fotmob_match_id, payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
           VALUES (?, ?, 'x', ?, ?, 'run1')""",
        (fotmob_match_id, json.dumps(payload), observed_at, observed_at),
    )


def _seed_sideline(conn, fotmob_match_id, team_id, sidelined, *, observed_at="2026-08-15T09:00:00Z"):
    conn.execute(
        """INSERT INTO bronze_fm_sideline_snap
           (fotmob_match_id, fotmob_team_id, payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
           VALUES (?, ?, ?, 'x', ?, ?, 'run1')""",
        (fotmob_match_id, team_id, json.dumps({"team_id": team_id, "sidelined": sidelined}),
         observed_at, observed_at),
    )


def _core_conn(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    return conn


_FULL_LINEUP = {
    "lineup_type": "lastStarting11",
    "source": "lastStartingLineups",
    "home": {"team_id": 9825, "formation": "4-3-3",
              "starters": [{"id": 1, "name": "A", "shirt_number": 1}], "subs": []},
    "away": {"team_id": 8455, "formation": "4-4-2",
              "starters": [{"id": 2, "name": "B", "shirt_number": 9}], "subs": []},
}


class TestLatestLineup:
    def test_no_snapshot_returns_none(self, data_dir):
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        assert q.latest_lineup(conn, core, 999999) is None

    def test_returns_normalized_shape(self, data_dir):
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        _seed_lineup(conn, 5803528, _FULL_LINEUP)
        conn.commit()
        out = q.latest_lineup(conn, core, 5803528)
        assert out["lineup_type"] == "lastStarting11"
        assert out["source"] == "lastStartingLineups"
        assert out["observed_at"] == "2026-08-15T09:00:00Z"
        assert out["home"]["team_id"] == 9825
        assert out["away"]["formation"] == "4-4-2"

    def test_old_snapshot_missing_lineup_type_returns_none_not_a_guess(self, data_dir):
        """2026-08-15 之前写入的行没有 lineup_type/source 键——不得回填猜测值。"""
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        old_shape = {"home": _FULL_LINEUP["home"], "away": _FULL_LINEUP["away"]}
        _seed_lineup(conn, 5803529, old_shape)
        conn.commit()
        out = q.latest_lineup(conn, core, 5803529)
        assert out["lineup_type"] is None
        assert out["source"] is None
        assert out["home"]["team_id"] == 9825  # 阵容本身仍正常返回

    def test_latest_by_observed_at_wins(self, data_dir):
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        _seed_lineup(conn, 5803530, {**_FULL_LINEUP, "source": "old"},
                     observed_at="2026-08-10T00:00:00Z")
        _seed_lineup(conn, 5803530, {**_FULL_LINEUP, "source": "new"},
                     observed_at="2026-08-15T09:00:00Z")
        conn.commit()
        out = q.latest_lineup(conn, core, 5803530)
        assert out["source"] == "new"

    def test_missing_side_degrades_to_empty_side_not_none(self, data_dir):
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        _seed_lineup(conn, 5803531, {"lineup_type": None, "source": None, "home": _FULL_LINEUP["home"]})
        conn.commit()
        out = q.latest_lineup(conn, core, 5803531)
        assert out["home"]["team_id"] == 9825
        assert out["away"]["team_id"] is None
        assert out["away"]["starters"] == []

    def test_starter_and_sub_names_translated_when_i18n_exists(self, data_dir):
        """dim_player_i18n 里已有译名的球员,starters/subs 里的 name 必须换成
        中文——此前本函数只把 bronze_fm_lineup_snap 的原始英文名原样透传,
        从没查过 i18n 表(match_report.py/league_stats.py/player_form.py
        都已经在用同一张表)。"""
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        core.execute(
            "INSERT INTO dim_player_i18n (Player_ID, name_en, name_zh, name_zh_short)"
            " VALUES ('1', 'Antoine Griezmann', '安托万·格里兹曼', '格里兹曼')"
        )
        core.commit()
        payload = {
            "lineup_type": "lastStarting11", "source": "lastStartingLineups",
            "home": {"team_id": 9825, "formation": "4-3-3",
                     "starters": [{"id": 1, "name": "Antoine Griezmann", "shirt_number": 7}],
                     "subs": [{"id": 3, "name": "Untranslated Sub", "shirt_number": 20}]},
            "away": _FULL_LINEUP["away"],
        }
        _seed_lineup(conn, 5803533, payload)
        conn.commit()
        out = q.latest_lineup(conn, core, 5803533)
        assert out["home"]["starters"][0]["name"] == "格里兹曼"
        # 没有译名的球员如实显示来源名,不因为查不到就报错或留空。
        assert out["home"]["subs"][0]["name"] == "Untranslated Sub"

    def test_coach_passed_through_per_side(self, data_dir):
        """coach 是每侧属性,两队教练不同,必须各自透传(2026-08-18,Fix 2)。"""
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        payload = {
            **_FULL_LINEUP,
            "home": {**_FULL_LINEUP["home"], "coach": {"id": 7, "name": "Diego Simeone"}},
            "away": {**_FULL_LINEUP["away"], "coach": {"id": 8, "name": "Someone Else"}},
        }
        _seed_lineup(conn, 5803535, payload)
        conn.commit()
        out = q.latest_lineup(conn, core, 5803535)
        assert out["home"]["coach"]["name"] == "Diego Simeone"
        assert out["away"]["coach"]["id"] == 8

    def test_old_snapshot_without_coach_gives_none_not_a_guess(self, data_dir):
        """2026-08-18 之前写入的行没有 coach 键——不得回填猜测值(镜像
        test_old_snapshot_missing_lineup_type_returns_none_not_a_guess)。"""
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        _seed_lineup(conn, 5803536, _FULL_LINEUP)  # 无 coach 键的旧形状
        conn.commit()
        out = q.latest_lineup(conn, core, 5803536)
        assert out["home"]["coach"] is None
        assert out["away"]["coach"] is None

    def test_player_i18n_row_with_same_id_does_not_rename_coach(self, data_dir):
        """教练 id 与球员 id 的命名空间无法证明互斥;coach 绝不能经过
        _translate_players,否则一次碰撞会把某位教练悄悄改名成某个球员。"""
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        core.execute(
            "INSERT INTO dim_player_i18n (Player_ID, name_en, name_zh, name_zh_short)"
            " VALUES ('7', 'Some Player', '某球员', NULL)"
        )
        core.commit()
        payload = {
            **_FULL_LINEUP,
            "home": {**_FULL_LINEUP["home"], "coach": {"id": 7, "name": "Diego Simeone"}},
        }
        _seed_lineup(conn, 5803537, payload)
        conn.commit()
        out = q.latest_lineup(conn, core, 5803537)
        assert out["home"]["coach"]["name"] == "Diego Simeone"


class TestLatestSidelinedForTeam:
    def test_no_snapshot_returns_empty_list(self, data_dir):
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        assert q.latest_sidelined_for_team(conn, core, 999999, 9825) == []

    def test_none_team_id_returns_empty_list(self, data_dir):
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        assert q.latest_sidelined_for_team(conn, core, 5803528, None) == []

    def test_returns_real_players(self, data_dir):
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        _seed_sideline(conn, 5803528, 9780, [
            {"id": 1158933, "name": "Sabit Abdulai", "reason": "injury", "expected_return": "A few days"},
        ])
        conn.commit()
        out = q.latest_sidelined_for_team(conn, core, 5803528, 9780)
        assert len(out) == 1
        assert out[0]["name"] == "Sabit Abdulai"
        assert out[0]["expected_return"] == "A few days"

    def test_confirmed_empty_list_is_a_legitimate_result(self, data_dir):
        """空列表是"这次观测确实没有伤停"的合法结果,不是异常或缺失。"""
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        _seed_sideline(conn, 5803528, 9781, [])
        conn.commit()
        assert q.latest_sidelined_for_team(conn, core, 5803528, 9781) == []

    def test_latest_by_observed_at_wins(self, data_dir):
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        _seed_sideline(conn, 5803532, 9782, [{"id": 1, "name": "旧伤员", "reason": "injury", "expected_return": None}],
                        observed_at="2026-08-10T00:00:00Z")
        _seed_sideline(conn, 5803532, 9782, [{"id": 2, "name": "新伤员", "reason": "injury", "expected_return": None}],
                        observed_at="2026-08-15T09:00:00Z")
        conn.commit()
        out = q.latest_sidelined_for_team(conn, core, 5803532, 9782)
        assert out == [{"id": 2, "name": "新伤员", "reason": "injury", "expected_return": None}]

    def test_wrong_team_id_does_not_leak_other_team_sidelined(self, data_dir):
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        _seed_sideline(conn, 5803528, 9780, [{"id": 1, "name": "主队伤员", "reason": "injury", "expected_return": None}])
        _seed_sideline(conn, 5803528, 9781, [{"id": 2, "name": "客队伤员", "reason": "injury", "expected_return": None}])
        conn.commit()
        assert [p["name"] for p in q.latest_sidelined_for_team(conn, core, 5803528, 9780)] == ["主队伤员"]
        assert [p["name"] for p in q.latest_sidelined_for_team(conn, core, 5803528, 9781)] == ["客队伤员"]

    def test_sidelined_player_name_translated_when_i18n_exists(self, data_dir):
        conn = connect_rw("odds")
        core = _core_conn(data_dir)
        core.execute(
            "INSERT INTO dim_player_i18n (Player_ID, name_en, name_zh, name_zh_short)"
            " VALUES ('1158933', 'Sabit Abdulai', '萨比特·阿卜杜拉伊', NULL)"
        )
        core.commit()
        _seed_sideline(conn, 5803534, 9780, [
            {"id": 1158933, "name": "Sabit Abdulai", "reason": "injury", "expected_return": "A few days"},
        ])
        conn.commit()
        out = q.latest_sidelined_for_team(conn, core, 5803534, 9780)
        assert out[0]["name"] == "萨比特·阿卜杜拉伊"
