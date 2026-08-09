"""
fotmob_competition_schedule_pilot.py — 按赛事端点聚合球队全赛事赛程 pilot
(独立试验,不进生产链路;不复用已判 NO_GO 的 analysis/team_schedule_pilot)。

背景:analysis/team_schedule_pilot 已证明 FotMobClient.team_data()(球队端点)
的 season 参数完全无效,只能拿到"最近+未来"滚动窗口,不能作为历史赛程来源
(见 docs/audits/team-schedule-pilot.md,结论 NO_GO)。

本 pilot 验证替代路线:复用现有、已在生产 ingest_league.py/ingest_future_fixtures.py
中真实验证过的 `FotMobClient.league_matches(league_id, season)`(赛事端点,按赛事+
赛季查询),对 Manchester City(FotMob Team ID=8456)2024/2025 赛季所涉及的多个赛事
(英超/足总杯/联赛杯/欧冠/社区盾)分别请求,统一解析后按 Match ID 去重合并,
筛出该队参与的比赛,在跨赛事时间线上计算真实 rest_hours。

本文件只包含纯函数(注册表校验、解析、合并、休息时间计算)+ SQLite 幂等写入 +
CLI。不访问网络的部分可离线单测;真实请求只在显式 `--live` 时发生。

不假设 team endpoint 与 competition endpoint JSON 结构相同——本模块的解析器
基于 `league_matches()` 已知的真实结构(fixtures.allMatches[] 内每场比赛含
{id, home:{id,name}, away:{id,name}, status:{utcTime,finished,cancelled,started,...},
round},已在 backend/ingest/ingest_future_fixtures.py 生产代码中验证)独立实现,
不 import team_schedule_pilot 的解析函数。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.db.util import normalize_utc_iso  # noqa: E402
from backend.schedules.pagination import inspect_known_pagination  # noqa: E402

PROVIDER = "fotmob"


# ─────────────────────────────────────────────────────────────────────────────
# 异常(fail-loud;不静默生成部分/错误数据)
# ─────────────────────────────────────────────────────────────────────────────

class ScheduleSchemaError(Exception):
    """response 缺少 fixtures 路径、fixtures 不是 list、或 list 元素不是 dict。
    只报告位置和类型,不打印完整 payload。"""


class ScheduleConflictError(Exception):
    """同一 provider_match_id 出现主客队/开球时间/赛事不一致——不得静默覆盖/last-write-wins。"""


class CompetitionIdentityError(Exception):
    """response 声明的赛事 ID/名称与请求注册表不一致——拒绝把这批数据当作该赛事的
    真实赛程使用,不得继续解析生成记录。"""


class SeasonVerificationError(Exception):
    """requested/returned season 无法验证或不一致时的低层 parser 基类。"""


class SeasonUnverifiableError(SeasonVerificationError):
    """requested/returned season 缺失、类型错误或格式非法。"""


class SeasonMismatchError(SeasonVerificationError):
    """returned season 合法但与 requested season 不一致。"""


class PilotSchemaIncompatibleError(Exception):
    """现有 pilot SQLite schema 与当前代码不兼容；拒绝原地迁移或写入。"""


# ─────────────────────────────────────────────────────────────────────────────
# 严格 ID 解析(与 team_schedule_pilot 的同名概念一致,但独立实现,不跨模块 import)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_strict_positive_int(value: Any) -> Optional[int]:
    """只接受 JSON integer(非 bool)或纯十进制数字字符串;拒绝 float(含整数值
    float)、bool、负数、0、小数点字符串、科学计数法字符串、空字符串、非数字
    字符串。不合法返回 None,不抛错(由调用方决定拒绝记录还是留空)。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        s = value.strip()
        if not s or not s.isdigit():
            return None
        n = int(s)
        return n if n > 0 else None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# B1. 显式赛事注册表(仅针对 Manchester City 2024/2025 本轮范围)
# ─────────────────────────────────────────────────────────────────────────────

def build_competition_registry(season: str = "2024/2025") -> dict[int, dict]:
    """返回 {requested_competition_id: registry_entry} 的初始(未校验)注册表。
    verification_status 初始为 'PENDING',须由 verify_competition_identity()
    结合真实/离线响应填充。"""
    def _entry(cid, name, cls, required, note):
        return {
            "requested_competition_id": cid,
            "expected_name": name,
            "competition_class": cls,
            "requested_season": season,
            "required_for_pilot": required,
            "verification_status": "PENDING",
            "verification_evidence": note,
        }

    return {
        47: _entry(47, "Premier League", "league", True,
                   "已在生产 allwin.db 历史数据中验证(五大联赛之一)"),
        132: _entry(132, "FA Cup", "domestic_cup", True,
                    "已从上一轮 team_schedule_pilot 真实球队 feed 观测到(tournament.leagueId=132),"
                    "本轮须经 competition endpoint 独立重新验证"),
        42: _entry(42, "Champions League", "continental", True,
                   "此前只出现在分类单元测试的 synthetic 用例中,从未真实验证;本轮真实请求"
                   "(2024-07-23,league_id=42)确认 id=42 且来源声明名称为 'Champions League'"
                   "(不带 'UEFA' 前缀)——已把 expected_name 由最初猜测的 'UEFA Champions "
                   "League' 更正为与来源一致的真实观测值,不是放宽比对规则去凑答案"),
        247: _entry(247, "Community Shield", "super_cup", True,
                    "已从上一轮 team_schedule_pilot 真实球队 feed 观测到(tournament.leagueId=247),"
                    "本轮须经 competition endpoint 独立重新验证"),
        133: _entry(133, "EFL Cup", "domestic_cup", True,
                    "候选 ID,未经任何真实验证——本轮必须验证,不得直接当作事实使用"),
        # FIFA Club World Cup:ID 未知,不纳入注册表(不得猜测)。是否能在本轮受限
        # daily_matches 发现流程中确认,见 discover_unknown_competition_id() 与报告。
    }


