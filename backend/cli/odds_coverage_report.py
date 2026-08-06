"""历史赔率存量覆盖报告 + 缺口清单(纯只读)。

目的:回答"五大联赛哪些完赛比赛已经有赔率、哪些没有",供人工决策 + 交给
AWS 的爬取清单使用。绝不修改、复制、移动任何数据——参数解析器里没有任何
`--import/--write/--fix/--move` 开关,也不会写 data/*.db。

三个外部资产的路径都不硬编码到仓库里(它们是这台机器的私有布局,线上
不存在):CLI flag 优先,环境变量兜底,两者都没有则该源标 NOT_CONFIGURED。

每个源的状态是三值 PRESENT / NOT_CONFIGURED / UNREADABLE,不塌缩成布尔。
core / asset_a / asset_b 是"硬源":任一不是 PRESENT,整份缺口清单不写、
CLI 退出码 2 —— 否则会把已有赔率的比赛误报成缺口,一旦被拿去 AWS 爬会是
几千场的无谓请求。legacy_mapping 是"软源",只影响缺口原因(gap_reason)
的细分程度,不影响缺口清单是否可信,缺失时不阻断。

用法:
  python -m backend.cli.odds_coverage_report \
      --asset-a-dir /path/to/miaomiaodi.vip/backend/odds \
      --asset-b-db  /path/to/football_uk.db \
      --legacy-mapping-dir /path/to/miaomiaodi.cc/backend/data

退出码:
  0  OK    源齐全,质量检查无异常
  1  WARN  源齐全,但存在质量异常(不可解析盘口 / A-B 不一致率高 / 有孤儿)
  2  CRITICAL  某个硬源缺失或不可读(且未加 --allow-partial-sources),
             或本仓自己的 core 数据库不可读——两种情况都不写缺口清单
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
import os
import re
import sqlite3
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_OUTPUT_DIR = Path("runtime/research/odds-coverage")

# ── 三值源状态 ────────────────────────────────────────────────────────

PRESENT = "PRESENT"
NOT_CONFIGURED = "NOT_CONFIGURED"
UNREADABLE = "UNREADABLE"


@dataclass(frozen=True)
class SourceState:
    name: str
    status: str
    detail: str | None = None
    record_count: int = 0
    files: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "record_count": self.record_count,
            "files": list(self.files),
        }


@dataclass(frozen=True)
class CoreMatch:
    match_id: int
    league_id: int
    season: str
    date: str | None
    home_team_id: int | None
    away_team_id: int | None
    home_name: str | None
    away_name: str | None
    home_score: int | None
    away_score: int | None
    match_round: str | None


# ── 加载层(IO,薄;每个 loader 返回状态与数据同一个元组,拿不到没有状态的数据) ──


def load_core_matches(
    core_db_path: Path,
    leagues: frozenset[int] | None,
    seasons: frozenset[str] | None,
) -> tuple[SourceState, tuple[CoreMatch, ...], frozenset[int]]:
    """完赛比赛(status='Finish')+ 全量 match_id 集合(用于孤儿判定,不受
    league/season 过滤,与本轮人工验证脚本一致)。"""
    if not core_db_path.exists():
        return (
            SourceState("allwin_core", UNREADABLE, "file_not_found"),
            (),
            frozenset(),
        )
    try:
        conn = sqlite3.connect(f"file:{core_db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        return (
            SourceState("allwin_core", UNREADABLE, f"connect_error:{exc.__class__.__name__}"),
            (),
            frozenset(),
        )
    try:
        all_ids = frozenset(
            int(r[0]) for r in conn.execute("SELECT Match_ID FROM dim_match")
        )
        rows = conn.execute(
            "SELECT Match_ID, League_ID, Season, Date, Home_Team_ID, Away_Team_ID,"
            " Home_Team_Name, Away_Team_Name, home_score, away_score, Match_Round"
            " FROM dim_match WHERE status='Finish'"
        ).fetchall()
    except sqlite3.Error as exc:
        conn.close()
        return (
            SourceState("allwin_core", UNREADABLE, f"query_error:{exc.__class__.__name__}"),
            (),
            frozenset(),
        )
    conn.close()

    matches = []
    for r in rows:
        if leagues is not None and int(r["League_ID"]) not in leagues:
            continue
        if seasons is not None and r["Season"] not in seasons:
            continue
        matches.append(
            CoreMatch(
                match_id=int(r["Match_ID"]),
                league_id=int(r["League_ID"]),
                season=r["Season"],
                date=r["Date"],
                home_team_id=r["Home_Team_ID"],
                away_team_id=r["Away_Team_ID"],
                home_name=r["Home_Team_Name"],
                away_name=r["Away_Team_Name"],
                home_score=r["home_score"],
                away_score=r["away_score"],
                match_round=r["Match_Round"],
            )
        )
    return (
        SourceState("allwin_core", PRESENT, "ok", record_count=len(matches)),
        tuple(matches),
        all_ids,
    )


def load_asset_a(asset_dir: Path | None) -> tuple[SourceState, dict[str, dict]]:
    """miaomiaodi 六槽赔率 JSON(match_odds_data*.json)。多个文件里出现同一
    fotmob_id 时,保留六槽完整度更高的那条(与本轮人工核实脚本口径一致)。"""
    if asset_dir is None:
        return SourceState("asset_a_json", NOT_CONFIGURED), {}
    if not asset_dir.exists() or not asset_dir.is_dir():
        return SourceState("asset_a_json", UNREADABLE, "directory_not_found"), {}

    paths = sorted(globmod.glob(str(asset_dir / "match_odds_data*.json")))
    if not paths:
        return SourceState("asset_a_json", UNREADABLE, "no_match_files_found"), {}

    best: dict[str, dict] = {}
    total_records = 0
    for p in paths:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return (
                SourceState(
                    "asset_a_json", UNREADABLE,
                    f"json_decode_error:{Path(p).name}:{exc.__class__.__name__}",
                ),
                {},
            )
        if not isinstance(data, list):
            return (
                SourceState("asset_a_json", UNREADABLE, f"unexpected_shape:{Path(p).name}"),
                {},
            )
        for rec in data:
            fid = str(rec.get("fotmob_id") or "").strip()
            if not fid:
                continue
            total_records += 1
            prior = best.get(fid)
            if prior is None or _grade_slots(rec) > _grade_slots(prior):
                best[fid] = rec

    files = tuple(Path(p).name for p in paths)
    return (
        SourceState("asset_a_json", PRESENT, "ok", record_count=total_records, files=files),
        best,
    )


def load_asset_b(db_path: Path | None) -> tuple[SourceState, dict[int, dict]]:
    """football_uk.db 的 silver_match_odds:聚合成 match_id -> {(market,period): (line,home,draw,away)}。"""
    if db_path is None:
        return SourceState("asset_b_sqlite", NOT_CONFIGURED), {}
    if not db_path.exists():
        return SourceState("asset_b_sqlite", UNREADABLE, "file_not_found"), {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            "SELECT match_id, market, period, raw_line, raw_home, raw_draw, raw_away"
            " FROM silver_match_odds"
        ).fetchall()
    except sqlite3.Error as exc:
        return (
            SourceState("asset_b_sqlite", UNREADABLE, f"query_error:{exc.__class__.__name__}"),
            {},
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass

    out: dict[int, dict] = defaultdict(dict)
    for match_id, market, period, raw_line, raw_home, raw_draw, raw_away in rows:
        out[int(match_id)][(market, period)] = (raw_line, raw_home, raw_draw, raw_away)
    return (
        SourceState("asset_b_sqlite", PRESENT, "ok", record_count=len(out)),
        dict(out),
    )


_SEASON_TAG_RE = re.compile(r"^(\d{4})/(\d{4})$")


def season_to_tag(season: str | None) -> str | None:
    if not season:
        return None
    m = _SEASON_TAG_RE.match(season)
    if not m:
        return None
    y1, y2 = m.group(1), m.group(2)
    return y1[2:] + y2[2:]


def load_legacy_mappings(
    mapping_dir: Path | None,
) -> tuple[SourceState, dict[str, dict[str, dict]]]:
    """旧仓 nowgoal_<tag>_mapping.json:tag -> {fotmob_id: {titan_id, ...}}。
    只用于把缺口细分成 season_never_attempted / never_mapped / mapped_but_no_odds,
    不影响一场比赛是否算"缺口"。"""
    if mapping_dir is None:
        return SourceState("legacy_mapping", NOT_CONFIGURED), {}
    if not mapping_dir.exists() or not mapping_dir.is_dir():
        return SourceState("legacy_mapping", UNREADABLE, "directory_not_found"), {}

    paths = sorted(globmod.glob(str(mapping_dir / "nowgoal_*_mapping.json")))
    if not paths:
        return SourceState("legacy_mapping", UNREADABLE, "no_mapping_files_found"), {}

    by_tag: dict[str, dict[str, dict]] = {}
    total = 0
    for p in paths:
        m = re.search(r"nowgoal_(\w+)_mapping\.json$", Path(p).name)
        if not m:
            continue
        tag = m.group(1)
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return (
                SourceState(
                    "legacy_mapping", UNREADABLE,
                    f"json_decode_error:{Path(p).name}:{exc.__class__.__name__}",
                ),
                {},
            )
        if not isinstance(data, dict):
            return (
                SourceState("legacy_mapping", UNREADABLE, f"unexpected_shape:{Path(p).name}"),
                {},
            )
        by_tag[tag] = data
        total += len(data)

    files = tuple(Path(p).name for p in paths)
    return (
        SourceState("legacy_mapping", PRESENT, "ok", record_count=total, files=files),
        by_tag,
    )


# ── 纯函数核心(无 IO/无时钟,可用 fixture 完整测试) ──────────────────────


def _values_present(d: Any, keys: tuple[str, ...]) -> bool:
    if not isinstance(d, dict):
        return False
    for k in keys:
        v = d.get(k)
        if v is None:
            return False
        if str(v).strip() in ("", "-"):
            return False
    return True


_AH_OU_KEYS = ("over_or_home", "line", "under_or_away")
_1X2_KEYS = ("home", "draw", "away")


def _grade_slots(rec: dict) -> int:
    """六槽(AH/OU/1x2 × initial/latest)完整度,0..6。"""
    odds = rec.get("odds") or {}
    ah = odds.get("asian_handicap") or {}
    ou = odds.get("over_under") or {}
    onex2 = odds.get("european_1x2") or {}
    slots = 0
    for d, keys in (
        (ah.get("initial"), _AH_OU_KEYS),
        (ah.get("latest"), _AH_OU_KEYS),
        (ou.get("initial"), _AH_OU_KEYS),
        (ou.get("latest"), _AH_OU_KEYS),
        (onex2.get("initial"), _1X2_KEYS),
        (onex2.get("latest"), _1X2_KEYS),
    ):
        if _values_present(d, keys):
            slots += 1
    return slots


def parse_handicap_line(raw: Any) -> float | None:
    """支持四分之一球记法("0/-0.5" → 两值均值),不可解析返回 None(绝不强转 0)。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "-", "N/A"):
        return None
    if "/" in s:
        parts = s.split("/")
        if len(parts) != 2:
            return None
        try:
            a, b = float(parts[0]), float(parts[1])
        except ValueError:
            return None
        return (a + b) / 2
    try:
        return float(s)
    except ValueError:
        return None


