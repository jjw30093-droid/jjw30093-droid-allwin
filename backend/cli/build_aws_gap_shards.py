"""把 Phase 0 的缺口清单(gaps.jsonl)与 Phase 1 的重映射结果
(resolution_results.jsonl)按 match_id 拼接，切成"联赛×赛季"分片，产出可以
直接喂给 ``nowgoal_top5_history_backfill.py --match-allowlist`` 的清单。

只读、只写 runtime/research/ 下的产物，不碰 data/*.db、不发网络请求。

每个分片两个文件：
- ``<league_key>-<season_dash>.ready.jsonl``：``resolution.status=='auto_ok'``
  的比赛，直接喂给 ``--match-allowlist``。
- ``<league_key>-<season_dash>.review.jsonl``：其余状态(needs_review /
  no_candidate / ambiguous / titan_conflict)，留给人工复核，不进爬取清单。
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT_DIR = Path("runtime/research/nowgoal-aws-gap-shards")

# FotMob League_ID -> nowgoal_top5_history_backfill.py 的 --league key
# (与 analysis/nowgoal_historical_capability_probe/...:TOP5_ARCHIVE_LEAGUES 的
# ArchiveLeagueSpec.key 完全对应，改这里必须同步核对那边)。
FOTMOB_LEAGUE_TO_ARCHIVE_KEY = {
    47: "premier_league",
    55: "serie_a",
    87: "la_liga",
    54: "bundesliga",
    53: "ligue_1",
}


def season_to_dash(season: str) -> str | None:
    """"2020/2021" -> "2020-2021"(与 --seasons 的 archive season 格式一致)。"""
    if not season or "/" not in season:
        return None
    y1, y2 = season.split("/", 1)
    if not (y1.isdigit() and y2.isdigit()):
        return None
    return f"{y1}-{y2}"


def load_gaps_by_match_id(path: Path) -> dict[int, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out[row["match_id"]] = row
    return out


def load_resolutions_by_match_id(path: Path) -> dict[int, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out[row["match_id"]] = row
    return out


def build_shards(gaps: dict[int, dict], resolutions: dict[int, dict]) -> dict[str, dict]:
    """返回 {shard_key: {"ready": [...], "review": [...], "unresolved": [...]}}。
    ``unresolved`` 记录 gaps 里有、但 resolutions 里完全没有对应结果的 match_id——
    这不该发生(Phase 1 对全部缺口跑了一遍),出现即说明两份输入不是同一批产出,
    必须如实报告而不是静默丢弃。"""
    shards: dict[str, dict] = defaultdict(lambda: {"ready": [], "review": [], "unresolved": []})
    for match_id, gap in gaps.items():
        dash_season = season_to_dash(gap["season"])
        archive_key = FOTMOB_LEAGUE_TO_ARCHIVE_KEY.get(gap["league_id"])
        shard_key = f"{archive_key or 'unknown-league'}-{dash_season or 'unknown-season'}"

        res = resolutions.get(match_id)
        if res is None:
            shards[shard_key]["unresolved"].append({"match_id": match_id, **gap})
            continue

        row = {
            "titan_id": res.get("titan_id"),
            "match_id": match_id,
            "league_id": gap["league_id"],
            "league_code": gap.get("league_code"),
            "season": gap["season"],
            "match_date_local": gap.get("match_date_local"),
            "home_team_name_en": gap.get("home_team_name_en"),
            "away_team_name_en": gap.get("away_team_name_en"),
            "home_score": gap.get("home_score"),
            "away_score": gap.get("away_score"),
            "resolution_status": res.get("status"),
            "resolution_evidence_kind": res.get("evidence_kind"),
            "resolution_detail": res.get("detail"),
            "home_away_inverted": res.get("home_away_inverted"),
            # NowGoal 侧自己的北京时间开球时刻(来自胜出候选,不是 FotMob 的自然日)。
            # 这是唯一可信的精确 kickoff 来源——下游要跳过 archive_season 重新发现、
            # 直接按 titan_id 抓历史赔率并做 pre-match/in-play 切分时必须用它。
            "nowgoal_kickoff_local": res.get("nowgoal_kickoff_local"),
        }
        if res.get("status") == "auto_ok":
            shards[shard_key]["ready"].append(row)
        else:
            shards[shard_key]["review"].append(row)
    return dict(shards)


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
    p = argparse.ArgumentParser(description="缺口清单 + 重映射结果 -> AWS 爬取分片(只读)")
    p.add_argument("--gaps-file", type=Path, required=True)
    p.add_argument("--resolution-file", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    gaps = load_gaps_by_match_id(args.gaps_file)
    resolutions = load_resolutions_by_match_id(args.resolution_file)
    shards = build_shards(gaps, resolutions)

    run_id = uuid.uuid4().hex[:20]
    run_dir = args.output_dir / run_id
    summary: dict[str, Any] = {"gap_total": len(gaps), "shards": {}}

    for shard_key, buckets in sorted(shards.items()):
        ready, review, unresolved = buckets["ready"], buckets["review"], buckets["unresolved"]
        if ready:
            _atomic_write(run_dir / f"{shard_key}.ready.jsonl", _jsonl_bytes(ready))
        if review:
            _atomic_write(run_dir / f"{shard_key}.review.jsonl", _jsonl_bytes(review))
        if unresolved:
            _atomic_write(run_dir / f"{shard_key}.unresolved.jsonl", _jsonl_bytes(unresolved))
        summary["shards"][shard_key] = {
            "ready": len(ready), "review": len(review), "unresolved": len(unresolved),
        }

    _atomic_write(
        run_dir / "summary.json",
        (json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"run_dir: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
