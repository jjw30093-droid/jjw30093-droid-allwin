"""odds_coverage_report 的纯只读单元/集成测试:全部用临时 sqlite/json fixture,
不碰 miaomiaodi 或 Football_Data_Lake 的任何真实文件,不依赖网络。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.cli import odds_coverage_report as m


def _make_core_db(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE dim_match (
            Match_ID INTEGER PRIMARY KEY, Season TEXT, League_ID INTEGER, Date TEXT,
            Home_Team_ID INTEGER, Away_Team_ID INTEGER, Home_Team_Name TEXT, Away_Team_Name TEXT,
            home_score INTEGER, away_score INTEGER, status TEXT, Match_Round TEXT
        )"""
    )
    for r in rows:
        conn.execute(
            "INSERT INTO dim_match (Match_ID,Season,League_ID,Date,Home_Team_ID,Away_Team_ID,"
            "Home_Team_Name,Away_Team_Name,home_score,away_score,status,Match_Round)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["match_id"], r["season"], r["league_id"], r.get("date", "2021-01-01"),
                r.get("home_id", 1), r.get("away_id", 2), r.get("home", "Home"), r.get("away", "Away"),
                r.get("home_score"), r.get("away_score"), r.get("status", "Finish"), r.get("round", "1"),
            ),
        )
    conn.commit()
    conn.close()