def line_movement(initial: float | None, latest: float | None) -> str:
    if initial is None or latest is None:
        return "unparseable"
    if abs(latest - initial) < 1e-9:
        return "flat"
    return "up" if latest > initial else "down"


def grade_asset_a(rec: dict) -> dict:
    odds = rec.get("odds") or {}
    ah = odds.get("asian_handicap") or {}
    ou = odds.get("over_under") or {}
    ah_i = parse_handicap_line((ah.get("initial") or {}).get("line"))
    ah_l = parse_handicap_line((ah.get("latest") or {}).get("line"))
    ou_i = parse_handicap_line((ou.get("initial") or {}).get("line"))
    ou_l = parse_handicap_line((ou.get("latest") or {}).get("line"))
    return {
        "slots_complete": _grade_slots(rec),
        "ah_initial_line": ah_i,
        "ah_latest_line": ah_l,
        "ah_movement": line_movement(ah_i, ah_l),
        "ou_initial_line": ou_i,
        "ou_latest_line": ou_l,
        "ou_movement": line_movement(ou_i, ou_l),
    }


def grade_asset_b(slots: Mapping[tuple[str, str], tuple]) -> dict:
    ah_open = slots.get(("ah", "opening"))
    ah_close = slots.get(("ah", "closing"))
    ou_open = slots.get(("ou", "opening"))
    ou_close = slots.get(("ou", "closing"))
    ah_i = ah_open[0] if ah_open else None
    ah_l = ah_close[0] if ah_close else None
    ou_i = ou_open[0] if ou_open else None
    ou_l = ou_close[0] if ou_close else None
    return {
        "has_ah_open_close": ah_open is not None and ah_close is not None,
        "has_ou_open_close": ou_open is not None and ou_close is not None,
        "ah_movement": line_movement(ah_i, ah_l),
        "ou_movement": line_movement(ou_i, ou_l),
    }


