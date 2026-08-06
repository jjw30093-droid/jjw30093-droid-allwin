"""nowgoal_historical_match_resolution 的纯函数单元测试:无 IO、无网络。"""

from __future__ import annotations

from backend.ingest import nowgoal_historical_match_resolution as m


def _match(match_id=1, league_id=47, date="2021-08-14", home_id=100, away_id=200,
           home_name="Arsenal", away_name="Brentford", home_score=0, away_score=2):
    return m.GapMatch(
        match_id=match_id, league_id=league_id, date=date,
        home_team_id=home_id, away_team_id=away_id,
        home_team_name=home_name, away_team_name=away_name,
        home_score=home_score, away_score=away_score,
    )


def _candidate(titan_id="t1", league_id=36, kickoff="2021-08-14 03:00", home_id=19, away_id=365,
                home_name="Arsenal", away_name="Brentford", home_score=0, away_score=2):
    return m.NowGoalCandidate(
        titan_id=titan_id, nowgoal_league_id=league_id, kickoff_local=kickoff,
        home_team_id=home_id, away_team_id=away_id,
        home_team_name=home_name, away_team_name=away_name,
        home_score=home_score, away_score=away_score,
    )


class TestKickoffPrecision:
    def test_sentinel_detected(self):
        assert m.kickoff_precision("2020-10-25 23:59") == "provider_unknown"

    def test_reported(self):
        assert m.kickoff_precision("2020-10-25 20:00") == "reported"

    def test_unknown(self):
        assert m.kickoff_precision(None) == "unknown"
        assert m.kickoff_precision("") == "unknown"


class TestDateWindow:
    def test_within_window(self):
        assert m.within_date_window("2021-08-14", "2021-08-15 03:00", 2) is True
        assert m.within_date_window("2021-08-14", "2021-08-16 03:00", 2) is True

    def test_outside_window(self):
        assert m.within_date_window("2021-08-14", "2021-08-17 03:00", 2) is False

    def test_unparseable_returns_none(self):
        assert m.within_date_window("2021-08-14", None, 2) is None
        assert m.within_date_window("garbage", "2021-08-14 03:00", 2) is None


class TestParseScore:
    def test_valid(self):
        assert m.parse_score("2-0") == (2, 0)
        assert m.parse_score(" 1 - 1 ") == (1, 1)

    def test_invalid(self):
        assert m.parse_score(None) is None
        assert m.parse_score("") is None
        assert m.parse_score("postponed") is None


class TestTeamDirectionFromScore:
    def test_direct(self):
        assert m.team_direction_from_score(2, 0, 2, 0) == m.DIRECT

    def test_inverted(self):
        assert m.team_direction_from_score(2, 0, 0, 2) == m.INVERTED

    def test_missing_score_returns_none(self):
        assert m.team_direction_from_score(None, 0, 2, 0) is None

    def test_symmetric_draw_is_ambiguous(self):
        assert m.team_direction_from_score(1, 1, 1, 1) is None

    def test_neither_direction_matches(self):
        assert m.team_direction_from_score(3, 1, 2, 0) is None


