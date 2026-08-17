"""Real active-league MVP assembly from saved provider artifacts.

This module is intentionally narrow: it turns the already captured FotMob and
NowGoal responses for one match into the existing core/platform/odds schemas.
It never opens a network connection and only writes to ``ALLWIN_DATA_DIR``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.commands.predictions import (
    get_or_create_model_version,
    publish_snapshot,
    register_snapshot,
    supersede_snapshot,
)
from backend.db.connections import connect_rw, tx
from backend.db.util import new_uuid, utc_now_iso
from backend.fotmob_client import FotMobClient
from backend.ingest.odds_snapshots import ingest_odds_records
from backend.providers.nowgoal import parse_odds
from backend.queries import matches as q_matches
from backend.studio.bundle import build_analysis_bundle, render_srt
from backend.studio.team_style import (
    DOUYIN_SAFE_PROFILE,
    build_team_style_profile,
    record_team_style_profile,
)


LEAGUE_ID = 59
LEAGUE_CODE = "eliteserien"
LEAGUE_NAME = "Eliteserien"
LEAGUE_NAME_ZH = "挪威超"
SEASON = "2026"
MATCH_ID = 5104968
NOWGOAL_MATCH_ID = "2912857"
HOME_ID = 8007
AWAY_ID = 8448
TEAM_ZH = {
    HOME_ID: ("Vålerenga", "瓦勒伦加"),
    AWAY_ID: ("Hamarkameratene", "汉坎"),
}
MODEL_VERSION = "market-baseline-nowgoal-v1"


@dataclass(frozen=True)
class ActiveLeagueContext:
    league_id: int
    league_code: str
    league_name: str
    league_name_zh: str
    season: str
    match_id: int
    nowgoal_match_id: str
    home_id: int
    away_id: int
    home_name_en: str
    away_name_en: str
    home_name_zh: str
    away_name_zh: str
    nowgoal_home_id: str | None = None
    nowgoal_away_id: str | None = None


DEFAULT_CONTEXT = ActiveLeagueContext(
    league_id=LEAGUE_ID,
    league_code=LEAGUE_CODE,
    league_name=LEAGUE_NAME,
    league_name_zh=LEAGUE_NAME_ZH,
    season=SEASON,
    match_id=MATCH_ID,
    nowgoal_match_id=NOWGOAL_MATCH_ID,
    home_id=HOME_ID,
    away_id=AWAY_ID,
    home_name_en=TEAM_ZH[HOME_ID][0],
    away_name_en=TEAM_ZH[AWAY_ID][0],
    home_name_zh=TEAM_ZH[HOME_ID][1],
    away_name_zh=TEAM_ZH[AWAY_ID][1],
    nowgoal_home_id="485",
    nowgoal_away_id="496",
)


class ActiveLeagueMVPError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActiveLeagueMVPError(f"invalid MVP artifact: {path.name}") from None
    if not isinstance(value, dict):
        raise ActiveLeagueMVPError(f"invalid MVP artifact object: {path.name}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _score(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fixture_row(
    item: dict,
    *,
    default_league: int = LEAGUE_ID,
    season: str = SEASON,
) -> dict | None:
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    kickoff = _utc(status.get("utcTime"))
    home = item.get("home") if isinstance(item.get("home"), dict) else {}
    away = item.get("away") if isinstance(item.get("away"), dict) else {}
    tournament = item.get("tournament") if isinstance(item.get("tournament"), dict) else {}
    match_id = item.get("id")
    if not match_id or not kickoff or not home.get("id") or not away.get("id"):
        return None
    if status.get("cancelled"):
        match_status = "Cancelled"
    elif status.get("finished"):
        match_status = "Finish"
    elif status.get("started"):
        match_status = "InPlay"
    else:
        match_status = "NotStarted"
    return {
        "Match_ID": int(match_id),
        "Season": season,
        "League_ID": int(tournament.get("leagueId") or default_league),
        "Date": kickoff[:10],
        "Home_Team_ID": int(home["id"]),
        "Away_Team_ID": int(away["id"]),
        "Home_Team_Name": str(home.get("name") or ""),
        "Away_Team_Name": str(away.get("name") or ""),
        "home_score": _score(home.get("score")) if match_status == "Finish" else None,
        "away_score": _score(away.get("score")) if match_status == "Finish" else None,
        "status": match_status,
        "Match_Round": str(item.get("round")) if item.get("round") is not None else None,
        "kickoff_at_utc": kickoff,
        "kickoff_precision": "exact",
        "kickoff_source": "fotmob:league_or_team_fixture",
    }


def _insert_match(conn: sqlite3.Connection, row: dict) -> None:
    columns = [r[1] for r in conn.execute("PRAGMA table_info(dim_match)")]
    values = {key: value for key, value in row.items() if key in columns}
    names = list(values)
    placeholders = ",".join("?" for _ in names)
    conn.execute(
        f"INSERT OR REPLACE INTO dim_match ({','.join(names)}) VALUES ({placeholders})",
        [values[name] for name in names],
    )


def _league_fixture_rows(
    league: dict, context: ActiveLeagueContext = DEFAULT_CONTEXT
) -> list[dict]:
    fixtures = league.get("fixtures") if isinstance(league.get("fixtures"), dict) else {}
    rows = fixtures.get("allMatches") if isinstance(fixtures.get("allMatches"), list) else []
    return [
        mapped
        for item in rows
        if isinstance(item, dict)
        and (
            mapped := _fixture_row(
                item,
                default_league=context.league_id,
                season=context.season,
            )
        )
    ]


def _team_fixture_rows(
    team: dict, context: ActiveLeagueContext = DEFAULT_CONTEXT
) -> list[dict]:
    fixtures = team.get("fixtures") if isinstance(team.get("fixtures"), dict) else {}
    all_fixtures = fixtures.get("allFixtures") if isinstance(fixtures.get("allFixtures"), dict) else {}
    rows = all_fixtures.get("fixtures") if isinstance(all_fixtures.get("fixtures"), list) else []
    mapped = [
        row
        for item in rows
        if isinstance(item, dict)
        and (
            row := _fixture_row(
                item,
                default_league=context.league_id,
                season=context.season,
            )
        )
    ]
    finished = [row for row in mapped if row["status"] == "Finish"]
    return sorted(finished, key=lambda row: row["kickoff_at_utc"])[-8:]


def _insert_table(
    conn: sqlite3.Connection,
    league: dict,
    context: ActiveLeagueContext = DEFAULT_CONTEXT,
) -> int:
    parser = FotMobClient(proxy="", max_retries=1)
    rows = parser.parse_league_table(
        league, context.league_id, context.season
    )
    conn.execute(
        "DELETE FROM fact_league_table WHERE League_ID=? AND Season=?",
        (context.league_id, context.season),
    )
    columns = [r[1] for r in conn.execute("PRAGMA table_info(fact_league_table)")]
    for row in rows:
        clean = dict(row)
        clean["extra_json"] = json.dumps(clean.pop("extra", {}), ensure_ascii=False, sort_keys=True)
        values = {key: value for key, value in clean.items() if key in columns}
        names = list(values)
        conn.execute(
            f"INSERT INTO fact_league_table ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
            [values[name] for name in names],
        )
    return len(rows)


def _market_probabilities(records: list[dict]) -> tuple[dict, dict]:
    selected = next(
        (
            row
            for row in records
            if row.get("market") == "1x2"
            and row.get("company_id") == "8"
            and isinstance(row.get("latest"), dict)
        ),
        None,
    )
    if selected is None:
        raise ActiveLeagueMVPError("real 1x2 market is unavailable")
    latest = selected["latest"]
    inverse = {key: 1.0 / float(latest[key]) for key in ("home", "draw", "away")}
    total = sum(inverse.values())
    probabilities = {key: value / total for key, value in inverse.items()}
    return probabilities, selected


def _script_cues(sections: list[dict]) -> list[dict]:
    cues: list[dict] = []
    cursor = 0.0
    for section in sections:
        for sentence in (
            text.strip()
            for text in section["text"].replace("。", "。|").split("|")
            if text.strip()
        ):
            duration = max(2.0, min(6.0, len(sentence) * 0.14))
            cues.append(
                {"start": round(cursor, 1), "end": round(cursor + duration, 1), "text": sentence}
            )
            cursor += duration
    return cues


def _recent_form(rows: list[dict], team_id: int) -> dict:
    selected = sorted(
        (
            row
            for row in rows
            if team_id in (row["Home_Team_ID"], row["Away_Team_ID"])
            and row["home_score"] is not None
            and row["away_score"] is not None
        ),
        key=lambda row: (row["kickoff_at_utc"], row["Match_ID"]),
    )[-5:]
    wins = draws = losses = goals_for = goals_against = 0
    for row in selected:
        is_home = row["Home_Team_ID"] == team_id
        scored = int(row["home_score"] if is_home else row["away_score"])
        conceded = int(row["away_score"] if is_home else row["home_score"])
        goals_for += scored
        goals_against += conceded
        if scored > conceded:
            wins += 1
        elif scored == conceded:
            draws += 1
        else:
            losses += 1
    return {
        "matches": len(selected),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "points": wins * 3 + draws,
    }


def _standing_text(name: str, row: dict) -> str:
    required = (
        "position",
        "played",
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
    )
    if any(row.get(key) is None for key in required):
        return f"{name}的积分排名当前 UNAVAILABLE"
    return (
        f"{name}{row['played']}场{row['wins']}胜{row['draws']}平"
        f"{row['losses']}负，进{row['goals_for']}球失{row['goals_against']}球，"
        f"暂列第{row['position']}"
    )


def _xg_text(
    home_name: str,
    away_name: str,
    home_xg: dict,
    away_xg: dict,
) -> str:
    values = (
        home_xg.get("xg"),
        home_xg.get("xg_conceded"),
        away_xg.get("xg"),
        away_xg.get("xg_conceded"),
    )
    if any(value is None for value in values):
        return "当前赛季 xG/xGA 有字段缺失，页面如实标记 UNAVAILABLE"
    return (
        f"当前赛季累计预期进球，{home_name}为{float(values[0]):.2f}，"
        f"预期失球{float(values[1]):.2f}；{away_name}预期进球"
        f"{float(values[2]):.2f}，预期失球{float(values[3]):.2f}"
    )


def apply_saved_artifacts(
    artifact_dir: Path,
    output_dir: Path,
    *,
    artifact_paths: dict[str, Path] | None = None,
    observed_at: str | None = None,
    style_cutoff_at: str | None = None,
    free_outcome: str = "home",
    context: ActiveLeagueContext = DEFAULT_CONTEXT,
) -> dict:
    paths = artifact_paths or {
        "league": artifact_dir / "fotmob-league-59.json",
        "match": artifact_dir / "fotmob-match-5104968.json",
        "home": artifact_dir / "fotmob-team-8007.json",
        "away": artifact_dir / "fotmob-team-8448.json",
        "odds": artifact_dir / "nowgoal-odds-2912857.json",
    }
    if free_outcome not in {"home", "draw", "away"}:
        raise ActiveLeagueMVPError("invalid free outcome")
    missing = [path.name for path in paths.values() if not path.is_file()]
    if missing:
        raise ActiveLeagueMVPError(f"missing MVP artifacts: {','.join(missing)}")

    league = _read_json(paths["league"])
    match = _read_json(paths["match"])
    home = _read_json(paths["home"])
    away = _read_json(paths["away"])
    odds_payload = _read_json(paths["odds"])

    details = league.get("details") if isinstance(league.get("details"), dict) else {}
    if (
        details.get("name") != context.league_name
        or str(details.get("selectedSeason")) != context.season
    ):
        raise ActiveLeagueMVPError("competition or season mismatch")
    general = match.get("general") if isinstance(match.get("general"), dict) else {}
    if int(general.get("matchId") or context.match_id) != context.match_id:
        raise ActiveLeagueMVPError("sample match mismatch")

    fixture_rows = _league_fixture_rows(league, context)
    sample = next(
        (row for row in fixture_rows if row["Match_ID"] == context.match_id), None
    )
    if sample is None:
        raise ActiveLeagueMVPError("sample match absent from league artifact")
    if (
        sample["Home_Team_ID"] != context.home_id
        or sample["Away_Team_ID"] != context.away_id
    ):
        raise ActiveLeagueMVPError("sample team direction mismatch")

    recent_rows = _team_fixture_rows(home, context) + _team_fixture_rows(
        away, context
    )
    with connect_rw("core") as conn:
        with tx(conn):
            for row in fixture_rows + recent_rows:
                _insert_match(conn, row)
            team_names = (
                (context.home_id, context.home_name_en, context.home_name_zh),
                (context.away_id, context.away_name_en, context.away_name_zh),
            )
            for team_id, name_en, name_zh in team_names:
                conn.execute(
                    """INSERT OR REPLACE INTO dim_team_i18n
                       (Team_ID,name_en,name_zh,source,updated_at) VALUES (?,?,?,?,?)""",
                    (
                        team_id,
                        name_en,
                        name_zh,
                        "real_fotmob_or_reviewed_zh",
                        utc_now_iso(),
                    ),
                )
            table_rows = _insert_table(conn, league, context)

    records = parse_odds(odds_payload)
    probabilities, selected_1x2 = _market_probabilities(records)
    observed_at = observed_at or datetime.fromtimestamp(
        paths["odds"].stat().st_mtime, timezone.utc
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with connect_rw("odds") as conn:
        with tx(conn):
            now = utc_now_iso()
            conn.execute(
                """INSERT INTO dim_match_xref
                   (fotmob_match_id,provider,provider_match_id,home_away_inverted,
                    confidence,verified,method,kickoff_diff_seconds,review_status,created_at,updated_at)
                   VALUES (?,?,?,?,1.0,1,'manual',0,'confirmed',?,?)
                   ON CONFLICT(provider,provider_match_id) DO UPDATE SET
                     fotmob_match_id=excluded.fotmob_match_id,
                     home_away_inverted=0,confidence=1.0,verified=1,
                     kickoff_diff_seconds=0,review_status='confirmed',updated_at=excluded.updated_at""",
                (
                    context.match_id,
                    "nowgoal",
                    context.nowgoal_match_id,
                    0,
                    now,
                    now,
                ),
            )
            team_xrefs = (
                (context.nowgoal_home_id, context.home_id),
                (context.nowgoal_away_id, context.away_id),
            )
            for provider_id, canonical_id in team_xrefs:
                if not provider_id:
                    continue
                conn.execute(
                    """INSERT INTO dim_team_xref
                       (provider,provider_team_id,canonical_team_id,confidence,verified,method,created_at,updated_at)
                       VALUES ('nowgoal',?,?,1.0,1,'manual',?,?)
                       ON CONFLICT(provider,provider_team_id) DO UPDATE SET
                         canonical_team_id=excluded.canonical_team_id,confidence=1.0,
                         verified=1,updated_at=excluded.updated_at""",
                    (provider_id, canonical_id, now, now),
                )
        odds_counts = ingest_odds_records(
            conn,
            context.nowgoal_match_id,
            records,
            observed_at,
            poll_run_id="active-league-mvp",
            market_phase="pre_match",
        )

    with connect_rw("platform") as conn:
        get_or_create_model_version(
            conn,
            MODEL_VERSION,
            "market_baseline",
            description="NowGoal Bet365 latest 1X2 de-vig baseline; not a proprietary model",
            params={"company_id": "8", "method": "inverse_odds_normalized"},
            applicable_league_ids=[context.league_id],
        )
        existing = conn.execute(
            """SELECT id,home_win,draw,away_win FROM prediction_snapshots
               WHERE match_id=? AND model_version_id=? AND superseded_by IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (context.match_id, MODEL_VERSION),
        ).fetchone()
        probability_tuple = (
            probabilities["home"],
            probabilities["draw"],
            probabilities["away"],
        )
        if existing is None:
            snapshot_id = register_snapshot(
                conn,
                match_id=context.match_id,
                kickoff_at_utc=sample["kickoff_at_utc"],
                kickoff_precision="exact",
                kickoff_source="fotmob:league_fixture",
                league_id=context.league_id,
                model_version_id=MODEL_VERSION,
                home_win=probabilities["home"],
                draw=probabilities["draw"],
                away_win=probabilities["away"],
                generated_at=observed_at,
                input_cutoff_at=observed_at,
                input_snapshot_hash=_sha256(paths["odds"]),
                confidence="market_baseline",
            )
            publish_snapshot(conn, snapshot_id, actor=None)
            conn.commit()
        elif all(
            abs(float(existing[key]) - probabilities[outcome]) < 0.000001
            for key, outcome in (
                ("home_win", "home"),
                ("draw", "draw"),
                ("away_win", "away"),
            )
        ):
            snapshot_id = existing["id"]
        else:
            snapshot_id = supersede_snapshot(
                conn,
                existing["id"],
                actor=None,
                home_win=probability_tuple[0],
                draw=probability_tuple[1],
                away_win=probability_tuple[2],
                generated_at=observed_at,
                input_cutoff_at=observed_at,
                input_snapshot_hash=_sha256(paths["odds"]),
                confidence="market_baseline",
            )
            publish_snapshot(conn, snapshot_id, actor=None)
            conn.commit()

    core = connect_rw("core")
    platform = connect_rw("platform")
    odds = connect_rw("odds")
    try:
        bundle = build_analysis_bundle(
            core, platform, odds, context.match_id
        )
    finally:
        core.close()
        platform.close()
        odds.close()
    if bundle is None:
        raise ActiveLeagueMVPError("analysis bundle was not built")

    # 直接查"近期战绩"聚合,不再从 bundle["chart_specs"] 里按 id 回捞
    # form_compare(该 chart_spec 已从 backend/studio/bundle.py 删除)。
    with connect_rw("core") as conn:
        home_form_result = q_matches.recent_form(conn, context.home_id, bundle["match"]["date_utc"])
        away_form_result = q_matches.recent_form(conn, context.away_id, bundle["match"]["date_utc"])
    style_profile = build_team_style_profile(
        home_payload=home,
        away_payload=away,
        home_source_sha=_sha256(paths["home"]),
        away_source_sha=_sha256(paths["away"]),
        match=bundle["match"],
        league_name_zh=context.league_name_zh,
        data_cutoff_at=style_cutoff_at or observed_at,
        recent_form={
            "home": [f["result"] for f in home_form_result][:5],
            "away": [f["result"] for f in away_form_result][:5],
        },
    )
    with connect_rw("platform") as conn:
        with tx(conn):
            style_profile_result = record_team_style_profile(conn, style_profile)

    core = connect_rw("core")
    platform = connect_rw("platform")
    odds = connect_rw("odds")
    try:
        bundle = build_analysis_bundle(core, platform, odds, context.match_id)
    finally:
        core.close()
        platform.close()
        odds.close()
    if bundle is None or DOUYIN_SAFE_PROFILE not in bundle.get("social_profiles", {}):
        raise ActiveLeagueMVPError("douyin-safe Studio profile was not built")

    with connect_rw("odds") as conn:
        odds_observation_count = conn.execute(
            """SELECT COUNT(DISTINCT observed_at) FROM bronze_ng_odds_snap
               WHERE provider_match_id=? AND market='1x2'""",
            (context.nowgoal_match_id,),
        ).fetchone()[0]

    with connect_rw("core") as conn:
        standings = {
            row["Team_ID"]: dict(row)
            for row in conn.execute(
                """SELECT Team_ID,position,played,wins,draws,losses,goals_for,
                          goals_against,points,xg,xg_conceded
                   FROM fact_league_table
                   WHERE League_ID=? AND Season=? AND table_type='all'
                     AND Team_ID IN (?,?)""",
                (
                    context.league_id,
                    context.season,
                    context.home_id,
                    context.away_id,
                ),
            )
        }
        xg_rows = {
            row["Team_ID"]: dict(row)
            for row in conn.execute(
                """SELECT Team_ID,xg,xg_conceded,x_points FROM fact_league_table
                   WHERE League_ID=? AND Season=? AND table_type='xg'
                     AND Team_ID IN (?,?)""",
                (
                    context.league_id,
                    context.season,
                    context.home_id,
                    context.away_id,
                ),
            )
        }
    home_table = standings.get(context.home_id, {})
    away_table = standings.get(context.away_id, {})
    home_xg = xg_rows.get(context.home_id, {})
    away_xg = xg_rows.get(context.away_id, {})
    home_form = _recent_form(recent_rows, context.home_id)
    away_form = _recent_form(recent_rows, context.away_id)
    rest_point = next(
        (item["text"] for item in bundle["evidence"] if item.get("kind") == "rest"),
        "两队休息时间数据不可用",
    )
    latest = selected_1x2["latest"]
    initial = selected_1x2["initial"]
    kickoff_cn = datetime.fromisoformat(
        sample["kickoff_at_utc"].replace("Z", "+00:00")
    ).astimezone(ZoneInfo("Asia/Shanghai"))
    kickoff_text = kickoff_cn.strftime("%m月%d日 %H:%M")
    round_text = (
        f"第{sample['Match_Round']}轮"
        if sample.get("Match_Round")
        else "轮次待定"
    )
    home_standing = _standing_text(context.home_name_zh, home_table)
    away_standing = _standing_text(context.away_name_zh, away_table)
    xg_summary = _xg_text(
        context.home_name_zh,
        context.away_name_zh,
        home_xg,
        away_xg,
    )
    form_summary = (
        f"最近{home_form['matches']}场，{context.home_name_zh}"
        f"{home_form['wins']}胜{home_form['draws']}平{home_form['losses']}负，"
        f"进{home_form['goals_for']}球失{home_form['goals_against']}球；"
        f"{context.away_name_zh}最近{away_form['matches']}场"
        f"{away_form['wins']}胜{away_form['draws']}平{away_form['losses']}负，"
        f"进{away_form['goals_for']}球失{away_form['goals_against']}球"
    )
    if isinstance(initial, dict):
        market_summary = (
            f"同一份 NowGoal 响应标注的 Bet365 初盘为"
            f"{initial['home']:.2f}/{initial['draw']:.2f}/{initial['away']:.2f}，"
            f"当前值为{latest['home']:.2f}/{latest['draw']:.2f}/{latest['away']:.2f}。"
            "这不是本系统已经连续观察到的赔率变化"
        )
    else:
        market_summary = (
            f"Bet365 当前 1X2 为{latest['home']:.2f}/"
            f"{latest['draw']:.2f}/{latest['away']:.2f}，来源没有提供可用初盘"
        )
    sections = [
        {
            "id": "hook",
            "title": "比赛与开球",
            "text": (
                f"北京时间{kickoff_text}，{context.league_name_zh}"
                f"{round_text}，{context.home_name_zh}主场对阵"
                f"{context.away_name_zh}。"
                "这是来自 FotMob 赛程与 NowGoal 同场赔率的真实赛前数据快照。"
            ),
        },
        {
            "id": "form",
            "title": "近期状态",
            "text": (
                f"积分榜上，{home_standing}；{away_standing}。{form_summary}。"
            ),
        },
        {
            "id": "advanced",
            "title": "高阶数据与休息",
            "text": (
                f"{xg_summary}。"
                f"{rest_point}。这两项都来自赛前可见数据，不用零值代替缺失。"
            ),
        },
        {
            "id": "market",
            "title": "盘口观察",
            "text": (
                f"{market_summary}。目前只有{odds_observation_count}个系统观测点，"
                "两个及以上真实观测点才展示赔率变化。"
            ),
        },
        {
            "id": "probability",
            "title": "市场基线",
            "text": (
                f"把最新一乘二赔率做简单去水后，市场基线约为主胜{probabilities['home'] * 100:.1f}%，"
                f"平局{probabilities['draw'] * 100:.1f}%，客胜{probabilities['away'] * 100:.1f}%。"
                "注意，这是 MARKET_BASELINE，不是本站自有模型预测。免费页面只公开其中最高一项，"
                "另外两项不会下发到公开接口。"
            ),
        },
        {
            "id": "risk",
            "title": "风险与结尾",
            "text": (
                "距离开球仍有时间，阵容、伤停和盘口都可能继续变化；"
                "缺失字段不以零值代替。"
                "以上只是数据分析，不构成投注建议。关注公众号，获取每日比赛数据分析和完整更新。"
            ),
        },
    ]
    cues = _script_cues(sections)
    bundle["script_sections"] = sections
    bundle["subtitle_cues"] = cues
    bundle["analysis_points"] = [
        f"积分：{home_standing}；{away_standing}",
        f"近期：{form_summary}",
        f"高阶数据：{xg_summary}",
        f"Bet365 当前主胜 {latest['home']:.2f}",
        f"MARKET_BASELINE 主胜 {probabilities['home'] * 100:.1f}%",
    ]
    bundle["bundle_hash"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in bundle.items() if k not in {"built_at", "bundle_hash"}},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / f"match-{context.match_id}-analysis-bundle.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    voice_path = output_dir / f"match-{context.match_id}-voiceover.md"
    voice_path.write_text(
        "# 60—90秒中文口播稿\n\n"
        + "\n\n".join(f"## {section['title']}\n\n{section['text']}" for section in sections)
        + "\n",
        encoding="utf-8",
    )
    srt_path = output_dir / f"match-{context.match_id}.srt"
    srt_path.write_text(render_srt(bundle, {"subtitle_cues": cues}), encoding="utf-8")
    titles_path = output_dir / f"match-{context.match_id}-titles.txt"
    titles_path.write_text(
        "\n".join(
            [
                f"{context.home_name_zh}vs{context.away_name_zh}：市场基线怎么看？",
                f"{context.league_name_zh}数据前瞻：近期状态、xG与1X2",
                f"主胜当前{latest['home']:.2f}，这场比赛有哪些不确定性？",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    article_path = output_dir / f"match-{context.match_id}-wechat-summary.md"
    article_path.write_text(
        "# 公众号文章摘要\n\n"
        f"{context.home_name_zh}将在北京时间{kickoff_text}主场迎战"
        f"{context.away_name_zh}。真实赛季数据中，{home_standing}；"
        f"{away_standing}。{form_summary}。{xg_summary}。"
        "Bet365当前1X2去水后，"
        f"市场基线为主胜{probabilities['home'] * 100:.1f}%、"
        f"平局{probabilities['draw'] * 100:.1f}%、客胜{probabilities['away'] * 100:.1f}%。"
        f"这不是自有模型预测，当前系统保存{odds_observation_count}个观测点。"
        "完整分析需结合赛前阵容和后续盘口更新。\n\n"
        f"数据来源：FotMob、NowGoal；数据观察时间：{observed_at}。\n",
        encoding="utf-8",
    )

    safe = bundle["social_profiles"][DOUYIN_SAFE_PROFILE]
    safe_slug = (
        f"{DOUYIN_SAFE_PROFILE}_{context.match_id}_"
        f"cutoff-{safe['data_cutoff_at'][:10]}_{safe['source_hash'][:12]}"
    )
    safe_bundle_path = output_dir / f"{safe_slug}.json"
    safe_bundle_path.write_text(
        json.dumps(safe, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    safe_voice_path = output_dir / f"{safe_slug}-voiceover.md"
    safe_voice_path.write_text(
        "# 45—60秒球队打法口播稿\n\n"
        + "\n\n".join(
            f"## {section['title']}\n\n{section['text']}"
            for section in safe["script_sections"]
        )
        + "\n",
        encoding="utf-8",
    )
    safe_srt_path = output_dir / f"{safe_slug}.srt"
    safe_srt_path.write_text(
        render_srt(safe, {"subtitle_cues": safe["subtitle_cues"]}),
        encoding="utf-8",
    )
    safe_titles_path = output_dir / f"{safe_slug}-titles.txt"
    safe_titles_path.write_text("\n".join(safe["titles"]) + "\n", encoding="utf-8")
    safe_xhs_path = output_dir / f"{safe_slug}-xiaohongshu.md"
    safe_xhs_path.write_text(safe["xiaohongshu_text"] + "\n", encoding="utf-8")
    safe_wechat_path = output_dir / f"{safe_slug}-wechat-summary.md"
    safe_wechat_path.write_text(safe["wechat_summary"] + "\n", encoding="utf-8")

    return {
        "league": {
            "id": context.league_id,
            "name": context.league_name,
            "name_zh": context.league_name_zh,
            "season": context.season,
        },
        "match": {
            "fotmob_id": context.match_id,
            "nowgoal_id": context.nowgoal_match_id,
            "kickoff_at_utc": sample["kickoff_at_utc"],
            "home": context.home_name_zh,
            "away": context.away_name_zh,
        },
        "fixture_rows": len(fixture_rows),
        "recent_rows": len({row["Match_ID"] for row in recent_rows}),
        "table_rows": table_rows,
        "odds": odds_counts,
        "market_baseline": {
            "source": "MARKET_BASELINE",
            "company": selected_1x2["company_name"],
            "home": round(probabilities["home"], 6),
            "draw": round(probabilities["draw"], 6),
            "away": round(probabilities["away"], 6),
            "observed_at": observed_at,
        },
        "free_outcome": free_outcome,
        "odds_observation_count": odds_observation_count,
        "odds_display_mode": (
            "current_odds" if odds_observation_count < 2 else "odds_changes"
        ),
        "bundle_path": str(bundle_path),
        "studio_files": [
            str(bundle_path),
            str(voice_path),
            str(srt_path),
            str(titles_path),
            str(article_path),
            str(safe_bundle_path),
            str(safe_voice_path),
            str(safe_srt_path),
            str(safe_titles_path),
            str(safe_xhs_path),
            str(safe_wechat_path),
        ],
        "team_style_profile": style_profile_result,
        "douyin_safe_profile": {
            "profile_id": DOUYIN_SAFE_PROFILE,
            "source_hash": safe["source_hash"],
            "scene_count": len(safe["scenes"]),
            "bundle_path": str(safe_bundle_path),
        },
        "snapshot_id": snapshot_id,
    }
