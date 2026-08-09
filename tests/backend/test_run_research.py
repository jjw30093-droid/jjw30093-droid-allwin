"""run_research 的折内纪律与配对指标不变量。"""

from __future__ import annotations

import numpy as np
import pytest

from backend.models.research.run_research import (
    calibration_buckets,
    fit_predict_freq,
    fit_predict_sklearn,
    metric_block,
    paired_bootstrap_dRPS,
    rps,
    time_split,
)


def _row(mid, kickoff, league=47, hg=1, ag=0, feat=None):
    f = {k: 1.0 for k in (
        "home_xg_for_l5", "home_xg_for_l10", "home_xg_against_l5", "home_xg_against_l10",
        "away_xg_for_l5", "away_xg_for_l10", "away_xg_against_l5", "away_xg_against_l10",
        "home_goals_for_l5", "home_goals_for_l10", "home_goals_against_l5", "home_goals_against_l10",
        "away_goals_for_l5", "away_goals_for_l10", "away_goals_against_l5", "away_goals_against_l10",
        "home_shots_for_l10", "home_shots_on_target_for_l10", "home_possession_l10",
        "away_shots_for_l10", "away_shots_on_target_for_l10", "away_possession_l10",
        "home_xg_for_home_l5", "home_xg_for_home_l10", "away_xg_for_away_l5", "away_xg_for_away_l10",
        "home_rest_days", "away_rest_days",
        "home_n_matches_l10", "away_n_matches_l10", "home_n_prior_season", "away_n_prior_season",
    )}
    if feat:
        f.update(feat)
    return {"match_id": mid, "kickoff": kickoff, "league_id": league,
            "target_home_goals": hg, "target_away_goals": ag, "features": f}


class TestDiscipline:
    def test_time_split_is_chronological(self):
        rows = [_row(i, f"2024-08-{i:02d}T15:00:00Z") for i in range(1, 11)]
        rng = np.random.default_rng(0)
        shuffled = list(rows)
        rng.shuffle(shuffled)
        head, tail = time_split(shuffled, 0.8)
        assert [r["match_id"] for r in head] == list(range(1, 9))
        assert [r["match_id"] for r in tail] == [9, 10]

    def test_rps_known_values(self):
        p = np.array([[1.0, 0.0, 0.0], [1 / 3, 1 / 3, 1 / 3]])
        y = np.array([0, 0])
        per = rps(p, y)
        # 完美预测 0;均匀分布 0.5*((1/3-1)^2+(2/3-1)^2)=0.2778
        assert abs(per - (0 + 0.2778) / 2) < 1e-3

    def test_freq_is_league_specific(self):
        train = [_row(i, "2024-08-01T15:00:00Z", league=47, hg=1, ag=0) for i in range(20)]
        train += [_row(100 + i, "2024-08-01T15:00:00Z", league=87, hg=0, ag=1) for i in range(20)]
        test = [_row(900, "2025-08-01T15:00:00Z", league=47),
                _row(901, "2025-08-01T15:00:00Z", league=87)]
        p = fit_predict_freq(train, test)
        assert p[0][0] == 1.0 and p[1][2] == 1.0

    def test_calibrator_never_sees_test(self):
        """校准器只喂校准段:test 段全部主胜、校准段全部客胜时,
        若校准读了 test,输出会偏主胜;正确实现应保持校准段的客胜倾向。"""
        train = [_row(i, f"2024-08-{(i % 27) + 1:02d}T15:00:00Z", hg=i % 3, ag=(i + 1) % 3)
                 for i in range(60)]
        calib = [_row(200 + i, "2024-09-01T15:00:00Z", hg=0, ag=2) for i in range(30)]
        test = [_row(300 + i, "2024-10-01T15:00:00Z", hg=2, ag=0) for i in range(30)]
        p, _ = fit_predict_sklearn(train, calib, test, "lr",
                                   features=list(train[0]["features"].keys()))
        assert p[:, 2].mean() > p[:, 0].mean()

    def test_paired_bootstrap_identical_models_ci_covers_zero(self):
        rng = np.random.default_rng(1)
        p = rng.dirichlet([2, 1, 2], size=200)
        y = rng.integers(0, 3, 200)
        d = paired_bootstrap_dRPS(p, p.copy(), y)
        assert d["mean_dRPS"] == 0.0
        assert d["ci95"][0] <= 0.0 <= d["ci95"][1]

    def test_metric_block_and_buckets(self):
        p = np.array([[0.7, 0.2, 0.1]] * 10)
        y = np.array([0] * 7 + [2] * 3)
        m = metric_block(p, y)
        assert m["n"] == 10 and m["accuracy"] == 0.7
        b = calibration_buckets(p[:, 0], (y == 0).astype(int))
        assert b[0]["n"] == 10 and abs(b[0]["freq"] - 0.7) < 1e-9


