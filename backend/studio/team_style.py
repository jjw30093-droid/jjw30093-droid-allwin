"""Versioned football team-style profiles and Douyin-safe Studio content.

The source is an already persisted FotMob ``team_data`` artifact.  This module
never performs network I/O and never reads betting/prediction data.  The safe
view model is deliberately constructed from a small allowlist so prediction,
odds, market and recommendation fields cannot leak by serialization.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from backend.db.util import new_uuid, utc_now_iso

PROFILE_VERSION = "team-style-v1"
DOUYIN_SAFE_PROFILE = "douyin-safe-v1"

FORBIDDEN_SAFE_TERMS = (
    "胜平负",
    "主胜",
    "客胜",
    "平局",
    "赔率",
    "盘口",
    "水位",
    "投注",
    "推荐",
    "稳胆",
    "命中率",
    "收益",
    "红单",
    "1X2",
    "MARKET_BASELINE",
)


class TeamStyleError(RuntimeError):
    """Fixed, non-provider-facing error for invalid style artifacts."""


@dataclass(frozen=True)
class MetricSpec:
    key: str
    source_stat: str
    label: str
    unit: str
    conversion: Literal["direct", "per_match", "total"]
    direction: Literal["higher", "lower"]
    decimals: int = 1


METRIC_REGISTRY: tuple[MetricSpec, ...] = (
    MetricSpec(
        "possession",
        "possession_percentage_team",
        "控球率",
        "%",
        "direct",
        "higher",
    ),
    MetricSpec(
        "accurate_passes",
        "accurate_pass_team",
        "准确传球",
        "次/场",
        "direct",
        "higher",
    ),
    MetricSpec(
        "final_third_wins",
        "poss_won_att_3rd_team",
        "前场夺回",
        "次/场",
        "direct",
        "higher",
    ),
    MetricSpec(
        "xg_per_match",
        "expected_goals_team",
        "xG",
        "/场",
        "per_match",
        "higher",
        2,
    ),
    MetricSpec(
        "shots_on_target",
        "ontarget_scoring_att_team",
        "射正",
        "次/场",
        "direct",
        "higher",
    ),
    MetricSpec(
        "box_touches_per_match",
        "touches_in_opp_box_team",
        "禁区触球",
        "次/场",
        "per_match",
        "higher",
    ),
    MetricSpec(
        "big_chances_per_match",
        "big_chance_team",
        "重大机会",
        "次/场",
        "per_match",
        "higher",
    ),
    MetricSpec(
        "corners_per_match",
        "corner_taken_team",
        "角球",
        "次/场",
        "per_match",
        "higher",
    ),
    MetricSpec(
        "accurate_crosses",
        "accurate_cross_team",
        "准确传中",
        "次/场",
        "direct",
        "higher",
    ),
    MetricSpec(
        "set_piece_goals",
        "_set_piece_goals_team",
        "定位球进球",
        "球",
        "total",
        "higher",
        0,
    ),
    MetricSpec(
        "xga_per_match",
        "expected_goals_conceded_team",
        "xGA",
        "/场",
        "per_match",
        "lower",
        2,
    ),
    MetricSpec(
        "tackles",
        "total_tackle_team",
        "抢断",
        "次/场",
        "direct",
        "higher",
    ),
    MetricSpec(
        "clearances",
        "effective_clearance_team",
        "解围",
        "次/场",
        "direct",
        "higher",
    ),
    MetricSpec(
        "clean_sheets",
        "clean_sheet_team",
        "零封",
        "场",
        "total",
        "higher",
        0,
    ),
)

_SPEC_BY_SOURCE = {spec.source_stat: spec for spec in METRIC_REGISTRY}


def _safe_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise TeamStyleError(f"invalid {field}")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise TeamStyleError(f"invalid {field}") from None
    if result <= 0:
        raise TeamStyleError(f"invalid {field}")
    return result


def _finite_nonnegative(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise TeamStyleError(f"invalid {field}")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise TeamStyleError(f"invalid {field}") from None
    if not math.isfinite(result) or result < 0:
        raise TeamStyleError(f"invalid {field}")
    return result


def _format_value(value: float, spec: MetricSpec) -> str:
    if spec.decimals == 0:
        number = str(int(round(value)))
    else:
        number = f"{value:.{spec.decimals}f}"
    return f"{number}{spec.unit}"


def _team_table_context(payload: dict, team_id: int, league_id: int) -> tuple[int, int]:
    tables = payload.get("table")
    if not isinstance(tables, list):
        raise TeamStyleError("invalid team standings")
    rows: list[dict] | None = None
    for item in tables:
        data = item.get("data") if isinstance(item, dict) else None
        if not isinstance(data, dict) or int(data.get("leagueId") or 0) != league_id:
            continue
        table = data.get("table")
        if isinstance(table, dict) and isinstance(table.get("all"), list):
            rows = table["all"]
            break
    if rows is None:
        raise TeamStyleError("invalid team standings")
    team_row = next(
        (
            row
            for row in rows
            if isinstance(row, dict) and int(row.get("id") or 0) == team_id
        ),
        None,
    )
    if team_row is None:
        raise TeamStyleError("team missing from standings")
    played = _safe_int(team_row.get("played"), field="played matches")
    return played, len(rows)


def parse_team_style_artifact(
    payload: dict,
    *,
    team_id: int,
    team_name: str,
    league_id: int,
    season: str,
    crest_url: str | None,
) -> dict:
    """Parse one real FotMob team artifact using the canonical metric registry."""

    details = payload.get("details")
    stats = payload.get("stats")
    if not isinstance(details, dict) or not isinstance(stats, dict):
        raise TeamStyleError("invalid team artifact")
    if (
        _safe_int(details.get("id"), field="team id") != team_id
        or _safe_int(stats.get("teamId"), field="stats team id") != team_id
        or _safe_int(details.get("primaryLeagueId"), field="league id") != league_id
        or _safe_int(stats.get("primaryLeagueId"), field="stats league id") != league_id
        or str(details.get("latestSeason") or "") != season
    ):
        raise TeamStyleError("team artifact identity mismatch")

    season_id = _safe_int(stats.get("primarySeasonId"), field="season id")
    seasons = stats.get("tournamentSeasons")
    if not isinstance(seasons, list) or not any(
        isinstance(item, dict)
        and int(item.get("tournamentId") or 0) == season_id
        and str(item.get("parentLeagueId") or "") == str(league_id)
        and str(item.get("season") or "") == season
        for item in seasons
    ):
        raise TeamStyleError("team artifact season mismatch")

    played, league_team_count = _team_table_context(payload, team_id, league_id)
    stat_rows = stats.get("teams")
    if not isinstance(stat_rows, list):
        raise TeamStyleError("team metrics missing")
    indexed: dict[str, dict] = {}
    for row in stat_rows:
        if not isinstance(row, dict):
            continue
        source_stat = row.get("stat")
        if source_stat not in _SPEC_BY_SOURCE:
            continue
        if source_stat in indexed:
            raise TeamStyleError("duplicate team metric")
        participant = row.get("participant")
        if (
            not isinstance(participant, dict)
            or _safe_int(participant.get("teamId"), field="metric team id") != team_id
        ):
            raise TeamStyleError("team metric identity mismatch")
        indexed[source_stat] = participant

    metrics: dict[str, dict] = {}
    missing: list[str] = []
    for spec in METRIC_REGISTRY:
        participant = indexed.get(spec.source_stat)
        if participant is None:
            missing.append(spec.key)
            continue
        raw = _finite_nonnegative(participant.get("value"), field=spec.key)
        value = raw / played if spec.conversion == "per_match" else raw
        if spec.key == "possession" and value > 100:
            raise TeamStyleError("invalid possession")
        rank = _safe_int(participant.get("rank"), field=f"{spec.key} rank")
        if rank > league_team_count:
            raise TeamStyleError("invalid metric rank")
        metrics[spec.key] = {
            "key": spec.key,
            "label": spec.label,
            "value": round(value, spec.decimals),
            "display": _format_value(value, spec),
            "unit": spec.unit,
            "rank": rank,
            "rank_total": league_team_count,
            "direction": spec.direction,
            "source_stat": spec.source_stat,
            "source_value": raw,
            "conversion": spec.conversion,
        }
    return {
        "team_id": team_id,
        "name": team_name,
        "provider_name": str(details.get("name") or team_name),
        "crest_url": crest_url,
        "played": played,
        "metrics": metrics,
        "missing_metrics": missing,
    }


def _metric_pair(profile: dict, key: str) -> dict | None:
    home = profile["teams"]["home"]["metrics"].get(key)
    away = profile["teams"]["away"]["metrics"].get(key)
    if home is None or away is None:
        return None
    return {
        "key": key,
        "label": home["label"],
        "unit": home["unit"],
        "direction": home["direction"],
        "home": {
            "value": home["value"],
            "display": home["display"],
            "rank": home["rank"],
            "rank_total": home["rank_total"],
        },
        "away": {
            "value": away["value"],
            "display": away["display"],
            "rank": away["rank"],
            "rank_total": away["rank_total"],
        },
    }


def build_team_style_profile(
    *,
    home_payload: dict,
    away_payload: dict,
    home_source_sha: str,
    away_source_sha: str,
    match: dict,
    league_name_zh: str,
    data_cutoff_at: str,
    recent_form: dict[str, list[str]] | None = None,
) -> dict:
    """Build a deterministic paired profile from two bound team artifacts."""

    home = match["home"]
    away = match["away"]
    league_id = _safe_int(match.get("league_id"), field="match league id")
    season = str(match.get("season") or "")
    if not season or not data_cutoff_at:
        raise TeamStyleError("style profile context is incomplete")
    home_profile = parse_team_style_artifact(
        home_payload,
        team_id=_safe_int(home.get("team_id"), field="home team id"),
        team_name=str(home.get("name") or ""),
        league_id=league_id,
        season=season,
        crest_url=home.get("crest_url"),
    )
    away_profile = parse_team_style_artifact(
        away_payload,
        team_id=_safe_int(away.get("team_id"), field="away team id"),
        team_name=str(away.get("name") or ""),
        league_id=league_id,
        season=season,
        crest_url=away.get("crest_url"),
    )
    source_hash = hashlib.sha256(
        f"{home_source_sha}:{away_source_sha}:{PROFILE_VERSION}".encode()
    ).hexdigest()
    return {
        "profile_version": PROFILE_VERSION,
        "match_id": _safe_int(match.get("match_id"), field="match id"),
        "league_id": league_id,
        "league_name_zh": league_name_zh,
        "season": season,
        "data_cutoff_at": data_cutoff_at,
        "source_hash": source_hash,
        "teams": {"home": home_profile, "away": away_profile},
        "recent_form": recent_form or {"home": [], "away": []},
    }


def record_team_style_profile(conn: sqlite3.Connection, profile: dict) -> str:
    """Append a profile or skip an exact replay; conflicting replay is rejected."""

    canonical = json.dumps(profile, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    existing = conn.execute(
        """SELECT source_hash,profile_version,payload_json
           FROM team_style_profiles WHERE match_id=? AND data_cutoff_at=?""",
        (profile["match_id"], profile["data_cutoff_at"]),
    ).fetchone()
    if existing is not None:
        if (
            existing["source_hash"] == profile["source_hash"]
            and existing["profile_version"] == profile["profile_version"]
            and existing["payload_json"] == canonical
        ):
            return "skipped"
        raise TeamStyleError("team style profile conflict")
    conn.execute(
        """INSERT INTO team_style_profiles
           (id,match_id,league_id,season,data_cutoff_at,source_hash,
            profile_version,payload_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            new_uuid(),
            profile["match_id"],
            profile["league_id"],
            profile["season"],
            profile["data_cutoff_at"],
            profile["source_hash"],
            profile["profile_version"],
            canonical,
            utc_now_iso(),
        ),
    )
    return "inserted"


