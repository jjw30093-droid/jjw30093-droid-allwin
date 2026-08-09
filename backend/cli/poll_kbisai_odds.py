"""kbisai(春秋直播)赔率完整变化序列采集 CLI(本轮任务 §9)。

前置条件:目标比赛必须已经在 dim_match_xref 里有 provider='kbisai' 的映射。
身份映射(backend.ingest.kbisai_match_resolution)是独立的一步,不在本 CLI 里
做——采集和身份解析分开,方便先复核映射结果、再决定要不要花请求去采集,也
避免把两种完全不同的失败模式(“匹配不到这场比赛”vs“匹配到了但赔率抓取失败”)
混在一次调用里。没有映射的 --match-id 会被跳过并如实报告在 summary 里,
不会用猜测的 provider_match_id 顶上去。

幂等:matchAllOdds 每次都返回该场比赛某个市场当前全部历史,所以每次调用天然
就是"从初盘到现在的完整序列"。重复调用只会让已存在的点位在写入层被识别成
duplicate(见 backend.ingest.kbisai_odds_snapshots),不会重复膨胀。

请求预算:每场比赛每个 market 一次 matchAllOdds 请求,外加一次 allCompany。
--max-requests 是硬上限——超过就整体中止(ABORTED_BUDGET)并如实报告,不会
静默只采集一部分然后当作成功退出。

用法:
  .venv/bin/python -m backend.cli.poll_kbisai_odds \
      --match-id 5104970 --match-id 5104971 \
      --companies 2,7,22 --markets eu,asia,bs
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from backend.db.connections import connect_ro, connect_rw
from backend.db.util import new_uuid, utc_now_iso
from backend.ingest.kbisai_odds_snapshots import ingest_kbisai_odds_points
from backend.providers.kbisai_live_scores import KbisaiLiveScoreError, safe_exception_class
from backend.providers.kbisai_odds import (
    ODDS_TYPES,
    KbisaiOddsError,
    fetch_all_companies,
    fetch_match_all_odds,
    parse_match_all_odds_points,
)

DEFAULT_OUTPUT_DIR = Path("runtime/research/kbisai-odds")
DEFAULT_COMPANY_IDS = ("2", "7", "22")   # 36* / 澳* / 平*
DEFAULT_MARKETS = ODDS_TYPES
DEFAULT_MAX_REQUESTS = 200
JITTER_SECONDS = (0.4, 1.2)


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


def _make_run_id() -> str:
    stamp = utc_now_iso().replace(":", "").replace("-", "").replace(".", "").replace("Z", "")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def _lookup_kbisai_targets(conn_odds, fotmob_match_ids: list[int]) -> dict[int, str]:
    if not fotmob_match_ids:
        return {}
    placeholders = ",".join("?" for _ in fotmob_match_ids)
    rows = conn_odds.execute(
        f"""SELECT fotmob_match_id, provider_match_id FROM dim_match_xref
            WHERE provider='kbisai' AND fotmob_match_id IN ({placeholders})""",
        tuple(fotmob_match_ids),
    ).fetchall()
    return {int(r["fotmob_match_id"]): r["provider_match_id"] for r in rows}


def _lookup_kickoffs(conn_core, fotmob_match_ids: list[int]) -> dict[int, str | None]:
    if not fotmob_match_ids:
        return {}
    placeholders = ",".join("?" for _ in fotmob_match_ids)
    rows = conn_core.execute(
        f"SELECT Match_ID, kickoff_at_utc FROM dim_match WHERE Match_ID IN ({placeholders})",
        tuple(fotmob_match_ids),
    ).fetchall()
    return {int(r["Match_ID"]): r["kickoff_at_utc"] for r in rows}


def run(
    *,
    fotmob_match_ids: list[int],
    company_ids: tuple[str, ...] = DEFAULT_COMPANY_IDS,
    markets: tuple[str, ...] = DEFAULT_MARKETS,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    sleep_fn=time.sleep,
    rand_fn=random.uniform,
) -> dict[str, Any]:
    run_id = _make_run_id()
    run_dir = output_dir / run_id

    conn_odds_ro = connect_ro("odds")
    try:
        target_map = _lookup_kbisai_targets(conn_odds_ro, fotmob_match_ids)
    finally:
        conn_odds_ro.close()
    conn_core_ro = connect_ro("core")
    try:
        kickoff_map = _lookup_kickoffs(conn_core_ro, fotmob_match_ids)
    finally:
        conn_core_ro.close()

    unmapped = [m for m in fotmob_match_ids if m not in target_map]
    requests_planned = len(target_map) * len(markets) + 1  # +1: fetch_all_companies

    summary: dict[str, Any] = {
        "status": "OK",
        "run_id": run_id,
        "requested_match_ids": list(fotmob_match_ids),
        "unmapped_match_ids": unmapped,
        "company_ids": list(company_ids),
        "markets": list(markets),
        "requests_planned": requests_planned,
        "requests_made": 0,
        "per_match": [],
        "totals": {"inserted": 0, "duplicate": 0, "rejected_constraint": 0},
        "failures": [],
        "artifact_files": ["summary.json"],
    }

    run_dir.mkdir(parents=True, mode=0o700)
    os.chmod(run_dir, 0o700)

    if requests_planned > max_requests:
        summary["status"] = "ABORTED_BUDGET"
        summary["reason"] = (
            f"planned {requests_planned} requests exceeds --max-requests={max_requests}; "
            "aborted before making any request"
        )
        _atomic_write(run_dir / "summary.json", _json_bytes(summary))
        return summary

    try:
        company_names = fetch_all_companies()
        summary["requests_made"] += 1
    except (KbisaiOddsError, KbisaiLiveScoreError) as exc:
        company_names = {}
        summary["failures"].append(f"fetch_all_companies: {safe_exception_class(exc)}")

    target_company_set = set(company_ids)
    conn_odds_rw = connect_rw("odds")
    poll_run_id = new_uuid()
    observed_at = utc_now_iso()
    try:
        for fotmob_match_id in fotmob_match_ids:
            provider_match_id = target_map.get(fotmob_match_id)
            match_entry: dict[str, Any] = {
                "fotmob_match_id": fotmob_match_id,
                "provider_match_id": provider_match_id,
                "markets": {},
            }
            if provider_match_id is None:
                match_entry["status"] = "SKIPPED_NO_XREF"
                summary["per_match"].append(match_entry)
                continue

            match_entry["status"] = "OK"
            kickoff_at_utc = kickoff_map.get(fotmob_match_id)
            for market in markets:
                if summary["requests_made"] >= max_requests:
                    match_entry["markets"][market] = {"status": "SKIPPED_BUDGET"}
                    continue

                market_entry: dict[str, Any] = {}
                try:
                    raw = fetch_match_all_odds(int(provider_match_id), market)
                    summary["requests_made"] += 1
                except (KbisaiOddsError, KbisaiLiveScoreError) as exc:
                    summary["failures"].append(
                        f"match {fotmob_match_id} market {market}: {safe_exception_class(exc)}"
                    )
                    match_entry["markets"][market] = {"status": "FETCH_FAILED"}
                    sleep_fn(rand_fn(*JITTER_SECONDS))
                    continue

                artifact_name = f"raw-{fotmob_match_id}-{market}.json"
                _atomic_write(run_dir / artifact_name, _json_bytes(raw))
                summary["artifact_files"].append(artifact_name)

                source_entries_for_targets = sum(
                    len((raw.get(cid) or {}).get("statusMatchOdds") or [])
                    for cid in company_ids
                )
                companies_present = sorted(set(raw.keys()) & target_company_set)

                rows = parse_match_all_odds_points(
                    raw, market,
                    provider_match_id=str(provider_match_id),
                    fotmob_match_id=fotmob_match_id,
                    kickoff_at_utc=kickoff_at_utc,
                    observed_at=observed_at,
                    ingested_at=utc_now_iso(),
                    poll_run_id=poll_run_id,
                    target_company_ids=target_company_set,
                )
                for row in rows:
                    if not row["company_name"] and row["company_id"] in company_names:
                        row["company_name"] = company_names[row["company_id"]]

                ingest_result = ingest_kbisai_odds_points(conn_odds_rw, rows)
                for key in ("inserted", "duplicate", "rejected_constraint"):
                    summary["totals"][key] += ingest_result[key]

                market_entry = {
                    "status": "OK",
                    "companies_present": companies_present,
                    "companies_missing": sorted(target_company_set - set(companies_present)),
                    "source_entries_for_target_companies": source_entries_for_targets,
                    "parsed_rows": len(rows),
                    "inserted": ingest_result["inserted"],
                    "duplicate": ingest_result["duplicate"],
                    "rejected_constraint": ingest_result["rejected_constraint"],
                }
                match_entry["markets"][market] = market_entry
                if ingest_result["rejected_samples"]:
                    summary["failures"].append(
                        f"match {fotmob_match_id} market {market}: "
                        f"{ingest_result['rejected_constraint']} rejected_constraint row(s), "
                        f"e.g. {ingest_result['rejected_samples'][0]}"
                    )
                sleep_fn(rand_fn(*JITTER_SECONDS))
            summary["per_match"].append(match_entry)
    finally:
        conn_odds_rw.close()

    if summary["totals"]["rejected_constraint"] > 0:
        summary["status"] = "PARTIAL_FAILURE"

    summary["artifact_files"].append("summary.json")
    _atomic_write(run_dir / "summary.json", _json_bytes(summary))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="kbisai 赔率完整变化序列采集")
    parser.add_argument("--match-id", type=int, action="append", required=True,
                         dest="match_ids", help="FotMob Match_ID(可重复)")
    parser.add_argument("--companies", default=",".join(DEFAULT_COMPANY_IDS),
                         help="逗号分隔的目标公司 id,默认 2,7,22(36*/澳*/平*)")
    parser.add_argument("--markets", default=",".join(DEFAULT_MARKETS),
                         help="逗号分隔的来源市场 token(eu/asia/bs 的子集)")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    company_ids = tuple(x.strip() for x in args.companies.split(",") if x.strip())
    markets = tuple(x.strip() for x in args.markets.split(",") if x.strip())
    invalid = [m for m in markets if m not in ODDS_TYPES]
    if invalid:
        print(f"未知 market token: {invalid}(必须是 {ODDS_TYPES} 的子集)")
        return 2

    summary = run(
        fotmob_match_ids=args.match_ids,
        company_ids=company_ids,
        markets=markets,
        max_requests=args.max_requests,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=1))
    return 0 if summary["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
