"""旧项目历史赔率(初盘+临场两点)入库 `bronze_legacy_odds_summary`。

来源(本机私有资产,不在 Git,路径必须经 CLI flag 或环境变量传入,不得硬编码):
- Asset A:`match_odds_data*.json`(miaomiaodi.vip/backend/odds,8,294 场,
  嵌套结构 odds.{asian_handicap,over_under,european_1x2}.{initial,latest});
- Asset B:`football_uk.db` 的 `silver_match_odds`(6,790 场,长表
  market×period×source,source∈{footballdata,nowgoal})。

两处必须修正的方向性缺陷(2026-08-06 用真实比分实证,见 docs/current-state.md):

1. Asset A 有 19.6%(1,587/8,088)记录的 `match_name` 主客顺序与 FotMob 相反。
   实证结论:反转记录里 **AH 已是 FotMob 方向(87.9% as-is 正确),
   1x2 是 match_name 方向(交换后 87.2% 正确)**——即修正规则是
   "只交换 european_1x2 的 home/away,不动 AH,不动 OU"。
   内部一致性交叉表:顺序正常的记录 AH↔1x2 一致 5580/5580=100.0%,
   反转记录一致仅 2/1388=0.1%,修正后全部归一。

2. Asset B 的 `footballdata` source AH 线符号与本系统相反
   (实测 (line>0)==(主队赢) 仅 24.5%,即 line>0 表示客队热门):
   入库时对 market='ah' 取反。`nowgoal` source 实测 72.3%,方向一致,不动。

统一后的 canonical 约定(与 bronze_ng_odds_snap 一致):
- 主客方向 = dim_match 的 Home/Away;
- ah line>0 = 主队让球(主队是热门);
- ou 与主客无关,恒不修正。

其他纪律:
- OU `raw_line IS NULL`(Asset B footballdata 有 2,867 行)→ 跳过,不补 0;
- 全空赔率记录 → 跳过;
- Asset A `match_name` 与 dim_match 两种顺序都对不上(约 88 场)→ 写 review
  文件人工复核,不静默入库;
- 幂等:UNIQUE(fotmob_match_id, source, market, period) + INSERT OR IGNORE。

用法:
  python -m backend.cli.ingest_legacy_odds --dry-run
  python -m backend.cli.ingest_legacy_odds --live
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.cli.odds_coverage_report import _grade_slots, parse_handicap_line

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ODDS_DB = _REPO_ROOT / "data" / "odds.db"
DEFAULT_CORE_DB = _REPO_ROOT / "data" / "allwin.db"

_B_PERIOD_MAP = {"opening": "initial", "closing": "latest"}
_B_SOURCE_MAP = {"footballdata": "asset_b_footballdata", "nowgoal": "asset_b_nowgoal"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ── Asset A ──────────────────────────────────────────────────────────────


def load_asset_a_records(asset_dir: Path) -> tuple[dict[str, tuple[dict, str]], int]:
    """fotmob_id -> (record, source_file);跨文件重复取六槽最完整者。"""
    best: dict[str, tuple[dict, str]] = {}
    total = 0
    for p in sorted(asset_dir.glob("match_odds_data*.json")):
        if ".bak" in p.name:
            continue
        data = json.loads(p.read_text())
        if not isinstance(data, list):
            raise ValueError(f"unexpected shape (not a list): {p.name}")
        for rec in data:
            fid = str(rec.get("fotmob_id") or "").strip()
            if not fid:
                continue
            total += 1
            prior = best.get(fid)
            if prior is None or _grade_slots(rec) > _grade_slots(prior[0]):
                best[fid] = (rec, p.name)
    return best, total


def resolve_orientation(rec: dict, home_name: str, away_name: str) -> bool | None:
    """False=顺序一致 / True=反转 / None=两种顺序都对不上(需人工复核)。"""
    name = rec.get("match_name") or ""
    if " vs " not in name:
        return None
    hn, an = [s.strip() for s in name.split(" vs ", 1)]
    if hn == home_name and an == away_name:
        return False
    if hn == away_name and an == home_name:
        return True
    return None


def asset_a_rows(rec: dict, inverted: bool) -> list[dict]:
    """一条 Asset A 记录 → 0..6 行 canonical 摘要行。

    修正规则(见模块 docstring 实证):inverted 时只交换 1x2 的 home/away;
    AH 已是 FotMob 方向不动;OU 与主客无关不动。
    """
    odds = rec.get("odds") or {}
    provider = str(odds.get("provider") or "").strip() or "unknown"
    out: list[dict] = []
    for market, key in (("ah", "asian_handicap"), ("ou", "over_under")):
        for period in ("initial", "latest"):
            grp = (odds.get(key) or {}).get(period) or {}
            line = parse_handicap_line(grp.get("line"))
            first = _to_float(grp.get("over_or_home"))
            second = _to_float(grp.get("under_or_away"))
            if line is None or first is None or second is None:
                continue
            out.append(
                {
                    "provider": provider,
                    "market": market,
                    "period": period,
                    "line": line,
                    "home_or_over": first,
                    "draw": None,
                    "away_or_under": second,
                    "orientation_fixed": 0,
                }
            )
    for period in ("initial", "latest"):
        grp = (odds.get("european_1x2") or {}).get(period) or {}
        h = _to_float(grp.get("home"))
        d = _to_float(grp.get("draw"))
        a = _to_float(grp.get("away"))
        if h is None or a is None:
            continue
        if inverted:
            h, a = a, h
        out.append(
            {
                "provider": provider,
                "market": "1x2",
                "period": period,
                "line": None,
                "home_or_over": h,
                "draw": d,
                "away_or_under": a,
                "orientation_fixed": 1 if inverted else 0,
            }
        )
    return out


# ── Asset B ──────────────────────────────────────────────────────────────


def asset_b_rows(db_path: Path, known_match_ids: set[int]) -> tuple[list[dict], Counter]:
    """silver_match_odds → canonical 摘要行(footballdata 的 ah 线取反)。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT match_id, market, period, source, provider,"
            " raw_line, raw_home, raw_draw, raw_away FROM silver_match_odds"
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    skipped: Counter = Counter()
    for r in rows:
        mid = int(r["match_id"])
        if mid not in known_match_ids:
            skipped["not_in_dim_match"] += 1
            continue
        period = _B_PERIOD_MAP.get(r["period"])
        source = _B_SOURCE_MAP.get(r["source"])
        if period is None or source is None:
            skipped["unknown_period_or_source"] += 1
            continue
        market = r["market"]
        home = _to_float(r["raw_home"])
        away = _to_float(r["raw_away"])
        if home is None or away is None:
            skipped["missing_prices"] += 1
            continue
        line = _to_float(r["raw_line"])
        fixed = 0
        if market == "1x2":
            line = None
        elif market in ("ah", "ou"):
            if line is None:
                skipped[f"{market}_null_line"] += 1
                continue
            if market == "ah" and r["source"] == "footballdata":
                # footballdata 的 AHh 惯例与本系统相反(实测 24.5%),取反归一
                line = -line
                fixed = 1
        else:
            skipped["unknown_market"] += 1
            continue
        out.append(
            {
                "fotmob_match_id": mid,
                "source": source,
                "provider": str(r["provider"] or "unknown"),
                "market": market,
                "period": period,
                "line": line,
                "home_or_over": home,
                "draw": _to_float(r["raw_draw"]) if market == "1x2" else None,
                "away_or_under": away,
                "orientation_fixed": fixed,
                "source_file": f"{db_path.name}:silver_match_odds",
            }
        )
    return out, skipped