def verify_competition_identity(
    raw: dict,
    requested_competition_id: int,
    expected_name: str,
) -> dict:
    """(非抛错)比较 response 声明的赛事 ID/名称与请求是否一致。纯函数,不修改
    传入的 registry entry,返回一个独立的校验结果 dict,供 CLI/报告层合并进注册表。"""
    details = raw.get("details") or {}
    observed_id = _parse_strict_positive_int(details.get("id"))
    observed_name = details.get("name")

    id_match = observed_id == requested_competition_id
    name_match = (
        isinstance(observed_name, str)
        and observed_name.strip().lower() == expected_name.strip().lower()
    )

    if observed_id is None and observed_name is None:
        status = "IDENTITY_UNVERIFIABLE"  # response 里没有 details.id/name 可比对
    elif id_match and name_match:
        status = "IDENTITY_VERIFIED"
    elif observed_id is not None and not id_match:
        status = "IDENTITY_MISMATCH"
    elif observed_name is not None and not name_match:
        status = "IDENTITY_MISMATCH"
    else:
        status = "IDENTITY_UNVERIFIABLE"

    return {
        "requested_competition_id": requested_competition_id,
        "expected_name": expected_name,
        "observed_id": observed_id,
        "observed_name": observed_name,
        "status": status,
    }


def verify_season_parameter_effectiveness(
    raw_season_a: dict,
    raw_season_b: dict,
) -> dict:
    """比较同一赛事两次不同 season 请求的真实响应,判定 season 参数是否真的
    生效(而不是像 team endpoint 那样返回同一滚动窗口)。纯函数,只读两份已
    保存的响应,不发请求。"""
    ids_a = _extract_match_ids(raw_season_a)
    ids_b = _extract_match_ids(raw_season_b)
    dates_a = _extract_kickoff_dates(raw_season_a)
    dates_b = _extract_kickoff_dates(raw_season_b)

    same_id_set = ids_a == ids_b and len(ids_a) > 0
    overlap = ids_a & ids_b
    date_range_a = (min(dates_a), max(dates_a)) if dates_a else (None, None)
    date_range_b = (min(dates_b), max(dates_b)) if dates_b else (None, None)
    same_date_range = date_range_a == date_range_b and date_range_a != (None, None)

    if same_id_set or same_date_range:
        verdict = "SEASON_PARAMETER_INEFFECTIVE"
    elif len(ids_a) == 0 or len(ids_b) == 0:
        verdict = "UNVERIFIABLE_EMPTY_RESPONSE"
    else:
        verdict = "SEASON_PARAMETER_EFFECTIVE"

    return {
        "match_id_count_a": len(ids_a),
        "match_id_count_b": len(ids_b),
        "match_id_overlap_count": len(overlap),
        "date_range_a": date_range_a,
        "date_range_b": date_range_b,
        "verdict": verdict,
    }


_SEASON_LABEL_RE = re.compile(r"(?:\d{4}|\d{4}/\d{4})\Z")


