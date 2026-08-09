"""kbisai_match_resolution.py 回归(本轮任务 §8b)。

真实数据锚点:kbisai matchId 4467576 ↔ FotMob Match_ID 5104970(挪超,
Sandefjord vs KFUM,2026-08-07T17:00:00Z)是本轮通过真实网络请求交叉验证过的
一对——kickoff 完全一致(diff=0),用作"单候选、无别名数据"路径的真实回归锚点。
"""

import sqlite3

import pytest

from backend.db.connections import connect_rw
from backend.ingest.kbisai_match_resolution import (
    HIGH_CONFIDENCE_KICKOFF_SECONDS,
    STATUS_AMBIGUOUS,
    STATUS_KICKOFF_OUT_OF_TOLERANCE,
    STATUS_MATCHED,
    STATUS_NO_CANDIDATE,
    FotMobMatchRef,
    KbisaiCandidate,
    resolve_kbisai_match,
    write_kbisai_xref,
)

# 真实验证过的挪超样例(本轮会话通过真实网络请求确认)。
REAL_FOTMOB = FotMobMatchRef(
    fotmob_match_id=5104970,
    kickoff_at_utc="2026-08-07T17:00:00Z",
    home_team_id=8007,
    away_team_id=8478,
)
REAL_KBISAI = KbisaiCandidate(
    provider_match_id="4467576",
    kickoff_at_utc="2026-08-07T17:00:00.000000Z",
    home_team_name="桑德菲杰",
    away_team_name="KFUM奥斯陆",
)


class TestNoAliasData:
    def test_real_single_candidate_no_alias_data_matches_needs_review(self):
        """真实挪超锚点:kickoff diff=0,没有别名数据(挪超 0/16 覆盖)——
        必须匹配成功,但 review_status 只能是 needs_review,不能是 auto_ok。"""
        result = resolve_kbisai_match(REAL_FOTMOB, [REAL_KBISAI])
        assert result.status == STATUS_MATCHED
        assert result.matched
        assert result.provider_match_id == "4467576"
        assert result.kickoff_diff_seconds == 0
        assert result.review_status == "needs_review"
        assert result.orientation_verified is False
        assert result.home_away_inverted == 0
        assert result.method == "auto"

    def test_no_candidates_within_tolerance(self):
        far = KbisaiCandidate(
            provider_match_id="999999",
            kickoff_at_utc="2026-08-09T17:00:00Z",   # 2 天后,远超容差
            home_team_name="桑德菲杰",
            away_team_name="KFUM奥斯陆",
        )
        result = resolve_kbisai_match(REAL_FOTMOB, [far])
        assert result.status == STATUS_NO_CANDIDATE
        assert not result.matched
        assert result.provider_match_id is None

    def test_single_candidate_diff_beyond_high_confidence_window_without_alias_fails_closed(self):
        near_but_not_tight = KbisaiCandidate(
            provider_match_id="4467576",
            kickoff_at_utc="2026-08-07T17:20:00Z",  # 20 分钟差,在 1800s 容差内但超过 60s 高置信窗口
            home_team_name="桑德菲杰",
            away_team_name="KFUM奥斯陆",
        )
        result = resolve_kbisai_match(REAL_FOTMOB, [near_but_not_tight])
        assert result.status == STATUS_KICKOFF_OUT_OF_TOLERANCE
        assert not result.matched

    def test_multiple_candidates_without_alias_data_fails_closed(self):
        c1 = KbisaiCandidate("111", "2026-08-07T17:00:00Z", "A", "B")
        c2 = KbisaiCandidate("222", "2026-08-07T17:00:30Z", "C", "D")
        result = resolve_kbisai_match(REAL_FOTMOB, [c1, c2])
        assert result.status == STATUS_AMBIGUOUS
        assert not result.matched