# ── 入库 ─────────────────────────────────────────────────────────────────


def backup_db(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.backup-pre-legacy-odds-ingest-{stamp}")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(backup_path))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return backup_path


def insert_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    now = _utc_now()
    inserted = 0
    for r in rows:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO bronze_legacy_odds_summary
                (fotmob_match_id, source, provider, market, period, line,
                 home_or_over, draw, away_or_under, orientation_fixed,
                 source_file, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["fotmob_match_id"], r["source"], r["provider"], r["market"],
                r["period"], r["line"], r["home_or_over"], r["draw"],
                r["away_or_under"], r["orientation_fixed"], r.get("source_file"), now,
            ),
        )
        inserted += cur.rowcount
    return inserted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--skip-backup", action="store_true", help="仅供测试用临时库使用")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_ODDS_DB)
    parser.add_argument("--core-db-path", type=Path, default=DEFAULT_CORE_DB)
    parser.add_argument(
        "--asset-a-dir", type=Path,
        default=Path(os.environ["ALLWIN_ODDS_ASSET_A_DIR"]) if os.environ.get("ALLWIN_ODDS_ASSET_A_DIR") else None,
    )
    parser.add_argument(
        "--asset-b-db", type=Path,
        default=Path(os.environ["ALLWIN_ODDS_ASSET_B_DB"]) if os.environ.get("ALLWIN_ODDS_ASSET_B_DB") else None,
    )
    parser.add_argument("--review-out", type=Path, default=None,
                        help="Asset A 名字对不上的记录写到这里(默认 stderr 摘要)")
    args = parser.parse_args(argv)

    if args.asset_a_dir is None and args.asset_b_db is None:
        print(json.dumps({"error": "neither --asset-a-dir nor --asset-b-db configured "
                          "(env: ALLWIN_ODDS_ASSET_A_DIR / ALLWIN_ODDS_ASSET_B_DB)"}))
        return 2

    core = sqlite3.connect(f"file:{args.core_db_path}?mode=ro", uri=True)
    core.row_factory = sqlite3.Row
    dim = {
        int(r["Match_ID"]): (r["Home_Team_Name"] or "", r["Away_Team_Name"] or "")
        for r in core.execute("SELECT Match_ID, Home_Team_Name, Away_Team_Name FROM dim_match")
    }
    core.close()

    all_rows: list[dict] = []
    review: list[dict] = []
    stats: dict[str, Any] = {"dim_match_ids": len(dim)}

    if args.asset_a_dir is not None:
        best, total_records = load_asset_a_records(args.asset_a_dir)
        a_counter: Counter = Counter()
        for fid, (rec, src_file) in best.items():
            if not fid.isdigit() or int(fid) not in dim:
                a_counter["orphan_not_in_dim_match"] += 1
                continue
            home_name, away_name = dim[int(fid)]
            inv = resolve_orientation(rec, home_name, away_name)
            if inv is None:
                a_counter["name_unresolved_review"] += 1
                review.append({
                    "fotmob_id": fid, "match_name": rec.get("match_name"),
                    "dim_match": f"{home_name} vs {away_name}", "source_file": src_file,
                })
                continue
            a_counter["inverted" if inv else "normal"] += 1
            rows = asset_a_rows(rec, inv)
            if not rows:
                a_counter["all_slots_empty"] += 1
                continue
            for r in rows:
                r["fotmob_match_id"] = int(fid)
                r["source"] = "asset_a_json"
                r["source_file"] = src_file
            all_rows.extend(rows)
        stats["asset_a"] = {
            "records_total": total_records, "unique_fotmob_ids": len(best),
            "breakdown": dict(a_counter),
        }

    if args.asset_b_db is not None:
        b_rows, b_skipped = asset_b_rows(args.asset_b_db, set(dim))
        all_rows.extend(b_rows)
        stats["asset_b"] = {"rows_emitted": len(b_rows), "skipped": dict(b_skipped)}

    stats["candidate_rows"] = len(all_rows)
    stats["distinct_matches"] = len({r["fotmob_match_id"] for r in all_rows})
    stats["orientation_fixed_rows"] = sum(r["orientation_fixed"] for r in all_rows)

    if review:
        if args.review_out:
            args.review_out.parent.mkdir(parents=True, exist_ok=True)
            with args.review_out.open("w") as f:
                for item in review:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            stats["review_file"] = str(args.review_out)
        stats["review_count"] = len(review)

    if args.dry_run:
        print(json.dumps({"mode": "DRY_RUN", **stats}, indent=2, ensure_ascii=False))
        return 0

    backup_path = None
    if not args.skip_backup:
        backup_path = backup_db(args.db_path)

    conn = sqlite3.connect(str(args.db_path), timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN")
        try:
            inserted = insert_rows(conn, all_rows)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    print(json.dumps({
        "mode": "LIVE",
        "backup_path": str(backup_path) if backup_path else None,
        "rows_inserted": inserted,
        "rows_ignored_existing": len(all_rows) - inserted,
        **stats,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