def latest_team_style_profile(conn: sqlite3.Connection, match_id: int) -> dict | None:
    try:
        row = conn.execute(
            """SELECT payload_json FROM team_style_profiles
               WHERE match_id=? ORDER BY data_cutoff_at DESC, id DESC LIMIT 1""",
            (match_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return json.loads(row["payload_json"]) if row is not None else None


def _available_pairs(profile: dict, keys: tuple[str, ...], limit: int = 3) -> list[dict]:
    return [pair for key in keys if (pair := _metric_pair(profile, key)) is not None][:limit]


def _comparison_sentence(pair: dict, home_name: str, away_name: str) -> str:
    hv = float(pair["home"]["value"])
    av = float(pair["away"]["value"])
    if abs(hv - av) < 0.05:
        return f"两队{pair['label']}接近，比赛方式的差别需要结合其他指标观察。"
    if pair["direction"] == "lower":
        better = home_name if hv < av else away_name
        return f"{better}的{pair['label']}更低，赛季防守数据相对更稳。"
    stronger = home_name if hv > av else away_name
    return f"{stronger}在{pair['label']}这一项更高，相关比赛环节更活跃。"


def _cues(sections: list[dict]) -> list[dict]:
    cues: list[dict] = []
    elapsed = 0.0
    for section in sections:
        for sentence in (
            part.strip()
            for part in section["text"].replace("。", "。|").split("|")
            if part.strip()
        ):
            duration = max(2.2, min(5.2, len(sentence) * 0.15))
            cues.append(
                {
                    "start": round(elapsed, 1),
                    "end": round(elapsed + duration, 1),
                    "text": sentence,
                }
            )
            elapsed += duration
    return cues


def assert_safe_content(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    hits = [term for term in FORBIDDEN_SAFE_TERMS if term.lower() in serialized.lower()]
    if hits:
        raise TeamStyleError("douyin-safe content contains forbidden terms")
    forbidden_keys = ("prediction", "odds", "market", "probability", "1x2")

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                lowered = str(key).lower()
                if any(term in lowered for term in forbidden_keys):
                    raise TeamStyleError("douyin-safe content contains forbidden fields")
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)


def build_douyin_safe_profile(profile: dict, match: dict, uncertainty: list[dict]) -> dict:
    """Return an allowlisted social view model with no betting/prediction fields."""

    home = profile["teams"]["home"]
    away = profile["teams"]["away"]
    home_name, away_name = home["name"], away["name"]
    scene_specs = (
        (
            "cover",
            "01",
            "比赛封面",
            "两队打法差在哪？",
            (),
        ),
        (
            "possession",
            "02",
            "控球与组织",
            "谁更主动控制比赛？",
            ("possession", "accurate_passes", "final_third_wins"),
        ),
        (
            "threat",
            "03",
            "禁区威胁",
            "谁更容易进入危险区域？",
            ("xg_per_match", "shots_on_target", "box_touches_per_match"),
        ),
        (
            "width",
            "04",
            "边路与定位球",
            "谁更依赖边路与定位球？",
            ("corners_per_match", "accurate_crosses", "set_piece_goals"),
        ),
        (
            "defense",
            "05",
            "无球与防守压力",
            "谁承担更多无球压力？",
            ("xga_per_match", "tackles", "clearances"),
        ),
    )
    scenes: list[dict] = []
    for scene_id, index, label, title, keys in scene_specs:
        metrics = _available_pairs(profile, keys)
        scenes.append(
            {
                "id": scene_id,
                "index": index,
                "label": label,
                "title": title,
                "metrics": metrics,
                "conclusions": (
                    [_comparison_sentence(item, home_name, away_name) for item in metrics[:2]]
                    if metrics
                    else []
                ),
            }
        )
    summary_pairs = (
        _available_pairs(profile, ("possession",), 1)
        + _available_pairs(profile, ("xg_per_match",), 1)
        + _available_pairs(profile, ("set_piece_goals",), 1)
    )
    risk_notes = [
        item["text"]
        for item in uncertainty
        if item.get("kind") in {"lineup_unavailable", "injury_unavailable", "short_history"}
    ][:2]
    if not risk_notes:
        risk_notes = ["赛前阵容与伤停信息尚未稳定覆盖，临场变化需另行核对。"]
    scenes.append(
        {
            "id": "summary",
            "index": "06",
            "label": "对位总结",
            "title": "一页看懂两队风格",
            "metrics": summary_pairs,
            "conclusions": [
                _comparison_sentence(item, home_name, away_name) for item in summary_pairs
            ][:3],
            "recent_form": profile.get("recent_form", {"home": [], "away": []}),
            "risk_notes": risk_notes,
            "cta": "关注公众号，查看每日比赛数据拆解",
        }
    )

    metric = lambda key: _metric_pair(profile, key)  # noqa: E731
    possession = metric("possession")
    passes = metric("accurate_passes")
    xg = metric("xg_per_match")
    shots = metric("shots_on_target")
    box_touches = metric("box_touches_per_match")
    corners = metric("corners_per_match")
    crosses = metric("accurate_crosses")
    set_piece = metric("set_piece_goals")
    xga = metric("xga_per_match")
    tackles = metric("tackles")
    clearances = metric("clearances")
    sections = [
        {
            "id": "opening",
            "title": "开场",
            "text": (
                f"{home_name}对{away_name}，不谈赛果，先看两队踢法差在哪。"
                "下面四组数据都来自本赛季已完成比赛，场均值按已赛场次统一换算。"
            ),
        },
        {
            "id": "control",
            "title": "控球与组织",
            "text": (
                (
                    f"控球率是{home_name}{possession['home']['display']}，"
                    f"{away_name}{possession['away']['display']}。"
                    if possession
                    else "两队控球率数据暂不完整。"
                )
                + (
                    f"每场准确传球分别为{passes['home']['display']}和"
                    f"{passes['away']['display']}，组织节奏比较接近。"
                    if passes
                    else "准确传球数据暂不完整。"
                )
            ),
        },
        {
            "id": "threat",
            "title": "禁区威胁",
            "text": (
                (
                    f"场均xG是{home_name}{xg['home']['display']}，"
                    f"{away_name}{xg['away']['display']}。"
                    if xg
                    else "两队xG数据暂不完整。"
                )
                + (
                    f"禁区触球为{box_touches['home']['display']}对"
                    f"{box_touches['away']['display']}，射正为"
                    f"{shots['home']['display']}对{shots['away']['display']}。"
                    if box_touches and shots
                    else "禁区触球或射正数据暂不完整。"
                )
            ),
        },
        {
            "id": "set_piece",
            "title": "边路与定位球",
            "text": (
                (
                    f"场均角球是{corners['home']['display']}对"
                    f"{corners['away']['display']}，准确传中是"
                    f"{crosses['home']['display']}对{crosses['away']['display']}。"
                    if corners and crosses
                    else "边路数据暂不完整。"
                )
                + (
                    f"赛季定位球进球为{home_name}{set_piece['home']['display']}，"
                    f"{away_name}{set_piece['away']['display']}，这里统计的是进球数。"
                    if set_piece
                    else "定位球进球数据暂不完整。"
                )
            ),
        },
        {
            "id": "defense",
            "title": "无球阶段",
            "text": (
                (
                    f"场均xGA是{home_name}{xga['home']['display']}，"
                    f"{away_name}{xga['away']['display']}，这一项越低越好。"
                    if xga
                    else "两队xGA数据暂不完整。"
                )
                + (
                    f"抢断为{tackles['home']['display']}对{tackles['away']['display']}，"
                    f"解围为{clearances['home']['display']}对{clearances['away']['display']}。"
                    if tackles and clearances
                    else "抢断或解围数据暂不完整。"
                )
            ),
        },
        {
            "id": "closing",
            "title": "结尾",
            "text": (
                "这些数据反映的是赛季打法，不代表单场结果，阵容变化也会影响临场表现。"
                "关注公众号，查看每日比赛数据拆解。"
            ),
        },
    ]
    titles = [
        f"{home_name}vs{away_name}：两队打法差在哪？",
        f"从控球到禁区触球，拆开看{home_name}与{away_name}",
        f"一场球看四个环节：组织、威胁、边路、无球",
    ]
    xhs = (
        f"【{profile['league_name_zh']}球队打法拆解】\n"
        f"{home_name} vs {away_name}\n\n"
        + "\n".join(
            f"{i + 1}. {item['conclusions'][0]}"
            for i, item in enumerate(scenes[1:5])
            if item["conclusions"]
        )
        + "\n\n数据按赛季已赛场次统一换算；缺失项不以 0 填充。"
        "\n关注公众号，查看每日比赛数据拆解。"
    )
    wechat = (
        f"{profile['league_name_zh']}本轮关注{home_name}与{away_name}。"
        "本文从控球组织、禁区威胁、边路定位球和无球压力四个环节拆解两队赛季打法。"
        "所有场均值按已赛场次换算，阵容与伤停缺口单独提示。"
    )
    result = {
        "profile_id": DOUYIN_SAFE_PROFILE,
        "profile_version": 1,
        "source_hash": profile["source_hash"],
        "data_cutoff_at": profile["data_cutoff_at"],
        "match": {
            "match_id": profile["match_id"],
            "league_name": profile["league_name_zh"],
            "season": profile["season"],
            "round": match.get("round"),
            "kickoff_at_utc": match.get("kickoff_at_utc"),
            "home": {
                "team_id": home["team_id"],
                "name": home_name,
                # 队徽是当前同源媒体投影，不属于历史球队统计 payload。
                # 允许在 profile 冻结后补齐媒体缓存，但仍要求球队 ID/name
                # 来自已绑定的 immutable style profile。
                "crest_url": match.get("home", {}).get("crest_url")
                or home.get("crest_url"),
            },
            "away": {
                "team_id": away["team_id"],
                "name": away_name,
                "crest_url": match.get("away", {}).get("crest_url")
                or away.get("crest_url"),
            },
        },
        "scenes": scenes,
        "script_sections": sections,
        "subtitle_cues": _cues(sections),
        "titles": titles,
        "xiaohongshu_text": xhs,
        "wechat_summary": wechat,
        "source_note": "球队统计来自已保存的 FotMob 赛季数据；场均值按已赛场次换算。",
    }
    assert_safe_content(result)
    return result


def beijing_cutoff_date(data_cutoff_at: str) -> str:
    parsed = datetime.fromisoformat(data_cutoff_at.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )
    return parsed.date().isoformat()