def _normalize_season_label(value: Any) -> Optional[str]:
    """接受 FotMob 已观测到的自然年 ``YYYY`` 或跨年 ``YYYY/YYYY`` season。

    跨年标签的第二年必须恰好是第一年加一。缺失、空白、非字符串和其它形状
    都不可验证；调用方必须 fail closed，不能把它们当作隐式匹配。
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not _SEASON_LABEL_RE.fullmatch(normalized):
        return None
    if "/" in normalized:
        start, end = (int(part) for part in normalized.split("/", 1))
        if end != start + 1:
            return None
    return normalized


def _returned_season_value(raw: dict) -> Any:
    """读取来源声明的 season；selectedSeason 存在时不以 fallback 掩盖空值。"""
    details = raw.get("details") if isinstance(raw, dict) else None
    if not isinstance(details, dict):
        return None
    if "selectedSeason" in details:
        return details.get("selectedSeason")
    return details.get("season")


def _fixtures_list(raw: dict) -> list:
    """提取 fixtures.allMatches[](league_matches() 已验证的真实结构,见
    backend/ingest/ingest_future_fixtures.py::fetch_fixture_rows)。
    路径不存在时抛错,不能返回 [] 冒充"0 场"(B4 规则 1)。"""
    if not isinstance(raw, dict):
        raise ScheduleSchemaError(f"raw response 不是 dict(实际类型 {type(raw).__name__})")
    fixtures_obj = raw.get("fixtures")
    if not isinstance(fixtures_obj, dict) or "allMatches" not in fixtures_obj:
        raise ScheduleSchemaError(
            "response 缺少 fixtures.allMatches 路径(可能是空赛事/来源 schema 变化),"
            "不能返回空列表冒充'0 场'"
        )
    all_matches = fixtures_obj.get("allMatches")
    if not isinstance(all_matches, list):
        raise ScheduleSchemaError(
            f"fixtures.allMatches 不是 list(实际类型 {type(all_matches).__name__})"
        )
    return all_matches


def _extract_match_ids(raw: dict) -> set:
    try:
        fixtures = _fixtures_list(raw)
    except ScheduleSchemaError:
        return set()
    out = set()
    for m in fixtures:
        if not isinstance(m, dict):
            continue
        mid = _parse_strict_positive_int(m.get("id"))
        if mid is not None:
            out.add(mid)
    return out


def _extract_kickoff_dates(raw: dict) -> list:
    try:
        fixtures = _fixtures_list(raw)
    except ScheduleSchemaError:
        return []
    out = []
    for m in fixtures:
        if not isinstance(m, dict):
            continue
        st = m.get("status") or {}
        utc = st.get("utcTime")
        if isinstance(utc, str) and len(utc) >= 10:
            out.append(utc[:10])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 赛事分类:优先用本轮已验证注册表(curated_registry_verified_against_source),
# 未覆盖的赛事回退到按名称的启发式(heuristic_name)——两者不得混淆命名来源。
# ─────────────────────────────────────────────────────────────────────────────

_HEURISTIC_FRIENDLY_NAMES = {"club friendlies", "friendlies", "friendly", "pre-season", "pre season"}
_HEURISTIC_LEAGUE_NAMES = {"premier league", "laliga", "la liga", "bundesliga", "serie a", "ligue 1"}


def classify_competition(
    competition_id: Optional[int],
    competition_name: Optional[str],
    registry: dict[int, dict],
) -> tuple[str, str]:
    """返回 (competition_class, classification_method)。
    命中本轮显式注册表(且该条目已通过身份校验)→ competition_class 取自注册表,
    classification_method='curated_registry_verified_against_source'。
    未命中注册表 → 退化为按名称的启发式,classification_method='heuristic_name',
    不得把启发式结果包装成来源原生分类。"""
    entry = registry.get(competition_id) if competition_id is not None else None
    if entry is not None and entry.get("verification_status") == "IDENTITY_VERIFIED":
        return entry["competition_class"], "curated_registry_verified_against_source"

    if not competition_name or not isinstance(competition_name, str):
        return "unknown", "heuristic_name"
    key = competition_name.strip().lower()
    if key in _HEURISTIC_FRIENDLY_NAMES:
        return "friendly", "heuristic_name"
    if key in _HEURISTIC_LEAGUE_NAMES:
        return "league", "heuristic_name"
    return "other", "heuristic_name"


# ─────────────────────────────────────────────────────────────────────────────
# kickoff 精度(同 CLAUDE.md §6.2.1 纪律:不凭字符串形状推断)
# ─────────────────────────────────────────────────────────────────────────────

def derive_kickoff(status_obj: dict) -> tuple[Optional[str], str]:
    status_obj = status_obj or {}
    raw = status_obj.get("utcTime")
    date_tbd = bool(status_obj.get("matchDateTbd"))
    time_tbd = bool(status_obj.get("matchTimeTbd"))

    normalized = normalize_utc_iso(raw) if isinstance(raw, str) else None

    if normalized and not date_tbd and not time_tbd:
        return normalized, "exact"
    if isinstance(raw, str) and len(raw) >= 10 and not date_tbd:
        return None, "date_only"
    return None, "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# B4. 单赛事 response 纯解析器
# ─────────────────────────────────────────────────────────────────────────────

def parse_competition_schedule_response(
    raw: dict,
    requested_competition: dict,
    requested_season: str,
    observed_at: str,
    source_endpoint: str,
) -> list[dict]:
    """把单个 league_matches() 原始响应解析成统一比赛记录列表(该赛事的全部比赛,
    未按球队过滤——球队过滤在 merge 之后的独立步骤完成)。

    requested_competition: build_competition_registry() 的一个 entry。本函数
    自己强制验证赛事身份、requested season、returned season 及两者相等；
    任一失败都在生成记录前抛出明确项目异常，不能依赖上层 gate 才保证安全。
    """
    identity = verify_competition_identity(
        raw, requested_competition["requested_competition_id"], requested_competition["expected_name"]
    )
    if identity["status"] != "IDENTITY_VERIFIED":
        raise CompetitionIdentityError(
            f"赛事身份校验失败(status={identity['status']}): "
            f"requested_id={identity['requested_competition_id']} expected_name={identity['expected_name']} "
            f"observed_id={identity['observed_id']} observed_name={identity['observed_name']}"
        )

    normalized_requested_season = _normalize_season_label(requested_season)
    if normalized_requested_season is None:
        raise SeasonUnverifiableError(
            "requested season 缺失、类型错误或格式不可验证"
        )
    season_returned = _normalize_season_label(_returned_season_value(raw))
    if season_returned is None:
        raise SeasonUnverifiableError(
            "returned season 缺失、类型错误或格式不可验证"
        )
    if season_returned != normalized_requested_season:
        raise SeasonMismatchError(
            "returned season 与 requested season 不一致: "
            f"returned={season_returned!r}, requested={normalized_requested_season!r}"
        )

    fixtures = _fixtures_list(raw)  # 规则 1/2:路径缺失/非 list 已在此抛错

    seen: dict[int, dict] = {}
    records: list[dict] = []

    for idx, m in enumerate(fixtures):
        if not isinstance(m, dict):
            raise ScheduleSchemaError(
                f"fixtures.allMatches[{idx}] 不是 dict(实际类型 {type(m).__name__})"
            )

        provider_match_id = _parse_strict_positive_int(m.get("id"))
        if provider_match_id is None:
            continue  # 规则 4:严格整数解析,非法 id 拒绝该条记录

        home = m.get("home") or {}
        away = m.get("away") or {}
        home_id = _parse_strict_positive_int(home.get("id"))
        away_id = _parse_strict_positive_int(away.get("id"))
        # 规则 5:home/away ID 非法不猜测——保留 None,不拒绝整条记录(该记录仍可能
        # 对其它赛事分析有用,但不会被任何 requested_team_id 过滤命中)。

        status_obj = m.get("status") or {}
        kickoff_utc, kickoff_precision = derive_kickoff(status_obj)  # 规则 6

        competition_id = requested_competition["requested_competition_id"]
        competition_name = requested_competition["expected_name"]
        competition_class, classification_method = classify_competition(
            competition_id, competition_name, {competition_id: requested_competition}
        )

        record = {
            "provider": PROVIDER,
            "provider_match_id": provider_match_id,
            "requested_competition_id": competition_id,
            "source_competition_id": identity["observed_id"],
            "competition_name": competition_name,
            "competition_class": competition_class,
            "classification_method": classification_method,
            "season_requested": normalized_requested_season,
            "season_returned": season_returned,
            "season_mismatch": False,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_team_name": home.get("name"),
            "away_team_name": away.get("name"),
            "kickoff_utc": kickoff_utc,
            "kickoff_precision": kickoff_precision,
            "status": status_obj.get("reason", {}).get("short") if isinstance(status_obj.get("reason"), dict) else None,
            "finished": bool(status_obj.get("finished")),
            "cancelled": bool(status_obj.get("cancelled")),
            "started": bool(status_obj.get("started")),
            "round": m.get("round"),
            "source_endpoint": source_endpoint,
            "observed_at": observed_at,
        }

        prev = seen.get(provider_match_id)  # 规则 10/11:去重 + 冲突检测
        if prev is not None:
            conflict_keys = ("home_team_id", "away_team_id", "kickoff_utc",
                              "requested_competition_id", "competition_name")
            if any(prev[k] != record[k] for k in conflict_keys):
                raise ScheduleConflictError(
                    f"provider_match_id={provider_match_id} 在同一赛事响应内出现冲突记录: "
                    f"prev={ {k: prev[k] for k in conflict_keys} } "
                    f"new={ {k: record[k] for k in conflict_keys} }"
                )
            continue
        seen[provider_match_id] = record
        records.append(record)

    return records


# ─────────────────────────────────────────────────────────────────────────────
# B5. 多赛事合并(以 (provider, provider_match_id) 为唯一键)
# ─────────────────────────────────────────────────────────────────────────────

_MERGE_CONFLICT_KEYS = ("home_team_id", "away_team_id", "kickoff_utc",
                         "requested_competition_id", "competition_name")


def merge_competition_schedules(record_lists: list[list[dict]]) -> list[dict]:
    """合并多个赛事的 parse_competition_schedule_response() 结果。同一 Match ID
    在两个来源赛事中完全一致(冲突字段全同)→ 去重,保留 provenance 列表;
    冲突字段不一致 → 抛 ScheduleConflictError,不 last-write-wins。"""
    merged: dict[int, dict] = {}

    for records in record_lists:
        for rec in records:
            mid = rec["provider_match_id"]
            existing = merged.get(mid)
            if existing is None:
                new_rec = dict(rec)
                new_rec["source_provenance"] = [rec["requested_competition_id"]]
                merged[mid] = new_rec
                continue

            if any(existing[k] != rec[k] for k in _MERGE_CONFLICT_KEYS):
                raise ScheduleConflictError(
                    f"provider_match_id={mid} 跨赛事合并冲突: "
                    f"existing={ {k: existing[k] for k in _MERGE_CONFLICT_KEYS} } "
                    f"new={ {k: rec[k] for k in _MERGE_CONFLICT_KEYS} }"
                )
            if rec["requested_competition_id"] not in existing["source_provenance"]:
                existing["source_provenance"].append(rec["requested_competition_id"])

    return list(merged.values())


def build_team_match_records(merged_records: list[dict], requested_team_id: int) -> list[dict]:
    """从合并后的比赛记录中筛出 requested_team_id 参与的比赛,生成球队比赛关系。"""
    out = []
    for rec in merged_records:
        home_id, away_id = rec["home_team_id"], rec["away_team_id"]
        if requested_team_id not in (home_id, away_id):
            continue
        is_home = requested_team_id == home_id
        opponent_team_id = away_id if is_home else home_id

        is_competitive = None
        cls = rec["competition_class"]
        if cls in ("league", "domestic_cup", "continental", "super_cup"):
            is_competitive = True
        elif cls == "friendly":
            is_competitive = False

        out.append({
            "provider": rec["provider"],
            "provider_match_id": rec["provider_match_id"],
            "team_id": requested_team_id,
            "opponent_team_id": opponent_team_id,
            "is_home": is_home,
            "competition_id": rec["requested_competition_id"],
            "competition_name": rec["competition_name"],
            "competition_class": cls,
            "kickoff_utc": rec["kickoff_utc"],
            "kickoff_precision": rec["kickoff_precision"],
            "finished": rec["finished"],
            "cancelled": rec["cancelled"],
            "is_competitive": is_competitive,
            "season_requested": rec["season_requested"],
            "source_provenance": list(rec["source_provenance"]),
            "went_to_extra_time": None,  # 来源无法判断,不得编造 False
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# B9. 休息时间(跨赛事合并后的球队时间线;point-in-time 纪律见 B10)
# ─────────────────────────────────────────────────────────────────────────────

def _qualifies_for_rest(rec: dict) -> bool:
    return (
        rec["finished"]
        and not rec["cancelled"]
        and rec["kickoff_precision"] == "exact"
        and rec["kickoff_utc"] is not None
        and rec.get("is_competitive") is True
    )


def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def compute_rest_hours(team_records: list[dict]) -> list[dict]:
    """按 kickoff_utc 升序,只用当前比赛**之前**已发生的比赛计算 rest/回看计数——
    不得使用列表中任何排在当前比赛之后的记录(B10 point-in-time 纪律,即使调用方
    把未来比赛也传了进来)。"""
    qualifying = [r for r in team_records if _qualifies_for_rest(r)]
    qualifying.sort(key=lambda r: r["kickoff_utc"])

    out = []
    league_history: list[dict] = []

    for idx, rec in enumerate(qualifying):
        cur_dt = _parse_dt(rec["kickoff_utc"])
        prev = qualifying[idx - 1] if idx > 0 else None

        if prev is not None:
            prev_dt = _parse_dt(prev["kickoff_utc"])
            rest_hours = (cur_dt - prev_dt).total_seconds() / 3600.0
        else:
            rest_hours = None  # 左边界:此前是否还有比赛未知,不得编造

        earlier = qualifying[:idx]  # 只回看当前比赛之前的记录(point-in-time)
        matches_last_7d = sum(
            1 for r2 in earlier
            if 0 < (cur_dt - _parse_dt(r2["kickoff_utc"])).total_seconds() / 86400.0 <= 7
        )
        matches_last_14d = sum(
            1 for r2 in earlier
            if 0 < (cur_dt - _parse_dt(r2["kickoff_utc"])).total_seconds() / 86400.0 <= 14
        )

        intervening_ids = [
            r2["provider_match_id"] for r2 in earlier
            if prev is None or _parse_dt(r2["kickoff_utc"]) >= _parse_dt(prev["kickoff_utc"])
        ] if prev is not None else []

        entry = {
            "provider_match_id": rec["provider_match_id"],
            "competition_class": rec["competition_class"],
            "competition_name": rec["competition_name"],
            "kickoff_utc": rec["kickoff_utc"],
            "previous_match_id": prev["provider_match_id"] if prev else None,
            "previous_competition_id": prev["competition_id"] if prev else None,
            "previous_competition_name": prev["competition_name"] if prev else None,
            "rest_hours": rest_hours,
            "calendar_gap_days": (rest_hours / 24.0) if rest_hours is not None else None,
            "short_rest_72h": (rest_hours < 72) if rest_hours is not None else None,
            "short_rest_96h": (rest_hours < 96) if rest_hours is not None else None,
            "matches_last_7d": matches_last_7d,
            "matches_last_14d": matches_last_14d,
            "is_long_break": (rest_hours > 21 * 24) if rest_hours is not None else None,
            "went_to_extra_time": rec.get("went_to_extra_time"),
        }

        if rec["competition_class"] == "league":
            prev_league = league_history[-1] if league_history else None
            if prev_league is not None:
                prev_league_dt = _parse_dt(prev_league["kickoff_utc"])
                league_only_rest_hours = (cur_dt - prev_league_dt).total_seconds() / 3600.0
                entry["league_only_previous_match_id"] = prev_league["provider_match_id"]
                # 上一场英超之后、当前之前发生的其它正式比赛(用于 rest 差异案例展示)
                intervening = [
                    r2["provider_match_id"] for r2 in earlier
                    if _parse_dt(r2["kickoff_utc"]) > prev_league_dt
                ]
            else:
                league_only_rest_hours = None
                entry["league_only_previous_match_id"] = None
                intervening = []
            entry["league_only_rest_hours"] = league_only_rest_hours
            entry["all_comp_rest_hours"] = rest_hours
            entry["intervening_non_league_match_ids"] = intervening
            league_history.append(rec)
        else:
            entry["league_only_rest_hours"] = None
            entry["all_comp_rest_hours"] = None
            entry["league_only_previous_match_id"] = None
            entry["intervening_non_league_match_ids"] = []

        out.append(entry)

    return out


def find_cross_comp_rest_examples(rest_rows: list[dict]) -> list[dict]:
    out = []
    for r in rest_rows:
        a, l = r.get("all_comp_rest_hours"), r.get("league_only_rest_hours")
        if a is not None and l is not None and a < l:
            out.append({**r, "rest_difference_hours": l - a})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# B6. 临时 SQLite(幂等)
# ─────────────────────────────────────────────────────────────────────────────

_REGISTRY_COLUMNS = [
    "competition_id", "expected_name", "observed_name", "competition_class",
    "requested_season", "returned_season", "season_parameter_verified",
    "fixture_count", "target_team_fixture_count", "identity_verified",
    "pagination_detected", "pagination_status", "pagination_evidence",
    "completeness_status",
]

_CALENDAR_COLUMNS = [
    "provider", "provider_match_id", "competition_id", "competition_name",
    "competition_class", "kickoff_utc", "kickoff_precision",
    "home_team_id", "away_team_id", "status", "finished", "cancelled",
    "source_provenance_json", "payload_hash",
]

_TEAM_MATCH_COLUMNS = [
    "provider", "provider_match_id", "team_id", "opponent_team_id",
    "is_home", "is_competitive", "season_requested",
]


def _assert_compatible_registry_schema(conn: sqlite3.Connection) -> None:
    """在任何 schema/data 写入前验证现有 registry 表。

    旧的 ``competition_id`` 单列主键不做原地迁移；缺列或主键顺序不同也一律
    拒绝。调用方应使用新的 output directory，保留旧表和已有记录原样不动。
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='pilot_competition_registry'"
    ).fetchone()
    if exists is None:
        return

    table_info = conn.execute(
        "PRAGMA table_info('pilot_competition_registry')"
    ).fetchall()
    columns = {row[1] for row in table_info}
    primary_key = tuple(
        row[1]
        for row in sorted(
            (row for row in table_info if row[5] > 0),
            key=lambda row: row[5],
        )
    )
    missing_columns = sorted(set(_REGISTRY_COLUMNS) - columns)
    expected_primary_key = ("competition_id", "requested_season")
    if primary_key != expected_primary_key or missing_columns:
        reason_parts = []
        if primary_key != expected_primary_key:
            reason_parts.append(
                "primary key must be exactly "
                "(competition_id, requested_season)"
            )
        if missing_columns:
            reason_parts.append(
                "required columns are missing: " + ", ".join(missing_columns)
            )
        raise PilotSchemaIncompatibleError(
            "existing pilot_competition_registry schema is incompatible ("
            + "; ".join(reason_parts)
            + "); use a new output directory"
        )


