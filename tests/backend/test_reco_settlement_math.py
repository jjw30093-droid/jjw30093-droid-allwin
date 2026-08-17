"""「每日精选」单腿结算纯函数测试(backend/commands/reco_settlement_math.py)。

覆盖四个市场(1x2/ou/corners_ou/ah)的整数线/半线/四分之一线三种粒度,以及
不可判定(SettlementUnresolvable)的 fail-closed 分支。每个用例的注释里写清楚
手算过程,不是凑一个碰巧通过的数字。

ah 让球盘的符号约定(line>0=主队让球/主队是热门)已用真实数据交叉验证,
证据见 backend/commands/reco_settlement_math.py 模块 docstring 与
docs/data-sources.md §2.5;四分之一线用例直接取自真实比赛
(FotMob Match_ID=5104972,Vålerenga 1-2 Bodø/Glimt,真实 ah line=-1.25)。
"""

import pytest

from backend.commands.reco_settlement_math import (
    SettlementUnresolvable,
    resolve_leg_result,
)


class TestOneXTwo:
    """1x2 只有 win/lose,没有 push/half_*。"""

    def test_home_win(self):
        assert resolve_leg_result(
            "1x2", None, "home", home_score=2, away_score=1
        ) == "win"

    def test_home_side_loses_on_away_win(self):
        assert resolve_leg_result(
            "1x2", None, "home", home_score=0, away_score=1
        ) == "lose"

    def test_draw_side_wins_on_draw(self):
        assert resolve_leg_result(
            "1x2", None, "draw", home_score=1, away_score=1
        ) == "win"

    def test_draw_side_loses_on_non_draw(self):
        assert resolve_leg_result(
            "1x2", None, "draw", home_score=2, away_score=0
        ) == "lose"

    def test_away_win(self):
        assert resolve_leg_result(
            "1x2", None, "away", home_score=0, away_score=3
        ) == "win"

    def test_illegal_side_unresolvable(self):
        with pytest.raises(SettlementUnresolvable):
            resolve_leg_result("1x2", None, "favorite", home_score=1, away_score=0)


class TestOverUnderWholeLine:
    """整数线(line=3.0):total>3 大赢,total<3 小赢,total==3 走水。
    手算:line=3.0,over 侧 higher_side_wins=True。"""

    def test_over_wins_when_total_above_line(self):
        # total = 2+2 = 4 > 3.0 → over 赢
        assert resolve_leg_result(
            "ou", 3.0, "over", home_score=2, away_score=2
        ) == "win"

    def test_over_loses_when_total_below_line(self):
        # total = 1+1 = 2 < 3.0 → over 输
        assert resolve_leg_result(
            "ou", 3.0, "over", home_score=1, away_score=1
        ) == "lose"

    def test_push_when_total_equals_whole_line(self):
        # total = 1+2 = 3 == 3.0 → 走水
        assert resolve_leg_result(
            "ou", 3.0, "over", home_score=1, away_score=2
        ) == "push"
        assert resolve_leg_result(
            "ou", 3.0, "under", home_score=1, away_score=2
        ) == "push"

    def test_under_wins_when_total_below_line(self):
        assert resolve_leg_result(
            "ou", 3.0, "under", home_score=1, away_score=1
        ) == "win"

    def test_under_loses_when_total_above_line(self):
        assert resolve_leg_result(
            "ou", 3.0, "under", home_score=2, away_score=2
        ) == "lose"


class TestOverUnderHalfLine:
    """半线(line=2.5):total 恒为整数,不可能走水。"""

    def test_over_wins_above_half_line(self):
        # total = 3 > 2.5 → over 赢
        assert resolve_leg_result(
            "ou", 2.5, "over", home_score=2, away_score=1
        ) == "win"

    def test_over_loses_below_half_line(self):
        # total = 2 < 2.5 → over 输
        assert resolve_leg_result(
            "ou", 2.5, "over", home_score=1, away_score=1
        ) == "lose"

    def test_under_wins_below_half_line(self):
        assert resolve_leg_result(
            "ou", 2.5, "under", home_score=1, away_score=1
        ) == "win"

    def test_under_loses_above_half_line(self):
        assert resolve_leg_result(
            "ou", 2.5, "under", home_score=2, away_score=1
        ) == "lose"


