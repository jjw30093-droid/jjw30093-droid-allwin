"""J1/韩K联/澳超 历史赔率导入 `data/odds.db` `bronze_legacy_odds_summary`。

来源与已完成的复核(2026-08-07,全量审计脚本
runtime/research/jka-odds-ingest/audit.py 真实跑过,报告 audit-report.json):

1. `football_uk_jka` —— 旧库 `football_uk.db` `silver_match_odds` 的
   `source='footballdata'` 行(J/K/A 三联赛 1,810 场)。已实证:
   - 标签是 footballdata,**数据实为 NowGoal**:11 场跨联赛/跨年份实爬对照,
     1x2/AH/OU × 开/收盘共 66 组数值全部精确一致;
   - 1,810 场 fotmob_match_id 全部存在于 all-win dim_match 且联赛一致、
     主客方向 token 比对 1810/1810 一致(零反转,与 Asset A 的 19.6% 反转不同);
   - AH 线符号 = 本表 canonical(line>0=主队让球):同场同期 1x2 热门方向
     交叉验证 2,834 agree / 1 borderline(4965624 开盘,1x2 近平半盘,非反转);
     与同一旧表五大联赛子集(真 football-data.co.uk,入库时取反)刻意不同,
     本子集**不取反**;
   - 数值全量过界:三项赔率 ≥1.01、两项 ∈[1.3,3.5]、overround ∈[1.0,1.25]、
     线在 0.25 步进合法域——0 违例。

2. `nowgoal_archive_refetch` —— NowGoal season archive 实爬两点摘要,
   仅用于旧库数据不可用的 32 场 2026 年尾部比赛:
   - 30 场:旧库开盘 OU 无线、收盘只有 watcher 盘中过时快照
     (5130400 仲裁:archive 收盘 = fd 值、≠ watcher 值,故 watcher 行弃用);
   - 5139869:旧库无 fd 行;
   - 4410075:旧库 1x2 开盘为脏数据(主胜 1.5 配平手盘,内部矛盾),
     archive 无赛前 1x2,只入 archive 的 ah/ou。

时间戳纪律(§6.2):两点摘要无逐条观测时间,入独立 bronze 表,绝不混入
bronze_ng_odds_snap,不生成 silver_odds_moves 假变化点。

用法:
    python -m backend.cli.ingest_jka_legacy_odds --dry-run
    python -m backend.cli.ingest_jka_legacy_odds --live
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
OLD_DB = Path.home() / "Desktop" / "Football_Data_Lake" / "football_uk.db"
ODDS_DB = _REPO / "data" / "odds.db"
CORE_DB = _REPO / "data" / "allwin.db"
REFETCH_DIR = _REPO / "runtime" / "research" / "jka-odds-ingest"

JKA_LEAGUES = (223, 9080, 113)
PERIOD_MAP = {"opening": "initial", "closing": "latest"}

# 4410075 旧 1x2 行内部矛盾(见 docstring),整场移交 refetch 类
REFETCH_SUPERSEDES = "refetch_match_ids"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_ok(market: str, home: float | None, draw: float | None,
            away: float | None, line: float | None) -> bool:
    """入库前逐行硬闸:复核报告为 0 违例,闸门保住这个不变量。"""
    if market == "1x2":
        if not (home and draw and away and home >= 1.01 and draw >= 1.01 and away >= 1.01):
            return False
        return 1.0 <= (1 / home + 1 / draw + 1 / away) <= 1.25
    if not (home and away and 1.3 <= home <= 3.5 and 1.3 <= away <= 3.5):
        return False
    if line is None or abs(line * 4 - round(line * 4)) > 1e-6:
        return False
    if market == "ah" and not -3.5 <= line <= 3.5:
        return False
    if market == "ou" and not 1.0 <= line <= 5.0:
        return False
    return 1.0 <= (1 / home + 1 / away) <= 1.25


def load_refetch_rows() -> tuple[dict[int, list[dict]], set[int]]:
    """两点摘要 rows(refetch 类 32 场),及其 match_id 集合。"""
    live = json.loads((REFETCH_DIR / "live_nowgoal_results.json").read_text())
    adj = json.loads((REFETCH_DIR / "adjudicate_results.json").read_text())
    gap_ids = set(json.loads((REFETCH_DIR / "gap_hits.json").read_text()))

    merged: dict[str, dict] = {mid: live[mid] for mid in gap_ids if mid in live}
    merged["5139869"] = adj["5139869"]
    merged["4410075"] = adj["4410075"]   # 无 1x2,仅 ah/ou
    # 5130400 仲裁后归 fd 类,不进 refetch

    out: dict[int, list[dict]] = {}
    for mid_s, rec in merged.items():
        mid = int(mid_s)
        rows: list[dict] = []
        for market in ("1x2", "ah", "ou"):
            block = rec.get(market)
            if not block:
                continue
            for src_period, period in PERIOD_MAP.items():
                g = block.get(src_period)
                if not g:
                    continue
                if market == "1x2":
                    home, draw, away, line = g.get("home"), g.get("draw"), g.get("away"), None
                else:
                    home, draw, away, line = g.get("home_or_over"), None, g.get("away_or_under"), g.get("line")
                if home is None or not _row_ok(market, home, draw, away, line):
                    continue
                rows.append({
                    "fotmob_match_id": mid, "market": market, "period": period,
                    "line": line, "home_or_over": home, "draw": draw, "away_or_under": away,
                    "source": "nowgoal_archive_refetch",
                    "source_file": f"nowgoal_archive:titan_{rec.get('titan_id')}",
                })
        if rows:
            out[mid] = rows
    return out, set(out)


def load_fd_rows(refetch_ids: set[int]) -> tuple[dict[int, list[dict]], dict]:
    """旧库 fd 行(排除 refetch 类比赛),带核对统计。"""
    old = sqlite3.connect(f"file:{OLD_DB}?mode=ro", uri=True)
    old.row_factory = sqlite3.Row
    core = sqlite3.connect(f"file:{CORE_DB}?mode=ro", uri=True)
    known = {r[0] for r in core.execute(
        f"SELECT Match_ID FROM dim_match WHERE League_ID IN ({','.join('?' * len(JKA_LEAGUES))})",
        JKA_LEAGUES)}
    core.close()

    stats = {"rows_seen": 0, "skipped_refetch_class": 0, "skipped_not_in_allwin": 0,
             "skipped_gate": 0, "skipped_null_line": 0}
    out: dict[int, list[dict]] = {}
    for r in old.execute("""
        SELECT so.match_id, so.market, so.period, so.raw_home, so.raw_draw,
               so.raw_away, so.raw_line
        FROM silver_match_odds so JOIN dim_match dm ON dm.Match_ID = so.match_id
        WHERE dm.League_ID IN (223, 9080, 113) AND so.source = 'footballdata'
        ORDER BY so.match_id, so.market, so.period"""):
        stats["rows_seen"] += 1
        mid = int(r["match_id"])
        if mid in refetch_ids:
            stats["skipped_refetch_class"] += 1
            continue
        if mid not in known:
            stats["skipped_not_in_allwin"] += 1
            continue
        market, period = r["market"], PERIOD_MAP[r["period"]]
        home, draw, away, line = r["raw_home"], r["raw_draw"], r["raw_away"], r["raw_line"]
        if market in ("ah", "ou") and line is None:
            stats["skipped_null_line"] += 1
            continue
        if not _row_ok(market, home, draw, away, line):
            stats["skipped_gate"] += 1
            continue
        out.setdefault(mid, []).append({
            "fotmob_match_id": mid, "market": market, "period": period,
            "line": line if market != "1x2" else None,
            "home_or_over": home, "draw": draw if market == "1x2" else None,
            "away_or_under": away,
            "source": "football_uk_jka",
            "source_file": "football_uk.db:silver_match_odds:footballdata",
        })
    old.close()
    return out, stats


def backup_db(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst_path = db_path.with_name(f"{db_path.name}.backup-pre-jka-legacy-ingest-{stamp}")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dst_path))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return dst_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--db-path", type=Path, default=ODDS_DB, help="仅测试用临时库覆盖")
    parser.add_argument("--skip-backup", action="store_true", help="仅测试用")
    args = parser.parse_args(argv)

    refetch_rows, refetch_ids = load_refetch_rows()
    fd_rows, fd_stats = load_fd_rows(refetch_ids)

    all_rows = [row for rows in fd_rows.values() for row in rows]
    all_rows += [row for rows in refetch_rows.values() for row in rows]

    summary = {
        "fd_matches": len(fd_rows), "fd_rows": sum(len(v) for v in fd_rows.values()),
        "refetch_matches": len(refetch_rows),
        "refetch_rows": sum(len(v) for v in refetch_rows.values()),
        "fd_stats": fd_stats,
    }
    if args.dry_run:
        print(json.dumps({"mode": "DRY_RUN", **summary}, ensure_ascii=False, indent=1))
        return 0

    backup_path = None
    if not args.skip_backup:
        backup_path = backup_db(args.db_path)

    conn = sqlite3.connect(str(args.db_path), timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    now = _utc_now()
    inserted = 0
    with conn:
        for row in all_rows:
            cur = conn.execute("""
                INSERT OR IGNORE INTO bronze_legacy_odds_summary
                  (fotmob_match_id, source, provider, market, period, line,
                   home_or_over, draw, away_or_under, orientation_fixed,
                   source_file, ingested_at)
                VALUES (?, ?, 'Bet365', ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (row["fotmob_match_id"], row["source"], row["market"], row["period"],
                 row["line"], row["home_or_over"], row["draw"], row["away_or_under"],
                 row["source_file"], now))
            inserted += cur.rowcount
    check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    print(json.dumps({
        "mode": "LIVE", "backup_path": str(backup_path) if backup_path else None,
        "rows_inserted": inserted, "rows_prepared": len(all_rows),
        "integrity_check": check, **summary,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
