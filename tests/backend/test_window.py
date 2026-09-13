"""backend/queries/window.py 测试:分级 fallback(venue_full → venue_partial →
mixed → unavailable)、最大回溯上限、确定性排序、边界精确到 kickoff。"""

from __future__ import annotations

import pytest

from backend.db.connections import connect_rw
from backend.queries import window as w
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
TEAM = 1001


def _seed_matches(conn, count, *, home, start_id, date_prefix, is_home_for_team,
                  league_id=LEAGUE):
    """给 TEAM 造 count 场同一主客场类型的历史比赛,近期在前。

    `league_id` 可选末位参数、默认 LEAGUE —— 既有调用点逐字不变。"""
    for i in range(count):
        mid = start_id + i
        if is_home_for_team:
            insert_match(conn, mid, league_id=league_id, date=f"{date_prefix}-{10+i:02d}",
                         home_id=TEAM, away_id=3000 + i, home="队A", away=f"对手{i}",
                         status="Finish", home_score=1, away_score=0)
        else:
            insert_match(conn, mid, league_id=league_id, date=f"{date_prefix}-{10+i:02d}",
                         home_id=3000 + i, away_id=TEAM, home=f"对手{i}", away="队A",
                         status="Finish", home_score=0, away_score=1)


class TestVenueWindowTiers:
    def test_venue_full_when_ten_or_more_same_venue_matches(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 12, home=True, start_id=9000, date_prefix="2026-01", is_home_for_team=True)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "venue_full"
        assert r.matches == 10  # 封顶 max_n,不是全部 12 场
        assert r.mixed_venues is False

    def test_venue_partial_when_fewer_than_ten_but_at_least_min_n(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 7, home=True, start_id=9100, date_prefix="2026-01", is_home_for_team=True)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "venue_partial"
        assert r.matches == 7
        assert "样本不足" in r.label_zh

    def test_falls_back_to_mixed_when_venue_specific_below_min_n(self, data_dir):
        """同主客场只有 2 场(< min_n=5)——必须回退到混合主客场,
        且返回值里显式标 tier='mixed',界面据此加"已合并主客场"提示。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 2, home=True, start_id=9200, date_prefix="2026-01", is_home_for_team=True)
        _seed_matches(conn, 4, home=False, start_id=9210, date_prefix="2026-01", is_home_for_team=False)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "mixed"
        assert r.matches == 6  # 2 主场 + 4 客场 混合
        assert r.mixed_venues is True
        assert "已合并主客场" in r.label_zh

    def test_unavailable_when_zero_history_even_mixed(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        r = w.venue_window(conn, 9999, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "unavailable"
        assert r.matches == 0
        assert r.match_ids == []
        assert r.from_date is None and r.to_date is None

    def test_venue_partial_boundary_exactly_at_min_n(self, data_dir):
        """恰好等于 min_n(5)场:必须走 venue_partial,不回退到 mixed
        (5 >= min_n 应该已经"够用",不该被当成不足)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 5, home=True, start_id=9300, date_prefix="2026-01", is_home_for_team=True)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "venue_partial"
        assert r.matches == 5


