"""三库 schema/行数/关键列空值率基线快照(Canonical v2 改造安全网,§Phase 0)。

目的:在 canonical v2 每个 Phase 开始前后各拍一份快照,用 `--compare` 证明既有
表的行数与内容没有被意外改动。整个 canonical v2 改造的红线是"只新增表,不
ALTER/DROP/UPDATE 任何现有生产表",这个脚本就是那条红线的可验证证据。

只读连接(mode=ro),不接受任何写路径开关——没有 --fix/--write/--repair。

用法:
  python -m backend.cli.schema_baseline --dump [--label phase0]
  python -m backend.cli.schema_baseline --compare docs/baselines/<file>.json

--dump 输出到 docs/baselines/<UTC 时间戳>[-<label>].json;用完整时间戳而不是
纯日期文件名,是因为同一天可能在同一 Phase 前后各拍一次(改造前/改造后对比),
纯日期文件名会互相覆盖——这是对 plan 文字("YYYY-MM-DD.json")的一个必要偏离,
两者的实际目的(可追溯、不覆盖)一致。

--compare 的判定:
  - 新基线里任何旧表"行数减少" → 判定为 REGRESSION(硬失败,退出码 2);
  - 新表(只在新基线出现)不算异常,归入 added_tables,单独列出;
  - 旧表 DDL sha256 变化 → 判定为 SCHEMA_DRIFT(硬失败,退出码 2)——
    该脚本的存在前提就是"改造阶段不允许动老表结构";
  - 关键列空值率变化 → 只警告(exit 仍可为 0),因为空值率随新数据流入
    自然变化,不是漂移信号。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.db.connections import connect_ro
from backend.db.paths import DB_FILES, PROJECT_ROOT

BASELINE_DIR = PROJECT_ROOT / "docs" / "baselines"

# 关键列空值率:只登记 plan 明确点名、后续 Phase 会依赖"零空值"这一事实的列。
# 不做全表全列空值扫描——大表(158 万行的 fact_match_lineup 等)逐列 SUM(IS NULL)
# 代价很高且信息价值低;这里只测 canonical v2 真正依赖的字段。
KEY_NULLABLE_COLUMNS: dict[str, dict[str, list[str]]] = {
    "core": {
        "dim_match": ["kickoff_at_utc", "kickoff_precision", "kickoff_source"],
    },
    "odds": {
        "dim_match_xref": ["review_status"],
    },
}


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _table_names(conn) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master"
        " WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _table_ddl_sha256(conn, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    sql = row[0] if row and row[0] else ""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _row_count(conn, table: str) -> int:
    # 表名来自 sqlite_master 本身(不是外部输入),f-string 拼接安全。
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _null_rate(conn, table: str, column: str) -> dict:
    total, nulls = conn.execute(
        f"SELECT COUNT(*), SUM({column} IS NULL) FROM {table}"
    ).fetchone()
    return {"total": total, "nulls": nulls or 0}


def dump_db(db_name: str) -> dict:
    conn = connect_ro(db_name)
    try:
        tables = {}
        for t in _table_names(conn):
            entry = {
                "row_count": _row_count(conn, t),
                "ddl_sha256": _table_ddl_sha256(conn, t),
            }
            key_cols = KEY_NULLABLE_COLUMNS.get(db_name, {}).get(t)
            if key_cols:
                entry["key_column_null_rates"] = {
                    c: _null_rate(conn, t, c) for c in key_cols
                }
            tables[t] = entry
        return {"db": db_name, "tables": tables}
    finally:
        conn.close()


def dump_all() -> dict:
    return {
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "databases": {name: dump_db(name) for name in DB_FILES},
    }


def compare(old: dict, new: dict) -> dict:
    """返回 {regressions, schema_drifts, added_tables, null_rate_changes}。"""
    regressions: list[str] = []
    schema_drifts: list[str] = []
    added_tables: list[str] = []
    null_rate_changes: list[str] = []

    for db_name, new_db in new["databases"].items():
        old_db = old["databases"].get(db_name, {"tables": {}})
        old_tables = old_db["tables"]
        new_tables = new_db["tables"]
        for t, new_entry in new_tables.items():
            old_entry = old_tables.get(t)
            if old_entry is None:
                added_tables.append(f"{db_name}.{t}")
                continue
            if new_entry["row_count"] < old_entry["row_count"]:
                regressions.append(
                    f"{db_name}.{t}: {old_entry['row_count']} -> {new_entry['row_count']} (行数减少)"
                )
            if new_entry["ddl_sha256"] != old_entry["ddl_sha256"]:
                schema_drifts.append(f"{db_name}.{t}: DDL sha256 changed")
            old_rates = old_entry.get("key_column_null_rates", {})
            new_rates = new_entry.get("key_column_null_rates", {})
            for col, new_rate in new_rates.items():
                old_rate = old_rates.get(col)
                if old_rate is not None and old_rate != new_rate:
                    null_rate_changes.append(
                        f"{db_name}.{t}.{col}: {old_rate} -> {new_rate}"
                    )
        removed = set(old_tables) - set(new_tables)
        for t in removed:
            regressions.append(f"{db_name}.{t}: 表在新基线中消失")

    return {
        "regressions": regressions,
        "schema_drifts": schema_drifts,
        "added_tables": added_tables,
        "null_rate_changes": null_rate_changes,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dump", action="store_true", help="拍摄三库当前基线快照")
    p.add_argument("--label", type=str, default=None, help="快照文件名后缀,例如 phase0/phase1-before")
    p.add_argument("--compare", type=str, default=None, metavar="OLD_JSON", help="与指定旧快照比较")
    args = p.parse_args(argv)

    if args.dump:
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = dump_all()
        suffix = f"-{args.label}" if args.label else ""
        out_path = BASELINE_DIR / f"{_utc_now_compact()}{suffix}.json"
        out_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
        total_tables = sum(len(d["tables"]) for d in snapshot["databases"].values())
        print(f"baseline written: {out_path} ({total_tables} tables across {len(snapshot['databases'])} dbs)")
        return 0

    if args.compare:
        old_path = Path(args.compare)
        if not old_path.is_absolute():
            old_path = PROJECT_ROOT / old_path
        old = json.loads(old_path.read_text(encoding="utf-8"))
        new = dump_all()
        diff = compare(old, new)
        print(json.dumps(diff, indent=2, ensure_ascii=False))
        if diff["regressions"] or diff["schema_drifts"]:
            print(
                f"\nFAIL: {len(diff['regressions'])} regression(s), "
                f"{len(diff['schema_drifts'])} schema drift(s)",
                file=sys.stderr,
            )
            return 2
        print(
            f"\nOK: no regressions, no schema drift "
            f"({len(diff['added_tables'])} table(s) added, "
            f"{len(diff['null_rate_changes'])} null-rate change(s))"
        )
        return 0

    p.error("需要 --dump 或 --compare <old.json>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
