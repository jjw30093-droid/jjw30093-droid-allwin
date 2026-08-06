"""nowgoal_historical_remap 驱动脚本的加载器测试:临时文件 fixture,不碰
miaomiaodi 或 runtime/research 里的真实文件,不依赖网络。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.cli import nowgoal_historical_remap as m


class TestIterScheduleRows:
    def test_flat_shape(self):
        schedule = {"R_1": [[1, 36, -1, "2020-09-12 19:30", 1, 2, "1-0"]]}
        rows = list(m._iter_schedule_rows(schedule))
        assert len(rows) == 1
        assert rows[0][0] == 1

    def test_nested_shape_regression(self):
        """真实回归:意甲 2020/21 archive 的 ScheduleList 比其它四个联赛多一层
        {"sub_2948": {"R_1": [...]}}——旧版解析器(只 for rows in schedule.values())
        会把这多出来的一层字典当成"行列表"直接吃掉,静默产出 0 行,而不报错。"""
        schedule = {"sub_2948": {"R_1": [[1, 34, -1, "2020-09-19 23:59", 176, 558, "1-0"]]}}
        rows = list(m._iter_schedule_rows(schedule))
        assert len(rows) == 1
        assert rows[0][0] == 1
        assert rows[0][1] == 34

    def test_empty_schedule(self):
        assert list(m._iter_schedule_rows({})) == []


class TestLoadGapMatches:
    def test_round_trip(self, tmp_path):
        gaps_path = tmp_path / "gaps.jsonl"
        row = {
            "match_id": 1, "league_id": 47, "match_date_local": "2021-08-14",
            "home_team_id": 100, "away_team_id": 200,
            "home_team_name_en": "Arsenal", "away_team_name_en": "Brentford",
            "home_score": 0, "away_score": 2,
        }
        gaps_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        matches = m.load_gap_matches(gaps_path)
        assert len(matches) == 1
        assert matches[0].match_id == 1
        assert matches[0].date == "2021-08-14"

    def test_skips_blank_lines(self, tmp_path):
        gaps_path = tmp_path / "gaps.jsonl"
        row = {
            "match_id": 1, "league_id": 47, "match_date_local": "2021-08-14",
            "home_team_id": 1, "away_team_id": 2,
            "home_team_name_en": "A", "away_team_name_en": "B",
            "home_score": 1, "away_score": 0,
        }
        gaps_path.write_text(json.dumps(row) + "\n\n", encoding="utf-8")
        assert len(m.load_gap_matches(gaps_path)) == 1


class TestLoadFixturesCandidates:
    def test_present(self, tmp_path):
        (tmp_path / "nowgoal_2122_fixtures.json").write_text(
            json.dumps(
                [
                    {
                        "titan_id": "999", "league_id_nowgoal": 36,
                        "kickoff_local": "2021-08-14 03:00",
                        "home_id": 19, "away_id": 365,
                        "home_name": "Arsenal", "away_name": "Brentford",
                        "score": "0-2",
                    }
                ]
            ),
            encoding="utf-8",
        )
        cands = m.load_fixtures_candidates(tmp_path, "2122")
        assert len(cands) == 1
        assert cands[0].titan_id == "999"
        assert cands[0].home_score == 0
        assert cands[0].away_score == 2

    def test_missing_file_returns_empty(self, tmp_path):
        assert m.load_fixtures_candidates(tmp_path, "9999") == []


class TestLoadArchiveCandidates:
    def test_parses_real_shape_including_nested(self, tmp_path):
        flat = {
            "TeamInfo": [[1, "Home FC"], [2, "Away FC"]],
            "ScheduleList": {"R_1": [[100, 36, -1, "2020-09-12 19:30", 1, 2, "1-0"]]},
        }
        nested = {
            "TeamInfo": [[3, "Nested Home"], [4, "Nested Away"]],
            "ScheduleList": {"sub_1": {"R_1": [[200, 34, -1, "2020-09-19 23:59", 3, 4, "2-1"]]}},
        }
        (tmp_path / "archive.premier_league.2020-2021.bin").write_text(
            json.dumps(flat), encoding="utf-8"
        )
        (tmp_path / "archive.serie_a.2020-2021.bin").write_text(
            json.dumps(nested), encoding="utf-8"
        )
        cands = m.load_archive_candidates(tmp_path)
        assert len(cands) == 2
        by_titan = {c.titan_id: c for c in cands}
        assert by_titan["100"].home_team_name == "Home FC"
        assert by_titan["200"].home_score == 2
        assert by_titan["200"].away_score == 1

    def test_missing_dir_returns_empty(self, tmp_path):
        assert m.load_archive_candidates(tmp_path / "nope") == []


class TestLoadTrainingPairs:
    def test_joins_mapping_fixtures_and_core_db(self, tmp_path):
        core_db = tmp_path / "core.db"
        conn = sqlite3.connect(core_db)
        conn.execute(
            "CREATE TABLE dim_match (Match_ID INTEGER, Home_Team_ID INTEGER, Away_Team_ID INTEGER,"
            " home_score INTEGER, away_score INTEGER)"
        )
        conn.execute("INSERT INTO dim_match VALUES (1, 100, 200, 0, 2)")
        conn.commit()
        conn.close()

        legacy_dir = tmp_path / "legacy"
        legacy_dir.mkdir()
        (legacy_dir / "nowgoal_2122_mapping.json").write_text(
            json.dumps({"1": {"titan_id": "999"}}), encoding="utf-8"
        )
        (legacy_dir / "nowgoal_2122_fixtures.json").write_text(
            json.dumps(
                [
                    {
                        "titan_id": "999", "league_id_nowgoal": 36,
                        "kickoff_local": "2021-08-14 03:00",
                        "home_id": 19, "away_id": 365, "score": "0-2",
                    }
                ]
            ),
            encoding="utf-8",
        )

        pairs = m.load_training_pairs(core_db, legacy_dir, legacy_dir)
        assert len(pairs) == 1
        p = pairs[0]
        assert p.ng_home_id == 19 and p.ng_away_id == 365
        assert p.fm_home_id == 100 and p.fm_away_id == 200
        assert p.ng_home_score == 0 and p.fm_home_score == 0

    def test_missing_fixture_for_titan_skipped(self, tmp_path):
        core_db = tmp_path / "core.db"
        conn = sqlite3.connect(core_db)
        conn.execute(
            "CREATE TABLE dim_match (Match_ID INTEGER, Home_Team_ID INTEGER, Away_Team_ID INTEGER,"
            " home_score INTEGER, away_score INTEGER)"
        )
        conn.execute("INSERT INTO dim_match VALUES (1, 100, 200, 0, 2)")
        conn.commit()
        conn.close()
        legacy_dir = tmp_path / "legacy"
        legacy_dir.mkdir()
        (legacy_dir / "nowgoal_2122_mapping.json").write_text(
            json.dumps({"1": {"titan_id": "no-such-titan"}}), encoding="utf-8"
        )
        (legacy_dir / "nowgoal_2122_fixtures.json").write_text(json.dumps([]), encoding="utf-8")
        pairs = m.load_training_pairs(core_db, legacy_dir, legacy_dir)
        assert pairs == []


class TestMainEndToEnd:
    def test_runs_and_writes_artifacts(self, tmp_path):
        core_db = tmp_path / "core.db"
        conn = sqlite3.connect(core_db)
        conn.execute(
            "CREATE TABLE dim_match (Match_ID INTEGER, Home_Team_ID INTEGER, Away_Team_ID INTEGER,"
            " home_score INTEGER, away_score INTEGER)"
        )
        conn.execute("INSERT INTO dim_match VALUES (1, 100, 200, 0, 2)")
        conn.commit()
        conn.close()

        legacy_dir = tmp_path / "legacy"
        legacy_dir.mkdir()
        # 10 条训练配对(fotmob_id 1000..1009,与下面要解析的 gap match_id=1 不冲突,
        # 比分各不相同且都不等于 gap 比赛的 0-2,避免被误当成 gap 比赛本身的候选),
        # 达到 min_votes 门槛,让球队 id 词典真的学到 19->100 / 365->200。
        (legacy_dir / "nowgoal_2122_mapping.json").write_text(
            json.dumps({str(1000 + i): {"titan_id": str(900 + i)} for i in range(10)}), encoding="utf-8"
        )
        training_fixtures = [
            {
                "titan_id": str(900 + i), "league_id_nowgoal": 36,
                "kickoff_local": "2021-01-01 03:00",
                "home_id": 19, "away_id": 365, "score": f"{5 + i}-{7 + i}",
            }
            for i in range(10)
        ]
        # 真正要解析的 gap 比赛(match_id=1)的候选,比分与训练数据不重叠。
        gap_candidate = {
            "titan_id": "999", "league_id_nowgoal": 36,
            "kickoff_local": "2021-08-14 03:00",
            "home_id": 19, "away_id": 365, "score": "0-2",
        }
        (legacy_dir / "nowgoal_2122_fixtures.json").write_text(
            json.dumps(training_fixtures + [gap_candidate]), encoding="utf-8"
        )
        conn = sqlite3.connect(core_db)
        for i in range(10):
            conn.execute(
                "INSERT INTO dim_match VALUES (?, 100, 200, ?, ?)", (1000 + i, 5 + i, 7 + i)
            )
        conn.commit()
        conn.close()

        gaps_path = tmp_path / "gaps.jsonl"
        gaps_path.write_text(
            json.dumps(
                {
                    "match_id": 1, "league_id": 47, "match_date_local": "2021-08-14",
                    "home_team_id": 100, "away_team_id": 200,
                    "home_team_name_en": "Arsenal", "away_team_name_en": "Brentford",
                    "home_score": 0, "away_score": 2,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        out_dir = tmp_path / "out"
        archive_dir = tmp_path / "archive_empty"
        code = m.main(
            [
                "--gaps-file", str(gaps_path),
                "--core-db", str(core_db),
                "--legacy-mapping-dir", str(legacy_dir),
                "--fixtures-dir", str(legacy_dir),
                "--archive-dir", str(archive_dir),
                "--output-dir", str(out_dir),
            ]
        )
        assert code == 0
        run_dir = next(out_dir.iterdir())
        assert (run_dir / "resolution_results.jsonl").exists()
        assert (run_dir / "summary.json").exists()
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["gap_total"] == 1
        assert summary["status_counts"].get("auto_ok") == 1
        result_row = json.loads((run_dir / "resolution_results.jsonl").read_text(encoding="utf-8").strip())
        assert result_row["nowgoal_kickoff_local"] == "2021-08-14 03:00"