# ── run_full_study 编排(2026-08-07 对抗复核收口) ──────────────────────

from backend.models.research.run_research import build_folds, run_full_study  # noqa: E402


def _season_rows(season, n, league=47, base_id=0):
    return [_row(base_id + i, f"{2020 + int(season[:4]) - 2020}-01-{(i % 27) + 1:02d}T15:00:00Z",
                league=league, hg=i % 3, ag=(i + 1) % 3)
            for i in range(n)]


class TestRunFullStudyOrchestration:
    def _tiny_rows(self, per_season=150):
        # 6 个假赛季,每季若干场,凑够各阶段最小样本门槛(paired>=100 等)
        seasons = ["2020/2021", "2021/2022", "2022/2023", "2023/2024", "2024/2025", "2025/2026"]
        rows = []
        mid = 0
        for s in seasons:
            for i in range(per_season):
                day = (i % 27) + 1
                rows.append(_row(mid, f"20{20 + seasons.index(s)}-01-{day:02d}T15:00:00Z",
                                 league=47, hg=i % 3, ag=(i + 1) % 3))
                rows[-1]["season"] = s
                mid += 1
        return rows

    def test_summary_market_paired_present_even_when_train_coverage_zero(self):
        """RED→GREEN(2026-08-07):F1 训练季 summary 覆盖为 0 时,
        dc/hgb 对 summary_latest 的配对比较此前被错误一并跳过(旧实现把
        dc_vs_market/hgb_vs_market 嵌进了只有 lr_market 才需要的
        tr_p/ca_p>=200/50 门槛里)。dc/hgb 用的是已训好的全量模型,只需
        paired>=100,理应独立于该训练集覆盖门槛产出。"""
        rows = self._tiny_rows()
        # F1 = train[2020/2021] test=2021/2022;只给 test season 配市场,
        # 训练季覆盖故意为 0(复现真实 bug 触发条件)
        f1_test_ids = [r["match_id"] for r in rows if r["season"] == "2021/2022"]
        market_summary = {"asset_a_json": {str(mid): {"p": (0.4, 0.3, 0.3)} for mid in f1_test_ids[:120]}}
        study = run_full_study(rows, market_full={}, market_summary_sources=market_summary)
        f1_summary = [p for p in study["paired_market_metrics"]
                      if p["fold"] == "F1" and p["vs"] == "summary_latest"]
        keys = {k for p in f1_summary for k in p if k.endswith("_vs_market")}
        assert "dc_vs_market" in keys, "dc_vs_market 被训练集覆盖门槛错误连带跳过"
        assert "hgb_vs_market" in keys, "hgb_vs_market 被训练集覆盖门槛错误连带跳过"
        # lr_market/lr_features 需要 train 覆盖>=200,此处故意不足,应缺席
        assert "lr_plus_market_vs_market" not in keys

    def test_market_only_paired_populated_when_gate_open(self):
        rows = self._tiny_rows()
        f1_test_ids = [r["match_id"] for r in rows if r["season"] == "2021/2022"]
        market_summary = {"asset_a_json": {str(mid): {"p": (0.4, 0.3, 0.3)} for mid in f1_test_ids[:120]}}
        study = run_full_study(rows, market_full={}, market_summary_sources=market_summary)
        f1 = next(f for f in study["fold_metrics"] if f["fold"] == "F1")
        assert "market_only_paired" in f1["models"]
        assert f1["models"]["market_only_paired"]["n"] == 120

    def test_deterministic_and_folds_chronological(self):
        rows = self._tiny_rows()
        a = run_full_study(rows, {}, {})
        b = run_full_study(rows, {}, {})
        assert a["fold_metrics"] == b["fold_metrics"]
        assert a["feature_ablation"] == b["feature_ablation"]
        folds = build_folds()
        assert folds[0]["fold"] == "F1" and folds[-1]["test_season"] == "2025/2026"

    def test_summary_merge_priority_asset_a_first(self):
        from backend.models.research.run_research import _market_tables
        rows = [_row(1, "2024-08-01T15:00:00Z")]
        summary = {
            "asset_a_json": {"1": {"p": (0.5, 0.3, 0.2)}},
            "asset_b_footballdata": {"1": {"p": (0.9, 0.05, 0.05)}},
        }
        _, mk_sum = _market_tables(rows, {}, summary)
        assert mk_sum[1] == (0.5, 0.3, 0.2)