class TestBuildTeamIdDictionary:
    def test_learns_direct_pairs(self):
        pairs = [
            m.TrainingPair(19, 365, 0, 2, 100, 200, 0, 2) for _ in range(12)
        ]
        d = m.build_team_id_dictionary(pairs, min_votes=10, min_margin_ratio=5.0)
        assert d[19] == 100
        assert d[365] == 200

    def test_learns_inverted_pairs_by_rederiving_direction(self):
        # NowGoal 记录 home=19,away=365,比分 0-2;FotMob 侧同一场比赛主客颠倒
        # (home=200,away=100,比分 2-0)——方向必须从比分重推,不能信任何
        # is_swapped 字段(本模块的 TrainingPair 根本不携带这个字段)。
        pairs = [m.TrainingPair(19, 365, 0, 2, 200, 100, 2, 0) for _ in range(12)]
        d = m.build_team_id_dictionary(pairs, min_votes=10, min_margin_ratio=5.0)
        assert d[19] == 100
        assert d[365] == 200

    def test_below_min_votes_excluded(self):
        pairs = [m.TrainingPair(19, 365, 0, 2, 100, 200, 0, 2) for _ in range(5)]
        d = m.build_team_id_dictionary(pairs, min_votes=10, min_margin_ratio=5.0)
        assert 19 not in d

    def test_insufficient_margin_excluded(self):
        # 19 -> 100 拿到 10 票,但 19 -> 999 拿到 3 票,领先倍数 10/3≈3.3 < 5 → 不进词典
        pairs = [m.TrainingPair(19, 365, 0, 2, 100, 200, 0, 2) for _ in range(10)]
        pairs += [m.TrainingPair(19, 500, 0, 2, 999, 200, 0, 2) for _ in range(3)]
        d = m.build_team_id_dictionary(pairs, min_votes=10, min_margin_ratio=5.0)
        assert 19 not in d

    def test_symmetric_draws_contribute_no_votes(self):
        pairs = [m.TrainingPair(19, 365, 1, 1, 100, 200, 1, 1) for _ in range(20)]
        d = m.build_team_id_dictionary(pairs, min_votes=10, min_margin_ratio=5.0)
        assert d == {}


class TestNameSimilarity:
    def test_identical_after_normalization(self):
        assert m.name_similarity("Manchester United", "manchester united!") == 1.0

    def test_dissimilar(self):
        assert m.name_similarity("Arsenal", "Chelsea") < 0.5


