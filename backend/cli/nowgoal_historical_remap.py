"""驱动 backend.ingest.nowgoal_historical_match_resolution 对 Phase 0 产出的
缺口清单(gaps.jsonl)做离线重映射:零网络请求,只读旧仓的 fixtures/mapping/
archive 文件和本仓 core 数据库,不写 data/odds.db,不移动/复制任何外部文件。

训练数据(球队 id 词典):从旧仓 nowgoal_<tag>_mapping.json(fotmob_id→titan_id)
+ nowgoal_<tag>_fixtures.json(titan_id→NowGoal home_id/away_id/score)+ 本仓
dim_match(fotmob_id→FotMob home_team_id/away_team_id/home_score/away_score)
三方 join 得到,方向由 team_direction_from_score 从比分重推,不读任何
``is_swapped`` 字段。

候选来源:
- 2021/22~2025/26:同一批 nowgoal_<tag>_fixtures.json(已含 titan_id/联赛/
  北京时间/队伍 id/队名/比分)。
- 2020/21:runtime/research/.../archive.<league>.2020-2021.bin(真实 JSON,
  ScheduleList 每行 [titan_id, league_id, -1, "北京时间", home_id, away_id,
  "H-A", ...],TeamInfo 提供 team_id→英文队名)。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from backend.ingest.nowgoal_historical_match_resolution import (
    FOTMOB_TO_NOWGOAL_LEAGUE,
    GapMatch,
    NowGoalCandidate,
    TrainingPair,
    build_team_id_dictionary,
    kickoff_precision,
    parse_score,
    resolve_batch,
)

DEFAULT_OUTPUT_DIR = Path("runtime/research/nowgoal-historical-remap")

_TAGS = ("2122", "2223", "2324", "2425", "2526")


def load_gap_matches(gaps_path: Path) -> list[GapMatch]:
    matches = []
    for line in gaps_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        matches.append(
            GapMatch(
                match_id=r["match_id"],
                league_id=r["league_id"],
                date=r["match_date_local"],
                home_team_id=r["home_team_id"],
                away_team_id=r["away_team_id"],
                home_team_name=r["home_team_name_en"],
                away_team_name=r["away_team_name_en"],
                home_score=r["home_score"],
                away_score=r["away_score"],
            )
        )
    return matches


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_fixtures_candidates(fixtures_dir: Path, tag: str) -> list[NowGoalCandidate]:
    path = fixtures_dir / f"nowgoal_{tag}_fixtures.json"
    if not path.exists():
        return []
    out = []
    for r in _load_json(path):
        score = parse_score(r.get("score"))
        out.append(
            NowGoalCandidate(
                titan_id=str(r["titan_id"]),
                nowgoal_league_id=int(r["league_id_nowgoal"]),
                kickoff_local=r.get("kickoff_local"),
                home_team_id=r.get("home_id"),
                away_team_id=r.get("away_id"),
                home_team_name=r.get("home_name"),
                away_team_name=r.get("away_name"),
                home_score=score[0] if score else None,
                away_score=score[1] if score else None,
            )
        )
    return out


def _iter_schedule_rows(node: Any):
    """ScheduleList 的嵌套深度不统一:英超/西甲/德甲/法甲是 {"R_1": [[...],...]},
    意甲实测多一层 {"sub_2948": {"R_1": [[...],...]}}——递归下钻直到遇到
    "list of row-lists"(每行以 titan_id 整数开头),不假设固定层数。"""
    if isinstance(node, dict):
        for v in node.values():
            yield from _iter_schedule_rows(v)
    elif isinstance(node, list):
        if node and isinstance(node[0], list):
            for row in node:
                yield row
        else:
            for item in node:
                yield from _iter_schedule_rows(item)


def load_archive_candidates(archive_dir: Path) -> list[NowGoalCandidate]:
    """2020/21 五大联赛 archive(runtime/research/.../raw/archive.*.2020-2021.bin,
    真实 JSON,非二进制——扩展名沿用探测阶段的命名)。"""
    out: list[NowGoalCandidate] = []
    if not archive_dir.exists():
        return out
    for path in sorted(archive_dir.glob("archive.*.2020-2021.bin")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        team_names: dict[int, str] = {}
        for entry in data.get("TeamInfo") or []:
            if isinstance(entry, list) and len(entry) >= 2:
                team_names[int(entry[0])] = str(entry[1])
        schedule = data.get("ScheduleList") or {}
        for row in _iter_schedule_rows(schedule):
            if not isinstance(row, list) or len(row) < 7:
                continue
            titan_id, league_id, _flag, kickoff_local, home_id, away_id, score_str = row[:7]
            score = parse_score(score_str)
            out.append(
                NowGoalCandidate(
                    titan_id=str(titan_id),
                    nowgoal_league_id=int(league_id),
                    kickoff_local=kickoff_local,
                    home_team_id=int(home_id) if home_id is not None else None,
                    away_team_id=int(away_id) if away_id is not None else None,
                    home_team_name=team_names.get(int(home_id)) if home_id is not None else None,
                    away_team_name=team_names.get(int(away_id)) if away_id is not None else None,
                    home_score=score[0] if score else None,
                    away_score=score[1] if score else None,
                )
            )
    return out


def load_training_pairs(core_db_path: Path, legacy_mapping_dir: Path, fixtures_dir: Path) -> list[TrainingPair]:
    """join: legacy mapping(fotmob_id→titan_id) × fixtures(titan_id→NowGoal 队伍/比分)
    × dim_match(fotmob_id→FotMob 队伍/比分)。"""
    conn = sqlite3.connect(f"file:{core_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    fm_rows = {
        str(r["Match_ID"]): r
        for r in conn.execute(
            "SELECT Match_ID, Home_Team_ID, Away_Team_ID, home_score, away_score FROM dim_match"
        )
    }
    conn.close()

    pairs: list[TrainingPair] = []
    for tag in _TAGS:
        mapping_path = legacy_mapping_dir / f"nowgoal_{tag}_mapping.json"
        if not mapping_path.exists():
            continue
        mapping = _load_json(mapping_path)
        fixtures_by_titan = {
            str(r["titan_id"]): r for r in load_fixtures_raw(fixtures_dir, tag)
        }
        for fotmob_id, entry in mapping.items():
            fm_row = fm_rows.get(str(fotmob_id))
            fx = fixtures_by_titan.get(str(entry.get("titan_id")))
            if fm_row is None or fx is None:
                continue
            ng_score = parse_score(fx.get("score"))
            pairs.append(
                TrainingPair(
                    ng_home_id=fx.get("home_id"),
                    ng_away_id=fx.get("away_id"),
                    ng_home_score=ng_score[0] if ng_score else None,
                    ng_away_score=ng_score[1] if ng_score else None,
                    fm_home_id=fm_row["Home_Team_ID"],
                    fm_away_id=fm_row["Away_Team_ID"],
                    fm_home_score=fm_row["home_score"],
                    fm_away_score=fm_row["away_score"],
                )
            )
    return pairs


def load_fixtures_raw(fixtures_dir: Path, tag: str) -> list[dict]:
    path = fixtures_dir / f"nowgoal_{tag}_fixtures.json"
    if not path.exists():
        return []
    return _load_json(path)


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


def _jsonl_bytes(rows: list[dict]) -> bytes:
    return "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for r in rows
    ).encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="缺口清单离线重映射(只读,不写 odds.db)")
    p.add_argument("--gaps-file", type=Path, required=True)
    p.add_argument("--core-db", type=Path, required=True)
    p.add_argument("--legacy-mapping-dir", type=Path, required=True)
    p.add_argument("--fixtures-dir", type=Path, required=True)
    p.add_argument("--archive-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--min-votes", type=int, default=10)
    p.add_argument("--min-margin-ratio", type=float, default=5.0)
    p.add_argument("--date-window-days", type=int, default=2)
    p.add_argument("--name-threshold", type=float, default=0.6)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    gap_matches = load_gap_matches(args.gaps_file)
    training_pairs = load_training_pairs(args.core_db, args.legacy_mapping_dir, args.fixtures_dir)
    team_id_dict = build_team_id_dictionary(
        training_pairs, min_votes=args.min_votes, min_margin_ratio=args.min_margin_ratio
    )

    all_candidates: list[NowGoalCandidate] = list(load_archive_candidates(args.archive_dir))
    for tag in _TAGS:
        all_candidates.extend(load_fixtures_candidates(args.fixtures_dir, tag))

    league_buckets: dict[int, list[NowGoalCandidate]] = defaultdict(list)
    for c in all_candidates:
        league_buckets[c.nowgoal_league_id].append(c)

    candidates_by_match_id = {}
    provider_unknown_kickoff_count = 0
    for gm in gap_matches:
        ng_league = FOTMOB_TO_NOWGOAL_LEAGUE.get(gm.league_id)
        bucket = league_buckets.get(ng_league, [])
        candidates_by_match_id[gm.match_id] = bucket

    for c in all_candidates:
        if kickoff_precision(c.kickoff_local) == "provider_unknown":
            provider_unknown_kickoff_count += 1

    results = resolve_batch(
        gap_matches, candidates_by_match_id, team_id_dict,
        date_window_days=args.date_window_days, name_threshold=args.name_threshold,
    )

    status_counts = Counter(r.status for r in results)
    evidence_counts = Counter((r.status, r.evidence_kind) for r in results if r.evidence_kind)
    inverted_count = sum(1 for r in results if r.direction == "inverted")

    rows = []
    for r in results:
        rows.append(
            {
                "match_id": r.match_id,
                "status": r.status,
                "titan_id": r.titan_id,
                "direction": r.direction,
                "evidence_kind": r.evidence_kind,
                "evidence_strength": r.evidence_strength,
                "home_away_inverted": r.home_away_inverted,
                "detail": r.detail,
                "nowgoal_kickoff_local": r.matched_titan_kickoff_local,
            }
        )

    summary = {
        "gap_total": len(gap_matches),
        "team_id_dictionary_size": len(team_id_dict),
        "training_pairs_total": len(training_pairs),
        "candidates_total": len(all_candidates),
        "provider_unknown_kickoff_candidates": provider_unknown_kickoff_count,
        "status_counts": dict(status_counts),
        "evidence_counts": {f"{s}:{k}": n for (s, k), n in evidence_counts.items()},
        "inverted_direction_count": inverted_count,
        "auto_ok_rate": round(status_counts.get("auto_ok", 0) / len(gap_matches), 4) if gap_matches else 0.0,
    }

    run_id = uuid.uuid4().hex[:20]
    run_dir = args.output_dir / run_id
    _atomic_write(run_dir / "resolution_results.jsonl", _jsonl_bytes(rows))
    _atomic_write(
        run_dir / "summary.json",
        (json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"run_dir: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