def compare_lines(a: float | None, b: float | None, tol: float) -> str:
    """两个独立来源的同一槽位盘口线比较:精确一致 / 互为相反数(符号约定不同)
    / 其他不一致 / 不可比。绝不静默把「互为相反数」归并进「一致」——那正是
    P0-2 揭示的两套不同符号约定,归并会掩盖真实差异。"""
    if a is None or b is None:
        return "not_comparable"
    if abs(a - b) <= tol:
        return "exact_match"
    if abs(a + b) <= tol:
        return "mirrored_match"
    return "other_mismatch"


def deterministic_sample(ids: list[int], size: int) -> list[int]:
    """确定性等距抽样,不用 RANDOM(),两次运行结果完全相同。"""
    ids_sorted = sorted(ids)
    if size <= 0 or not ids_sorted:
        return []
    if len(ids_sorted) <= size:
        return ids_sorted
    step = len(ids_sorted) / size
    picks = []
    seen = set()
    for i in range(size):
        idx = int(i * step)
        if idx not in seen:
            seen.add(idx)
            picks.append(ids_sorted[idx])
    return picks


def classify_gap(
    match: CoreMatch,
    legacy_state: SourceState,
    legacy_by_tag: Mapping[str, Mapping[str, dict]],
) -> tuple[str, str | None]:
    if legacy_state.status != PRESENT:
        return "unclassified", "legacy_mapping_source_not_available"
    tag = season_to_tag(match.season)
    if tag is None:
        return "unclassified", f"unparseable_season:{match.season}"
    season_map = legacy_by_tag.get(tag)
    if season_map is None:
        return "season_never_attempted", f"no_mapping_file_for_tag_{tag}"
    entry = season_map.get(str(match.match_id))
    if entry is None:
        return "never_mapped", None
    return "mapped_but_no_odds", f"titan_id={entry.get('titan_id')}"