class TestMaxLookback:
    def test_matches_older_than_lookback_are_excluded(self, data_dir):
        """两年前的比赛不该被无条件拉进来凑场次——即使凑不够 min_n,
        也要诚实降级,而不是跨越多个赛季硬凑数字。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 3 场"新鲜"历史 + 2 场超过 730 天回溯上限的"古老"历史
        _seed_matches(conn, 3, home=True, start_id=9400, date_prefix="2026-01", is_home_for_team=True)
        insert_match(conn, 9410, league_id=LEAGUE, date="2020-01-10",
                     home_id=TEAM, away_id=4000, home="队A", away="老对手1",
                     status="Finish", home_score=1, away_score=0)
        insert_match(conn, 9411, league_id=LEAGUE, date="2020-01-11",
                     home_id=TEAM, away_id=4001, home="队A", away="老对手2",
                     status="Finish", home_score=1, away_score=0)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        # 只有 3 场新鲜历史进窗口(不足 min_n=5,但也不该混进 2020 年的比赛凑数)
        assert r.matches == 3
        assert r.from_date >= "2024-02-01"  # 远早于 730 天回溯上限的比赛不该出现


class TestDeterministicOrder:
    def test_same_calendar_date_matches_ordered_by_kickoff_not_ambiguous(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 9500, league_id=LEAGUE, date="2026-01-10",
                     home_id=TEAM, away_id=5000, home="队A", away="对手甲",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2026-01-10T08:00:00Z")
        insert_match(conn, 9501, league_id=LEAGUE, date="2026-01-10",
                     home_id=TEAM, away_id=5001, home="队A", away="对手乙",
                     status="Finish", home_score=2, away_score=0,
                     kickoff_at_utc="2026-01-10T20:00:00Z")
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-01-11", is_home=True, max_n=1, min_n=1)
        assert r.match_ids == [9501]  # kickoff 更晚的那场


class TestFutureExclusion:
    def test_future_match_on_same_day_as_boundary_is_excluded(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 9600, league_id=LEAGUE, date="2026-01-10",
                     home_id=TEAM, away_id=6000, home="队A", away="过去",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2026-01-10T08:00:00Z")
        insert_match(conn, 9601, league_id=LEAGUE, date="2026-01-10",
                     home_id=TEAM, away_id=6001, home="队A", away="未来",
                     status="Finish", home_score=2, away_score=0,
                     kickoff_at_utc="2026-01-10T22:00:00Z")
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-01-10T12:00:00Z", is_home=True, max_n=5, min_n=1)
        assert r.match_ids == [9600]  # 22:00 那场晚于边界(12:00),不算历史


class TestLeagueIsolation:
    def test_other_league_matches_never_leak_in(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 3, home=True, start_id=9700, date_prefix="2026-01", is_home_for_team=True)
        insert_match(conn, 9710, league_id=87, date="2026-01-15",
                     home_id=TEAM, away_id=7000, home="队A", away="西甲对手",
                     status="Finish", home_score=1, away_score=0)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert 9710 not in r.match_ids


class TestCrossLeagueWindow:
    """`league_id=None`(不限赛事)——只给参赛队来自不同国内联赛的赛事用。

    2026-09-10 真实缺陷:欧冠比赛的「数据→风格」整个 tab 全空,因为窗口按
    本场 League_ID(42)圈,而欧冠联赛阶段每队只踢 8 场(主客各 4),同主客场
    永远到不了 min_n=5(生产实测欧冠够格球队 0 支)。
    """

    def _seed_domestic_plus_cup(self, conn):
        """挪超 6 个主场 + 欧冠 1 个主场——模拟维京的真实形态。"""
        for i in range(6):
            insert_match(conn, 9800 + i, league_id=59, season="2026",
                         date=f"2026-08-{10+i:02d}", home_id=TEAM, away_id=8100 + i,
                         home="队A", away=f"挪超对手{i}", status="Finish",
                         home_score=1, away_score=0)
        insert_match(conn, 9850, league_id=42, season="2026/2027", date="2026-09-09",
                     home_id=TEAM, away_id=8200, home="队A", away="欧冠对手",
                     status="Finish", home_score=0, away_score=2)

    def test_scoped_to_cup_league_is_empty_which_is_the_bug(self, data_dir):
        """先把缺陷本身钉住:圈在欧冠内只有 1 场,连 mixed 都救不回 min_n。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        self._seed_domestic_plus_cup(conn)
        conn.commit()
        r = w.venue_window(conn, TEAM, 42, "2026-10-13", is_home=True)
        assert r.matches == 1
        assert r.tier == "mixed"  # 同主客场 1 场 < min_n,退到混合仍只有这 1 场

    def test_unscoped_window_spans_competitions(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        self._seed_domestic_plus_cup(conn)
        conn.commit()
        r = w.venue_window(conn, TEAM, None, "2026-10-13", is_home=True)
        assert r.matches == 7  # 6 场挪超 + 1 场欧冠
        assert 9850 in r.match_ids  # 欧冠那场也算数
        assert r.tier == "venue_partial"  # 7 >= min_n,不再是 mixed
        assert r.cross_league is True

    def test_label_says_cross_competition_not_just_recent_home_games(self, data_dir):
        """tier 仍是 venue_full/partial,光看档位读不出"这几场不在同一联赛里"
        ——label 必须自己说出来,否则界面写"近 7 个主场"就是错误陈述。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        self._seed_domestic_plus_cup(conn)
        conn.commit()
        r = w.venue_window(conn, TEAM, None, "2026-10-13", is_home=True)
        assert "不限赛事" in r.label_zh
        assert "主场" in r.label_zh

    def test_unavailable_tier_gets_no_misleading_prefix(self, data_dir):
        """零场比赛时不该出现"不限赛事·暂无可比较的历史比赛"这种拼接。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        r = w.venue_window(conn, 9999, None, "2026-10-13", is_home=True)
        assert r.tier == "unavailable"
        assert "不限赛事" not in r.label_zh

    def test_league_scoped_path_byte_identical_to_before(self, data_dir):
        """零回归锚:传 int 时行为与放宽之前完全一致——跨赛事的比赛不进窗口,
        label 也不带"不限赛事"前缀。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        self._seed_domestic_plus_cup(conn)
        conn.commit()
        r = w.venue_window(conn, TEAM, 59, "2026-10-13", is_home=True)
        assert r.match_ids == [9805, 9804, 9803, 9802, 9801, 9800]
        assert 9850 not in r.match_ids
        assert r.tier == "venue_partial"
        assert r.cross_league is False
        # 2026-09-13:label 现在写出联赛名(59=挪威超)。窗口只圈本联赛、欧战和
        # 国内杯赛都不在里面,不写出来就是把"近 6 个挪威超主场"说成"近 6 个主场"。
        # 本条测试其余部分(选中的 match_ids、tier、cross_league)仍是当初那条
        # 跨赛事放宽的零回归锚,没有变。
        assert r.label_zh == "近 6 个挪威超主场(样本不足 10,已如实展示实际场次)"


class TestLabelNamesTheCompetition:
    """窗口默认圈在单个 League_ID 内(欧战和国内杯赛全被排除),label 必须说出来。

    与 `不限赛事·` 前缀是同一条纪律的两个方向:那边防的是"跨了赛事却不说",
    这边防的是"只有本联赛却说得像全部比赛"。2026-09-13 先在百分位模块
    (matchProfile.ts::profileWindowNote)修过一次,这里补齐。
    """

    def test_league_scoped_label_names_the_league(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 10, home=True, start_id=9300, date_prefix="2026-01", is_home_for_team=True)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "venue_full"
        assert r.league_zh == "英超"
        assert r.label_zh == "近 10 个英超主场"

    def test_mixed_tier_also_names_the_league(self, data_dir):
        """合并主客场那一档同样只在本联赛内合并,联赛名不能掉。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 2, home=True, start_id=9400, date_prefix="2026-01", is_home_for_team=True)
        _seed_matches(conn, 4, home=False, start_id=9410, date_prefix="2026-01", is_home_for_team=False)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "mixed"
        assert r.label_zh.startswith("近 6 场英超(")
        assert "已合并主客场" in r.label_zh  # 既有的这句免责不能被挤掉

    def test_cross_league_window_has_no_league_name(self, data_dir):
        """不限赛事的窗口没有联赛可写,硬塞一个名字就是假的;
        措辞必须与加 league_zh 之前逐字节相同。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 10, home=True, start_id=9500, date_prefix="2026-01", is_home_for_team=True)
        conn.commit()
        r = w.venue_window(conn, TEAM, None, "2026-02-01", is_home=True)
        assert r.league_zh is None
        assert r.label_zh == "不限赛事·近 10 个主场"

    def test_missing_league_name_falls_back_to_old_wording(self):
        """拿不到译名时回落到不带联赛名的旧措辞,不产出"近 10 个None主场"。

        走 WindowResult 直接构造而不是造数据:`dim_match` 有触发器
        (migrations/core/0011)拒绝未登记联赛写入,这种行(League_ID 在
        LEAGUE_META 之外)在库里结构上不可能存在——`LEAGUE_META.get()` 的
        兜底是纯防御,只能在这一层验证。四档模板都要验,任何一档漏了
        `{league}` 的空串处理都会在这里暴露。
        """
        for tier, expected in (
            ("venue_full", "近 6 个主场"),
            ("venue_partial", "近 6 个主场(样本不足 10,已如实展示实际场次)"),
            ("mixed", "近 6 场(主客场样本均不足,已合并主客场——不能与纯主场/客场窗口直接比较)"),
            ("unavailable", "暂无可比较的历史比赛"),
        ):
            r = w.WindowResult(team_id=TEAM, is_home=True, tier=tier, match_ids=[],
                               matches=6, from_date=None, to_date=None, league_zh=None)
            assert r.label_zh == expected, tier
