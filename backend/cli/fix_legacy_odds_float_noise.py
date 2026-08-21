"""一次性清洗 bronze_legacy_odds_summary 的浮点存储 ULP 噪声(2026-08-21)。

背景:真实用户在赛前赔率时间轴看到"1.9300000000000002"这种数字
(match 5125184,ah 市场 initial home_or_over)。核查发现 88,457 行里有
1,910 行(asset_b_footballdata 25 / asset_b_nowgoal 121 / football_uk_jka
1,764)在写入源头(旧库 football_uk.db 与 Asset A/B 导入)就已经带上浮点
表示噪声——逐行核实每一行与其"两位小数最近邻值"的偏差恒为 1–2 个 ULP
(实测最大 4.44e-16),即写入时算出的 double 不是十进制值最近邻的那个,
不是真实盘口数字本身有争议。这个表的赔率/盘口线在业务上恒为两位小数
(全表扫描确认没有一行是真实存在的三位小数),round(v, 2) 对这批数据是
无损、无歧义的还原。

这是数据层面的根治;backend/queries/odds.py::legacy_summary_points() 另外
在读侧加了同一个 round(v, 2) 作为第二道防线(防止未来的导入脚本再引入
同类噪声时读侧还原不了),两处不互相替代。

用法:
  python -m backend.cli.fix_legacy_odds_float_noise --dry-run
  python -m backend.cli.fix_legacy_odds_float_noise --live
  python -m backend.cli.fix_legacy_odds_float_noise --live --skip-backup --db-path <测试用临时库>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ODDS_DB = _REPO_ROOT / "data" / "odds.db"

_VALUE_COLUMNS = ("line", "home_or_over", "draw", "away_or_under")


def backup_db(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.backup-pre-odds-float-fix-{stamp}")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(backup_path))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return backup_path


def find_corrupted_rows(conn: sqlite3.Connection) -> list[dict]:
    """挑出至少一列与其两位小数最近邻值不完全相等的行(Python round() 逐值比对,
    不用 SQL 端 ROUND()——SQLite 的 ROUND 内部也走浮点,不能反过来当"这一行
    是否已经干净"的判据)。"""
    rows = conn.execute(
        f"SELECT id, source, {', '.join(_VALUE_COLUMNS)} FROM bronze_legacy_odds_summary"
    ).fetchall()
    bad: list[dict] = []
    for r in rows:
        d = dict(r)
        if any(
            d[c] is not None and d[c] != round(d[c], 2) for c in _VALUE_COLUMNS
        ):
            bad.append(d)
    return bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_ODDS_DB, help="仅测试用临时库覆盖")
    parser.add_argument("--skip-backup", action="store_true", help="仅测试用")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(str(args.db_path))
    conn.row_factory = sqlite3.Row
    bad_rows = find_corrupted_rows(conn)
    by_source: dict[str, int] = {}
    for r in bad_rows:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1

    if args.dry_run:
        conn.close()
        print(json.dumps(
            {"mode": "DRY_RUN", "corrupted_rows": len(bad_rows), "by_source": by_source},
            ensure_ascii=False, indent=1,
        ))
        return 0

    backup_path = None
    if not args.skip_backup:
        conn.close()
        backup_path = backup_db(args.db_path)
        conn = sqlite3.connect(str(args.db_path))
        conn.row_factory = sqlite3.Row

    updated = 0
    with conn:
        for r in bad_rows:
            values = {c: (round(r[c], 2) if r[c] is not None else None) for c in _VALUE_COLUMNS}
            cur = conn.execute(
                "UPDATE bronze_legacy_odds_summary"
                " SET line=?, home_or_over=?, draw=?, away_or_under=?"
                " WHERE id=?",
                (values["line"], values["home_or_over"], values["draw"],
                 values["away_or_under"], r["id"]),
            )
            updated += cur.rowcount

    remaining_bad = find_corrupted_rows(conn)
    check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    print(json.dumps(
        {
            "mode": "LIVE",
            "backup_path": str(backup_path) if backup_path else None,
            "corrupted_rows_found": len(bad_rows),
            "rows_updated": updated,
            "by_source": by_source,
            "remaining_corrupted_rows": len(remaining_bad),
            "integrity_check": check,
        },
        ensure_ascii=False, indent=1,
    ))
    return 0 if not remaining_bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
