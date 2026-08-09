"""backend/cli/ingest_nowgoal_season_odds.py(2026-08-08 挪超/瑞典超历史赔率
回补)的门禁、身份解析、幂等性测试。全部离线,不发真实网络请求。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import backend.cli.ingest_nowgoal_season_odds as cli
from backend.providers.nowgoal_archive import ArchiveRow

_ODDS_SCHEMA = Path(__file__).resolve().parents[2] / "backend" / "migrations" / "odds"


@pytest.fixture()
def odds_db(tmp_path: Path) -> Path:
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db))
    for mig in ("0001_init.sql", "0004_legacy_odds_summary.sql", "0005_legacy_source_jka.sql"):
        conn.executescript((_ODDS_SCHEMA / mig).read_text())
    conn.commit()
    conn.close()
    return db


def _mk_core(tmp_path: Path, matches: list[tuple]) -> Path:
    """matches: (match_id, kickoff_iso, precision, home_id, away_id, home_name,
    away_name, hs, as_, date)."""
    db = tmp_path / "core.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE dim_match (Match_ID INT PRIMARY KEY, League_ID INT, Season TEXT,
          Date TEXT, kickoff_at_utc TEXT, kickoff_precision TEXT,
          Home_Team_ID INT, Away_Team_ID INT, home_score INT, away_score INT,
          Match_Round TEXT, status TEXT, Home_Team_Name TEXT, Away_Team_Name TEXT);
    """)
    for (mid, ko, prec, hid, aid, hname, aname, hs, as_, date) in matches:
        conn.execute(
            "INSERT INTO dim_match(Match_ID,League_ID,Season,Date,kickoff_at_utc,"
            "kickoff_precision,Home_Team_ID,Away_Team_ID,home_score,away_score,"
            "Match_Round,status,Home_Team_Name,Away_Team_Name) "
            "VALUES (?,67,'2026',?,?,?,?,?,?,?,'1','Finish',?,?)",
            (mid, date, ko, prec, hid, aid, hs, as_, hname, aname))
    conn.commit()
    conn.close()
    return db


def _row(titan_id, kickoff_utc, home_ng, away_ng, hs, as_):
    return ArchiveRow(titan_id=titan_id, ng_league_id=26, kickoff_utc=kickoff_utc,
                      home_ng_id=home_ng, away_ng_id=away_ng, home_score=hs, away_score=as_)


class TestArchiveSeasonKey:
    def test_bare_year_passthrough(self):
        assert cli._archive_season_key("2026") == "2026"

    def test_cross_year_dashed(self):
        assert cli._archive_season_key("2020/2021") == "2020-2021"


class TestLoadTargetMatches:
    def test_only_exact_kickoff_included(self, tmp_path):
        db = _mk_core(tmp_path, [
            (1, "2026-03-14T15:00:00Z", "exact", 100, 200, "A", "B", 1, 0, "2026-03-14"),
            (2, "2026-03-15T15:00:00Z", "date_only", 100, 300, "A", "C", 1, 1, "2026-03-15"),
        ])
        target, skipped = cli.load_target_matches(db, 67, "2026")
        assert [m["Match_ID"] for m in target] == [1]
        assert skipped == 1


class TestResolveAndGate:
    """构造一个干净、无歧义的 4 队 2 场比赛小场景,验证:唯一候选 -> auto_ok;
    kickoff 差超过 1800s -> 降级 needs_review;team_id_dict 双射冲突 -> 报错。"""

    def _matches(self):
        return [
            {"Match_ID": 1, "Date": "2026-03-14", "kickoff_at_utc": "2026-03-14T15:00:00Z",
             "Home_Team_ID": 100, "Away_Team_ID": 200, "Home_Team_Name": "Team A",
             "Away_Team_Name": "Team B", "home_score": 2, "away_score": 1},
            {"Match_ID": 2, "Date": "2026-03-21", "kickoff_at_utc": "2026-03-21T15:00:00Z",
             "Home_Team_ID": 200, "Away_Team_ID": 100, "Home_Team_Name": "Team B",
             "Away_Team_Name": "Team A", "home_score": 0, "away_score": 3},
        ]

    def _rows(self):
        return [
            _row("9001", "2026-03-14T15:00:00Z", 10, 20, 2, 1),
            _row("9002", "2026-03-21T15:00:00Z", 20, 10, 0, 3),
        ]

    def test_unambiguous_matches_resolve_auto_ok(self):
        # 单独一场种子(比分+日期窗口内唯一)门槛设 1 票即可学出词典
        resolved = cli.resolve_and_gate(self._matches(), self._rows(), league_id=67,
                                        min_votes=1, min_margin_ratio=1.0)
        statuses = {r["match_id"]: r["status"] for r in resolved}
        assert statuses == {1: "auto_ok", 2: "auto_ok"}
        titans = {r["match_id"]: r["titan_id"] for r in resolved}
        assert titans == {1: "9001", 2: "9002"}

    def test_kickoff_beyond_tolerance_downgraded(self):
        """archive 候选的 kickoff 与目标比赛真实 kickoff 差 > 1800s,即使身份
        解析本身没问题,也必须降级为 needs_review——这是本 CLI 在原模块
        (为 kickoff 全 NULL 的历史场次设计)基础上新加的门禁。"""
        matches = self._matches()
        matches[0]["kickoff_at_utc"] = "2026-03-14T18:00:00Z"  # 与候选差 3 小时
        resolved = cli.resolve_and_gate(matches, self._rows(), league_id=67,
                                        min_votes=1, min_margin_ratio=1.0)
        r1 = next(r for r in resolved if r["match_id"] == 1)
        assert r1["status"] == "needs_review"
        assert "kickoff_diff" in r1["detail"]

    def test_team_id_dictionary_collision_raises(self, monkeypatch):
        """人为构造一个会产生非双射词典的种子集合,断言 fail-closed 报错而不是
        静默使用一个有冲突的映射。"""
        import backend.ingest.nowgoal_historical_match_resolution as resmod

        def _fake_dict(seed_pairs, *, min_votes, min_margin_ratio):
            return {10: 100, 20: 100}  # 两个 ng_id 映射到同一个 fm_id:非双射

        monkeypatch.setattr(cli, "build_team_id_dictionary", _fake_dict)
        with pytest.raises(cli.TeamIdDictionaryError, match="双射"):
            cli.resolve_and_gate(self._matches(), self._rows(), league_id=67)

    def test_team_names_enable_similarity_fallback(self):
        """词典票数不够(min_votes 设得很高,词典必然为空)时,如果候选带队名
        且与目标队名高度相似,应该走 name 证据路径而不是直接 no_candidate。"""
        resolved = cli.resolve_and_gate(
            self._matches(), self._rows(), league_id=67,
            team_names={10: "Team A", 20: "Team B"},
            min_votes=999, min_margin_ratio=999.0,
        )
        statuses = {r["match_id"]: r["status"] for r in resolved}
        # 队名完全相同(相似度 1.0)应该直接 auto_ok(kind=name, strength>=0.999)
        assert statuses == {1: "auto_ok", 2: "auto_ok"}


