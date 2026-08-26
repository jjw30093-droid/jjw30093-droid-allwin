"""backend/cli/backfill_fixtures.py 测试(全离线,FotMobClient 用假对象替代)。

覆盖:dry-run 零写入、新建/更新拆分正确、反退化拒绝写入、赛季回声预检
"报出来而不是抛异常"、--detail-limit 分块留有余量、--commit 后不存在
Finish+比分为空的行。参见 CLAUDE.md §6.3 与 backend/cli/backfill_fixtures.py
头注释——本 CLI 是"中途接入联赛,历史已完赛场次永久漏采"这类事故的补采路径。
"""

import pytest

from backend.cli import backfill_fixtures as bf
from backend.db.connections import connect_ro, connect_rw
from tests.backend.coreseed import seed_core_schema

LEAGUE_ID = 268
SEASON = "2026"


class _FakeClient:
    """假 FotMobClient:只实现 enumerate_fixtures 依赖的 league_matches()。"""

    def __init__(self, fixtures_payload, league_id=LEAGUE_ID, season=SEASON):
        self._payload = fixtures_payload
        self._league_id = league_id
        self._season = season

    def league_matches(self, league_id, season_param):
        return {
            "details": {"id": self._league_id, "selectedSeason": self._season},
            "fixtures": {"allMatches": self._payload},
        }


def _match(mid, round_, finished, home_id, away_id, home_score=None,
           away_score=None, utc="2026-08-12T18:00:00Z", cancelled=False):
    status = {"utcTime": utc, "finished": finished, "cancelled": cancelled}
    if finished and not cancelled:
        status["scoreStr"] = f"{home_score}-{away_score}"
    return {
        "id": mid,
        "round": round_,
        "status": status,
        "home": {"id": home_id, "name": f"H{home_id}"},
        "away": {"id": away_id, "name": f"A{away_id}"},
    }


@pytest.fixture(autouse=True)
def _no_notify(monkeypatch):
    monkeypatch.setenv("NOTIFY_ENABLED", "0")


def _seed_existing(conn, mid, round_, status="Finish", home_score=1, away_score=0,
                    league_id=LEAGUE_ID, season=SEASON):
    conn.execute(
        """INSERT INTO dim_match
           (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
            Home_Team_Name, Away_Team_Name, home_score, away_score, status,
            Match_Round, kickoff_at_utc, kickoff_precision, kickoff_source)
           VALUES (?, ?, ?, '2026-08-12', ?, ?, 'H', 'A', ?, ?, ?, ?, ?, 'exact',
                   'fotmob:fixtures')""",
        (mid, season, league_id, mid * 10, mid * 10 + 1, home_score, away_score,
         status, round_, "2026-08-12T18:00:00Z"),
    )


