from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from backend.db import migrate
from backend.studio.team_style import (
    DOUYIN_SAFE_PROFILE,
    FORBIDDEN_SAFE_TERMS,
    TeamStyleError,
    assert_safe_content,
    build_douyin_safe_profile,
    build_team_style_profile,
    parse_team_style_artifact,
    record_team_style_profile,
)

FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "fotmob"
    / "team_style_pair.json"
)


def _payloads() -> tuple[dict, dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload["home"], payload["away"]


def _match() -> dict:
    return {
        "match_id": 5104968,
        "league_id": 59,
        "season": "2026",
        "round": "16",
        "kickoff_at_utc": "2026-07-31T17:00:00Z",
        "home": {
            "team_id": 8007,
            "name": "瓦勒伦加",
            "crest_url": "/api/v1/media/team-crests/8007/hash.png",
        },
        "away": {
            "team_id": 8448,
            "name": "汉坎",
            "crest_url": "/api/v1/media/team-crests/8448/hash.png",
        },
    }


def _profile() -> dict:
    home, away = _payloads()
    return build_team_style_profile(
        home_payload=home,
        away_payload=away,
        home_source_sha="a" * 64,
        away_source_sha="b" * 64,
        match=_match(),
        league_name_zh="挪威超",
        data_cutoff_at="2026-07-28T07:36:12Z",
        recent_form={"home": ["W", "D", "W", "L", "W"], "away": ["L", "D", "W", "L", "D"]},
    )


def test_real_shape_metrics_have_correct_units_and_denominators() -> None:
    profile = _profile()
    home = profile["teams"]["home"]
    assert home["played"] == 14
    assert home["metrics"]["possession"]["value"] == 49.3
    assert home["metrics"]["accurate_passes"]["value"] == 357.9
    assert home["metrics"]["xg_per_match"]["value"] == pytest.approx(1.71)
    assert home["metrics"]["box_touches_per_match"]["value"] == pytest.approx(32.3)
    assert home["metrics"]["corners_per_match"]["value"] == pytest.approx(7.1)
    assert home["metrics"]["set_piece_goals"]["value"] == 3
    assert home["metrics"]["set_piece_goals"]["label"] == "定位球进球"
    assert home["metrics"]["set_piece_goals"]["conversion"] == "total"
    assert home["metrics"]["xga_per_match"]["direction"] == "lower"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p["details"].__setitem__("id", 999), "identity mismatch"),
        (lambda p: p["details"].__setitem__("latestSeason", "2025"), "identity mismatch"),
        (lambda p: p["stats"]["tournamentSeasons"].clear(), "season mismatch"),
        (
            lambda p: p["stats"]["teams"].append(copy.deepcopy(p["stats"]["teams"][0])),
            "duplicate team metric",
        ),
        (
            lambda p: p["stats"]["teams"][0]["participant"].__setitem__("value", float("nan")),
            "invalid possession",
        ),
    ],
)
def test_parser_fails_closed_on_identity_duplicate_and_invalid_values(
    mutation, message: str
) -> None:
    home, _ = _payloads()
    mutation(home)
    with pytest.raises(TeamStyleError, match=message):
        parse_team_style_artifact(
            home,
            team_id=8007,
            team_name="瓦勒伦加",
            league_id=59,
            season="2026",
            crest_url=None,
        )


def test_missing_metric_is_omitted_not_zero_filled() -> None:
    home, _ = _payloads()
    home["stats"]["teams"] = [
        row for row in home["stats"]["teams"] if row["stat"] != "corner_taken_team"
    ]
    parsed = parse_team_style_artifact(
        home,
        team_id=8007,
        team_name="瓦勒伦加",
        league_id=59,
        season="2026",
        crest_url=None,
    )
    assert "corners_per_match" not in parsed["metrics"]
    assert "corners_per_match" in parsed["missing_metrics"]


def test_safe_profile_has_six_scenes_max_three_metrics_and_no_sensitive_surface() -> None:
    profile = _profile()
    # 媒体缓存可能晚于球队统计 profile 到达；安全视图应读取当前同源队徽，
    # 不要求改写 immutable 历史 profile。
    profile["teams"]["home"]["crest_url"] = None
    profile["teams"]["away"]["crest_url"] = None
    safe = build_douyin_safe_profile(
        profile,
        _match(),
        [{"kind": "lineup_unavailable", "text": "赛前阵容尚不可用"}],
    )
    assert safe["profile_id"] == DOUYIN_SAFE_PROFILE
    assert [scene["id"] for scene in safe["scenes"]] == [
        "cover",
        "possession",
        "threat",
        "width",
        "defense",
        "summary",
    ]
    assert all(len(scene["metrics"]) <= 3 for scene in safe["scenes"])
    assert safe["scenes"][3]["metrics"][2]["label"] == "定位球进球"
    assert safe["match"]["home"]["crest_url"] == _match()["home"]["crest_url"]
    assert safe["match"]["away"]["crest_url"] == _match()["away"]["crest_url"]
    serialized = json.dumps(safe, ensure_ascii=False)
    assert not any(term.lower() in serialized.lower() for term in FORBIDDEN_SAFE_TERMS)
    for forbidden_key in ("prediction", "odds", "market", "probability", "1x2"):
        assert forbidden_key not in serialized.lower()
    assert_safe_content(safe)


def test_safe_gate_rejects_text_and_nested_field_leaks() -> None:
    with pytest.raises(TeamStyleError):
        assert_safe_content({"text": "这里有赔率信息"})
    with pytest.raises(TeamStyleError):
        assert_safe_content({"nested": {"prediction_member": None}})


def test_profile_storage_is_append_only_idempotent_and_conflict_safe(tmp_path: Path) -> None:
    db = tmp_path / "platform.db"
    migrate.apply_all(
        "platform",
        db_file=db,
        migrations_dir=migrate.MIGRATIONS_ROOT / "platform",
        quiet=True,
    )
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    profile = _profile()
    conn.execute("BEGIN")
    assert record_team_style_profile(conn, profile) == "inserted"
    conn.execute("COMMIT")
    assert record_team_style_profile(conn, profile) == "skipped"

    changed = copy.deepcopy(profile)
    changed["teams"]["home"]["played"] = 15
    with pytest.raises(TeamStyleError, match="conflict"):
        record_team_style_profile(conn, changed)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE team_style_profiles SET season='2027'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM team_style_profiles")
    conn.close()