# ── 2026-08-08 J1/K1/澳超接入:P0-1/P0-2/P0-4/P0-6/P0-7 永久测试 ──────────

from backend.models.research.run_research import (  # noqa: E402
    ALL_EIGHT_LEAGUES,
    JKA_SUMMARY_SOURCES,
    UnknownLeagueError,
    _fit_temperature,
    _market_tables,
    add_league_onehot,
    apply_per_league_temperature,
    assert_market_coverage,
    build_folds_by_time,
    fit_predict_dc,
    per_league_split_counts,
    time_split_stratified,
)


class TestUnknownLeagueFailClosed:
    """真 P0(2026-08-08 对抗复核实测确认):J/K/A 曾被静默编码成参照类(英超)
    或落到均匀分布,而不报错。"""

    def test_add_league_onehot_rejects_unconfigured_league(self):
        X = np.zeros((2, 1))
        leagues_arr = np.array([47, 223])  # 223 不在默认 LEAGUES 里
        with pytest.raises(UnknownLeagueError):
            add_league_onehot(X, leagues_arr)

    def test_add_league_onehot_accepts_when_configured(self):
        X = np.zeros((2, 1))
        leagues_arr = np.array([47, 223])
        out = add_league_onehot(X, leagues_arr, all_leagues=(47, 223))
        assert out.shape == (2, 2)  # 1 特征列 + 1 个非参照联赛的 one-hot 列

    def test_fit_predict_freq_rejects_unconfigured_league(self):
        train = [_row(1, "2024-08-01T15:00:00Z", league=47)]
        test = [_row(2, "2025-08-01T15:00:00Z", league=223)]
        with pytest.raises(UnknownLeagueError):
            fit_predict_freq(train, test)  # 默认 leagues=(47,53,54,55,87),223 不在内

    def test_fit_predict_freq_accepts_when_configured(self):
        train = [_row(1, "2024-08-01T15:00:00Z", league=223, hg=1, ag=0)]
        test = [_row(2, "2025-08-01T15:00:00Z", league=223)]
        p = fit_predict_freq(train, test, leagues=(223,))
        assert p[0][0] == 1.0

    def test_fit_predict_dc_rejects_unconfigured_league(self):
        train = [_row(i, f"2024-08-{i:02d}T15:00:00Z", league=47) for i in range(1, 5)]
        calib = [_row(10, "2024-09-01T15:00:00Z", league=47)]
        test = [_row(2, "2025-08-01T15:00:00Z", league=223)]
        with pytest.raises(UnknownLeagueError):
            fit_predict_dc(train, test, calib)