LEAGUE_CODE_ZH = {
    47: ("epl", "英超"),
    87: ("laliga", "西甲"),
    55: ("seriea", "意甲"),
    54: ("bundesliga", "德甲"),
    53: ("ligue1", "法甲"),
}


def build_coverage(
    *,
    matches: tuple[CoreMatch, ...],
    all_core_ids: frozenset[int],
    asset_a: Mapping[str, dict],
    asset_a_state: SourceState,
    asset_b: Mapping[int, dict],
    asset_b_state: SourceState,
    legacy_by_tag: Mapping[str, Mapping[str, dict]],
    legacy_state: SourceState,
    consistency_sample_size: int,
    odds_tolerance: float,
    generated_at: str,
) -> dict:
    matrix_acc: dict[tuple[int, str], dict] = defaultdict(
        lambda: {"finished": 0, "has_asset_a": 0, "has_asset_b": 0, "union": 0}
    )
    gaps: list[dict] = []
    gap_reason_counts: Counter = Counter()

    for m in matches:
        key = (m.league_id, m.season)
        row = matrix_acc[key]
        row["finished"] += 1
        sid = str(m.match_id)
        in_a = sid in asset_a
        in_b = m.match_id in asset_b
        if in_a:
            row["has_asset_a"] += 1
        if in_b:
            row["has_asset_b"] += 1
        if in_a or in_b:
            row["union"] += 1
            continue
        reason, detail = classify_gap(m, legacy_state, legacy_by_tag)
        gap_reason_counts[reason] += 1
        gaps.append(
            {
                "schema_version": 1,
                "match_id": m.match_id,
                "league_id": m.league_id,
                "league_code": LEAGUE_CODE_ZH.get(m.league_id, (None, None))[0],
                "league_name_zh": LEAGUE_CODE_ZH.get(m.league_id, (None, None))[1],
                "season": m.season,
                "match_round": m.match_round,
                "match_date_local": m.date,
                "kickoff_at_utc": None,
                "kickoff_precision": "date_only",
                "home_team_id": m.home_team_id,
                "away_team_id": m.away_team_id,
                "home_team_name_en": m.home_name,
                "away_team_name_en": m.away_name,
                "home_score": m.home_score,
                "away_score": m.away_score,
                "gap_reason": reason,
                "gap_reason_detail": detail,
            }
        )

    matrix = []
    totals = {"finished": 0, "has_asset_a": 0, "has_asset_b": 0, "union": 0, "gap": 0}
    for (league_id, season), row in sorted(matrix_acc.items()):
        gap = row["finished"] - row["union"]
        code, name_zh = LEAGUE_CODE_ZH.get(league_id, (None, None))
        matrix.append(
            {
                "league_id": league_id,
                "league_code": code,
                "league_name_zh": name_zh,
                "season": season,
                "finished": row["finished"],
                "has_asset_a": row["has_asset_a"],
                "has_asset_b": row["has_asset_b"],
                "union": row["union"],
                "gap": gap,
            }
        )
        totals["finished"] += row["finished"]
        totals["has_asset_a"] += row["has_asset_a"]
        totals["has_asset_b"] += row["has_asset_b"]
        totals["union"] += row["union"]
        totals["gap"] += gap

    # 六槽完整度与盘口升降三分布(资产 A)
    six_slot = 0
    ah_move: Counter = Counter()
    ou_move: Counter = Counter()
    for rec in asset_a.values():
        g = grade_asset_a(rec)
        if g["slots_complete"] == 6:
            six_slot += 1
        ah_move[g["ah_movement"]] += 1
        ou_move[g["ou_movement"]] += 1

    # 资产 B 的 AH/OU open+close 覆盖与升降
    b_ah_open_close = 0
    b_ou_open_close = 0
    b_ah_move: Counter = Counter()
    b_ou_move: Counter = Counter()
    for slots in asset_b.values():
        g = grade_asset_b(slots)
        if g["has_ah_open_close"]:
            b_ah_open_close += 1
        if g["has_ou_open_close"]:
            b_ou_open_close += 1
        b_ah_move[g["ah_movement"]] += 1
        b_ou_move[g["ou_movement"]] += 1

    # A/B 一致性抽样(确定性)
    overlap_ids = [int(mid) for mid in asset_a if mid.isdigit() and int(mid) in asset_b]
    sample_ids = deterministic_sample(overlap_ids, consistency_sample_size)
    consistency_rows = []
    consistency_counts: Counter = Counter()
    for mid in sample_ids:
        a_rec = asset_a[str(mid)]
        b_slots = asset_b[mid]
        ag = grade_asset_a(a_rec)
        bg_open = b_slots.get(("ah", "opening"))
        bg_close = b_slots.get(("ah", "closing"))
        b_init = bg_open[0] if bg_open else None
        b_late = bg_close[0] if bg_close else None
        init_cls = compare_lines(ag["ah_initial_line"], b_init, odds_tolerance)
        late_cls = compare_lines(ag["ah_latest_line"], b_late, odds_tolerance)
        consistency_counts[("initial", init_cls)] += 1
        consistency_counts[("latest", late_cls)] += 1
        consistency_rows.append(
            {
                "match_id": mid,
                "asset_a_ah_initial": ag["ah_initial_line"],
                "asset_b_ah_opening": b_init,
                "initial_classification": init_cls,
                "asset_a_ah_latest": ag["ah_latest_line"],
                "asset_b_ah_closing": b_late,
                "latest_classification": late_cls,
            }
        )

    orphans = sorted(
        int(mid) for mid in asset_a if mid.isdigit() and int(mid) not in all_core_ids
    )

    return {
        "generated_at": generated_at,
        "sources": {
            "allwin_core": SourceState("allwin_core", PRESENT).as_dict(),
            "asset_a_json": asset_a_state.as_dict(),
            "asset_b_sqlite": asset_b_state.as_dict(),
            "legacy_mapping": legacy_state.as_dict(),
        },
        "matrix": matrix,
        "totals": totals,
        "gap_reason_counts": dict(gap_reason_counts),
        "market_quality": {
            "asset_a": {
                "unique_fotmob_ids": len(asset_a),
                "six_slot_complete": six_slot,
                "ah_movement": dict(ah_move),
                "ou_movement": dict(ou_move),
            },
            "asset_b": {
                "matches_total": len(asset_b),
                "ah_open_close": b_ah_open_close,
                "ou_open_close": b_ou_open_close,
                "ah_movement": dict(b_ah_move),
                "ou_movement": dict(b_ou_move),
            },
        },
        "consistency_sample": {
            "sample_size": len(sample_ids),
            "overlap_population": len(overlap_ids),
            "counts": {f"{slot}:{cls}": n for (slot, cls), n in consistency_counts.items()},
            "rows": consistency_rows,
        },
        "orphans": {"count": len(orphans), "ids": orphans},
    }, gaps


