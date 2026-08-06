"""build_aws_gap_shards 的纯函数/端到端测试:临时文件 fixture,不依赖真实
gaps.jsonl/resolution_results.jsonl,不发网络请求。"""

from __future__ import annotations

import json
from pathlib import Path

from backend.cli import build_aws_gap_shards as m


class TestSeasonToDash:
    def test_valid(self):
        assert m.season_to_dash("2020/2021") == "2020-2021"

    def test_invalid(self):
        assert m.season_to_dash("garbage") is None
        assert m.season_to_dash("") is None


class TestBuildShards:
    def _gap(self, match_id=1, league_id=47, season="2020/2021"):
        return {
            "match_id": match_id, "league_id": league_id, "league_code": "epl",
            "season": season, "match_date_local": "2020-09-12",
            "home_team_name_en": "A", "away_team_name_en": "B",
            "home_score": 1, "away_score": 0,
        }

    def test_auto_ok_goes_to_ready(self):
        gaps = {1: self._gap(1)}
        res = {1: {"match_id": 1, "status": "auto_ok", "titan_id": "999",
                    "evidence_kind": "id", "detail": "team_id_dictionary", "home_away_inverted": 0,
                    "nowgoal_kickoff_local": "2020-09-12 19:30"}}
        shards = m.build_shards(gaps, res)
        key = "premier_league-2020-2021"
        assert key in shards
        assert len(shards[key]["ready"]) == 1
        assert shards[key]["ready"][0]["titan_id"] == "999"
        assert shards[key]["ready"][0]["nowgoal_kickoff_local"] == "2020-09-12 19:30"
        assert shards[key]["review"] == []

    def test_needs_review_goes_to_review(self):
        gaps = {1: self._gap(1)}
        res = {1: {"match_id": 1, "status": "needs_review", "titan_id": "999",
                    "evidence_kind": "name", "detail": "name_similarity=0.700", "home_away_inverted": 0}}
        shards = m.build_shards(gaps, res)
        key = "premier_league-2020-2021"
        assert shards[key]["ready"] == []
        assert len(shards[key]["review"]) == 1

    def test_missing_resolution_is_unresolved(self):
        gaps = {1: self._gap(1)}
        shards = m.build_shards(gaps, {})
        key = "premier_league-2020-2021"
        assert len(shards[key]["unresolved"]) == 1

    def test_shard_keys_split_by_league_and_season(self):
        gaps = {
            1: self._gap(1, league_id=47, season="2020/2021"),
            2: self._gap(2, league_id=54, season="2021/2022"),
        }
        res = {
            1: {"match_id": 1, "status": "auto_ok", "titan_id": "1", "evidence_kind": "id",
                "detail": "x", "home_away_inverted": 0},
            2: {"match_id": 2, "status": "auto_ok", "titan_id": "2", "evidence_kind": "id",
                "detail": "x", "home_away_inverted": 0},
        }
        shards = m.build_shards(gaps, res)
        assert set(shards) == {"premier_league-2020-2021", "bundesliga-2021-2022"}


class TestMainEndToEnd:
    def test_writes_ready_review_and_unresolved_files(self, tmp_path):
        gaps_path = tmp_path / "gaps.jsonl"
        gaps_path.write_text(
            "\n".join(
                json.dumps(g)
                for g in [
                    {"match_id": 1, "league_id": 47, "league_code": "epl", "season": "2020/2021",
                     "match_date_local": "2020-09-12", "home_team_name_en": "A", "away_team_name_en": "B",
                     "home_score": 1, "away_score": 0},
                    {"match_id": 2, "league_id": 47, "league_code": "epl", "season": "2020/2021",
                     "match_date_local": "2020-09-13", "home_team_name_en": "C", "away_team_name_en": "D",
                     "home_score": 0, "away_score": 0},
                    {"match_id": 3, "league_id": 47, "league_code": "epl", "season": "2020/2021",
                     "match_date_local": "2020-09-14", "home_team_name_en": "E", "away_team_name_en": "F",
                     "home_score": 2, "away_score": 1},
                ]
            ),
            encoding="utf-8",
        )
        res_path = tmp_path / "resolution_results.jsonl"
        res_path.write_text(
            "\n".join(
                json.dumps(r)
                for r in [
                    {"match_id": 1, "status": "auto_ok", "titan_id": "900",
                     "evidence_kind": "id", "detail": "x", "home_away_inverted": 0},
                    {"match_id": 2, "status": "no_candidate", "titan_id": None,
                     "evidence_kind": None, "detail": "no_surviving_candidate", "home_away_inverted": 0},
                    # match_id 3 缺失 resolution -> unresolved
                ]
            ),
            encoding="utf-8",
        )
        out_dir = tmp_path / "out"
        code = m.main(
            ["--gaps-file", str(gaps_path), "--resolution-file", str(res_path), "--output-dir", str(out_dir)]
        )
        assert code == 0
        run_dir = next(out_dir.iterdir())
        ready = (run_dir / "premier_league-2020-2021.ready.jsonl").read_text(encoding="utf-8").strip().splitlines()
        review = (run_dir / "premier_league-2020-2021.review.jsonl").read_text(encoding="utf-8").strip().splitlines()
        unresolved = (
            (run_dir / "premier_league-2020-2021.unresolved.jsonl").read_text(encoding="utf-8").strip().splitlines()
        )
        assert len(ready) == 1
        assert json.loads(ready[0])["titan_id"] == "900"
        assert len(review) == 1
        assert len(unresolved) == 1
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["gap_total"] == 3
        assert summary["shards"]["premier_league-2020-2021"] == {"ready": 1, "review": 1, "unresolved": 1}