class TestOverUnderQuarterLine:
    """四分之一线标准拆分:line=2.75 拆成 2.5(半线,不可走水)与
    3.0(整线,可走水)两个半仓,各半仓独立判定后合并。

    手算(line=2.75):
    - total=4:vs 2.5 → win(4>2.5);vs 3.0 → win(4>3.0)⇒ 合并 win。
    - total=3:vs 2.5 → win(3>2.5);vs 3.0 → push(3==3.0)⇒ 合并 half_win
      (标准亚洲盘"大2.75,踢3球=半赢")。
    - total=2:vs 2.5 → lose(2<2.5);vs 3.0 → lose(2<3.0)⇒ 合并 lose。
    对应 under 2.75 在 total=3 时是 half_loss(标准亚洲盘"小2.75,踢3球=半输")。

    另用 line=2.25(拆成 2.0 与 2.5)覆盖 push+lose→half_loss、
    push+win→half_win 两种合并分支,line=2.75 覆盖 win+push→half_win、
    lose+push→half_loss——四种合并结果(win/half_win/half_loss/lose)全覆盖。
    """

    def test_total_above_both_components_is_full_win(self):
        assert resolve_leg_result(
            "ou", 2.75, "over", home_score=2, away_score=2
        ) == "win"

    def test_total_hits_integer_component_is_half_win_for_over(self):
        # total=3:2.5(win) + 3.0(push) → half_win
        assert resolve_leg_result(
            "ou", 2.75, "over", home_score=2, away_score=1
        ) == "half_win"

    def test_total_hits_integer_component_is_half_loss_for_under(self):
        # total=3:同一场比赛,under 侧 2.5(lose) + 3.0(push) → half_loss
        assert resolve_leg_result(
            "ou", 2.75, "under", home_score=2, away_score=1
        ) == "half_loss"

    def test_total_below_both_components_is_full_lose(self):
        assert resolve_leg_result(
            "ou", 2.75, "over", home_score=1, away_score=1
        ) == "lose"

    def test_line_2_25_total_2_is_half_loss_for_over(self):
        # line=2.25 拆成 2.0(整数)与 2.5(半数);total=2:
        # vs 2.0 → push(2==2.0);vs 2.5 → lose(2<2.5) ⇒ 合并 half_loss。
        assert resolve_leg_result(
            "ou", 2.25, "over", home_score=1, away_score=1
        ) == "half_loss"

    def test_line_2_25_total_2_is_half_win_for_under(self):
        # 同一场比赛,under 侧:vs 2.0 → push;vs 2.5 → win(2<2.5) ⇒ 合并 half_win。
        assert resolve_leg_result(
            "ou", 2.25, "under", home_score=1, away_score=1
        ) == "half_win"


class TestCornersOuSharesLogicWithOu:
    """corners_ou 判定逻辑与 ou 完全相同,只是总量换成 home_corners+away_corners
    (真实字段来源:fact_team_match_stats.extra_json->corners,例如
    FotMob Match_ID=5803527 主队 corners=7.0、客队 corners=4.0,总角球=11)。"""

    def test_whole_line_win(self):
        # 总角球 7+4=11 > 10.0 → 大角球赢
        assert resolve_leg_result(
            "corners_ou", 10.0, "over",
            home_score=1, away_score=0, home_corners=7.0, away_corners=4.0,
        ) == "win"

    def test_whole_line_push(self):
        assert resolve_leg_result(
            "corners_ou", 11.0, "over",
            home_score=1, away_score=0, home_corners=7.0, away_corners=4.0,
        ) == "push"

    def test_whole_line_lose(self):
        # 总角球 11 < 12.0 → 大角球输
        assert resolve_leg_result(
            "corners_ou", 12.0, "over",
            home_score=1, away_score=0, home_corners=7.0, away_corners=4.0,
        ) == "lose"

    def test_half_line_win_and_lose(self):
        # 总角球 11,半线 10.5:大角球赢(11>10.5),小角球输
        assert resolve_leg_result(
            "corners_ou", 10.5, "over",
            home_score=1, away_score=0, home_corners=7.0, away_corners=4.0,
        ) == "win"
        assert resolve_leg_result(
            "corners_ou", 10.5, "under",
            home_score=1, away_score=0, home_corners=7.0, away_corners=4.0,
        ) == "lose"

    def test_quarter_line_half_loss_for_under(self):
        # line=10.75 拆成 10.5(半)与 11.0(整);总角球=11:
        # vs 10.5 → lose(11>10.5 对 under 不利);vs 11.0 → push ⇒ half_loss。
        assert resolve_leg_result(
            "corners_ou", 10.75, "under",
            home_score=1, away_score=0, home_corners=7.0, away_corners=4.0,
        ) == "half_loss"

    def test_missing_corners_unresolvable(self):
        with pytest.raises(SettlementUnresolvable):
            resolve_leg_result(
                "corners_ou", 10.0, "over",
                home_score=1, away_score=0, home_corners=None, away_corners=4.0,
            )

    def test_missing_line_unresolvable(self):
        with pytest.raises(SettlementUnresolvable):
            resolve_leg_result(
                "corners_ou", None, "over",
                home_score=1, away_score=0, home_corners=7.0, away_corners=4.0,
            )