class TestFetchTwoPointRows:
    class _StubTransport:
        def __init__(self, mix_payload, euro_payload):
            self._mix = mix_payload
            self._euro = euro_payload

        def mix_history(self, titan_id, cid="8"):
            return self._mix

        def euro_history(self, titan_id, cid="281"):
            return self._euro

    def test_rows_shape_and_direction_not_inverted(self):
        mix = {"ah": [{"odds": {"u": "0.9", "g": "-0.5", "d": "0.9"}, "mt": 1000}],
              "ou": []}
        euro = [{"HomeWin": "2.0", "Standoff": "3.2", "GuestWin": "3.8",
                "TimeShow": "1970,01,01,00,00,00"}]
        transport = self._StubTransport(mix, euro)
        rows = cli.fetch_two_point_rows(transport, match_id=1, titan_id="9001",
                                        kickoff_utc="2026-01-01T00:00:00Z",
                                        inverted=False, company="bet365")
        ah_rows = [r for r in rows if r["market"] == "ah"]
        assert len(ah_rows) == 2  # initial + latest(同一行,opening==closing)
        assert ah_rows[0]["home_or_over"] == 0.9
        assert ah_rows[0]["line"] == -0.5
        assert all(r["source"] == "nowgoal_archive_refetch" for r in rows)
        assert all(r["orientation_fixed"] == 0 for r in rows)

    def test_inverted_direction_swaps_and_negates(self):
        mix = {"ah": [{"odds": {"u": "0.9", "g": "-0.5", "d": "0.85"}, "mt": 1000}], "ou": []}
        euro = []
        transport = self._StubTransport(mix, euro)
        rows = cli.fetch_two_point_rows(transport, match_id=1, titan_id="9001",
                                        kickoff_utc="2026-01-01T00:00:00Z",
                                        inverted=True, company="bet365")
        ah_row = next(r for r in rows if r["market"] == "ah")
        # 翻转后:home/away 互换,line 取负
        assert ah_row["home_or_over"] == 0.85
        assert ah_row["away_or_under"] == 0.9
        assert ah_row["line"] == 0.5

    def test_no_pre_match_odds_yields_no_rows(self):
        transport = self._StubTransport({"ah": [], "ou": []}, [])
        rows = cli.fetch_two_point_rows(transport, match_id=1, titan_id="9001",
                                        kickoff_utc="2026-01-01T00:00:00Z",
                                        inverted=False, company="bet365")
        assert rows == []


class TestLiveWriteIdempotency:
    def test_insert_or_ignore_dedupes_on_rerun(self, odds_db):
        rows = [{
            "fotmob_match_id": 1, "source": "nowgoal_archive_refetch", "provider": "Bet365",
            "market": "1x2", "period": "initial", "line": None, "home_or_over": 2.0,
            "draw": 3.2, "away_or_under": 3.8, "orientation_fixed": 0,
            "source_file": "nowgoal_archive:titan_9001",
        }]
        conn = sqlite3.connect(str(odds_db))
        now = cli._utc_now()
        for _ in range(2):  # 模拟重跑两次
            with conn:
                for row in rows:
                    conn.execute("""
                        INSERT OR IGNORE INTO bronze_legacy_odds_summary
                          (fotmob_match_id, source, provider, market, period, line,
                           home_or_over, draw, away_or_under, orientation_fixed,
                           source_file, ingested_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (row["fotmob_match_id"], row["source"], row["provider"], row["market"],
                         row["period"], row["line"], row["home_or_over"], row["draw"],
                         row["away_or_under"], row["orientation_fixed"], row["source_file"], now))
        count = conn.execute("SELECT COUNT(*) FROM bronze_legacy_odds_summary").fetchone()[0]
        conn.close()
        assert count == 1
