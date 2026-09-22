from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import backend.cli.backfill_nowgoal_odds_full_history as cli
from backend.providers.nowgoal_archive import ArchiveRow


class _StubTransport:
    """镜像 test_ingest_nowgoal_season_odds.py::TestFetchTwoPointRows._StubTransport,
    同一种离线打桩方式,按 cid 返回不同的固定 payload。"""

    def __init__(self, mix_by_cid: dict, euro_by_cid: dict):
        self._mix_by_cid = mix_by_cid
        self._euro_by_cid = euro_by_cid

    def mix_history(self, titan_id, cid="8"):
        return self._mix_by_cid.get(cid, {"ah": [], "ou": []})

    def euro_history(self, titan_id, cid="281"):
        return self._euro_by_cid.get(cid, [])


_MIX_BET365 = {
    "ah": [{"odds": {"u": "0.9", "g": "-0.5", "d": "0.9"}, "mt": 900},
           {"odds": {"u": "0.85", "g": "-0.5", "d": "0.95"}, "mt": 1000}],
    "ou": [{"odds": {"u": "0.9", "g": "2.5", "d": "0.9"}, "mt": 950}],
}
_EURO_BET365 = [{"HomeWin": "2.0", "Standoff": "3.2", "GuestWin": "3.8", "TimeShow": "1970,01,01,00,00,00"}]


class TestFetchAndBuildRecords:
    def test_covers_all_three_companies(self):
        transport = _StubTransport(
            mix_by_cid={"8": _MIX_BET365, "3": _MIX_BET365, "1": _MIX_BET365},
            euro_by_cid={"281": _EURO_BET365, "80": _EURO_BET365},
        )
        out = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=False)
        assert set(out.keys()) == {"8", "3", "1"}
        assert len(out["8"]) > 0
        assert len(out["1"]) > 0

    def test_bet365_and_macauslot_include_1x2_crown_does_not(self):
        """cid=3(皇冠)的历史 1x2 cid 未知,euro_cid=None,应该跳过 1x2,
        不是抓取失败,是刻意不发这个请求(见模块 docstring 的已知空缺)。"""
        transport = _StubTransport(
            mix_by_cid={"8": _MIX_BET365, "3": _MIX_BET365, "1": _MIX_BET365},
            euro_by_cid={"281": _EURO_BET365, "80": _EURO_BET365},
        )
        out = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=False)
        assert any(r["market"] == "1x2" for r in out["8"])
        assert any(r["market"] == "1x2" for r in out["1"])
        assert not any(r["market"] == "1x2" for r in out["3"])

    def test_ah_present_for_all_companies_when_mix_history_has_data(self):
        transport = _StubTransport(
            mix_by_cid={"8": _MIX_BET365, "3": _MIX_BET365, "1": _MIX_BET365},
            euro_by_cid={},
        )
        out = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=False)
        for cid in ("8", "3", "1"):
            assert any(r["market"] == "ah" for r in out[cid]), f"company {cid} missing ah"

    def test_inverted_flag_propagates_to_ah_records(self):
        transport = _StubTransport(mix_by_cid={"8": _MIX_BET365}, euro_by_cid={})
        not_inv = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=False)
        inv = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=True)
        ah_not_inv = next(r for r in not_inv["8"] if r["market"] == "ah")
        ah_inv = next(r for r in inv["8"] if r["market"] == "ah")
        assert ah_not_inv["latest"]["line"] == -ah_inv["latest"]["line"]

    def test_no_pre_match_data_yields_empty_records_not_crash(self):
        transport = _StubTransport(mix_by_cid={}, euro_by_cid={})
        out = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=False)
        assert out == {"8": [], "3": [], "1": []}


