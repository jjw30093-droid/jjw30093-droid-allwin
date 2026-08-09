"""parse_league_table 对分组赛制赛季的复合结构(2026-08-07)。

真实来源形状(K League 1 2024 / J1 2026 过渡赛,真实响应观察):完赛分组赛季
没有 data.table,只有 data.tables[]——每个子表带自己的 leagueName 和内层
table.{all,home,away,xg}。不变量:
- 与主联赛同名的子表是总表,按普通 table_type('all' 等)发出——standings
  查询只消费 table_type='all',总表必须落在这里;
- 分组子表的 table_type 加 ":组名" 后缀,绝不与总表混进同一 'all'
  (两组各自 1..N 的位次混在一起就是错误的积分榜);
- 没有总表的赛季(J1 2026 东西分组)只有带后缀的分组行,'all' 诚实为空。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from fotmob_client import FotMobClient


def _team(idx: int, name: str) -> dict:
    return {
        "id": 1000 + idx, "name": name, "idx": idx, "played": 3,
        "wins": 2, "draws": 1, "losses": 0, "scoresStr": "5-2", "pts": 7,
    }


def _composite_payload(with_overall: bool) -> dict:
    tables = [
        {"leagueName": "K-League 1 Final Group A",
         "table": {"all": [_team(1, "蔚山"), _team(2, "浦项")]}},
        {"leagueName": "K-League 1 Final Group B",
         "table": {"all": [_team(1, "大邱"), _team(2, "水原")]}},
    ]
    if with_overall:
        tables.append({"leagueName": "K-League 1",
                       "table": {"all": [_team(i, f"队{i}") for i in range(1, 5)],
                                 "home": [_team(1, "队1")]}})
    # 顶层名刻意用无连字符写法:真实响应里两处写法不一致("K League 1" vs
    # "K-League 1"),总表识别必须做归一化比较而非严格相等(真实踩坑)。
    return {"table": [{"data": {"leagueName": "K League 1", "tables": tables}}]}


class TestCompositeLeagueTable:
    def test_overall_table_lands_in_plain_all(self):
        rows = FotMobClient.parse_league_table(
            FotMobClient.__new__(FotMobClient), _composite_payload(True), 9080, "2024")
        by_type: dict[str, int] = {}
        for r in rows:
            by_type[r["table_type"]] = by_type.get(r["table_type"], 0) + 1
        assert by_type["all"] == 4                       # 总表 4 队 → standings 可见
        assert by_type["home"] == 1
        assert by_type["all:K-League 1 Final Group A"] == 2
        assert by_type["all:K-League 1 Final Group B"] == 2
        # 总表位次连续且不被分组行污染
        overall = sorted(r["position"] for r in rows if r["table_type"] == "all")
        assert overall == [1, 2, 3, 4]

    def test_no_overall_table_keeps_all_empty(self):
        """J1 2026 过渡赛:只有东西分组,没有总表——'all' 不得被伪造。"""
        rows = FotMobClient.parse_league_table(
            FotMobClient.__new__(FotMobClient), _composite_payload(False), 223, "2026")
        assert rows                                       # 分组数据保留
        assert all(r["table_type"] != "all" for r in rows)

    def test_plain_single_table_shape_unchanged(self):
        """普通赛季(data.table 直出)行为不变——回归保护既有五大联赛路径。"""
        payload = {"table": [{"data": {"leagueName": "Premier League",
                                       "table": {"all": [_team(1, "曼联")]}}}]}
        rows = FotMobClient.parse_league_table(
            FotMobClient.__new__(FotMobClient), payload, 47, "2024/2025")
        assert len(rows) == 1 and rows[0]["table_type"] == "all"
