"""回补一个 (FotMob league_id, season) 已完赛比赛的**完整**赛前赔率变化序列
(不是两点摘要),写 `data/odds.db` `bronze_ng_odds_snap`——跟当前赛季实时轮询
落的是同一张表,下游全部分析代码(`analyze.py`/`ah_study.py` 等)不用改一行
就能吃到历史赛季数据。

2026-09-22 新增。跟 `backend/cli/ingest_nowgoal_season_odds.py`(2026-08-08,
只存两点摘要到 `bronze_legacy_odds_summary`)共用同一套实体解析
(`resolve_and_gate`,原样导入,不复制),区别只在"存两点"还是"存完整序列"、
存到哪张表。这次新增是因为实测 NowGoal 的 mix_history/euro_history 接口本身
就返回完整历史(不是只有两点),两点摘要等于白白丢弃已经下载到本地的数据
(见 backend/providers/nowgoal_archive.py 模块内 all_points_from_* 系列函数
的 docstring)。

覆盖的公司(2026-09-22 真实测试,用 titan_id=2789129 核实):
    Bet365(mix cid=8, euro cid=281):ah/ou/1x2 均有完整历史
    Crown皇冠 (mix cid=3):ah/ou 有完整历史;euro_history(1x2) 用 cid=3
        实测返回空数组,历史 1x2 专用 cid 未知,**皇冠的 1x2 暂时空缺**
        (如实跳过,不是漏了,是没找到正确的历史 cid,后续找到了再补)
    Macauslot澳门(mix cid=1, euro cid=80):ah/ou/1x2 均有完整历史

用法:
    python -m backend.cli.backfill_nowgoal_odds_full_history \\
        --league-id 47 --season 2025/2026 --nowgoal-league-id 36 --dry-run
    python -m backend.cli.backfill_nowgoal_odds_full_history \\
        --league-id 47 --season 2025/2026 --nowgoal-league-id 36 --live
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from backend.cli.ingest_nowgoal_season_odds import (
    KICKOFF_TOLERANCE_SECONDS,  # noqa: F401 - 供人工核对参数一致性
    _archive_season_key,
    load_target_matches,
    resolve_and_gate,
)
from backend.ingest.nowgoal_historical_odds import (
    historical_snap_records,
    ingest_historical_odds,
    upsert_xref_from_resolution,
)
from backend.ingest.nowgoal_historical_match_resolution import STATUS_AUTO_OK
from backend.providers.nowgoal_archive import (
    NowGoalArchiveTransport,
    all_points_from_euro_history,
    all_points_from_mix_history,
    parse_archive_season,
    parse_team_info,
)

CORE_DB = _REPO / "data" / "allwin.db"
ODDS_DB = _REPO / "data" / "odds.db"

# (mix_cid, euro_cid | None) —— euro_cid=None 表示该公司的历史 1x2 cid 未知,跳过
COMPANIES: dict[str, tuple[str, str | None]] = {
    "8": ("8", "281"),
    "3": ("3", None),
    "1": ("1", "80"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def backup_db(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst_path = db_path.with_name(f"{db_path.name}.backup-pre-nowgoal-full-history-{stamp}")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dst_path))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return dst_path


def fetch_and_build_records(
    transport: NowGoalArchiveTransport, titan_id: str, kickoff_utc: str, inverted: bool,
) -> dict[str, list[dict]]:
    """一场比赛、三家公司,各自抓 mix_history+euro_history,转成待写库记录。
    返回 {company_id: records}——单家公司抓取失败不阻塞其它公司(如实记录
    在调用方的 fetch_errors 里,见 main())。"""
    out: dict[str, list[dict]] = {}
    for company_id, (mix_cid, euro_cid) in COMPANIES.items():
        mix = transport.mix_history(titan_id, cid=mix_cid)
        all_pts = all_points_from_mix_history(mix, kickoff_utc)
        x12_pts: list[dict] = []
        if euro_cid is not None:
            euro = transport.euro_history(titan_id, cid=euro_cid)
            x12_pts = all_points_from_euro_history(euro, kickoff_utc)
        out[company_id] = historical_snap_records(
            all_pts["ah"], all_pts["ou"], x12_pts, company_id=company_id, inverted=inverted,
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", type=int, required=True)
    parser.add_argument("--season", type=str, required=True)
    parser.add_argument("--nowgoal-league-id", type=int, required=True)
    parser.add_argument("--archive-season-key", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-votes", type=int, default=3)
    parser.add_argument("--min-margin-ratio", type=float, default=3.0)
    parser.add_argument("--sleep-min", type=float, default=0.3)
    parser.add_argument("--sleep-max", type=float, default=0.8)
    parser.add_argument("--proxy-env", type=str, default="THORDATA_PROXY")
    parser.add_argument("--core-db", type=Path, default=CORE_DB, help="仅测试用")
    parser.add_argument("--db-path", type=Path, default=ODDS_DB, help="仅测试用临时库覆盖")
    parser.add_argument("--skip-backup", action="store_true", help="仅测试用")
    parser.add_argument("--xref-only", action="store_true",
                        help="只解析身份、写 dim_match_xref,跳过逐场抓赔率——"
                             "补救\"赔率已经跑完但 xref 没写\"这种情况用,"
                             "只需 archive_season() 一次请求,几秒钟跑完,"
                             "不用重新花几小时抓一遍已经有的赔率数据")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)

    import os
    proxy = os.environ.get(args.proxy_env)
    if not proxy:
        from dotenv import load_dotenv
        load_dotenv()
        proxy = os.environ.get(args.proxy_env)
    if not proxy:
        print(f"FAILED: {args.proxy_env} not set", file=sys.stderr)
        return 1

    season_key = args.archive_season_key or _archive_season_key(args.season)
    target_matches, skipped_no_kickoff = load_target_matches(args.core_db, args.league_id, args.season)
    if args.limit:
        target_matches = target_matches[: args.limit]

    transport = NowGoalArchiveTransport(proxy=proxy, sleep_min=args.sleep_min, sleep_max=args.sleep_max)
    archive_payload = transport.archive_season(args.nowgoal_league_id, season_key)
    archive_rows = parse_archive_season(archive_payload, ng_league_id=args.nowgoal_league_id)
    team_names = parse_team_info(archive_payload)

    resolved = resolve_and_gate(target_matches, archive_rows, args.league_id, team_names=team_names,
                                min_votes=args.min_votes, min_margin_ratio=args.min_margin_ratio)
    by_status: dict[str, int] = {}
    for r in resolved:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    summary = {
        "league_id": args.league_id, "season": args.season,
        "nowgoal_league_id": args.nowgoal_league_id, "archive_season_key": season_key,
        "target_matches": len(target_matches), "skipped_no_exact_kickoff": skipped_no_kickoff,
        "archive_rows": len(archive_rows), "resolution_by_status": by_status,
    }

    if args.dry_run:
        print(json.dumps({"mode": "DRY_RUN", **summary}, ensure_ascii=False, indent=1))
        return 0

    backup_path = None if args.skip_backup else backup_db(args.db_path)

    conn = sqlite3.connect(str(args.db_path), timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")

    auto_ok_rows = [r for r in resolved if r["status"] == STATUS_AUTO_OK]
    with conn:
        xref_upserted = upsert_xref_from_resolution(conn, auto_ok_rows)

    if args.xref_only:
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        print(json.dumps({
            "mode": "LIVE_XREF_ONLY", "backup_path": str(backup_path) if backup_path else None,
            "xref_upserted": xref_upserted, "integrity_check": check, **summary,
        }, ensure_ascii=False, indent=1))
        return 0

    target_by_id = {m["Match_ID"]: m for m in target_matches}
    fetch_errors: list[dict] = []
    per_company_totals: dict[str, dict[str, int]] = {c: {"inserted": 0, "skipped": 0} for c in COMPANIES}

    poll_run_id = f"historical-full-backfill-{args.league_id}-{args.season}-{_utc_now()}"

    matches_written = 0
    for r in resolved:
        if r["status"] != STATUS_AUTO_OK:
            continue
        match = target_by_id[r["match_id"]]
        try:
            by_company = fetch_and_build_records(
                transport, r["titan_id"], match["kickoff_at_utc"],
                inverted=(r["direction"] == "inverted"),
            )
        except Exception as e:  # noqa: BLE001 - 单场失败不阻塞整批,如实记录
            fetch_errors.append({"match_id": r["match_id"], "titan_id": r["titan_id"], "error": type(e).__name__})
            continue
        with conn:
            for company_id, records in by_company.items():
                if not records:
                    continue
                result = ingest_historical_odds(conn, r["titan_id"], records, poll_run_id)
                per_company_totals[company_id]["inserted"] += result["inserted"]
                per_company_totals[company_id]["skipped"] += result["skipped"]
        matches_written += 1
        print(json.dumps({"event": "match_done", "match_id": r["match_id"], "titan_id": r["titan_id"]}), flush=True)

    check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    print(json.dumps({
        "mode": "LIVE", "backup_path": str(backup_path) if backup_path else None,
        "xref_upserted": xref_upserted, "matches_written": matches_written, "fetch_errors": fetch_errors,
        "per_company_totals": per_company_totals, "integrity_check": check, **summary,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
