"""parse_season_team_stats:来源方赛季**球队**榜单解析(2026-09-10 新增)。

背景(CLAUDE.md §6.3 / migrations/core/0016 头注释):站长发现联赛球队统计里
有「单场在进攻三区赢得的球权」,单场技术统计里却没有。反编译 FotMob 安卓包
236.17398 确认这不是漏放——他们的单场球队统计模型 PeriodOptaStats 那 72 个
字段里根本没有这个指标,它只存在于赛季聚合模型
(com.fotmob.models.Stats.possessionWonFinal3rd),对外就是这条榜单。

fixture 是 2026-09-10 对英超 2025/2026 的**真实抓取**(仅裁剪行数),不是手写
的形状猜测;断言里的具体数值(Brighton 5.1 rank=1 / 曼城 120 rank=1)都是当时
实测值。

本测试要守住的三条不变量:
1. 身份键必须是 TeamId —— 球队榜里 ParticiantId 恒为 0,拿它当主键会把整个
   联赛压成一行;
2. StatValue 原样落库,不做任何"除以场次"换算 —— poss_won_att_3rd_team 来源
   给的就是场均值;
3. 榜单元数据(stat_title/stat_format/stat_decimals/category)必须进 extra,
   否则前端无法区分"场均"和"赛季合计" —— 这两种在同一批榜里混着出现,且
   StatFormat 都可能是 'fraction'(expected_goals_team 是赛季总数却也是
   fraction),靠字段名或中文标签猜必错。
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from fotmob_client import FotMobClient  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "fotmob" / "season-team-stats-epl-2025-2026.json"
)


def _client_with(leaderboards: dict, fail_for: set[str] | None = None):
    """离线 client:fetch_stat_leaderboard 走 fixture,不发任何网络请求。"""
    client = FotMobClient.__new__(FotMobClient)
    fail_for = fail_for or set()

    def _fetch(url):
        if url in fail_for:
            raise RuntimeError("simulated transport failure")
        return leaderboards[url]

    client.fetch_stat_leaderboard = _fetch  # type: ignore[method-assign]
    return client


def _load():
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return fx["league_payload"], fx["leaderboards"]


class TestParseSeasonTeamStats:
    def test_real_payload_shape(self):
        payload, boards = _load()
        rows = _client_with(boards).parse_season_team_stats(payload, 47, "2025/2026")

        assert {r["stat_name"] for r in rows} == {
            "poss_won_att_3rd_team", "big_chance_team"
        }
        assert all(r["League_ID"] == 47 and r["Season"] == "2025/2026" for r in rows)

        top = next(
            r for r in rows
            if r["stat_name"] == "poss_won_att_3rd_team" and r["rank"] == 1
        )
        # 身份键是 TeamId,不是恒为 0 的 ParticiantId
        assert top["Team_ID"] == 10204
        assert top["Team_Name"] == "Brighton & Hove Albion"
        # 来源已经给的是场均值,原样落库,不再除以 MatchesPlayed
        assert top["value"] == 5.1
        assert top["extra"]["MatchesPlayed"] == 38

    def test_team_id_is_identity_not_participant_id(self):
        """ParticiantId 在球队榜里恒为 0 —— 一旦被当成主键,同一榜的所有球队
        会塌成同一行(自然键 UNIQUE 会直接报错或只剩 1 队)。"""
        payload, boards = _load()
        rows = _client_with(boards).parse_season_team_stats(payload, 47, "2025/2026")
        poss = [r for r in rows if r["stat_name"] == "poss_won_att_3rd_team"]
        assert all(r["extra"]["ParticiantId"] == 0 for r in poss)
        assert len({r["Team_ID"] for r in poss}) == len(poss)

    def test_board_metadata_travels_in_extra(self):
        """场均 vs 赛季合计只能靠来源自报的标题判断,元数据必须落库。"""
        payload, boards = _load()
        rows = _client_with(boards).parse_season_team_stats(payload, 47, "2025/2026")

        poss = next(r for r in rows if r["stat_name"] == "poss_won_att_3rd_team")
        assert poss["extra"]["stat_title"] == "Possession won final 3rd per match"
        assert poss["extra"]["stat_format"] == "fraction"
        assert poss["extra"]["stat_decimals"] == 1
        assert poss["extra"]["category"] == "Defending"

        big = next(r for r in rows if r["stat_name"] == "big_chance_team")
        # 同一批榜里的另一种口径:赛季合计(标题里没有 "per match")
        assert big["extra"]["stat_title"] == "Big chances"
        assert big["extra"]["stat_format"] == "number"
        assert "per match" not in big["extra"]["stat_title"].lower()
        assert next(
            r["value"] for r in rows
            if r["stat_name"] == "big_chance_team" and r["rank"] == 1
        ) == 120.0

    def test_single_dimension_failure_does_not_kill_the_rest(self):
        payload, boards = _load()
        failing = next(iter(boards))
        rows = _client_with(boards, fail_for={failing}).parse_season_team_stats(
            payload, 47, "2025/2026"
        )
        assert rows, "一个维度抓取失败不应让整个赛季颗粒无收"
        assert failing not in {r["stat_name"] for r in rows}

    def test_row_without_team_id_is_skipped(self):
        payload, boards = _load()
        url = next(iter(boards))
        mutated = json.loads(json.dumps(boards))
        mutated[url]["TopLists"][0]["StatList"][0].pop("TeamId")
        rows = _client_with(mutated).parse_season_team_stats(payload, 47, "2025/2026")
        stat_name = mutated[url]["TopLists"][0]["StatName"]
        kept = [r for r in rows if r["stat_name"] == stat_name]
        assert len(kept) == len(mutated[url]["TopLists"][0]["StatList"]) - 1
        assert all(r["Team_ID"] is not None for r in kept)
