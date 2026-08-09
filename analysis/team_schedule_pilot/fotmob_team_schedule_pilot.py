"""
fotmob_team_schedule_pilot.py — 球队全赛事赛程采集可行性 pilot(独立试验,不进生产链路)。

背景与结论见 docs/audits/team-schedule-pilot.md。核心发现(2026-07-23 真实探测,
Team ID=8456 Manchester City):

    `FotMobClient.team_data(team_id, season=...)` 对应端点
    `https://www.fotmob.com/api/data/teams?id=<team_id>&season=<season>`
    真实返回一个以"当前时刻"为中心的 **最近+未来滚动窗口**(实测 50 场),
    `season` 查询参数对返回内容**没有任何可观测影响**——用 season=2024/2025
    与 season=2025/2026 两次真实请求得到完全相同的窗口(同一组 50 场比赛、
    同一 min/max kickoff),且响应内 `details.latestSeason`/`overview.season`
    恒为来源当前赛季,不随请求参数变化。这不是一个"历史赛季查询"接口。

    因此本模块只能诚实地把单次响应解析成"该次真实观测到的滚动窗口"里的比赛,
    不能、也不尝试把它包装成"完整历史赛季"。是否完整由调用方(CLI/报告)
    基于实际字段自行判断,解析器本身不做该断言。

本文件只包含纯函数(解析、分类、休息时间计算)+ SQLite 幂等写入 + CLI。
不访问网络的部分可离线单测;真实请求只在显式 `--live` 时发生。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.db.util import normalize_utc_iso  # noqa: E402

PROVIDER = "fotmob"

# ─────────────────────────────────────────────────────────────────────────────
# 赛事分类(heuristic_name;来源没有提供正式 type 字段,详见模块docstring)
# ─────────────────────────────────────────────────────────────────────────────

# 以下列表只覆盖五大联赛常见的国内杯赛/超级杯/欧战名称,基于公开可观察的比赛
# 名称字符串人工整理,不是来源提供的正式分类枚举——真实探测中 FotMob 球队
# 赛程 feed 每场比赛只给 {name, stage, leagueId},没有独立的"type"字段。
_FRIENDLY_NAMES = {
    "club friendlies", "friendlies", "friendly", "pre-season", "pre season",
}
_SUPER_CUP_NAMES = {
    "community shield", "fa community shield", "supercopa de espana",
    "supercoppa italiana", "dfl-supercup", "dfl supercup",
    "trophee des champions",
}
_CONTINENTAL_NAMES = {
    "champions league", "uefa champions league",
    "europa league", "uefa europa league",
    "europa conference league", "uefa europa conference league",
    "fifa club world cup", "club world cup",
}
_DOMESTIC_CUP_NAMES = {
    "fa cup", "efl cup", "carabao cup", "league cup",
    "copa del rey", "dfb-pokal", "dfb pokal",
    "coupe de france", "coppa italia",
}
_LEAGUE_NAMES = {
    "premier league", "laliga", "la liga", "bundesliga", "serie a", "ligue 1",
}
# 五大联赛 FotMob League_ID(docs/data-sources.md):47 英超/53 法甲/54 德甲/55 意甲/87 西甲。
# 仅作为 name 判断的辅助信号,不改变 classification_method 仍为 heuristic_name
# (来源本身没有独立的"是否为顶级联赛"字段,ID 集合同样是人工整理的先验知识)。
_TOP5_LEAGUE_IDS = {47, 53, 54, 55, 87}


def classify_competition(name: Optional[str], league_id: Optional[int]) -> tuple[str, str]:
    """返回 (competition_class, classification_method)。

    classification_method 恒为 'heuristic_name'——来源没有提供正式赛事类型,
    不得包装成来源原生分类(CLAUDE.md 真实输出纪律)。
    """
    if not name or not isinstance(name, str):
        return "unknown", "heuristic_name"
    key = name.strip().lower()
    if key in _FRIENDLY_NAMES:
        return "friendly", "heuristic_name"
    if key in _SUPER_CUP_NAMES:
        return "super_cup", "heuristic_name"
    if key in _CONTINENTAL_NAMES:
        return "continental", "heuristic_name"
    if key in _DOMESTIC_CUP_NAMES:
        return "domestic_cup", "heuristic_name"
    if key in _LEAGUE_NAMES or (league_id in _TOP5_LEAGUE_IDS if league_id is not None else False):
        return "league", "heuristic_name"
    return "other", "heuristic_name"


# ─────────────────────────────────────────────────────────────────────────────
# 开球时间精度判定(依据显式 TBD 标记,不凭字符串形状推断——同 CLAUDE.md §6.2.1
# normalize_exact_kickoff 的纪律;真实数据发现 33/50 场未开赛比赛
# matchDateTbd=true——虽然带着一个占位 utcTime 字符串,但来源明确说明日期本身
# 未确定,不能算 exact,也不能算"日期可信、只是时间未知"的 date_only,只能算
# unknown)。
# ─────────────────────────────────────────────────────────────────────────────

def derive_kickoff(status_obj: dict) -> tuple[Optional[str], str]:
    """返回 (kickoff_utc, kickoff_precision)。"""
    status_obj = status_obj or {}
    raw = status_obj.get("utcTime")
    date_tbd = bool(status_obj.get("matchDateTbd"))
    time_tbd = bool(status_obj.get("matchTimeTbd"))

    normalized = normalize_utc_iso(raw) if isinstance(raw, str) else None

    if normalized and not date_tbd and not time_tbd:
        return normalized, "exact"
    if isinstance(raw, str) and len(raw) >= 10 and not date_tbd:
        # 日期本身可信(source 未标 date_tbd),但时间不满足 exact 的严格校验
        # (time_tbd 或格式不可解析)——只能算 date_only,不补造具体时刻。
        return None, "date_only"
    return None, "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 异常
# ─────────────────────────────────────────────────────────────────────────────

class ScheduleConflictError(Exception):
    """同一 provider_match_id 出现主客队/开球时间/赛事不一致——不得静默覆盖。"""


class ScheduleSchemaError(Exception):
    """fixtures 数组内出现非 dict 元素——来源 schema 可能已变化,fail-loud,
    不静默生成"跳过坏元素后凑出来的部分赛程"。只报告索引和类型,不打印完整 payload。"""


# ─────────────────────────────────────────────────────────────────────────────
# 严格 ID 解析(A2 收口:provider_match_id / home / away ID 不得靠 int() 静默
# 截断浮点数;来源没有正式"整数"类型保证,必须显式拒绝非法形状)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_strict_positive_int(value: Any) -> Optional[int]:
    """只接受 JSON integer(非 bool)或纯十进制数字字符串(如 "4813720");
    拒绝 float(含整数值 float,如 9.0/9.9)、bool、负数、0、小数点字符串、
    科学计数法字符串、空字符串、非数字字符串。不合法返回 None(不抛错,
    由调用方决定拒绝该记录还是把 competition_id 留空)。"""
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
    return None  # float 及其它类型一律拒绝,不做 int() 静默截断


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数解析器
# ─────────────────────────────────────────────────────────────────────────────

def parse_team_schedule_response(
    raw: dict,
    requested_team_id: int,
    season_requested: str,
    source_endpoint: str,
    observed_at: str,
) -> list[dict]:
    """把 team_data() 原始响应解析成统一结构记录列表(见模块顶部字段说明)。

    纯函数:不写库、不发请求。requested_team_id 必须是每场比赛的主队或客队之一,
    否则拒绝该条记录(不静默丢弃对手也不猜测)。同一 provider_match_id 出现
    冲突数据时抛 ScheduleConflictError,不静默覆盖。
    """
    fixtures = (
        (raw.get("fixtures") or {}).get("allFixtures") or {}
    ).get("fixtures") or []

    seen: dict[int, dict] = {}
    records: list[dict] = []

    for idx, m in enumerate(fixtures):
        if not isinstance(m, dict):
            raise ScheduleSchemaError(
                f"fixtures[{idx}] 不是 dict(实际类型 {type(m).__name__});"
                f"来源 schema 可能已变化,拒绝在坏元素上静默跳过继续生成部分赛程"
            )

        raw_id = m.get("id")
        provider_match_id = _parse_strict_positive_int(raw_id)
        if provider_match_id is None:
            continue  # provider_match_id 必须为严格正整数,规则 1(A2:不做 int() 静默截断)

        home = m.get("home") or {}
        away = m.get("away") or {}
        home_id_int = _parse_strict_positive_int(home.get("id"))
        away_id_int = _parse_strict_positive_int(away.get("id"))

        if requested_team_id not in (home_id_int, away_id_int):
            continue  # 规则 2:requested_team_id 必须确实是主队或客队之一(不猜测非法 ID)

        is_home = requested_team_id == home_id_int
        opponent_team_id = away_id_int if is_home else home_id_int  # 规则 3:由主客关系计算

        status_obj = m.get("status") or {}
        kickoff_utc, kickoff_precision = derive_kickoff(status_obj)  # 规则 4/5/6

        tournament = m.get("tournament") or {}
        competition_name = tournament.get("name")
        # 来源缺失 leagueId → None(允许);leagueId 是非法类型(float/非数字字符串等)
        # → 同样落 None,不把非法值静默截断/伪装成合法 competition_id(规则 A2)。
        competition_id = _parse_strict_positive_int(tournament.get("leagueId"))
        competition_class, classification_method = classify_competition(
            competition_name, competition_id
        )

        record = {
            "provider": PROVIDER,
            "provider_match_id": provider_match_id,
            "requested_team_id": requested_team_id,
            "home_team_id": home_id_int,
            "away_team_id": away_id_int,
            "opponent_team_id": opponent_team_id,
            "is_home": is_home,
            "competition_id": competition_id,
            "competition_name": competition_name,
            "competition_raw_type": None,  # 来源没有独立于 name 的 type 字段(如实留空)
            "competition_class": competition_class,
            "classification_method": classification_method,
            "season_requested": season_requested,
            "kickoff_utc": kickoff_utc,
            "kickoff_precision": kickoff_precision,
            "status": status_obj.get("reason", {}).get("short") if isinstance(status_obj.get("reason"), dict) else None,
            "finished": bool(status_obj.get("finished")),
            "cancelled": bool(status_obj.get("cancelled")),  # 规则 7:cancelled 不参与休息时间(在计算阶段过滤)
            "started": bool(status_obj.get("started")),
            "round": m.get("roundName") or tournament.get("stage") or None,
            "source_endpoint": source_endpoint,
            "observed_at": observed_at,
            "went_to_extra_time": None,  # 来源无法判断,规则 16:不得编造 False
        }

        prev = seen.get(provider_match_id)  # 规则 9/10:去重 + 冲突检测
        if prev is not None:
            conflict_keys = ("home_team_id", "away_team_id", "kickoff_utc", "competition_id", "competition_name")
            if any(prev[k] != record[k] for k in conflict_keys):
                raise ScheduleConflictError(
                    f"provider_match_id={provider_match_id} 出现冲突记录: "
                    f"prev={ {k: prev[k] for k in conflict_keys} } "
                    f"new={ {k: record[k] for k in conflict_keys} }"
                )
            continue  # 完全相同的重复记录,直接去重跳过

        seen[provider_match_id] = record
        records.append(record)

    return records


def payload_hash(record: dict) -> str:
    """对解析后的记录(不是原始 payload)算 canonical JSON 的 sha256,用于变化检测。"""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# SQLite 幂等写入(临时库,见 CLAUDE.md 数据库纪律与本 pilot 任务边界)
# ─────────────────────────────────────────────────────────────────────────────

_CALENDAR_COLUMNS = [
    "provider", "provider_match_id", "competition_id", "competition_name",
    "competition_raw_type", "competition_class", "classification_method",
    "kickoff_utc", "kickoff_precision", "home_team_id", "away_team_id",
    "status", "finished", "cancelled", "source_endpoint", "observed_at",
    "payload_hash",
]

_TEAM_MATCH_COLUMNS = [
    "provider", "provider_match_id", "team_id", "opponent_team_id",
    "is_home", "is_competitive", "went_to_extra_time", "season_requested",
]


def init_pilot_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pilot_match_calendar (
            provider TEXT NOT NULL,
            provider_match_id INTEGER NOT NULL,
            competition_id INTEGER,
            competition_name TEXT,
            competition_raw_type TEXT,
            competition_class TEXT,
            classification_method TEXT,
            kickoff_utc TEXT,
            kickoff_precision TEXT,
            home_team_id INTEGER,
            away_team_id INTEGER,
            status TEXT,
            finished INTEGER,
            cancelled INTEGER,
            source_endpoint TEXT,
            observed_at TEXT,
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
            went_to_extra_time INTEGER,
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


def write_match_calendar(conn: sqlite3.Connection, records: list[dict]) -> dict:
    """幂等写入 pilot_match_calendar。冲突报错,不静默覆盖;完全相同则跳过。"""
    inserted = skipped = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for rec in records:
            row = {
                "provider": rec["provider"],
                "provider_match_id": rec["provider_match_id"],
                "competition_id": rec["competition_id"],
                "competition_name": rec["competition_name"],
                "competition_raw_type": rec["competition_raw_type"],
                "competition_class": rec["competition_class"],
                "classification_method": rec["classification_method"],
                "kickoff_utc": rec["kickoff_utc"],
                "kickoff_precision": rec["kickoff_precision"],
                "home_team_id": rec["home_team_id"],
                "away_team_id": rec["away_team_id"],
                "status": rec["status"],
                "finished": int(rec["finished"]),
                "cancelled": int(rec["cancelled"]),
                "source_endpoint": rec["source_endpoint"],
                "observed_at": rec["observed_at"],
                "payload_hash": payload_hash(rec),
            }
            existing = _existing_row(
                conn, "pilot_match_calendar",
                ["provider", "provider_match_id"],
                (row["provider"], row["provider_match_id"]),
            )
            if existing is not None:
                conflict_keys = ["home_team_id", "away_team_id", "kickoff_utc",
                                  "competition_id", "competition_name"]
                if any(existing[k] != row[k] for k in conflict_keys):
                    raise ScheduleConflictError(
                        f"pilot_match_calendar 冲突: provider_match_id={row['provider_match_id']} "
                        f"existing={ {k: existing[k] for k in conflict_keys} } "
                        f"new={ {k: row[k] for k in conflict_keys} }"
                    )
                skipped += 1
                continue
            cols = _CALENDAR_COLUMNS
            placeholders = ",".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO pilot_match_calendar ({','.join(cols)}) VALUES ({placeholders})",
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
            is_competitive = None
            if rec["competition_class"] in ("league", "domestic_cup", "continental", "super_cup"):
                is_competitive = 1
            elif rec["competition_class"] == "friendly":
                is_competitive = 0
            # 'other' / 'unknown' → NULL,不猜测

            row = {
                "provider": rec["provider"],
                "provider_match_id": rec["provider_match_id"],
                "team_id": rec["requested_team_id"],
                "opponent_team_id": rec["opponent_team_id"],
                "is_home": int(rec["is_home"]),
                "is_competitive": is_competitive,
                "went_to_extra_time": rec["went_to_extra_time"],  # 恒 NULL,规则 16
                "season_requested": rec["season_requested"],
            }
            existing = _existing_row(
                conn, "pilot_team_match",
                ["provider", "provider_match_id", "team_id"],
                (row["provider"], row["provider_match_id"], row["team_id"]),
            )
            if existing is not None:
                conflict_keys = ["opponent_team_id", "is_home"]
                if any(existing[k] != row[k] for k in conflict_keys):
                    raise ScheduleConflictError(
                        f"pilot_team_match 冲突: provider_match_id={row['provider_match_id']} "
                        f"team_id={row['team_id']}"
                    )
                skipped += 1
                continue
            cols = _TEAM_MATCH_COLUMNS
            placeholders = ",".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO pilot_team_match ({','.join(cols)}) VALUES ({placeholders})",
                [row[c] for c in cols],
            )
            inserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"inserted": inserted, "skipped": skipped}


# ─────────────────────────────────────────────────────────────────────────────
# 休息时间计算(纯函数,输入内存记录列表,不依赖 DB)
# ─────────────────────────────────────────────────────────────────────────────

def _qualifies_for_rest(rec: dict) -> bool:
    """只对:已完赛 + 非取消 + kickoff_precision=exact + 正式比赛(非 friendly) 的比赛计算。"""
    return (
        rec["finished"]
        and not rec["cancelled"]
        and rec["kickoff_precision"] == "exact"
        and rec["competition_class"] != "friendly"
        and rec["competition_class"] != "unknown"
        and rec["kickoff_utc"] is not None
    )


def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def compute_rest_hours(team_records: list[dict]) -> list[dict]:
    """按 kickoff_utc 升序,对符合条件的比赛计算 rest_hours 及派生字段。

    返回:每场"符合条件"的比赛一条 dict,包含原始字段 + 派生字段。
    league_only_rest_hours / all_comp_rest_hours 只在当前比赛本身是 'league'
    时计算(任务要求"英超比赛的两个口径")。
    """
    qualifying = [r for r in team_records if _qualifies_for_rest(r)]
    qualifying.sort(key=lambda r: r["kickoff_utc"])

    out = []
    league_history: list[dict] = []  # 只含 competition_class == 'league' 的已处理记录,按时间序

    for idx, rec in enumerate(qualifying):
        cur_dt = _parse_dt(rec["kickoff_utc"])

        prev = qualifying[idx - 1] if idx > 0 else None
        if prev is not None:
            prev_dt = _parse_dt(prev["kickoff_utc"])
            rest_hours = (cur_dt - prev_dt).total_seconds() / 3600.0
        else:
            rest_hours = None  # 窗口左边界之前是否还有比赛未知(截断风险),不得臆造

        matches_last_7d = sum(
            1 for r2 in qualifying[:idx]
            if 0 < (cur_dt - _parse_dt(r2["kickoff_utc"])).total_seconds() / 86400.0 <= 7
        )
        matches_last_14d = sum(
            1 for r2 in qualifying[:idx]
            if 0 < (cur_dt - _parse_dt(r2["kickoff_utc"])).total_seconds() / 86400.0 <= 14
        )

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
        }

        if rec["competition_class"] == "league":
            prev_league = league_history[-1] if league_history else None
            if prev_league is not None:
                prev_league_dt = _parse_dt(prev_league["kickoff_utc"])
                league_only_rest_hours = (cur_dt - prev_league_dt).total_seconds() / 3600.0
                entry["league_only_previous_match_id"] = prev_league["provider_match_id"]
            else:
                league_only_rest_hours = None
                entry["league_only_previous_match_id"] = None
            entry["league_only_rest_hours"] = league_only_rest_hours
            entry["all_comp_rest_hours"] = rest_hours
            league_history.append(rec)
        else:
            entry["league_only_rest_hours"] = None
            entry["all_comp_rest_hours"] = None
            entry["league_only_previous_match_id"] = None

        out.append(entry)

    return out


def find_cross_comp_rest_examples(rest_rows: list[dict]) -> list[dict]:
    """筛出 all_comp_rest_hours < league_only_rest_hours 的真实样本(§八)。"""
    out = []
    for r in rest_rows:
        a, l = r.get("all_comp_rest_hours"), r.get("league_only_rest_hours")
        if a is not None and l is not None and a < l:
            out.append({
                **r,
                "diff_hours": l - a,
            })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _redact_check(text: str) -> None:
    """stdout/stderr 不得包含代理凭证——防御性自检(不是万能扫描,只挡最常见形状)。"""
    lowered = text.lower()
    if "thordata_proxy" in lowered and "=" in text:
        raise RuntimeError("输出中检测到疑似 THORDATA_PROXY 赋值,已阻止打印")
    if "@" in text and "://" in text and (":" in text.split("://", 1)[-1].split("@")[0]):
        # user:pass@host 形状的粗略防御性检测
        raise RuntimeError("输出中检测到疑似 user:password@host 形式的凭证 URL,已阻止打印")


def _utc_now_iso() -> str:
    from backend.db.util import utc_now_iso
    return utc_now_iso()


def run_cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-id", type=int, required=True)
    parser.add_argument("--season", type=str, required=True)
    parser.add_argument("--live", action="store_true", help="真实发起网络请求(默认关闭)")
    parser.add_argument("--offline-fixture", type=str, default=None, help="离线 fixture JSON 路径")
    parser.add_argument("--max-requests", type=int, default=1, help="--live 模式下允许的最大真实请求数")
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

    if args.offline_fixture:
        with open(args.offline_fixture) as f:
            raw = json.load(f)
        source_endpoint = f"offline_fixture:{args.offline_fixture}"
    else:
        if args.max_requests < 1:
            print(json.dumps({"error": "--max-requests must be >= 1 for --live"}), file=sys.stderr)
            return 2
        from backend.fotmob_client import FotMobClient
        client = FotMobClient()
        raw = client.team_data(args.team_id, season=args.season)
        source_endpoint = "https://www.fotmob.com/api/data/teams"

    try:
        records = parse_team_schedule_response(
            raw, args.team_id, args.season, source_endpoint, observed_at
        )
    except ScheduleConflictError as e:
        print(json.dumps({"error": "schedule_conflict", "detail": str(e)}), file=sys.stderr)
        return 1
    except ScheduleSchemaError as e:
        print(json.dumps({"error": "schedule_schema_error", "detail": str(e)}), file=sys.stderr)
        return 1

    db_path = os.path.join(args.output_dir, "pilot_team_schedule.db")
    conn = sqlite3.connect(db_path)
    try:
        init_pilot_db(conn)
        cal_result = write_match_calendar(conn, records)
        team_result = write_team_match(conn, records)
    except ScheduleConflictError as e:
        print(json.dumps({"error": "db_conflict", "detail": str(e)}), file=sys.stderr)
        return 1
    finally:
        conn.close()

    rest_rows = compute_rest_hours(records)
    cross_comp_examples = find_cross_comp_rest_examples(rest_rows)

    competition_counts: dict[str, int] = {}
    precision_counts: dict[str, int] = {}
    status_counts = {"finished": 0, "cancelled": 0, "upcoming": 0}
    for r in records:
        competition_counts[r["competition_class"]] = competition_counts.get(r["competition_class"], 0) + 1
        precision_counts[r["kickoff_precision"]] = precision_counts.get(r["kickoff_precision"], 0) + 1
        if r["cancelled"]:
            status_counts["cancelled"] += 1
        elif r["finished"]:
            status_counts["finished"] += 1
        else:
            status_counts["upcoming"] += 1

    summary = {
        "provider": PROVIDER,
        "team_id": args.team_id,
        "season_requested": args.season,
        "source_endpoint": source_endpoint,
        "observed_at": observed_at,
        "total_records": len(records),
        "competition_class_counts": competition_counts,
        "kickoff_precision_counts": precision_counts,
        "status_counts": status_counts,
        "db_path": db_path,
        "calendar_write": cal_result,
        "team_match_write": team_result,
        "rest_hours_computed_count": len(rest_rows),
        "cross_comp_rest_examples_found": len(cross_comp_examples),
        "cross_comp_rest_examples": cross_comp_examples[:5],
    }

    out_text = json.dumps(summary, indent=2, ensure_ascii=False, default=str)
    _redact_check(out_text)
    print(out_text)
    return 0


def main() -> None:
    sys.exit(run_cli())


if __name__ == "__main__":
    main()