class TestAsianHandicap:
    """ah 让球盘,符号约定 line>0=主队让球(主队是热门)——见模块 docstring 证据。
    margin = home_score - away_score;判定用 value=margin 与 ou 完全一致的
    _line_result 逻辑,higher_side_wins: home=True / away=False。
    """

    def test_whole_line_home_covers(self):
        # line=1.0(主队让1球是热门):margin=3-1=2>1.0 → 主队方赢盘
        assert resolve_leg_result(
            "ah", 1.0, "home", home_score=3, away_score=1
        ) == "win"

    def test_whole_line_home_fails_to_cover_is_push_at_exact_margin(self):
        # margin=2-1=1==line(1.0) → 走水
        assert resolve_leg_result(
            "ah", 1.0, "home", home_score=2, away_score=1
        ) == "push"
        assert resolve_leg_result(
            "ah", 1.0, "away", home_score=2, away_score=1
        ) == "push"

    def test_negative_line_away_is_favorite(self):
        # line=-1.0(客队让1球是热门):margin=1-2=-1==line(-1.0) → 走水
        assert resolve_leg_result(
            "ah", -1.0, "home", home_score=1, away_score=2
        ) == "push"

    def test_negative_line_home_side_wins_when_favorite_fails_outright(self):
        # line=-2.0(客队让2球是热门):真实结果客队只赢1球,margin=-1>-2.0
        # → 主队方(受让方)赢盘——热门没让够球数。
        assert resolve_leg_result(
            "ah", -2.0, "home", home_score=0, away_score=1
        ) == "win"

    def test_real_match_quarter_line_home_half_win(self):
        """真实比赛 FotMob Match_ID=5104972(Vålerenga 1-2 Bodø/Glimt),
        真实 ah line=-1.25(客队 Bodø/Glimt 是热门,让 1.25 球)。

        手算:margin = 1-2 = -1。line=-1.25 拆成 low=-1.5、high=-1.0。
        home 侧(higher_side_wins=True):
          vs low(-1.5) → win(-1 > -1.5);vs high(-1.0) → push(-1==-1.0)
          ⇒ 合并 half_win(主队/受让方半赢——热门只赢1球,没赢够1.25)。
        """
        assert resolve_leg_result(
            "ah", -1.25, "home", home_score=1, away_score=2
        ) == "half_win"

    def test_real_match_quarter_line_away_half_loss(self):
        """同一场真实比赛,away 侧(higher_side_wins=False):
          vs low(-1.5) → lose(-1>-1.5 对 away 不利);vs high(-1.0) → push
          ⇒ 合并 half_loss(客队/让球热门半输——赢了但没赢够 1.25 球)。
        """
        assert resolve_leg_result(
            "ah", -1.25, "away", home_score=1, away_score=2
        ) == "half_loss"

    def test_illegal_side_unresolvable(self):
        with pytest.raises(SettlementUnresolvable):
            resolve_leg_result("ah", 0.5, "draw", home_score=1, away_score=1)

    def test_missing_line_unresolvable(self):
        with pytest.raises(SettlementUnresolvable):
            resolve_leg_result("ah", None, "home", home_score=1, away_score=1)


class TestFailClosedEdgeCases:
    def test_unsupported_market_unresolvable(self):
        with pytest.raises(SettlementUnresolvable):
            resolve_leg_result("correct_score", None, "1-0", home_score=1, away_score=0)

    def test_non_quarter_step_line_unresolvable(self):
        # 2.1 不是 0.25 的整数倍,不是合法亚洲盘口线,拒绝猜测。
        with pytest.raises(SettlementUnresolvable):
            resolve_leg_result("ou", 2.1, "over", home_score=3, away_score=0)

    def test_missing_scores_unresolvable(self):
        with pytest.raises(SettlementUnresolvable):
            resolve_leg_result("1x2", None, "home", home_score=None, away_score=1)