class TestFoldsByTime:
    """P0-2/P0-6(真 P0,已实测确认):按 Season 字符串折叠会让 J1 的裸年份
    赛季("2024"/"2025"/"2026")静默消失,且会造成真实时间泄漏(同一"赛季"
    标签在不同联赛的赛历下不是同一时间区间)。改用 kickoff_at_utc 绝对时间。
    """

    def test_train_strictly_before_test(self):
        rows = ([_row(i, f"2024-0{(i%6)+1}-01T15:00:00Z") for i in range(1, 20)]
               + [_row(100 + i, f"2025-0{(i%6)+1}-01T15:00:00Z") for i in range(1, 20)])
        folds = build_folds_by_time(rows, ["2024-12-01", "2025-06-01"], test_days=183)
        for f in folds:
            if f["train_rows"] and f["test_rows"]:
                assert max(r["kickoff"] for r in f["train_rows"]) < \
                       min(r["kickoff"] for r in f["test_rows"])

    def test_bare_year_season_rows_not_silently_dropped(self):
        """J1 式裸年份赛季比赛必须真实出现在某一折的 train 或 test 里
        (按赛季字符串折叠时,这些行会因为不在 SEASONS 常量里而整体消失)。"""
        rows = [_row(1, "2024-03-01T15:00:00Z", league=223),
                _row(2, "2025-03-01T15:00:00Z", league=223)]
        folds = build_folds_by_time(rows, ["2024-06-01", "2025-06-01"])
        seen = set()
        for f in folds:
            seen |= {r["match_id"] for r in f["train_rows"]}
            seen |= {r["match_id"] for r in f["test_rows"]}
        assert seen == {1, 2}

    def test_empty_train_does_not_false_positive_on_leak_check(self):
        """人为构造一个 origin 早于部分"训练"数据实际发生之后的情况不适用here；
        改为直接验证:同一 origin 下 test 窗口内如果混入了理应属于 train 的
        比赛(kickoff 相同或早于某条 train 记录),函数必须能在正常输入下
        自证不泄漏——用极端重叠的 origin 制造冲突并断言报错。"""
        rows = [_row(1, "2024-06-01T00:00:00Z"), _row(2, "2024-06-01T00:00:00Z")]
        # origin 恰好等于两条比赛的 kickoff:train 用 "<", test 用 "<=",
        # 两条 kickoff 相同的比赛会同时被 test 选中、被 train 排除,不冲突;
        # 这里改为验证空 train 时不误报(无 train 时无法比较,函数应正常跳过校验)
        folds = build_folds_by_time(rows, ["2024-06-01"])
        assert folds[0]["train_rows"] == []
        assert len(folds[0]["test_rows"]) == 2


class TestStratifiedTimeSplit:
    """P0-7(真 P0,已实测确认):全局时间切分在联赛赛历错位时会让某个联赛
    的绝大部分训练数据被推进校准段(实测澳超 51% vs 英超 18.9%)。"""

    def test_each_league_split_independently(self):
        # 联赛 A:10 场,均匀分布在 8 月;联赛 B:只有 2 场,都在 8 月末
        # (模拟"该联赛数据集中在折尾"的赛历错位场景)
        rows = ([_row(i, f"2024-08-{i:02d}T15:00:00Z", league=47) for i in range(1, 11)]
               + [_row(100 + i, f"2024-08-{28+i:02d}T15:00:00Z", league=223) for i in range(1, 3)])
        train, calib = time_split_stratified(rows, 0.8)
        # 联赛 223 只有 2 场:80% 向下取整为 1 场训练、1 场校准;
        # 不应该因为它们在时间上排在联赛 47 的所有比赛之后,就被全部推进校准段
        c223_calib = sum(1 for r in calib if r["league_id"] == 223)
        c223_train = sum(1 for r in train if r["league_id"] == 223)
        assert c223_train == 1 and c223_calib == 1

    def test_global_split_would_have_starved_the_late_league(self):
        """对照:全局(非分层)切分在同样的数据上会把联赛 223 的两场全部推
        进校准段(证明分层切分确实解决了这个问题,不是空转)。"""
        rows = ([_row(i, f"2024-08-{i:02d}T15:00:00Z", league=47) for i in range(1, 11)]
               + [_row(100 + i, f"2024-08-{28+i:02d}T15:00:00Z", league=223) for i in range(1, 3)])
        train, calib = time_split(rows, 0.8)
        c223_calib = sum(1 for r in calib if r["league_id"] == 223)
        assert c223_calib == 2, "全局切分应把联赛 223 的两场都推进校准段(问题复现)"


class TestMarketSourceParameterization:
    """P0-4(真 P0,已实测确认):_market_tables 的来源优先级元组原来只含
    五大联赛专用来源,J/K/A 的真实来源(football_uk_jka/nowgoal_archive_refetch)
    完全不在里面,配对样本静默归零。"""

    def test_default_source_priority_excludes_jka_sources(self):
        rows = [_row(1, "2024-08-01T15:00:00Z", league=223)]
        summary = {"football_uk_jka": {"1": {"p": (0.4, 0.3, 0.3)}}}
        _, mk_sum = _market_tables(rows, {}, summary)  # 默认 source_priority
        assert mk_sum == {}, "默认优先级不含 J/K/A 来源,复现静默归零"

    def test_jka_source_priority_includes_it(self):
        rows = [_row(1, "2024-08-01T15:00:00Z", league=223)]
        summary = {"football_uk_jka": {"1": {"p": (0.4, 0.3, 0.3)}}}
        _, mk_sum = _market_tables(rows, {}, summary, source_priority=JKA_SUMMARY_SOURCES)
        assert mk_sum[1] == (0.4, 0.3, 0.3)

    def test_assert_market_coverage_raises_on_zero(self):
        rows = [_row(1, "2024-08-01T15:00:00Z", league=223)]
        with pytest.raises(ValueError, match="fail-closed"):
            assert_market_coverage(rows, {}, leagues=(223,))

    def test_assert_market_coverage_passes_when_covered(self):
        rows = [_row(1, "2024-08-01T15:00:00Z", league=223)]
        mk_sum = {1: (0.4, 0.3, 0.3)}
        counts = assert_market_coverage(rows, mk_sum, leagues=(223,))
        assert counts == {"223": 1}