# ── 写出层(复用 kbisai_live_scores.py 的原子写约定) ──────────────────────


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: list[dict]) -> bytes:
    return "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for r in rows
    ).encode("utf-8")


def write_artifacts(output_dir: Path, run_id: str, report: dict, gaps: list[dict], partial: bool) -> Path:
    run_dir = output_dir / run_id
    _atomic_write(run_dir / "coverage_report.json", _json_bytes(report))
    gaps_name = "gaps.PARTIAL.jsonl" if partial else "gaps.jsonl"
    _atomic_write(run_dir / gaps_name, _jsonl_bytes(gaps))
    return run_dir


# ── --expect-json 反幻觉核对 ────────────────────────────────────────────


def compare_expectations(report: dict, gaps: list[dict], expect: dict) -> list[str]:
    """把外部提供的先验数字与本次复算逐项比对,冲突时以复算值为准并如实报告。"""
    recomputed = {
        "total_finished": report["totals"]["finished"],
        "asset_a_unique_ids": report["market_quality"]["asset_a"]["unique_fotmob_ids"],
        "asset_a_six_slot_complete": report["market_quality"]["asset_a"]["six_slot_complete"],
        "gap_total": report["totals"]["gap"],
        "orphan_count": report["orphans"]["count"],
    }
    lines = []
    for key, prior in expect.items():
        if key not in recomputed:
            continue
        actual = recomputed[key]
        if actual == prior:
            lines.append(f"AGREE {key}={actual}")
        else:
            lines.append(f"DISAGREE {key} prior={prior} recomputed={actual}")
    return lines