def init_pilot_db(conn: sqlite3.Connection) -> None:
    """初始化当前 pilot schema；不兼容旧 registry 会在任何写入前明确拒绝。"""
    _assert_compatible_registry_schema(conn)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pilot_competition_registry (
            competition_id INTEGER NOT NULL,
            expected_name TEXT,
            observed_name TEXT,
            competition_class TEXT,
            requested_season TEXT NOT NULL,
            returned_season TEXT,
            season_parameter_verified TEXT,
            fixture_count INTEGER,
            target_team_fixture_count INTEGER,
            identity_verified TEXT,
            pagination_detected INTEGER,
            pagination_status TEXT,
            pagination_evidence TEXT,
            completeness_status TEXT,
            PRIMARY KEY (competition_id, requested_season)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pilot_match_calendar (
            provider TEXT NOT NULL,
            provider_match_id INTEGER NOT NULL,
            competition_id INTEGER,
            competition_name TEXT,
            competition_class TEXT,
            kickoff_utc TEXT,
            kickoff_precision TEXT,
            home_team_id INTEGER,
            away_team_id INTEGER,
            status TEXT,
            finished INTEGER,
            cancelled INTEGER,
            source_provenance_json TEXT,
            payload_hash TEXT,
            PRIMARY KEY (provider, provider_match_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pilot_team_match (
            provider TEXT NOT NULL,
            provider_match_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            opponent_team_id INTEGER,
            is_home INTEGER,
            is_competitive INTEGER,
            season_requested TEXT,
            PRIMARY KEY (provider, provider_match_id, team_id)
        )
    """)
    conn.commit()


def _existing_row(conn: sqlite3.Connection, table: str, pk_cols: list[str], pk_vals: tuple) -> Optional[dict]:
    where = " AND ".join(f"{c}=?" for c in pk_cols)
    cur = conn.execute(f"SELECT * FROM {table} WHERE {where}", pk_vals)
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _payload_hash(record: dict) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_match_calendar(conn: sqlite3.Connection, records: list[dict]) -> dict:
    inserted = skipped = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for rec in records:
            prov_json = json.dumps(sorted(rec.get("source_provenance", [])))
            row = {
                "provider": rec["provider"],
                "provider_match_id": rec["provider_match_id"],
                "competition_id": rec["requested_competition_id"],
                "competition_name": rec["competition_name"],
                "competition_class": rec["competition_class"],
                "kickoff_utc": rec["kickoff_utc"],
                "kickoff_precision": rec["kickoff_precision"],
                "home_team_id": rec["home_team_id"],
                "away_team_id": rec["away_team_id"],
                "status": rec["status"],
                "finished": int(rec["finished"]),
                "cancelled": int(rec["cancelled"]),
                "source_provenance_json": prov_json,
                "payload_hash": _payload_hash(rec),
            }
            existing = _existing_row(
                conn, "pilot_match_calendar", ["provider", "provider_match_id"],
                (row["provider"], row["provider_match_id"]),
            )
            if existing is not None:
                # 冲突检测语义扩大到全部业务可观察字段,不只是"这是同一场比赛"的
                # 最小充要集合——kickoff_precision/competition_class/finished/
                # cancelled 任一改变(如重新分类、或来源更新了完赛状态)都必须
                # fail-loud,不能被"existing 已存在"悄悄吞掉旧值。
                conflict_keys = [
                    "home_team_id", "away_team_id", "kickoff_utc", "kickoff_precision",
                    "competition_id", "competition_name", "competition_class",
                    "finished", "cancelled",
                ]
                if any(existing[k] != row[k] for k in conflict_keys):
                    raise ScheduleConflictError(
                        f"pilot_match_calendar 冲突: provider_match_id={row['provider_match_id']}"
                    )
                skipped += 1
                continue
            cols = _CALENDAR_COLUMNS
            conn.execute(
                f"INSERT INTO pilot_match_calendar ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                [row[c] for c in cols],
            )
            inserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"inserted": inserted, "skipped": skipped}


def write_team_match(conn: sqlite3.Connection, records: list[dict]) -> dict:
    inserted = skipped = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for rec in records:
            ic = rec.get("is_competitive")
            row = {
                "provider": rec["provider"],
                "provider_match_id": rec["provider_match_id"],
                "team_id": rec["team_id"],
                "opponent_team_id": rec["opponent_team_id"],
                "is_home": int(rec["is_home"]),
                "is_competitive": None if ic is None else int(ic),
                "season_requested": rec["season_requested"],
            }
            existing = _existing_row(
                conn, "pilot_team_match", ["provider", "provider_match_id", "team_id"],
                (row["provider"], row["provider_match_id"], row["team_id"]),
            )
            if existing is not None:
                # 同上,扩大到 is_competitive/season_requested——分类结果或请求
                # 赛季变了却被当作"已存在"静默跳过,会让陈旧的 is_competitive
                # 永久保留在库里且不产生任何告警。
                conflict_keys = ["opponent_team_id", "is_home", "is_competitive", "season_requested"]
                if any(existing[k] != row[k] for k in conflict_keys):
                    raise ScheduleConflictError(
                        f"pilot_team_match 冲突: provider_match_id={row['provider_match_id']} team_id={row['team_id']}"
                    )
                skipped += 1
                continue
            cols = _TEAM_MATCH_COLUMNS
            conn.execute(
                f"INSERT INTO pilot_team_match ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                [row[c] for c in cols],
            )
            inserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"inserted": inserted, "skipped": skipped}


def write_competition_registry(conn: sqlite3.Connection, registry_rows: list[dict]) -> dict:
    """按 ``(competition_id, requested_season)`` 幂等写入 registry。

    同一自然键的任一验证字段变化都显式冲突；不同 season 必须保留为不同记录。
    """
    inserted = skipped = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for row in registry_rows:
            natural_key = (row["competition_id"], row["requested_season"])
            existing = _existing_row(
                conn,
                "pilot_competition_registry",
                ["competition_id", "requested_season"],
                natural_key,
            )
            if existing is not None:
                conflict_keys = [
                    column for column in _REGISTRY_COLUMNS
                    if column not in {"competition_id", "requested_season"}
                ]
                if any(existing[k] != row.get(k) for k in conflict_keys):
                    raise ScheduleConflictError(
                        "pilot_competition_registry 冲突: "
                        f"competition_id={row['competition_id']} "
                        f"requested_season={row['requested_season']!r}"
                    )
                skipped += 1
                continue
            cols = _REGISTRY_COLUMNS
            conn.execute(
                f"INSERT INTO pilot_competition_registry ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                [row.get(c) for c in cols],
            )
            inserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"inserted": inserted, "skipped": skipped}


# ─────────────────────────────────────────────────────────────────────────────
# B7. 与 allwin.db 对齐(只读)
# ─────────────────────────────────────────────────────────────────────────────

def align_with_allwin(allwin_match_ids: set, pilot_epl_match_ids: set) -> dict:
    """纯函数版对齐计算(输入均为已从各自来源读出的 Match ID 集合,不在本函数内
    连接数据库——调用方负责用 mode=ro&immutable=1 只读读取 allwin.db)。"""
    intersection = allwin_match_ids & pilot_epl_match_ids
    return {
        "allwin_count": len(allwin_match_ids),
        "pilot_epl_count": len(pilot_epl_match_ids),
        "intersection": len(intersection),
        "only_in_allwin": len(allwin_match_ids - pilot_epl_match_ids),
        "only_in_pilot": len(pilot_epl_match_ids - allwin_match_ids),
        "match_id_duplicate": 0,  # 集合天然去重;若上游传入 list 且有重复,调用方应先转 set
        "league_completeness_verified": (
            intersection == allwin_match_ids and len(allwin_match_ids - pilot_epl_match_ids) == 0
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# B3. 受限未知赛事发现(daily_matches,只在必要时、只在已知真实比赛日期使用)
# ─────────────────────────────────────────────────────────────────────────────

def extract_team_matches_from_daily_response(
    daily_raw: dict,
    requested_team_id: int,
) -> list[dict]:
    """从 FotMobClient.daily_matches() 响应中只提取 requested_team_id 参与的比赛
    的最小字段(Match ID/home/away/competition/kickoff/status),不抓取无关完整
    数据集。daily_matches() 响应结构:{"leagues": [{"name","primaryId","matches":[...]}]}。"""
    out = []
    leagues = daily_raw.get("leagues") or []
    for lg in leagues:
        comp_id = lg.get("primaryId") or lg.get("id")
        comp_name = lg.get("name")
        for m in lg.get("matches") or []:
            home = m.get("home") or {}
            away = m.get("away") or {}
            home_id = _parse_strict_positive_int(home.get("id"))
            away_id = _parse_strict_positive_int(away.get("id"))
            if requested_team_id not in (home_id, away_id):
                continue
            status_obj = m.get("status") or {}
            kickoff_utc, kickoff_precision = derive_kickoff(status_obj)
            mid = _parse_strict_positive_int(m.get("id"))
            if mid is None:
                continue
            out.append({
                "provider_match_id": mid,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "competition_id": _parse_strict_positive_int(comp_id),
                "competition_name": comp_name,
                "kickoff_utc": kickoff_utc,
                "kickoff_precision": kickoff_precision,
                "finished": bool(status_obj.get("finished")),
                "cancelled": bool(status_obj.get("cancelled")),
            })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _redact_check(text: str) -> None:
    lowered = text.lower()
    if "thordata_proxy" in lowered and "=" in text:
        raise RuntimeError("输出中检测到疑似 THORDATA_PROXY 赋值,已阻止打印")
    if "@" in text and "://" in text and (":" in text.split("://", 1)[-1].split("@")[0]):
        raise RuntimeError("输出中检测到疑似 user:password@host 形式的凭证 URL,已阻止打印")


def _utc_now_iso() -> str:
    from backend.db.util import utc_now_iso
    return utc_now_iso()


# ─────────────────────────────────────────────────────────────────────────────
# 全量赛事校验(registry 门禁的核心逻辑;纯函数,不写库、不发请求)
# ─────────────────────────────────────────────────────────────────────────────

def verify_all_competitions(
    per_competition_raw: dict,
    registry: dict[int, dict],
    requested_season: str,
    observed_at: str,
    source_endpoint_prefix: str,
    requested_team_id: int,
) -> "tuple[list[dict], dict[int, list[dict]]]":
    """对 registry 里**每一个**赛事做完整校验(身份 + season + 响应结构),不因为
    某个赛事的 required_for_pilot 是 False 就跳过校验——是否整体阻断流水线由
    调用方(run_cli)根据 required_for_pilot 决定,本函数只负责如实产出每个
    赛事的校验结果。

    校验状态(entry["verification_status"] / registry_rows 里的 identity_verified):
      MISSING_RESPONSE     — 未提供该赛事的响应(既不在 offline fixture 里也未抓到)
      IDENTITY_MISMATCH / IDENTITY_UNVERIFIABLE — verify_competition_identity() 的失败态
      SEASON_UNVERIFIABLE  — returned season 缺失、空或不符合已知 season 形状
      SEASON_MISMATCH      — 身份通过,但可验证的 returned season 与请求不一致
      EMPTY_FIXTURES       — fixtures.allMatches 存在且为 list,但 list 为空
      PAGINATION_UNRESOLVED — 已知 continuation marker 显示还有页面或状态无法判定
      ScheduleSchemaError  — 身份/season 通过,但 fixtures 路径缺失/非法
      ScheduleConflictError — 响应内部本身就有互相冲突的重复记录
      IDENTITY_VERIFIED    — 全部通过

    **关键区分(不得混淆)**:IDENTITY_VERIFIED **不代表** requested_team_id 在
    该赛事有比赛。Community Shield 这类"响应完整、身份正确,但目标球队该赛季
    确实 0 场"的情况仍可通过 response gate；其 completeness 仍明确保留 season
    parameter 未验证边界。target_team_fixture_count=0 是如实观测,不是失败信号。

    返回 (registry_rows, parsed_records_by_cid):
      - registry_rows 可直接传给 write_competition_registry(),同时包含成功和
        失败的赛事——两种状态都要真实入库,不能只记录成功的。
      - parsed_records_by_cid 只含 verification_status == 'IDENTITY_VERIFIED'
        的赛事,值是该赛事全部比赛记录(未按球队过滤,供后续 merge 使用)。
    """
    registry_rows: list[dict] = []
    parsed_records_by_cid: dict[int, list[dict]] = {}

    for cid, entry in registry.items():
        raw = per_competition_raw.get(str(cid))
        source_endpoint = f"{source_endpoint_prefix}#{cid}"

        observed_name = None
        returned_season = None
        fixture_count = None
        target_team_fixture_count = None
        pagination_status = None
        pagination_evidence: list[str] = []
        status: str
        evidence: Optional[str]

        if raw is None:
            status = "MISSING_RESPONSE"
            evidence = "未提供该赛事的响应(既不在 offline fixture 的 per_competition 里,也未真实抓取到)"
        else:
            identity = verify_competition_identity(raw, cid, entry["expected_name"])
            observed_name = identity["observed_name"]
            if identity["status"] != "IDENTITY_VERIFIED":
                status = identity["status"]
                evidence = (
                    f"身份校验失败: observed_id={identity['observed_id']} "
                    f"observed_name={identity['observed_name']!r}"
                )
            else:
                raw_returned_season = _returned_season_value(raw)
                returned_season = _normalize_season_label(raw_returned_season)
                normalized_requested_season = _normalize_season_label(
                    requested_season,
                )
                if returned_season is None:
                    status = "SEASON_UNVERIFIABLE"
                    evidence = (
                        "响应 returned season 缺失、空或格式不可验证"
                    )
                elif normalized_requested_season is None:
                    status = "SEASON_UNVERIFIABLE"
                    evidence = "请求 season 格式不可验证"
                elif returned_season != normalized_requested_season:
                    status = "SEASON_MISMATCH"
                    evidence = (
                        f"响应声明的 season={returned_season!r},"
                        f"与请求的 {normalized_requested_season!r} 不一致"
                    )
                else:
                    try:
                        fixtures = _fixtures_list(raw)
                    except (ScheduleSchemaError, CompetitionIdentityError, ScheduleConflictError) as e:
                        status = type(e).__name__
                        evidence = str(e)
                    else:
                        if len(fixtures) == 0:
                            status = "EMPTY_FIXTURES"
                            evidence = (
                                "fixtures.allMatches 是空 list；不能据此声明赛事完整"
                            )
                            fixture_count = 0
                            target_team_fixture_count = 0
                        else:
                            try:
                                records = parse_competition_schedule_response(
                                    raw,
                                    entry,
                                    normalized_requested_season,
                                    observed_at,
                                    source_endpoint,
                                )
                            except (
                                ScheduleSchemaError,
                                CompetitionIdentityError,
                                ScheduleConflictError,
                                SeasonUnverifiableError,
                                SeasonMismatchError,
                            ) as e:
                                if isinstance(e, SeasonUnverifiableError):
                                    status = "SEASON_UNVERIFIABLE"
                                elif isinstance(e, SeasonMismatchError):
                                    status = "SEASON_MISMATCH"
                                else:
                                    status = type(e).__name__
                                evidence = str(e)
                            else:
                                fixture_count = len(records)
                                target_team_fixture_count = sum(
                                    1 for r in records
                                    if requested_team_id
                                    in (
                                        r["home_team_id"],
                                        r["away_team_id"],
                                    )
                                )
                                if not records:
                                    status = "NO_VALID_FIXTURES"
                                    evidence = (
                                        "allMatches 非空但没有可解析的有效比赛记录"
                                    )
                                else:
                                    pagination = inspect_known_pagination(raw)
                                    pagination_status = pagination["status"]
                                    pagination_evidence = pagination["evidence"]
                                    if pagination_status in {
                                        "DETECTED",
                                        "UNRESOLVED",
                                    }:
                                        status = "PAGINATION_UNRESOLVED"
                                        evidence = (
                                            "当前响应存在未闭合的已知 pagination marker: "
                                            + ", ".join(pagination_evidence)
                                        )
                                    else:
                                        status = "IDENTITY_VERIFIED"
                                        evidence = None
                                        parsed_records_by_cid[cid] = records

        entry["verification_status"] = status
        entry["verification_evidence"] = evidence

        pagination_detected = (
            1 if pagination_status == "DETECTED"
            else 0 if pagination_status == "NOT_DETECTED"
            else None
        )
        registry_rows.append({
            "competition_id": cid,
            "expected_name": entry["expected_name"],
            "observed_name": observed_name,
            "competition_class": entry["competition_class"],
            "requested_season": requested_season,
            "returned_season": returned_season,
            # 单份响应的 returned season 匹配只证明这份响应自称属于所请求
            # season；没有第二份 season 响应对照时，不能夸大为 endpoint 参数
            # 已被证明有效。completeness_status 明确保留这一限制。
            "season_parameter_verified": None,
            "fixture_count": fixture_count,
            "target_team_fixture_count": target_team_fixture_count,
            "identity_verified": status,
            "pagination_detected": pagination_detected,
            "pagination_status": pagination_status,
            "pagination_evidence": (
                json.dumps(pagination_evidence, ensure_ascii=False)
                if pagination_evidence else None
            ),
            "completeness_status": (
                "RESPONSE_VALIDATED_SEASON_PARAMETER_UNVERIFIED"
                if status == "IDENTITY_VERIFIED"
                else "FAILED"
            ),
        })

    return registry_rows, parsed_records_by_cid


def run_cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-id", type=int, required=True)
    parser.add_argument("--season", type=str, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--offline-fixture", type=str, default=None)
    parser.add_argument("--output-dir", type=str, required=True)
    args = parser.parse_args(argv)

    if not args.live and not args.offline_fixture:
        print(json.dumps({"error": "must pass --live or --offline-fixture"}), file=sys.stderr)
        return 2
    if args.live and args.offline_fixture:
        print(json.dumps({"error": "--live and --offline-fixture are mutually exclusive"}), file=sys.stderr)
        return 2

    os.makedirs(args.output_dir, exist_ok=True)
    observed_at = _utc_now_iso()
    registry = build_competition_registry(args.season)

    # ── 1. 抓取/离线载入 ──────────────────────────────────────────────────
    if args.offline_fixture:
        with open(args.offline_fixture) as f:
            fixture_doc = json.load(f)
        per_competition_raw = fixture_doc["per_competition"]  # {competition_id_str: raw_response}
        source_endpoint_prefix = f"offline_fixture:{args.offline_fixture}"
    else:
        # 真实请求:对注册表里每个赛事调用现有 FotMobClient.league_matches()(不修改该
        # 方法行为),season 需调用方自行 quote() 编码(同 backend/ingest/ingest_league.py
        # 的既有用法)。
        from backend.fotmob_client import FotMobClient
        client = FotMobClient()
        per_competition_raw = {}
        for cid in registry:
            season_param = quote(args.season, safe="")
            per_competition_raw[str(cid)] = client.league_matches(cid, season_param)
        source_endpoint_prefix = "https://www.fotmob.com/api/data/leagues"

    # ── 2. 验证所有赛事(遍历整个 registry;门禁只看 required_for_pilot) ────
    registry_rows, parsed_records_by_cid = verify_all_competitions(
        per_competition_raw, registry, args.season, observed_at,
        source_endpoint_prefix, args.team_id,
    )

    # ── 3. 持久化 registry——无论成功失败,都要真实入库,先于门禁判断 ────────
    db_path = os.path.join(args.output_dir, "pilot_competition_schedule.db")
    conn = sqlite3.connect(db_path)
    try:
        try:
            init_pilot_db(conn)
            registry_write = write_competition_registry(conn, registry_rows)
        except PilotSchemaIncompatibleError as e:
            error = {
                "error": "pilot_schema_incompatible",
                "status": "FAILED",
                "message": str(e),
                "action": "use_new_output_directory",
                "db_path": db_path,
            }
            out_text = json.dumps(
                error, indent=2, ensure_ascii=False, default=str,
            )
            _redact_check(out_text)
            print(out_text, file=sys.stderr)
            return 1
    finally:
        conn.close()

    # ── 4. required 赛事是否全部通过?任一失败则整体失败,不算 rest,
    #      不写 calendar/team_match,summary 明确列出失败赛事,CLI 非零退出。
    #      注意:身份验证通过但目标球队 0 场(如 Community Shield)不算失败——
    #      verification_status 仍是 IDENTITY_VERIFIED,不会出现在这里。 ────
    failed_required = [
        cid for cid, entry in registry.items()
        if entry["required_for_pilot"] and entry["verification_status"] != "IDENTITY_VERIFIED"
    ]
    if failed_required:
        summary = {
            "provider": PROVIDER,
            "team_id": args.team_id,
            "season_requested": args.season,
            "observed_at": observed_at,
            "status": "FAILED",
            "failed_required_competitions": [
                {
                    "competition_id": cid,
                    "expected_name": registry[cid]["expected_name"],
                    "verification_status": registry[cid]["verification_status"],
                    "verification_evidence": registry[cid]["verification_evidence"],
                }
                for cid in failed_required
            ],
            "registry_write": registry_write,
            "db_path": db_path,
        }
        out_text = json.dumps(summary, indent=2, ensure_ascii=False, default=str)
        _redact_check(out_text)
        print(out_text, file=sys.stderr)
        return 1

    # ── 5. 合并比赛(只用验证通过的赛事的记录) ────────────────────────────
    merged = merge_competition_schedules(list(parsed_records_by_cid.values()))
    team_records = build_team_match_records(merged, args.team_id)

    # ── 6. 写 calendar/team_match ─────────────────────────────────────────
    conn = sqlite3.connect(db_path)
    try:
        cal_result = write_match_calendar(conn, merged)
        team_result = write_team_match(conn, team_records)
    except ScheduleConflictError as e:
        print(json.dumps({"error": "db_conflict", "detail": str(e)}), file=sys.stderr)
        return 1
    finally:
        conn.close()

    # ── 7. 计算 rest ──────────────────────────────────────────────────────
    rest_rows = compute_rest_hours(team_records)
    cross_comp_examples = find_cross_comp_rest_examples(rest_rows)

    summary = {
        "provider": PROVIDER,
        "team_id": args.team_id,
        "season_requested": args.season,
        "observed_at": observed_at,
        "status": "OK",
        "total_merged_records": len(merged),
        "total_team_records": len(team_records),
        "db_path": db_path,
        "registry_write": registry_write,
        "calendar_write": cal_result,
        "team_match_write": team_result,
        "rest_hours_computed_count": len(rest_rows),
        "cross_comp_rest_examples_found": len(cross_comp_examples),
        "cross_comp_rest_examples": cross_comp_examples[:5],
        "registry_status": {cid: e["verification_status"] for cid, e in registry.items()},
    }

    out_text = json.dumps(summary, indent=2, ensure_ascii=False, default=str)
    _redact_check(out_text)
    print(out_text)
    return 0


def main() -> None:
    sys.exit(run_cli())


if __name__ == "__main__":
    main()