class TestMainXrefOnly:
    """2026-09-22 事故修复的集成测试:--xref-only 必须写 dim_match_xref、
    绝不触碰 mix_history/euro_history(不然就失去了"不用重新抓几小时"的意义)。
    """

    @pytest.fixture()
    def core_db(self, tmp_path):
        db = tmp_path / "core.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE dim_match (Match_ID INT PRIMARY KEY, League_ID INT, Season TEXT,
              Date TEXT, kickoff_at_utc TEXT, kickoff_precision TEXT,
              Home_Team_ID INT, Away_Team_ID INT, home_score INT, away_score INT,
              Match_Round TEXT, status TEXT, Home_Team_Name TEXT, Away_Team_Name TEXT);
        """)
        conn.execute(
            "INSERT INTO dim_match VALUES (5001,47,'2025/2026','2025-08-16',"
            "'2025-08-16T03:00:00Z','exact',1,2,4,2,'1','Finish','Liverpool','Bournemouth')")
        conn.commit()
        conn.close()
        return db

    @pytest.fixture()
    def odds_db(self, tmp_path):
        db = tmp_path / "odds.db"
        schema_path = Path(__file__).resolve().parents[2] / "backend" / "migrations" / "odds" / "0001_init.sql"
        schema = schema_path.read_text()
        conn = sqlite3.connect(str(db))
        conn.executescript(schema)
        conn.commit()
        conn.close()
        return db

    def _patch_transport(self, monkeypatch):
        archive_row = ArchiveRow(titan_id="2789129", ng_league_id=36,
                                 kickoff_utc="2025-08-15T19:00:00Z",
                                 home_ng_id=25, away_ng_id=348, home_score=4, away_score=2)
        monkeypatch.setattr(
            cli.NowGoalArchiveTransport, "archive_season",
            lambda self, ng_league_id, season_key: {"LeagueInfo": [ng_league_id, "EPL", season_key]},
        )
        monkeypatch.setattr(cli, "parse_archive_season", lambda payload, ng_league_id: [archive_row])
        monkeypatch.setattr(cli, "parse_team_info", lambda payload: {})
        monkeypatch.setattr(cli, "resolve_and_gate", lambda *a, **k: [
            {"match_id": 5001, "status": cli.STATUS_AUTO_OK, "titan_id": "2789129",
             "direction": "direct", "evidence_kind": "id", "kickoff_diff_seconds": 5.0},
        ])
        calls = {"mix": 0, "euro": 0}
        monkeypatch.setattr(cli.NowGoalArchiveTransport, "mix_history",
                            lambda self, *a, **k: calls.__setitem__("mix", calls["mix"] + 1) or {"ah": [], "ou": []})
        monkeypatch.setattr(cli.NowGoalArchiveTransport, "euro_history",
                            lambda self, *a, **k: calls.__setitem__("euro", calls["euro"] + 1) or [])
        return calls

    def test_xref_only_writes_xref_and_skips_odds_fetch(self, monkeypatch, core_db, odds_db):
        calls = self._patch_transport(monkeypatch)
        monkeypatch.setenv("THORDATA_PROXY", "http://fake-proxy:8080")
        rc = cli.main([
            "--league-id", "47", "--season", "2025/2026", "--nowgoal-league-id", "36",
            "--core-db", str(core_db), "--db-path", str(odds_db), "--skip-backup",
            "--xref-only", "--live",
        ])
        assert rc == 0
        assert calls == {"mix": 0, "euro": 0}, "xref-only 不该发起任何赔率抓取请求"
        conn = sqlite3.connect(str(odds_db))
        xref = conn.execute(
            "SELECT fotmob_match_id, provider_match_id FROM dim_match_xref"
        ).fetchall()
        assert xref == [(5001, "2789129")]
        bronze_count = conn.execute("SELECT COUNT(*) FROM bronze_ng_odds_snap").fetchone()[0]
        assert bronze_count == 0
        conn.close()

    def test_normal_live_run_also_writes_xref(self, monkeypatch, core_db, odds_db):
        """补救已经跑过的旧数据用 --xref-only 就够了,但确认以后正常跑
        (不带 --xref-only)同样会写 xref,不会重演这次的缺口。"""
        self._patch_transport(monkeypatch)
        monkeypatch.setenv("THORDATA_PROXY", "http://fake-proxy:8080")
        rc = cli.main([
            "--league-id", "47", "--season", "2025/2026", "--nowgoal-league-id", "36",
            "--core-db", str(core_db), "--db-path", str(odds_db), "--skip-backup", "--live",
        ])
        assert rc == 0
        conn = sqlite3.connect(str(odds_db))
        xref = conn.execute("SELECT fotmob_match_id FROM dim_match_xref").fetchall()
        assert xref == [(5001,)]
        conn.close()
