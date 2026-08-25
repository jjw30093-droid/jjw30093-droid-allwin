"""backend/cli/reingest_matches.py(2026-08-24)——按 match_id 强制重新落库的
手动补采工具。钉住:默认 dry-run 不写库、--commit 才真正调用 ingest_match、
不在库里的 match_id 如实报告 skipped、单场失败不中止整批、League_ID/Season
从库里现有行读取(不需要调用方猜)。
"""

import pytest

from backend.cli.reingest_matches import reingest
from backend.db.connections import connect_rw
from tests.backend.coreseed import insert_match, seed_core_schema


@pytest.fixture
def core(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    insert_match(conn, match_id=5795371, league_id=47, season="2026/2027",
                 status="InPlay", kickoff_at_utc="2026-08-20T15:30:00Z")
    insert_match(conn, match_id=5868022, league_id=87, season="2026/2027",
                 status="InPlay", kickoff_at_utc="2026-08-21T15:00:00Z")
    conn.commit()
    conn.close()


class TestReingestMatches:
    def test_dry_run_does_not_call_ingest(self, core, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "backend.cli.reingest_matches.ingest_match",
            lambda mid, league_id=None: calls.append(mid),
        )
        result = reingest([5795371, 5868022], commit=False)
        assert result["mode"] == "dry-run"
        assert calls == []
        targets = {t["match_id"]: t for t in result["targets"]}
        assert targets[5795371]["found"] is True
        assert targets[5795371]["league_id"] == 47
        assert targets[5795371]["season"] == "2026/2027"
        assert targets[5795371]["status_before"] == "InPlay"

    def test_commit_calls_ingest_with_league_from_db_and_no_season(self, core, monkeypatch):
        """2026-08-25(CLAUDE.md §6.3):ingest_match 不再接受 season——此前这里
        把库里的 Season 读回再传回去,是错误赛季的自我繁殖路径。League_ID 仍从
        库里读;season 只出现在 dry-run 的展示字段里,不进任何写调用。"""
        calls = []
        monkeypatch.setattr(
            "backend.cli.reingest_matches.ingest_match",
            lambda mid, league_id=None: calls.append((mid, league_id)),
        )
        result = reingest([5795371], commit=True)
        assert result["mode"] == "commit"
        assert calls == [(5795371, 47)]
        assert result["results"][0]["result"] == "ok"

    def test_unknown_match_id_reported_not_crashed(self, core, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "backend.cli.reingest_matches.ingest_match",
            lambda mid, league_id=None: calls.append(mid),
        )
        result = reingest([9999999], commit=True)
        assert calls == []
        assert result["results"] == [{"match_id": 9999999, "found": False,
                                       "result": "skipped_not_in_db"}]

    def test_one_failure_does_not_abort_batch(self, core, monkeypatch):
        """手动补采工具:操作者要看到全部结果,单场失败不能拖累其它场次
        (与 scheduler.py 那种链式任务遇错即停是刻意不同的行为)。"""
        calls = []

        def fake_ingest(mid, league_id=None):
            calls.append(mid)
            if mid == 5795371:
                raise ValueError("模拟页面结构漂移")

        monkeypatch.setattr("backend.cli.reingest_matches.ingest_match", fake_ingest)
        result = reingest([5795371, 5868022], commit=True)
        assert calls == [5795371, 5868022]  # 两场都被尝试了
        results = {r["match_id"]: r["result"] for r in result["results"]}
        assert "failed" in results[5795371]
        assert results[5868022] == "ok"