class TestWithAliasData:
    HOME_ALIASES = frozenset({"阿森纳", "阿仙奴"})
    AWAY_ALIASES = frozenset({"考文垂", "高云地利"})

    def test_direct_orientation_confirmed_gives_auto_ok(self):
        candidate = KbisaiCandidate("777", "2026-08-21T19:00:00Z", "阿森纳", "考文垂")
        fotmob = FotMobMatchRef(5795363, "2026-08-21T19:00:00Z", 9825, 8669)
        result = resolve_kbisai_match(
            fotmob, [candidate],
            fotmob_home_aliases=self.HOME_ALIASES, fotmob_away_aliases=self.AWAY_ALIASES,
        )
        assert result.status == STATUS_MATCHED
        assert result.review_status == "auto_ok"
        assert result.orientation_verified is True
        assert result.home_away_inverted == 0

    def test_inverted_orientation_detected_and_flagged(self):
        candidate = KbisaiCandidate("777", "2026-08-21T19:00:00Z", "考文垂", "阿森纳")
        fotmob = FotMobMatchRef(5795363, "2026-08-21T19:00:00Z", 9825, 8669)
        result = resolve_kbisai_match(
            fotmob, [candidate],
            fotmob_home_aliases=self.HOME_ALIASES, fotmob_away_aliases=self.AWAY_ALIASES,
        )
        assert result.status == STATUS_MATCHED
        assert result.review_status == "auto_ok"
        assert result.home_away_inverted == 1

    def test_names_disagree_in_both_directions_fails_closed(self):
        candidate = KbisaiCandidate("777", "2026-08-21T19:00:00Z", "曼联", "利物浦")
        fotmob = FotMobMatchRef(5795363, "2026-08-21T19:00:00Z", 9825, 8669)
        result = resolve_kbisai_match(
            fotmob, [candidate],
            fotmob_home_aliases=self.HOME_ALIASES, fotmob_away_aliases=self.AWAY_ALIASES,
        )
        assert result.status == STATUS_AMBIGUOUS
        assert not result.matched

    def test_multiple_candidates_disambiguated_by_name_uniquely(self):
        """英超同一轮 3 场比赛同时 14:00Z 开球的真实场景(2026-08-22)——
        kickoff 无法唯一定位,但队名可以。"""
        wrong1 = KbisaiCandidate("501", "2026-08-22T14:00:00Z", "埃弗顿", "水晶宫")
        wrong2 = KbisaiCandidate("502", "2026-08-22T14:00:00Z", "伊普斯维奇", "桑德兰")
        correct = KbisaiCandidate("503", "2026-08-22T14:00:00Z", "阿森纳", "考文垂")
        fotmob = FotMobMatchRef(5795363, "2026-08-22T14:00:00Z", 9825, 8669)
        result = resolve_kbisai_match(
            fotmob, [wrong1, wrong2, correct],
            fotmob_home_aliases=self.HOME_ALIASES, fotmob_away_aliases=self.AWAY_ALIASES,
        )
        assert result.status == STATUS_MATCHED
        assert result.provider_match_id == "503"
        assert result.review_status == "auto_ok"

    def test_multiple_candidates_that_all_match_by_name_fails_closed(self):
        """两个候选都能通过队名对上(理论边界情况:数据重复/脏数据)——不能随便选一个。"""
        dup1 = KbisaiCandidate("601", "2026-08-22T14:00:00Z", "阿森纳", "考文垂")
        dup2 = KbisaiCandidate("602", "2026-08-22T14:05:00Z", "阿森纳", "考文垂")
        fotmob = FotMobMatchRef(5795363, "2026-08-22T14:00:00Z", 9825, 8669)
        result = resolve_kbisai_match(
            fotmob, [dup1, dup2],
            fotmob_home_aliases=self.HOME_ALIASES, fotmob_away_aliases=self.AWAY_ALIASES,
        )
        assert result.status == STATUS_AMBIGUOUS
        assert not result.matched

    def test_multiple_candidates_none_match_by_name_fails_closed(self):
        c1 = KbisaiCandidate("701", "2026-08-22T14:00:00Z", "曼城", "利物浦")
        c2 = KbisaiCandidate("702", "2026-08-22T14:03:00Z", "热刺", "切尔西")
        fotmob = FotMobMatchRef(5795363, "2026-08-22T14:00:00Z", 9825, 8669)
        result = resolve_kbisai_match(
            fotmob, [c1, c2],
            fotmob_home_aliases=self.HOME_ALIASES, fotmob_away_aliases=self.AWAY_ALIASES,
        )
        assert result.status == STATUS_AMBIGUOUS
        assert not result.matched