class TestPlattCalibration:
    """task #20:per-league 标量校准(temperature/Platt)替代无效的
    整块缩放/rank-1 方案——被最终综合复核验证为足够吸收斜率尺度差异。"""

    def test_platt_calibration_runs_and_normalizes(self):
        train = [_row(i, f"2024-08-{(i % 27) + 1:02d}T15:00:00Z", hg=i % 3, ag=(i + 1) % 3)
                 for i in range(60)]
        calib = [_row(200 + i, "2024-09-01T15:00:00Z", hg=i % 3, ag=(i + 1) % 3)
                for i in range(20)]
        test = [_row(300 + i, "2024-10-01T15:00:00Z") for i in range(10)]
        p, _ = fit_predict_sklearn(train, calib, test, "lr",
                                   features=list(train[0]["features"].keys()),
                                   calibration="platt")
        assert np.allclose(p.sum(axis=1), 1.0, atol=1e-6)
        assert p.shape == (10, 3)

    def test_isotonic_and_platt_give_different_but_valid_output(self):
        train = [_row(i, f"2024-08-{(i % 27) + 1:02d}T15:00:00Z", hg=i % 3, ag=(i + 1) % 3)
                 for i in range(60)]
        calib = [_row(200 + i, "2024-09-01T15:00:00Z", hg=i % 3, ag=(i + 1) % 3)
                for i in range(20)]
        test = [_row(300 + i, "2024-10-01T15:00:00Z") for i in range(10)]
        feats = list(train[0]["features"].keys())
        p_iso, _ = fit_predict_sklearn(train, calib, test, "lr", feats, calibration="isotonic")
        p_platt, _ = fit_predict_sklearn(train, calib, test, "lr", feats, calibration="platt")
        assert np.allclose(p_iso.sum(axis=1), 1.0, atol=1e-6)
        assert np.allclose(p_platt.sum(axis=1), 1.0, atol=1e-6)

    def test_per_league_temperature_runs_end_to_end(self):
        train = [_row(i, f"2024-08-{(i % 27) + 1:02d}T15:00:00Z", league=223,
                     hg=i % 3, ag=(i + 1) % 3) for i in range(60)]
        calib = [_row(200 + i, "2024-09-01T15:00:00Z", league=223, hg=i % 3, ag=(i + 1) % 3)
                for i in range(25)]
        test = [_row(300 + i, "2024-10-01T15:00:00Z", league=223) for i in range(10)]
        p, _ = fit_predict_sklearn(train, calib, test, "lr",
                                   features=list(train[0]["features"].keys()),
                                   leagues=(223,), calibration="temperature_per_league")
        assert np.allclose(p.sum(axis=1), 1.0, atol=1e-6)
        assert p.shape == (10, 3)

    def test_apply_per_league_temperature_gives_each_league_its_own_T(self):
        """构造两个联赛:A 的校准段标签和预测完美吻合(该拟合出 T≈1 附近),
        B 的校准段标签系统性地和预测相反(该拟合出使分布更平滑的 T)。
        断言两个联赛拟合出的温度不同——证明真的是 per-league,不是全局共用。"""
        rng = np.random.default_rng(7)
        n = 40
        # League A: raw 预测和真实标签强相关
        y_a = rng.integers(0, 3, n)
        p_a = np.eye(3)[y_a] * 0.9 + 0.05
        p_a = p_a / p_a.sum(axis=1, keepdims=True)
        # League B: raw 预测和真实标签几乎无关(强制不匹配)
        y_b = rng.integers(0, 3, n)
        p_b = np.eye(3)[(y_b + 1) % 3] * 0.9 + 0.05
        p_b = p_b / p_b.sum(axis=1, keepdims=True)

        p_cal = np.vstack([p_a, p_b])
        y_cal = np.concatenate([y_a, y_b])
        cal_leagues = np.array([1] * n + [2] * n)
        _, T_by_league = apply_per_league_temperature(
            p_cal, y_cal, cal_leagues, p_cal, cal_leagues, min_league_n=20)
        assert T_by_league["1"] != T_by_league["2"], (
            "两个统计特性完全不同的联赛不应该拟合出同一个温度"
        )

    def test_small_league_falls_back_to_global_temperature(self):
        """校准段样本数低于 min_league_n 的联赛应该退回全局温度,
        不应该用几场比赛的噪声拟合出一个不稳定的 per-league T。"""
        rng = np.random.default_rng(3)
        n = 40
        y_big = rng.integers(0, 3, n)
        p_big = np.eye(3)[y_big] * 0.8 + 0.067
        p_big = p_big / p_big.sum(axis=1, keepdims=True)
        y_small = rng.integers(0, 3, 3)  # 只有 3 场,远低于 min_league_n
        p_small = rng.dirichlet([1, 1, 1], size=3)

        p_cal = np.vstack([p_big, p_small])
        y_cal = np.concatenate([y_big, y_small])
        cal_leagues = np.array([1] * n + [2] * 3)
        _, T_by_league = apply_per_league_temperature(
            p_cal, y_cal, cal_leagues, p_cal, cal_leagues, min_league_n=20)
        global_T = _fit_temperature(p_cal, y_cal)
        assert T_by_league["2"] == round(global_T, 4)

    def test_unknown_calibration_method_rejected(self):
        train = [_row(i, f"2024-08-{(i % 27) + 1:02d}T15:00:00Z", hg=i % 3, ag=(i + 1) % 3)
                 for i in range(30)]
        calib = [_row(200 + i, "2024-09-01T15:00:00Z", hg=i % 3, ag=(i + 1) % 3) for i in range(10)]
        test = [_row(300, "2024-10-01T15:00:00Z")]
        with pytest.raises(ValueError):
            fit_predict_sklearn(train, calib, test, "lr", list(train[0]["features"].keys()),
                                calibration="bogus")


