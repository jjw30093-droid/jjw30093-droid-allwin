"""球员榜的名次重编与正/负榜标注(2026-09-16 站长真实反馈)。

站长的两条反馈:

1. **「并列吃名额」**——来源自己的 rank 带了我们看不见的次级排序。实测英超
   2026/2027 进球榜:5 个人 3 球排第 2,另外两个**也是 3 球**却排第 7。差别在
   点球数(`extra_json.SubStatValue` 与我们 shotmap 里的点球进球数逐人吻合,
   B费/萨卡各 1 个点球)。来源的排序合理,但界面只显示总进球数,读者看到
   "一串 3 球却排 2,2,2,2,2,7,7" 只会觉得排错了。
   结论:**顺序沿用来源的**(保住那份次级排序),**名次按显示值重编**。

2. **正/负榜没区分**——来源把 28 个榜**全部**按数值降序排,所以失球榜第 1 名
   是丢球最多的、错失绝佳机会第 1 名是错失最多的。这些榜此前和进球榜长得
   一模一样,读者没有任何线索。站长要求保留负榜但标出来。
"""

import pytest

from backend.db.connections import connect_rw
from backend.queries import league_stats as q

from .coreseed import seed_basic_core


@pytest.fixture
def core_conn(data_dir):
    seed_basic_core(data_dir)
    conn = connect_rw("core")
    try:
        yield conn
    finally:
        conn.close()


def _insert_board(conn, stat_name, rows, league_id=47, season="2025/2026"):
    """rows: [(来源rank, 球员名, 数值)] —— 直接照抄来源的 rank,包括跳号。

    先清空同 (联赛, 赛季, 维度):seed_basic_core 自带的布景行会混进来,
    把名次整体顶偏一位。
    """
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "DELETE FROM fact_season_player_stats WHERE League_ID=? AND Season=? AND stat_name=?",
        (league_id, season, stat_name),
    )
    for i, (rank, name, value) in enumerate(rows):
        conn.execute(
            "INSERT INTO fact_season_player_stats"
            " (League_ID, Season, stat_name, Player_ID, Player_Name, Team_ID, Team_Name, rank, value)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (league_id, season, stat_name, f"p{i}", name, 8650, "队A", rank, value),
        )
    conn.execute("COMMIT")


class TestRankByValue:
    def test_名次按显示值重编_来源的跳号不再泄漏到界面(self, core_conn):
        # 真实形状:哈兰德 4 球独占第 1;5 人 3 球来源排第 2;B费/萨卡同样 3 球
        # 但因为各有 1 个点球,来源把他们排到第 7。
        _insert_board(
            core_conn,
            "goals",
            [
                (1, "哈兰德", 4.0),
                (2, "伊萨克", 3.0),
                (2, "若昂·佩德罗", 3.0),
                (2, "沙德", 3.0),
                (2, "塔弗尼尔", 3.0),
                (2, "罗杰斯", 3.0),
                (7, "B费", 3.0),
                (7, "萨卡", 3.0),
                (9, "埃兰加", 2.0),
            ],
        )
        board = next(
            b
            for b in q.player_leaderboards(core_conn, 47, "2025/2026")["boards"]
            if b["stat_name"] == "goals"
        )
        ranks = [e["rank"] for e in board["entries"]]
        values = [e["value"] for e in board["entries"]]

        # 同样是 3 球的 7 个人,名次必须一致——这是站长看到的那个怪象
        assert ranks == [1, 2, 2, 2, 2, 2, 2, 2, 9]
        # 顺序仍然是来源那一份(非点球进球多的在前),没有被我们重排
        assert [e["name"] for e in board["entries"]][:8] == [
            "哈兰德", "伊萨克", "若昂·佩德罗", "沙德", "塔弗尼尔", "罗杰斯", "B费", "萨卡",
        ]
        # 名次与显示值自洽:值相同 ⇒ 名次相同;值不同 ⇒ 名次不同
        for i in range(1, len(values)):
            assert (values[i] == values[i - 1]) == (ranks[i] == ranks[i - 1])

    def test_并列后跳号仍然正确_不是密集名次(self, core_conn):
        # 2 人并列第 1 ⇒ 下一位是第 3(竞技名次 1-1-3),不是 1-1-2
        _insert_board(core_conn, "yellow_card", [(1, "甲", 12.0), (1, "乙", 12.0), (3, "丙", 11.0)])
        board = next(
            b
            for b in q.player_leaderboards(core_conn, 47, "2025/2026")["boards"]
            if b["stat_name"] == "yellow_card"
        )
        assert [e["rank"] for e in board["entries"]] == [1, 1, 3]


class TestBoardDirection:
    def test_六张负榜被标出_其余都是正榜(self, core_conn):
        _insert_board(core_conn, "goals", [(1, "甲", 1.0)])
        boards = q.player_leaderboards(core_conn, 47, "2025/2026")["boards"]
        bad = {b["stat_name"] for b in boards if b["direction"] == "high_bad"}
        assert bad == {
            "yellow_card", "red_card", "fouls",
            "big_chance_missed", "penalty_conceded", "goals_conceded",
        }
        assert all(b["direction"] in ("high_good", "high_bad") for b in boards)

    def test_扑救不算负榜(self, core_conn):
        """扑救多通常意味着面对的射门多,但它本身是门将的正面动作,
        判成"越多越差"会冤枉人(见 HIGH_IS_BAD_PLAYER_BOARDS 的注释)。"""
        _insert_board(core_conn, "saves", [(1, "门将甲", 3.9)])
        board = next(
            b
            for b in q.player_leaderboards(core_conn, 47, "2025/2026")["boards"]
            if b["stat_name"] == "saves"
        )
        assert board["direction"] == "high_good"


class TestTopN:
    def test_球员榜取前20_球队榜不受影响(self, core_conn):
        _insert_board(core_conn, "goals", [(i + 1, f"球员{i}", 30.0 - i) for i in range(40)])
        board = next(
            b
            for b in q.player_leaderboards(core_conn, 47, "2025/2026")["boards"]
            if b["stat_name"] == "goals"
        )
        assert len(board["entries"]) == 20
        assert board["entries"][-1]["rank"] == 20
        # 球队榜是另一个常量,这次刻意没动
        assert q._BOARD_TOP_N == 10
        assert q._PLAYER_BOARD_TOP_N == 20