class TestWriteKbisaiXref:
    def test_raises_on_unmatched_result(self):
        unmatched = resolve_kbisai_match(REAL_FOTMOB, [])
        conn = None
        with pytest.raises(ValueError):
            write_kbisai_xref(conn, unmatched, now_iso="2026-08-04T00:00:00Z")

    def test_insert_then_idempotent_rerun_is_unchanged(self, data_dir):
        result = resolve_kbisai_match(REAL_FOTMOB, [REAL_KBISAI])
        conn = connect_rw("odds")
        try:
            outcome1 = write_kbisai_xref(conn, result, now_iso="2026-08-04T00:00:00Z")
            conn.commit()
            assert outcome1 == "inserted"

            row = conn.execute(
                "SELECT * FROM dim_match_xref WHERE provider='kbisai' AND fotmob_match_id=?",
                (REAL_FOTMOB.fotmob_match_id,),
            ).fetchone()
            assert row["provider_match_id"] == "4467576"
            assert row["verified"] == 0
            assert row["method"] == "auto"
            assert row["review_status"] == "needs_review"

            # 幂等重跑:同样的结果再写一次,不应该报错、不应该产生第二行。
            outcome2 = write_kbisai_xref(conn, result, now_iso="2026-08-04T01:00:00Z")
            conn.commit()
            assert outcome2 == "unchanged"
            count = conn.execute(
                "SELECT COUNT(*) FROM dim_match_xref WHERE provider='kbisai' AND fotmob_match_id=?",
                (REAL_FOTMOB.fotmob_match_id,),
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_genuine_conflict_is_not_silently_swallowed(self, data_dir):
        """同一场 FotMob 比赛,第二次解析出了不同的 provider_match_id(比如来源
        数据变了)——这是需要人工介入的真实冲突,不能被当成"重复调用"静默吞掉。"""
        result1 = resolve_kbisai_match(REAL_FOTMOB, [REAL_KBISAI])
        conflicting_candidate = KbisaiCandidate(
            provider_match_id="9999999",
            kickoff_at_utc=REAL_KBISAI.kickoff_at_utc,
            home_team_name=REAL_KBISAI.home_team_name,
            away_team_name=REAL_KBISAI.away_team_name,
        )
        result2 = resolve_kbisai_match(REAL_FOTMOB, [conflicting_candidate])

        conn = connect_rw("odds")
        try:
            write_kbisai_xref(conn, result1, now_iso="2026-08-04T00:00:00Z")
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                write_kbisai_xref(conn, result2, now_iso="2026-08-04T01:00:00Z")
        finally:
            conn.close()

    def test_coexists_with_nowgoal_xref_for_same_fotmob_match(self, data_dir):
        """dim_match_xref 的 UNIQUE 是 (provider, fotmob_match_id),同一场比赛
        可以同时有 nowgoal 与 kbisai 两条映射——这是 §5 修复的前提,这里验证
        write_kbisai_xref 不会跟已有的 nowgoal 行冲突。"""
        conn = connect_rw("odds")
        try:
            conn.execute(
                """INSERT INTO dim_match_xref
                   (fotmob_match_id, provider, provider_match_id, confidence, verified,
                    method, review_status, created_at, updated_at)
                   VALUES (?, 'nowgoal', '2912857', 1.0, 1, 'manual', 'confirmed',
                           '2026-07-29T00:00:00Z', '2026-07-29T00:00:00Z')""",
                (REAL_FOTMOB.fotmob_match_id,),
            )
            conn.commit()

            result = resolve_kbisai_match(REAL_FOTMOB, [REAL_KBISAI])
            outcome = write_kbisai_xref(conn, result, now_iso="2026-08-04T00:00:00Z")
            conn.commit()
            assert outcome == "inserted"

            rows = conn.execute(
                "SELECT provider, provider_match_id FROM dim_match_xref WHERE fotmob_match_id=? ORDER BY provider",
                (REAL_FOTMOB.fotmob_match_id,),
            ).fetchall()
            assert {(r["provider"], r["provider_match_id"]) for r in rows} == {
                ("kbisai", "4467576"), ("nowgoal", "2912857"),
            }
        finally:
            conn.close()