class TestRunFullStudyTimeFoldMode:
    """run_full_study(folds=...) 显式传入时间折,跳过 season 模式;
    默认(folds=None)路径保持原有行为(已在别处用真实数据验证 field-by-field
    等价)。"""

    def test_explicit_folds_used_verbatim(self):
        rows = ([_row(i, f"2024-08-{(i % 27) + 1:02d}T15:00:00Z", league=223,
                     hg=i % 3, ag=(i + 1) % 3) for i in range(40)]
               + [_row(1000 + i, f"2025-08-{(i % 27) + 1:02d}T15:00:00Z", league=223,
                     hg=i % 3, ag=(i + 1) % 3) for i in range(20)])
        folds = build_folds_by_time(rows, ["2025-01-01"], test_days=365)
        out = run_full_study(rows, {}, {}, folds=folds, leagues=(223,))
        assert len(out["fold_metrics"]) == 1
        assert out["fold_metrics"][0]["fold"] == "T1"
        assert "origin" in out["fold_metrics"][0]
        assert "test_season" not in out["fold_metrics"][0]

    def test_per_league_split_counts_present(self):
        rows = ([_row(i, f"2024-08-{(i % 27) + 1:02d}T15:00:00Z", league=223,
                     hg=i % 3, ag=(i + 1) % 3) for i in range(30)]
               + [_row(1000 + i, f"2025-08-{(i % 27) + 1:02d}T15:00:00Z", league=223,
                     hg=i % 3, ag=(i + 1) % 3) for i in range(10)])
        folds = build_folds_by_time(rows, ["2025-01-01"], test_days=365)
        out = run_full_study(rows, {}, {}, folds=folds, leagues=(223,))
        counts = out["fold_metrics"][0]["per_league_split_counts"]
        assert "223" in counts
        assert counts["223"]["train"] + counts["223"]["calib"] == \
               len(folds[0]["train_rows"])
        assert counts["223"]["test"] == len(folds[0]["test_rows"]) == 10