class TestResolveGapMatch:
    TEAM_DICT = {19: 100, 365: 200}

    def test_auto_ok_via_team_id_dictionary(self):
        match = _match()
        cand = _candidate()
        r = m.resolve_gap_match(match, [cand], self.TEAM_DICT)
        assert r.status == m.STATUS_AUTO_OK
        assert r.titan_id == "t1"
        assert r.direction == m.DIRECT
        assert r.evidence_kind == "id"
        assert r.home_away_inverted == 0
        assert r.matched_titan_kickoff_local == "2021-08-14 03:00"

    def test_no_candidate_result_has_no_kickoff_local(self):
        match = _match(date="2021-08-14")
        cand = _candidate(kickoff="2021-08-20 03:00")  # 超出 ±2 天窗口
        r = m.resolve_gap_match(match, [cand], self.TEAM_DICT)
        assert r.status == m.STATUS_NO_CANDIDATE
        assert r.matched_titan_kickoff_local is None

    def test_no_candidate_when_league_mismatch(self):
        match = _match(league_id=47)
        cand = _candidate(league_id=99)  # 不是 FOTMOB_TO_NOWGOAL_LEAGUE[47]=36
        r = m.resolve_gap_match(match, [cand], self.TEAM_DICT)
        assert r.status == m.STATUS_NO_CANDIDATE

    def test_no_candidate_when_outside_date_window(self):
        match = _match(date="2021-08-14")
        cand = _candidate(kickoff="2021-08-20 03:00")
        r = m.resolve_gap_match(match, [cand], self.TEAM_DICT)
        assert r.status == m.STATUS_NO_CANDIDATE

    def test_no_candidate_when_score_mismatch(self):
        match = _match(home_score=0, away_score=2)
        cand = _candidate(home_score=1, away_score=1)
        r = m.resolve_gap_match(match, [cand], self.TEAM_DICT)
        assert r.status == m.STATUS_NO_CANDIDATE

    def test_ambiguous_when_two_survive(self):
        match = _match()
        cand1 = _candidate(titan_id="t1")
        cand2 = _candidate(titan_id="t2")
        r = m.resolve_gap_match(match, [cand1, cand2], self.TEAM_DICT)
        assert r.status == m.STATUS_AMBIGUOUS

    def test_inverted_direction_forced_to_needs_review(self):
        # 词典方向判定为 inverted(NowGoal home 对应 FotMob away),比分也吻合
        # 反转后的取向——唯一存活候选,但方向是 inverted,必须强制 needs_review。
        match = _match(home_id=200, away_id=100, home_score=2, away_score=0)
        cand = _candidate(home_id=19, away_id=365, home_score=0, away_score=2)
        r = m.resolve_gap_match(match, [cand], self.TEAM_DICT)
        assert r.status == m.STATUS_NEEDS_REVIEW
        assert r.direction == m.INVERTED
        assert r.home_away_inverted == 1
        assert r.matched_titan_kickoff_local == "2021-08-14 03:00"

    def test_name_fallback_exact_match_is_auto_ok(self):
        match = _match(home_id=None, away_id=None, home_name="Arsenal", away_name="Brentford")
        cand = _candidate(home_id=None, away_id=None, home_name="Arsenal", away_name="Brentford")
        r = m.resolve_gap_match(match, [cand], {})
        assert r.status == m.STATUS_AUTO_OK
        assert r.evidence_kind == "name"

    def test_name_fallback_fuzzy_match_needs_review(self):
        match = _match(home_id=None, away_id=None, home_name="Manchester United", away_name="Leeds United")
        cand = _candidate(home_id=None, away_id=None, home_name="Man United", away_name="Leeds Utd",
                           home_score=0, away_score=2)
        r = m.resolve_gap_match(match, [cand], {})
        assert r.status == m.STATUS_NEEDS_REVIEW
        assert r.evidence_kind == "name"

    def test_name_below_threshold_excluded(self):
        match = _match(home_id=None, away_id=None, home_name="Arsenal", away_name="Brentford")
        cand = _candidate(home_id=None, away_id=None, home_name="Totally Different FC", away_name="Nope United")
        r = m.resolve_gap_match(match, [cand], {})
        assert r.status == m.STATUS_NO_CANDIDATE

    def test_team_dict_conflicting_direction_excludes_candidate(self):
        # 两队都在词典里,但既不是 direct 也不是 inverted 组合——不该退化去试队名。
        match = _match(home_id=100, away_id=999)
        cand = _candidate(home_id=19, away_id=365)  # dict: 19->100, 365->200(!=999)
        r = m.resolve_gap_match(match, [cand], self.TEAM_DICT)
        assert r.status == m.STATUS_NO_CANDIDATE


class TestResolveBatch:
    TEAM_DICT = {19: 100, 365: 200, 30: 300, 40: 400}

    def test_no_conflict_passthrough(self):
        matches = [_match(match_id=1), _match(match_id=2, home_id=300, away_id=400, home_name="X", away_name="Y")]
        cands = {
            1: [_candidate(titan_id="t1")],
            2: [_candidate(titan_id="t2", home_id=30, away_id=40, home_name="X", away_name="Y")],
        }
        results = m.resolve_batch(matches, cands, self.TEAM_DICT)
        assert {r.match_id: r.status for r in results} == {1: m.STATUS_AUTO_OK, 2: m.STATUS_AUTO_OK}

    def test_titan_conflict_downgrades_both(self):
        matches = [_match(match_id=1), _match(match_id=2, home_id=300, away_id=400, home_name="X", away_name="Y")]
        cands = {
            1: [_candidate(titan_id="shared")],
            2: [_candidate(titan_id="shared", home_id=30, away_id=40, home_name="X", away_name="Y")],
        }
        results = m.resolve_batch(matches, cands, self.TEAM_DICT)
        statuses = {r.match_id: r.status for r in results}
        assert statuses == {1: m.STATUS_TITAN_CONFLICT, 2: m.STATUS_TITAN_CONFLICT}

    def test_no_candidate_results_do_not_trigger_conflict_check(self):
        matches = [_match(match_id=1, league_id=999)]
        results = m.resolve_batch(matches, {}, self.TEAM_DICT)
        assert results[0].status == m.STATUS_NO_CANDIDATE