class TestPlanDryRun:
    def test_dry_run_zero_writes(self, data_dir, monkeypatch):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        _seed_existing(conn_core, 1, "1")
        conn_core.commit()
        conn_core.close()

        payload = [_match(1, "1", True, 10, 11, 1, 0), _match(2, "2", True, 20, 21, 2, 2)]
        client = _FakeClient(payload)
        conn_ro = connect_ro("core")
        try:
            result = bf.plan(client, conn_ro, LEAGUE_ID, SEASON)
        finally:
            conn_ro.close()

        assert result["to_create"] == 1
        assert result["unchanged"] == 1
        # dry-run 本身不写库:重新只读查询,行数应该还是种子时的 1 行
        conn_ro2 = connect_ro("core")
        try:
            n = conn_ro2.execute(
                "SELECT COUNT(*) FROM dim_match WHERE League_ID=? AND Season=?",
                (LEAGUE_ID, SEASON),
            ).fetchone()[0]
        finally:
            conn_ro2.close()
        assert n == 1

    def test_create_update_split(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        _seed_existing(conn_core, 1, "1", status="Cancelled")  # provider 说已改成 Finish
        conn_core.commit()
        conn_core.close()

        payload = [
            _match(1, "1", True, 10, 11, 1, 0),   # 状态变化 → to_update
            _match(2, "2", True, 20, 21, 0, 0),   # 新行 → to_create
        ]
        client = _FakeClient(payload)
        conn_ro = connect_ro("core")
        try:
            result = bf.plan(client, conn_ro, LEAGUE_ID, SEASON)
        finally:
            conn_ro.close()
        assert result["to_create"] == 1
        assert result["to_update"] == 1
        assert result["unchanged"] == 0

    def test_round_gap_before_after(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        conn_core.commit()
        conn_core.close()

        payload = [_match(i, str(i), True, i * 10, i * 10 + 1, 1, 0) for i in (1, 2, 5)]
        client = _FakeClient(payload)
        conn_ro = connect_ro("core")
        try:
            result = bf.plan(client, conn_ro, LEAGUE_ID, SEASON)
        finally:
            conn_ro.close()
        assert result["round_gap_before"]["max_round"] is None
        assert result["round_gap_after"]["max_round"] == 5
        assert result["round_gap_after"]["missing_rounds"] == [3, 4]


class TestRegressionGuard:
    def test_provider_count_below_existing_is_flagged(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        for i in range(1, 6):
            _seed_existing(conn_core, i, str(i))
        conn_core.commit()
        conn_core.close()

        # provider 只回 2 场,远少于库里已有的 5 场
        payload = [_match(1, "1", True, 10, 11, 1, 0), _match(2, "2", True, 20, 21, 1, 0)]
        client = _FakeClient(payload)
        conn_ro = connect_ro("core")
        try:
            result = bf.plan(client, conn_ro, LEAGUE_ID, SEASON)
        finally:
            conn_ro.close()
        assert result["regression"] is True

    def test_commit_refuses_on_regression(self, data_dir, monkeypatch, capsys):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        for i in range(1, 6):
            _seed_existing(conn_core, i, str(i))
        conn_core.commit()
        conn_core.close()

        payload = [_match(1, "1", True, 10, 11, 1, 0)]
        monkeypatch.setattr(bf, "FotMobClient", lambda: _FakeClient(payload))
        rc = bf.main(["--league-id", str(LEAGUE_ID), "--season", SEASON, "--commit"])
        assert rc == 1
        conn_ro = connect_ro("core")
        try:
            n = conn_ro.execute(
                "SELECT COUNT(*) FROM dim_match WHERE League_ID=? AND Season=?",
                (LEAGUE_ID, SEASON),
            ).fetchone()[0]
        finally:
            conn_ro.close()
        assert n == 5, "反退化拒绝时不得写入,已有行数不变"


class TestSeasonMismatchPreflight:
    def test_reports_not_raises(self, data_dir):
        """新建行按 (League_ID, Date) 推导出的赛季与 --season 不一致时,
        plan() 必须把问题如实报出来,而不是等到 commit 时被存储层触发器炸掉。"""
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        conn_core.commit()
        conn_core.close()

        # utc 落在很久以前的日期,按 268 的制度表推导出的赛季大概率不是 "2026"
        payload = [_match(1, "1", True, 10, 11, 1, 0, utc="2019-03-01T18:00:00Z")]
        client = _FakeClient(payload)
        conn_ro = connect_ro("core")
        try:
            result = bf.plan(client, conn_ro, LEAGUE_ID, SEASON)
        finally:
            conn_ro.close()
        assert result["season_mismatches"], "预检必须发现赛季不一致,不能留到写入时才炸"
        assert result["season_mismatches"][0]["match_id"] == 1

    def test_commit_refuses_before_any_write(self, data_dir, monkeypatch):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        conn_core.commit()
        conn_core.close()

        payload = [_match(1, "1", True, 10, 11, 1, 0, utc="2019-03-01T18:00:00Z")]
        monkeypatch.setattr(bf, "FotMobClient", lambda: _FakeClient(payload))
        rc = bf.main(["--league-id", str(LEAGUE_ID), "--season", SEASON, "--commit"])
        assert rc == 1
        conn_ro = connect_ro("core")
        try:
            n = conn_ro.execute(
                "SELECT COUNT(*) FROM dim_match WHERE League_ID=?", (LEAGUE_ID,)
            ).fetchone()[0]
        finally:
            conn_ro.close()
        assert n == 0, "赛季预检失败必须在任何写入之前拒绝"


class TestCommitDetailAndNullScoreGuard:
    def test_detail_limit_leaves_resumable_remainder(self, data_dir, monkeypatch):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        conn_core.commit()
        conn_core.close()

        payload = [_match(i, str(i), True, i * 10, i * 10 + 1, 1, 0) for i in range(1, 7)]
        client = _FakeClient(payload)

        calls = []

        def _fake_ingest_matches_sequential(targets, **kw):
            calls.append(len(targets))
            ok = [t["match_id"] for t in targets]
            return ok, []

        monkeypatch.setattr(bf, "ingest_matches_sequential", _fake_ingest_matches_sequential)
        monkeypatch.setattr(bf, "FotMobClient", lambda: client)

        rc = bf.main(["--league-id", str(LEAGUE_ID), "--season", SEASON,
                       "--commit", "--detail-limit", "3"])
        assert rc == 0
        assert calls == [3], "detail-limit=3 时一次 commit 只应尝试 3 场明细"

        # 骨架已经全部写入(骨架不受 detail-limit 限制)
        conn_ro = connect_ro("core")
        try:
            n = conn_ro.execute(
                "SELECT COUNT(*) FROM dim_match WHERE League_ID=? AND Season=?",
                (LEAGUE_ID, SEASON),
            ).fetchone()[0]
        finally:
            conn_ro.close()
        assert n == 6

    def test_no_null_score_finish_rows_remain_after_full_commit(self, data_dir, monkeypatch):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        conn_core.commit()
        conn_core.close()

        payload = [_match(1, "1", True, 10, 11, 1, 0), _match(2, "2", True, 20, 21, 2, 2)]
        client = _FakeClient(payload)

        def _fake_ingest_matches_sequential(targets, **kw):
            # 模拟明细补采真正把比分写回 dim_match(骨架步骤本身不写比分列)。
            conn = connect_rw("core")
            try:
                for t in targets:
                    conn.execute(
                        "UPDATE dim_match SET home_score=1, away_score=0 WHERE Match_ID=?",
                        (t["match_id"],),
                    )
                conn.commit()
            finally:
                conn.close()
            return [t["match_id"] for t in targets], []

        monkeypatch.setattr(bf, "ingest_matches_sequential", _fake_ingest_matches_sequential)
        monkeypatch.setattr(bf, "FotMobClient", lambda: client)

        rc = bf.main(["--league-id", str(LEAGUE_ID), "--season", SEASON, "--commit"])
        assert rc == 0

        conn_ro = connect_ro("core")
        try:
            leftover = conn_ro.execute(
                "SELECT COUNT(*) FROM dim_match WHERE League_ID=? AND Season=?"
                " AND status='Finish' AND (home_score IS NULL OR away_score IS NULL)",
                (LEAGUE_ID, SEASON),
            ).fetchone()[0]
        finally:
            conn_ro.close()
        assert leftover == 0

    def test_partial_detail_failure_reported_not_swallowed(self, data_dir, monkeypatch):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        conn_core.commit()
        conn_core.close()

        payload = [_match(1, "1", True, 10, 11, 1, 0), _match(2, "2", True, 20, 21, 2, 2)]
        client = _FakeClient(payload)

        def _fake_ingest_matches_sequential(targets, **kw):
            ok = [targets[0]["match_id"]]
            fail = [t["match_id"] for t in targets[1:]]
            return ok, fail

        monkeypatch.setattr(bf, "ingest_matches_sequential", _fake_ingest_matches_sequential)
        monkeypatch.setattr(bf, "FotMobClient", lambda: client)

        rc = bf.main(["--league-id", str(LEAGUE_ID), "--season", SEASON, "--commit"])
        assert rc == 0, "部分场次明细失败不应让整体命令视为结构性失败(可重跑)"
