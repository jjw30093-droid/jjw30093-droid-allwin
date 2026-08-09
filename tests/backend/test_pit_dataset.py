"""pit_dataset 严格 point-in-time 不变量(任务规格最低测试要求)。"""

from __future__ import annotations

import sqlite3

import pytest

from backend.models.research.pit_dataset import (
    ALL_EIGHT_LEAGUES, FIVE_LEAGUES, build_dataset, detect_stage_splits,
    load_matches, _is_stage_round,
)


def _mk_core():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE dim_match (Match_ID INT PRIMARY KEY, League_ID INT, Season TEXT,
          Date TEXT, kickoff_at_utc TEXT, kickoff_precision TEXT,
          Home_Team_ID INT, Away_Team_ID INT, home_score INT, away_score INT,
          Match_Round TEXT, status TEXT);
        CREATE TABLE fact_team_match_stats (Match_ID INT, Team_ID INT,
          Period TEXT, extra_json TEXT);
    """)
    return conn


def _add_match(conn, mid, kickoff, home, away, hs=1, as_=0, season="2024/2025",
               league=47, xg_home=1.5, xg_away=0.8, round_="1", precision="exact",
               status="Finish"):
    conn.execute(
        "INSERT INTO dim_match VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (mid, league, season, kickoff[:10] if kickoff else "2024-08-01",
         kickoff, precision, home, away, hs, as_, round_, status))
    for tid, xg in ((home, xg_home), (away, xg_away)):
        conn.execute(
            "INSERT INTO fact_team_match_stats VALUES (?,?,?,?)",
            (mid, tid, "All",
             f'{{"expected_goals": {xg}, "total_shots": 10, "ShotsOnTarget": 4, "BallPossesion": 50}}'))


class TestPointInTime:
    def test_target_match_not_in_own_features(self):
        conn = _mk_core()
        _add_match(conn, 1, "2024-08-01T15:00:00Z", 10, 11, xg_home=2.0)
        _add_match(conn, 2, "2024-08-08T15:00:00Z", 10, 12, xg_home=9.9)
        ds = build_dataset(conn)
        m2 = next(r for r in ds["rows"] if r["match_id"] == 2)
        # match 2 的主队 rolling 只能来自 match 1 的 xg(2.0),绝不含当场 9.9
        assert m2["features"]["home_xg_for_l5"] == 2.0
        m1 = next(r for r in ds["rows"] if r["match_id"] == 1)
        assert m1["features"]["home_xg_for_l5"] is None      # 无历史,不用当场值

    def test_same_kickoff_no_mutual_leak(self):
        conn = _mk_core()
        # 同刻两场:队 10 与队 20 各踢一场,互不进入对方历史
        _add_match(conn, 1, "2024-08-01T15:00:00Z", 10, 11)
        _add_match(conn, 2, "2024-08-01T15:00:00Z", 20, 21)
        _add_match(conn, 3, "2024-08-08T15:00:00Z", 10, 20)
        ds = build_dataset(conn)
        m1 = next(r for r in ds["rows"] if r["match_id"] == 1)
        m2 = next(r for r in ds["rows"] if r["match_id"] == 2)
        assert m1["features"]["home_n_matches_l5"] == 0
        assert m2["features"]["home_n_matches_l5"] == 0
        m3 = next(r for r in ds["rows"] if r["match_id"] == 3)
        assert m3["features"]["home_n_matches_l5"] == 1      # 之后的场能看到两者
        assert m3["features"]["away_n_matches_l5"] == 1

    def test_same_day_ordering_strictly_by_kickoff(self):
        conn = _mk_core()
        # 同日两场,早场 match_id 更大——若按 match_id 排序会颠倒
        _add_match(conn, 9, "2024-08-01T12:00:00Z", 10, 11, xg_home=3.0)
        _add_match(conn, 5, "2024-08-01T18:00:00Z", 10, 12)
        ds = build_dataset(conn)
        late = next(r for r in ds["rows"] if r["match_id"] == 5)
        assert late["features"]["home_xg_for_l5"] == 3.0     # 早场进了晚场历史
        early = next(r for r in ds["rows"] if r["match_id"] == 9)
        assert early["features"]["home_n_matches_l5"] == 0   # 晚场没进早场历史

    def test_future_match_never_in_past_sample(self):
        conn = _mk_core()
        _add_match(conn, 1, "2024-08-01T15:00:00Z", 10, 11)
        _add_match(conn, 2, "2025-01-01T15:00:00Z", 10, 12, xg_home=5.0)
        ds = build_dataset(conn)
        m1 = next(r for r in ds["rows"] if r["match_id"] == 1)
        assert m1["features"]["home_n_matches_l5"] == 0
        assert m1["n_lineage_inputs"] == 0

    def test_cross_season_history_allowed_and_counted(self):
        conn = _mk_core()
        _add_match(conn, 1, "2024-05-01T15:00:00Z", 10, 11, season="2023/2024", xg_home=2.5)
        _add_match(conn, 2, "2024-08-15T15:00:00Z", 10, 12, season="2024/2025")
        ds = build_dataset(conn)
        m2 = next(r for r in ds["rows"] if r["match_id"] == 2)
        assert m2["features"]["home_xg_for_l5"] == 2.5       # 跨季历史可用
        assert m2["features"]["home_n_prior_season"] == 0    # 但本季场次数如实为 0

    def test_missing_kickoff_fail_closed(self):
        conn = _mk_core()
        _add_match(conn, 1, None, 10, 11, precision="date_only")
        _add_match(conn, 2, "2024-08-08T15:00:00Z", 10, 12)
        ds = build_dataset(conn)
        assert {r["match_id"] for r in ds["rows"]} == {2}
        assert ds["manifest"]["quality"]["dropped_no_exact_kickoff"] == 1

    def test_playoff_flagged_and_excluded_by_default(self):
        conn = _mk_core()
        _add_match(conn, 4185671, "2023-06-11T18:00:00Z", 10, 11,
                   season="2022/2023", league=55, round_="final")
        _add_match(conn, 2, "2023-08-20T15:00:00Z", 10, 12, league=55, season="2023/2024")
        ds = build_dataset(conn, leagues=(55,))
        assert all(r["match_id"] != 4185671 for r in ds["rows"])
        # 但附加赛真实发生过 → 作为后续比赛的历史仍然可见
        m2 = next(r for r in ds["rows"] if r["match_id"] == 2)
        assert m2["features"]["home_n_matches_l5"] == 1
        ds_inc = build_dataset(conn, leagues=(55,), include_playoff=True)
        row = next(r for r in ds_inc["rows"] if r["match_id"] == 4185671)
        assert row["is_playoff"] == 1

    def test_deterministic_rebuild(self):
        conn = _mk_core()
        for i in range(1, 8):
            _add_match(conn, i, f"2024-08-0{i}T15:00:00Z", 10 + (i % 3), 20 + (i % 4))
        a = build_dataset(conn)
        b = build_dataset(conn)
        assert a["dataset_hash"] == b["dataset_hash"]
        assert a["rows"] == b["rows"]

    def test_rest_days_and_lineage(self):
        conn = _mk_core()
        _add_match(conn, 1, "2024-08-01T15:00:00Z", 10, 11)
        _add_match(conn, 2, "2024-08-04T15:00:00Z", 10, 12)
        ds = build_dataset(conn)
        m2 = next(r for r in ds["rows"] if r["match_id"] == 2)
        assert m2["features"]["home_rest_days"] == 3.0
        assert m2["n_lineage_inputs"] == 1
        assert m2["input_cutoff_at"] == "2024-08-04T15:00:00Z"


class TestLineageCompleteness:
    def test_venue_window_inputs_counted_in_lineage(self):
        """RED(2026-08-07 对抗复核):venue 窗口可回溯到 overall-10 之外,
        这些比赛是真实特征输入,必须计入 lineage(真实数据 5,821 个案例)。

        构造:队 10 先踢 3 场主场,再踢 10 场客场,然后第 14 场主场——
        home venue 窗口用到最早 3 场主场,它们已被挤出 overall-10。"""
        conn = _mk_core()
        mid = 1
        for i in range(3):                       # 3 场主场(最早)
            _add_match(conn, mid, f"2024-01-{i+1:02d}T15:00:00Z", 10, 90 + mid)
            mid += 1
        for i in range(10):                      # 10 场客场
            _add_match(conn, mid, f"2024-02-{i+1:02d}T15:00:00Z", 90 + mid, 10)
            mid += 1
        _add_match(conn, 99, "2024-03-01T15:00:00Z", 10, 88)   # 目标主场比赛
        ds = build_dataset(conn)
        target = next(r for r in ds["rows"] if r["match_id"] == 99)
        # 主队输入 = overall 最近10(场4..13) ∪ venue 主场窗口(场1..3);
        # 客队 88 无历史。lineage 必须 ≥ 13 场
        assert target["n_lineage_inputs"] >= 13, (
            f"lineage 只记了 {target['n_lineage_inputs']} 场,venue 窗口输入被漏记"
        )
        # 且 venue 特征确实用上了旧主场比赛(非 None)
        assert target["features"]["home_xg_for_home_l5"] is not None


class TestFrozenDefaultHashRegression:
    """P0-8(2026-08-08 J1/K1/澳超接入收口):默认参数路径(leagues=FIVE_LEAGUES,
    不传任何新增的 include_playoff_in_history/detect_stage_splits_flag 参数)
    的 dataset_hash 必须对固定 fixture 保持逐位不变——这是保护"新增能力不
    改变旧行为"的 CI 级别回归测试。

    不对真实生产库的 172d4428…… 冻结哈希做硬编码断言:那个值只在特定时刻
    的真实数据快照上有效,生产库会随新比赛结算持续增长,未来必然不再等于
    今天的值,把它写成 pytest 断言反而会制造一个必然过期的假阳性失败。
    对生产库的一次性验证记录在 docs/audits/multileague-jka-integration-v1.md
    (2026-08-08 从只读快照重新计算,逐位确认仍为 172d4428……)。
    """

    def test_default_params_hash_pinned_on_fixture(self):
        conn = _mk_core()
        mid = 1
        for rnd in range(1, 6):
            for (h, a) in [(1, 2), (3, 4)]:
                _add_match(conn, mid, f"2024-08-{rnd:02d}T15:00:00Z", h, a,
                          hs=(mid % 3), as_=((mid + 1) % 3), round_=str(rnd))
                mid += 1
        ds = build_dataset(conn)
        assert ds["dataset_hash"] == (
            "e30769155b1f2742aaa9bc8a78e8b43e033129652628e8b9a1e321c08a7245c8"
        ), "默认参数路径的 dataset_hash 变了——检查是否意外改变了行结构"
        assert len(ds["rows"]) == 10

    def test_new_optional_params_default_to_no_behavior_change(self):
        """显式传入新参数的默认值,必须和完全不传参数得到同一个 hash——
        证明新增的 include_playoff_in_history/detect_stage_splits_flag
        真的是"不传就不变"的纯加法式扩展。"""
        conn = _mk_core()
        _add_match(conn, 1, "2024-08-01T15:00:00Z", 1, 2)
        _add_match(conn, 2, "2024-08-08T15:00:00Z", 1, 3)
        implicit = build_dataset(conn)
        explicit = build_dataset(conn, include_playoff=False,
                                 include_playoff_in_history=True,
                                 detect_stage_splits_flag=False)
        assert implicit["dataset_hash"] == explicit["dataset_hash"]
        assert implicit["rows"] == explicit["rows"]


class TestStageRoundDetection:
    """2026-08-08 J1/K1/澳超接入:赛制阶段(附加赛/季后赛/排位赛)规则识别,
    等价于 SQL `Match_Round GLOB '*[^0-9]*'`。已实测该规则在五大联赛上推导出
    的集合与硬编码 PLAYOFF_MATCH_IDS 完全相同(均为 {4185671}),默认行为不变。
    """

    def test_pure_numeric_round_is_not_stage(self):
        assert _is_stage_round("1") is False
        assert _is_stage_round("38") is False

    def test_non_numeric_round_is_stage(self):
        assert _is_stage_round("final") is True
        assert _is_stage_round("bronze") is True
        assert _is_stage_round("1/4") is True
        assert _is_stage_round("1/2") is True
        assert _is_stage_round("5/6") is True

    def test_none_round_is_not_stage(self):
        # 数据库里没有 NULL Match_Round(已实测验证);防御性地不当成赛制阶段
        assert _is_stage_round(None) is False

    def test_playoff_target_excludes_non_numeric_round_by_default(self):
        conn = _mk_core()
        _add_match(conn, 1, "2024-08-01T15:00:00Z", 1, 2, round_="1")
        _add_match(conn, 2, "2024-08-08T15:00:00Z", 1, 2, round_="final")
        ds = build_dataset(conn)
        ids = {r["match_id"] for r in ds["rows"]}
        assert 1 in ids
        assert 2 not in ids, "非纯数字轮次(赛制阶段)默认不应进入目标样本"

    def test_playoff_still_enters_history_by_default(self):
        """target 排除赛制阶段比赛,但默认(include_playoff_in_history=True)
        它仍应作为后续比赛的滚动历史输入——这是 2026-08-08 之前就有的隐式行为,
        现在显式成一个独立开关,默认值必须保持不变。"""
        conn = _mk_core()
        _add_match(conn, 1, "2024-08-01T15:00:00Z", 1, 2, round_="final", xg_home=3.0)
        _add_match(conn, 2, "2024-08-08T15:00:00Z", 1, 3, round_="2")
        ds = build_dataset(conn)
        target = next(r for r in ds["rows"] if r["match_id"] == 2)
        assert target["features"]["home_xg_for_l5"] == 3.0, (
            "赛制阶段比赛应计入历史滚动窗口(默认行为)"
        )

    def test_playoff_excluded_from_history_when_flag_off(self):
        conn = _mk_core()
        _add_match(conn, 1, "2024-08-01T15:00:00Z", 1, 2, round_="final", xg_home=3.0)
        _add_match(conn, 2, "2024-08-08T15:00:00Z", 1, 3, round_="2")
        ds = build_dataset(conn, include_playoff_in_history=False)
        target = next(r for r in ds["rows"] if r["match_id"] == 2)
        assert target["features"]["home_xg_for_l5"] is None, (
            "include_playoff_in_history=False 时赛制阶段比赛不得进入历史窗口"
        )


class TestStageSplitDetection:
    """2026-08-08 对抗复核:K1 每季第 34-38 轮 Final A/Final B 分组、
    J1 2026 东西分区,schema 里没有标记,用赛程图连通分量确定性识别。

    初版算法(从最后一轮开始逐轮累加、遇到 comps==1 立即停)已被对抗复核
    证明会在任意正常赛季的最后 1-2 轮误判(单轮天然不连通)。修正版要求
    窗口至少 3 轮、每个分量至少 3 队。以下测试覆盖正反两向,并且已对全部
    39 个真实 league-season 空跑验证零误报(2026-08-08,见审计文档)。
    """

    def test_two_disjoint_groups_detected_as_split(self):
        """构造:12 队分成两组各 6 队,5 轮组内单循环(每队和组内其他队各踢
        一场,类 K1 34-38 轮的 Final A/Final B),两组之间一场不打。"""
        conn = _mk_core()
        group_a = [1, 2, 3, 4, 5, 6]
        group_b = [11, 12, 13, 14, 15, 16]

        def _round_robin_pairs(teams):
            """标准 circle method 排出 len(teams)-1 轮单循环,每轮 3 场不重复对阵。"""
            n = len(teams)
            fixed, rot = teams[0], teams[1:]
            rounds = []
            for _ in range(n - 1):
                cur = [fixed] + rot
                rounds.append([(cur[i], cur[n - 1 - i]) for i in range(n // 2)])
                rot = rot[-1:] + rot[:-1]
            return rounds

        mid = 1
        for rnd, (pa, pb) in enumerate(zip(_round_robin_pairs(group_a),
                                            _round_robin_pairs(group_b)), start=1):
            for i, (a, b) in enumerate(pa):
                _add_match(conn, mid, f"2024-01-{rnd:02d}T{15+i:02d}:00:00Z", a, b,
                           round_=str(rnd))
                mid += 1
            for i, (a, b) in enumerate(pb):
                _add_match(conn, mid, f"2024-01-{rnd:02d}T{18+i:02d}:00:00Z", a, b,
                           round_=str(rnd))
                mid += 1
        matches, _ = load_matches(conn, leagues=(47,))
        split_ids = detect_stage_splits(matches)
        assert len(split_ids) == len(matches), "两组各自完全图应被判定为整体分裂"

    def test_single_connected_pool_not_flagged(self):
        """对照:同样 12 队但正常单循环(每轮都跨组对阵),不应被判定为分裂。"""
        conn = _mk_core()
        mid = 1
        # 5 轮,每轮 6 场,但对阵关系跨越两组(制造充分连通)
        schedule = [
            [(1, 11), (2, 12), (3, 13), (4, 14), (5, 15), (6, 16)],
            [(1, 12), (2, 13), (3, 14), (4, 15), (5, 16), (6, 11)],
            [(1, 13), (2, 14), (3, 15), (4, 16), (5, 11), (6, 12)],
            [(1, 2), (3, 4), (5, 6), (11, 12), (13, 14), (15, 16)],
            [(1, 3), (2, 5), (4, 6), (11, 13), (12, 15), (14, 16)],
        ]
        for rnd, pairs in enumerate(schedule, start=1):
            for i, (a, b) in enumerate(pairs):
                _add_match(conn, mid, f"2024-01-{rnd:02d}T{15+i:02d}:00:00Z", a, b,
                           round_=str(rnd))
                mid += 1
        matches, _ = load_matches(conn, leagues=(47,))
        split_ids = detect_stage_splits(matches)
        assert split_ids == set(), "跨组对阵的正常赛季不应被判定为分裂"

    def test_single_trailing_round_alone_not_flagged(self):
        """反例保护:一个正常赛季的最后一轮单独看必然不连通(N/2 场比赛=
        N/2 个不相交分量),初版算法会误判,修正版靠"窗口至少 3 轮"排除。"""
        conn = _mk_core()
        mid = 1
        for rnd in range(1, 4):
            for i, (a, b) in enumerate([(1, 2), (3, 4), (5, 6), (7, 8)]):
                _add_match(conn, mid, f"2024-01-{rnd:02d}T{15+i:02d}:00:00Z", a, b,
                           round_=str(rnd))
                mid += 1
        matches, _ = load_matches(conn, leagues=(47,))
        split_ids = detect_stage_splits(matches)
        assert split_ids == set(), (
            "只有 3 轮且轮次内配对不断轮换的赛季不应被单轮不连通误判为分裂"
        )

    def test_real_data_zero_false_positive_and_matches_known_cases(self, tmp_path):
        """用只读快照跑全部 39 个真实 league-season,断言:
        (a) 五大联赛 30 个 league-season 全部 0 误报;
        (b) K1 2024/2025 恰好识别出 34-38 轮(各 30 场);
        (c) J1 2026 恰好识别出全部 180 场(整季分区);
        (d) K1 2026(尚未打到第 34 轮)、澳超三季均为 0。
        跳过条件:只读快照不存在时跳过(不阻塞不依赖真实数据的单元测试)。"""
        import os

        snap = "/tmp/mlmodel-jka/allwin.db"
        if not os.path.exists(snap):
            pytest.skip("real read-only snapshot not present in this environment")
        rconn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        rconn.row_factory = sqlite3.Row
        matches, _ = load_matches(rconn, leagues=ALL_EIGHT_LEAGUES)
        split_ids = detect_stage_splits(matches)

        by_ls = {}
        for m in matches:
            by_ls.setdefault((m["league_id"], m["season"]), set()).add(m["match_id"])

        for lid in FIVE_LEAGUES:
            for (l2, season), ids in by_ls.items():
                if l2 == lid:
                    assert not (ids & split_ids), f"五大联赛 {lid}/{season} 出现误报"

        k1_2024 = by_ls.get((9080, "2024"), set())
        k1_2025 = by_ls.get((9080, "2025"), set())
        assert len(k1_2024 & split_ids) == 30
        assert len(k1_2025 & split_ids) == 30

        j1_2026 = by_ls.get((223, "2026"), set())
        # 2026 赛季 200 场里 20 场是非数字排位赛轮次(已被 is_playoff 处理,
        # 不进入 detect_stage_splits 的候选集合),故只有 180 场参与判定
        assert len(j1_2026 & split_ids) == 180

        assert len(by_ls.get((9080, "2026"), set()) & split_ids) == 0
        for season in ("2023/2024", "2024/2025", "2025/2026"):
            assert len(by_ls.get((113, season), set()) & split_ids) == 0