# ── CLI ──────────────────────────────────────────────────────────────


def _default_league_ids() -> frozenset[int]:
    from backend.queries.leagues import LEAGUE_META

    return frozenset(
        lid
        for lid, meta in LEAGUE_META.items()
        if meta["entitlement"] in ("league:epl", "league:top5")
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="历史赔率存量覆盖报告 + 缺口清单(只读,不写任何数据库)"
    )
    p.add_argument("--asset-a-dir", type=Path, default=None)
    p.add_argument("--asset-b-db", type=Path, default=None)
    p.add_argument("--legacy-mapping-dir", type=Path, default=None)
    p.add_argument("--core-db", type=Path, default=None)
    p.add_argument("--league", type=int, action="append", default=None)
    p.add_argument("--season", type=str, action="append", default=None)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--allow-partial-sources", action="store_true")
    p.add_argument("--consistency-sample", type=int, default=200)
    p.add_argument("--odds-tolerance", type=float, default=1e-6)
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--no-artifacts", action="store_true")
    p.add_argument("--expect-json", type=Path, default=None)
    return p


def _print_text(report: dict) -> None:
    print(f"覆盖报告 @ {report['generated_at']}")
    for name, s in report["sources"].items():
        print(f"  源 {name}: {s['status']} ({s.get('detail')}) 记录数={s['record_count']}")
    print(f"\n{'联赛':<8}{'赛季':<11}{'完赛':>6}{'资产A':>7}{'资产B':>7}{'并集':>7}{'缺口':>7}")
    for row in report["matrix"]:
        print(
            f"{row['league_name_zh'] or row['league_id']:<8}{row['season']:<11}"
            f"{row['finished']:>6}{row['has_asset_a']:>7}{row['has_asset_b']:>7}"
            f"{row['union']:>7}{row['gap']:>7}"
        )
    t = report["totals"]
    print(f"{'合计':<19}{t['finished']:>6}{t['has_asset_a']:>7}{t['has_asset_b']:>7}{t['union']:>7}{t['gap']:>7}")
    print(f"\n缺口原因分布: {report['gap_reason_counts']}")
    print(f"\n资产A: 唯一id={report['market_quality']['asset_a']['unique_fotmob_ids']}"
          f" 六槽完整={report['market_quality']['asset_a']['six_slot_complete']}"
          f" AH升降={report['market_quality']['asset_a']['ah_movement']}"
          f" OU升降={report['market_quality']['asset_a']['ou_movement']}")
    print(f"资产B: 比赛数={report['market_quality']['asset_b']['matches_total']}"
          f" AH开收全={report['market_quality']['asset_b']['ah_open_close']}"
          f" AH升降={report['market_quality']['asset_b']['ah_movement']}")
    cs = report["consistency_sample"]
    print(f"\nA/B 一致性抽样(n={cs['sample_size']}/{cs['overlap_population']}): {cs['counts']}")
    print(f"\n孤儿(资产A有、all-win完全没有): {report['orphans']['count']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from backend.db.paths import db_path

    core_db_path = args.core_db or db_path("core")
    asset_a_dir = args.asset_a_dir or (
        Path(os.environ["ALLWIN_ODDS_ASSET_A_DIR"]) if os.environ.get("ALLWIN_ODDS_ASSET_A_DIR") else None
    )
    asset_b_db = args.asset_b_db or (
        Path(os.environ["ALLWIN_ODDS_ASSET_B_DB"]) if os.environ.get("ALLWIN_ODDS_ASSET_B_DB") else None
    )
    legacy_dir = args.legacy_mapping_dir or (
        Path(os.environ["ALLWIN_ODDS_LEGACY_MAPPING_DIR"])
        if os.environ.get("ALLWIN_ODDS_LEGACY_MAPPING_DIR")
        else None
    )

    leagues = frozenset(args.league) if args.league else _default_league_ids()
    seasons = frozenset(args.season) if args.season else None

    core_state, matches, all_core_ids = load_core_matches(core_db_path, leagues, seasons)
    if core_state.status != PRESENT:
        print(
            json.dumps({"status": "CRITICAL", "reason": "core_db_unavailable", "detail": core_state.detail}),
            file=sys.stderr,
        )
        return 2

    asset_a_state, asset_a = load_asset_a(asset_a_dir)
    asset_b_state, asset_b = load_asset_b(asset_b_db)
    legacy_state, legacy_by_tag = load_legacy_mappings(legacy_dir)

    hard_missing = [s for s in (asset_a_state, asset_b_state) if s.status != PRESENT]
    if hard_missing and not args.allow_partial_sources:
        print(
            json.dumps(
                {
                    "status": "CRITICAL",
                    "reason": "hard_source_unavailable",
                    "sources": [s.as_dict() for s in hard_missing],
                    "hint": "配置缺失的 --asset-a-dir/--asset-b-db(或对应环境变量),"
                    "或显式加 --allow-partial-sources 承担风险",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    partial = bool(hard_missing) and args.allow_partial_sources

    report, gaps = build_coverage(
        matches=matches,
        all_core_ids=all_core_ids,
        asset_a=asset_a,
        asset_a_state=asset_a_state,
        asset_b=asset_b,
        asset_b_state=asset_b_state,
        legacy_by_tag=legacy_by_tag,
        legacy_state=legacy_state,
        consistency_sample_size=args.consistency_sample,
        odds_tolerance=args.odds_tolerance,
        generated_at="unset",
    )
    report["partial"] = partial
    if partial:
        for g in gaps:
            g["sources_consulted"] = [
                s.name for s in (asset_a_state, asset_b_state) if s.status == PRESENT
            ]

    if not args.no_artifacts:
        run_id = uuid.uuid4().hex[:20]
        run_dir = write_artifacts(args.output_dir, run_id, report, gaps, partial)
        report["run_dir"] = str(run_dir)

    if args.expect_json:
        try:
            expect = json.loads(args.expect_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"--expect-json 读取失败: {exc}", file=sys.stderr)
            return 2
        for line in compare_expectations(report, gaps, expect):
            print(line)

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text(report)

    anomaly = (
        report["market_quality"]["asset_a"]["ah_movement"].get("unparseable", 0) > 0
        or report["orphans"]["count"] > 0
        or report["consistency_sample"]["counts"].get("initial:other_mismatch", 0) > 0
    )
    return 1 if anomaly else 0


if __name__ == "__main__":
    raise SystemExit(main())