def _make_asset_b_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE silver_match_odds (
            match_id INTEGER, market TEXT, period TEXT,
            raw_line REAL, raw_home REAL, raw_draw REAL, raw_away REAL
        )"""
    )
    conn.executemany(
        "INSERT INTO silver_match_odds (match_id,market,period,raw_line,raw_home,raw_draw,raw_away)"
        " VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _ah_rec(fotmob_id, initial_line, latest_line, match_name="Home vs Away"):
    def slot(line):
        return {"over_or_home": "0.9", "line": str(line), "under_or_away": "0.9"}

    return {
        "fotmob_id": fotmob_id,
        "match_name": match_name,
        "odds": {
            "asian_handicap": {"initial": slot(initial_line), "latest": slot(latest_line)},
            "over_under": {"initial": slot(2.5), "latest": slot(2.5)},
            "european_1x2": {
                "initial": {"home": "1.5", "draw": "3.5", "away": "5.0"},
                "latest": {"home": "1.4", "draw": "3.6", "away": "5.2"},
            },
        },
    }


class TestPureFunctions:
    def test_parse_handicap_line_plain(self):
        assert m.parse_handicap_line("-0.5") == -0.5
        assert m.parse_handicap_line("1.25") == 1.25

    def test_parse_handicap_line_quarter_notation(self):
        assert m.parse_handicap_line("0/-0.5") == pytest.approx(-0.25)
        assert m.parse_handicap_line("1/1.5") == pytest.approx(1.25)

    def test_parse_handicap_line_invalid(self):
        assert m.parse_handicap_line(None) is None
        assert m.parse_handicap_line("") is None
        assert m.parse_handicap_line("-") is None
        assert m.parse_handicap_line("N/A") is None
        assert m.parse_handicap_line("abc") is None

    def test_line_movement(self):
        assert m.line_movement(1.0, 1.5) == "up"
        assert m.line_movement(1.5, 1.0) == "down"
        assert m.line_movement(1.0, 1.0) == "flat"
        assert m.line_movement(None, 1.0) == "unparseable"
        assert m.line_movement(1.0, None) == "unparseable"

    def test_compare_lines(self):
        assert m.compare_lines(1.25, 1.25, 1e-6) == "exact_match"
        assert m.compare_lines(1.25, -1.25, 1e-6) == "mirrored_match"
        assert m.compare_lines(1.25, 1.5, 1e-6) == "other_mismatch"
        assert m.compare_lines(None, 1.25, 1e-6) == "not_comparable"
        assert m.compare_lines(0.0, 0.0, 1e-6) == "exact_match"

    def test_deterministic_sample_reproducible(self):
        ids = list(range(1000, 1100))
        s1 = m.deterministic_sample(ids, 10)
        s2 = m.deterministic_sample(list(reversed(ids)), 10)
        assert s1 == s2
        assert len(s1) == 10
        assert s1 == sorted(s1)

    def test_deterministic_sample_smaller_than_size_returns_all(self):
        assert m.deterministic_sample([5, 3, 1], 10) == [1, 3, 5]

    def test_deterministic_sample_empty(self):
        assert m.deterministic_sample([], 10) == []

    def test_season_to_tag(self):
        assert m.season_to_tag("2021/2022") == "2122"
        assert m.season_to_tag("2025/2026") == "2526"
        assert m.season_to_tag("garbage") is None
        assert m.season_to_tag(None) is None

    def test_grade_asset_a_six_slots_and_movement(self):
        rec = _ah_rec("1", -0.5, -0.25)
        g = m.grade_asset_a(rec)
        assert g["slots_complete"] == 6
        assert g["ah_movement"] == "up"

    def test_grade_asset_a_incomplete_slot(self):
        rec = _ah_rec("1", -0.5, -0.25)
        rec["odds"]["european_1x2"]["latest"]["away"] = ""
        g = m.grade_asset_a(rec)
        assert g["slots_complete"] == 5


class TestClassifyGap:
    def _match(self, match_id=1, season="2021/2022"):
        return m.CoreMatch(
            match_id=match_id, league_id=47, season=season, date="2021-08-01",
            home_team_id=1, away_team_id=2, home_name="A", away_name="B",
            home_score=1, away_score=0, match_round="1",
        )

    def test_legacy_not_present_is_unclassified(self):
        state = m.SourceState("legacy_mapping", m.NOT_CONFIGURED)
        reason, detail = m.classify_gap(self._match(), state, {})
        assert reason == "unclassified"

    def test_season_never_attempted_when_no_tag_file(self):
        state = m.SourceState("legacy_mapping", m.PRESENT)
        reason, _ = m.classify_gap(self._match(season="2020/2021"), state, {"2122": {}})
        assert reason == "season_never_attempted"

    def test_never_mapped_when_tag_file_present_but_no_entry(self):
        state = m.SourceState("legacy_mapping", m.PRESENT)
        reason, _ = m.classify_gap(self._match(match_id=999, season="2021/2022"), state, {"2122": {"1": {}}})
        assert reason == "never_mapped"

    def test_mapped_but_no_odds_when_entry_present(self):
        state = m.SourceState("legacy_mapping", m.PRESENT)
        reason, detail = m.classify_gap(
            self._match(match_id=1, season="2021/2022"), state, {"2122": {"1": {"titan_id": "999"}}}
        )
        assert reason == "mapped_but_no_odds"
        assert "999" in detail


class TestLoaders:
    def test_load_core_matches_present_and_filtered(self, tmp_path):
        db = tmp_path / "core.db"
        _make_core_db(
            db,
            [
                {"match_id": 1, "season": "2021/2022", "league_id": 47, "status": "Finish"},
                {"match_id": 2, "season": "2021/2022", "league_id": 99, "status": "Finish"},
                {"match_id": 3, "season": "2021/2022", "league_id": 47, "status": "NotStarted"},
            ],
        )
        state, matches, all_ids = m.load_core_matches(db, frozenset({47}), None)
        assert state.status == m.PRESENT
        assert len(matches) == 1
        assert matches[0].match_id == 1
        assert all_ids == frozenset({1, 2, 3})

    def test_load_core_matches_missing_file(self, tmp_path):
        state, matches, all_ids = m.load_core_matches(tmp_path / "nope.db", None, None)
        assert state.status == m.UNREADABLE
        assert matches == ()

    def test_load_asset_a_not_configured(self):
        state, data = m.load_asset_a(None)
        assert state.status == m.NOT_CONFIGURED
        assert data == {}

    def test_load_asset_a_missing_dir(self, tmp_path):
        state, data = m.load_asset_a(tmp_path / "nope")
        assert state.status == m.UNREADABLE
        assert state.detail == "directory_not_found"

    def test_load_asset_a_empty_dir(self, tmp_path):
        (tmp_path / "empty").mkdir()
        state, data = m.load_asset_a(tmp_path / "empty")
        assert state.status == m.UNREADABLE
        assert state.detail == "no_match_files_found"

    def test_load_asset_a_dedupes_by_best_slots(self, tmp_path):
        d = tmp_path / "odds"
        d.mkdir()
        (d / "match_odds_data.json").write_text(
            json.dumps([_ah_rec("1", -0.5, -0.25)]), encoding="utf-8"
        )
        incomplete = _ah_rec("1", -0.5, -0.25)
        incomplete["odds"]["over_under"]["latest"]["line"] = ""
        (d / "match_odds_data_2122.json").write_text(json.dumps([incomplete]), encoding="utf-8")
        state, data = m.load_asset_a(d)
        assert state.status == m.PRESENT
        assert state.record_count == 2
        assert len(data) == 1
        assert m.grade_asset_a(data["1"])["slots_complete"] == 6

    def test_load_asset_a_corrupt_json(self, tmp_path):
        d = tmp_path / "odds"
        d.mkdir()
        (d / "match_odds_data.json").write_text("{not valid json", encoding="utf-8")
        state, data = m.load_asset_a(d)
        assert state.status == m.UNREADABLE
        assert "json_decode_error" in state.detail
        assert data == {}

    def test_load_asset_b_not_configured(self):
        state, data = m.load_asset_b(None)
        assert state.status == m.NOT_CONFIGURED

    def test_load_asset_b_missing_file(self, tmp_path):
        state, data = m.load_asset_b(tmp_path / "nope.db")
        assert state.status == m.UNREADABLE
        assert state.detail == "file_not_found"

    def test_load_asset_b_present(self, tmp_path):
        db = tmp_path / "lake.db"
        _make_asset_b_db(
            db,
            [
                (1, "ah", "opening", -0.5, 0.9, None, 0.9),
                (1, "ah", "closing", -0.25, 0.9, None, 0.9),
            ],
        )
        state, data = m.load_asset_b(db)
        assert state.status == m.PRESENT
        assert 1 in data
        g = m.grade_asset_b(data[1])
        assert g["has_ah_open_close"] is True
        assert g["ah_movement"] == "up"

    def test_load_legacy_mappings_not_configured(self):
        state, data = m.load_legacy_mappings(None)
        assert state.status == m.NOT_CONFIGURED

    def test_load_legacy_mappings_present(self, tmp_path):
        d = tmp_path / "legacy"
        d.mkdir()
        (d / "nowgoal_2122_mapping.json").write_text(
            json.dumps({"1": {"titan_id": "999", "is_swapped": False}}), encoding="utf-8"
        )
        state, data = m.load_legacy_mappings(d)
        assert state.status == m.PRESENT
        assert data["2122"]["1"]["titan_id"] == "999"


class TestBuildCoverageIntegration:
    def test_end_to_end_small_fixture(self):
        matches = (
            m.CoreMatch(1, 47, "2021/2022", "2021-08-01", 1, 2, "Home", "Away", 1, 0, "1"),
            m.CoreMatch(2, 47, "2021/2022", "2021-08-02", 3, 4, "H2", "A2", 0, 0, "1"),
            m.CoreMatch(3, 47, "2020/2021", "2020-08-01", 5, 6, "H3", "A3", 2, 1, "1"),
        )
        asset_a = {"1": _ah_rec("1", -0.5, -0.25)}
        asset_b = {1: {("ah", "opening"): (-0.5, 0.9, None, 0.9), ("ah", "closing"): (-0.25, 0.9, None, 0.9)}}
        legacy = {"2122": {"1": {"titan_id": "999"}}}

        report, gaps = m.build_coverage(
            matches=matches,
            all_core_ids=frozenset({1, 2, 3}),
            asset_a=asset_a,
            asset_a_state=m.SourceState("asset_a_json", m.PRESENT),
            asset_b=asset_b,
            asset_b_state=m.SourceState("asset_b_sqlite", m.PRESENT),
            legacy_by_tag=legacy,
            legacy_state=m.SourceState("legacy_mapping", m.PRESENT),
            consistency_sample_size=200,
            odds_tolerance=1e-6,
            generated_at="2026-01-01T00:00:00Z",
        )

        assert report["totals"] == {"finished": 3, "has_asset_a": 1, "has_asset_b": 1, "union": 1, "gap": 2}
        assert len(gaps) == 2
        reasons = {g["match_id"]: g["gap_reason"] for g in gaps}
        assert reasons[2] == "never_mapped"
        assert reasons[3] == "season_never_attempted"
        assert report["market_quality"]["asset_a"]["six_slot_complete"] == 1
        cs = report["consistency_sample"]
        assert cs["counts"].get("initial:exact_match") == 1
        assert cs["counts"].get("latest:exact_match") == 1
        assert report["orphans"]["count"] == 0
        for g in gaps:
            assert g["kickoff_at_utc"] is None
            assert g["kickoff_precision"] == "date_only"

    def test_orphans_detected(self):
        matches = ()
        asset_a = {"999": _ah_rec("999", -0.5, -0.5)}
        report, gaps = m.build_coverage(
            matches=matches,
            all_core_ids=frozenset({1, 2}),
            asset_a=asset_a,
            asset_a_state=m.SourceState("asset_a_json", m.PRESENT),
            asset_b={},
            asset_b_state=m.SourceState("asset_b_sqlite", m.PRESENT),
            legacy_by_tag={},
            legacy_state=m.SourceState("legacy_mapping", m.NOT_CONFIGURED),
            consistency_sample_size=200,
            odds_tolerance=1e-6,
            generated_at="2026-01-01T00:00:00Z",
        )
        assert report["orphans"]["count"] == 1
        assert report["orphans"]["ids"] == [999]


class TestCompareExpectations:
    def test_agree_and_disagree(self):
        report = {
            "totals": {"finished": 10, "gap": 3},
            "market_quality": {"asset_a": {"unique_fotmob_ids": 8, "six_slot_complete": 7}},
            "orphans": {"count": 0},
        }
        lines = m.compare_expectations(report, [], {"total_finished": 10, "gap_total": 999})
        assert "AGREE total_finished=10" in lines
        assert "DISAGREE gap_total prior=999 recomputed=3" in lines


class TestCliMain:
    def _core_db(self, tmp_path):
        db = tmp_path / "core.db"
        _make_core_db(
            db,
            [
                {"match_id": 1, "season": "2021/2022", "league_id": 47, "status": "Finish"},
                {"match_id": 2, "season": "2021/2022", "league_id": 47, "status": "Finish"},
            ],
        )
        return db

    def test_exit_2_when_hard_source_missing_and_no_artifacts_written(self, tmp_path, capsys):
        core_db = self._core_db(tmp_path)
        out_dir = tmp_path / "out"
        code = m.main(
            [
                "--core-db", str(core_db),
                "--asset-b-db", str(tmp_path / "lake.db"),  # asset_a not configured at all
                "--output-dir", str(out_dir),
                "--no-artifacts",
            ]
        )
        assert code == 2
        assert not out_dir.exists()
        captured = capsys.readouterr()
        assert "hard_source_unavailable" in captured.err

    def test_full_run_writes_artifacts_with_atomic_perms(self, tmp_path):
        core_db = self._core_db(tmp_path)
        asset_a_dir = tmp_path / "odds"
        asset_a_dir.mkdir()
        (asset_a_dir / "match_odds_data.json").write_text(
            json.dumps([_ah_rec("1", -0.5, -0.25)]), encoding="utf-8"
        )
        asset_b_db = tmp_path / "lake.db"
        _make_asset_b_db(asset_b_db, [])
        out_dir = tmp_path / "out"

        code = m.main(
            [
                "--core-db", str(core_db),
                "--asset-a-dir", str(asset_a_dir),
                "--asset-b-db", str(asset_b_db),
                "--output-dir", str(out_dir),
                "--league", "47",
                "--format", "json",
            ]
        )
        assert code == 0
        run_dirs = list(out_dir.iterdir())
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]
        assert (run_dir / "gaps.jsonl").exists()
        assert not (run_dir / "gaps.PARTIAL.jsonl").exists()
        import stat

        assert oct(run_dir.stat().st_mode)[-3:] == "700"
        assert oct((run_dir / "gaps.jsonl").stat().st_mode)[-3:] == "600"
        lines = (run_dir / "gaps.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["match_id"] == 2

    def test_allow_partial_writes_partial_gaps_file(self, tmp_path):
        core_db = self._core_db(tmp_path)
        asset_b_db = tmp_path / "lake.db"
        _make_asset_b_db(asset_b_db, [])
        out_dir = tmp_path / "out"

        code = m.main(
            [
                "--core-db", str(core_db),
                "--asset-b-db", str(asset_b_db),
                "--output-dir", str(out_dir),
                "--allow-partial-sources",
                "--league", "47",
            ]
        )
        assert code in (0, 1)
        run_dir = next(out_dir.iterdir())
        assert (run_dir / "gaps.PARTIAL.jsonl").exists()
        lines = (run_dir / "gaps.PARTIAL.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            row = json.loads(line)
            assert "sources_consulted" in row
            assert "asset_a_json" not in row["sources_consulted"]

    def test_exit_2_when_core_db_missing(self, tmp_path):
        code = m.main(
            [
                "--core-db", str(tmp_path / "nope.db"),
                "--no-artifacts",
            ]
        )
        assert code == 2
