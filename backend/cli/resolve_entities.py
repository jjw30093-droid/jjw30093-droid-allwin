"""实体解析独立 CLI / Worker job。

- 幂等补种 dim_team_alias(core 全联赛队名 + i18n 中英文名,不再只限英超);
- 汇报 dim_match_xref 各审核状态数量(auto_ok/needs_review/confirmed/rejected);
- --schedule-file 可离线解析日程行(JSON 数组 [{titan_id, home_name, away_name, date}]),
  与 poll_nowgoal 用同一 resolve_match 代码路径。

日常运行中,新日程行的解析内联在 nowgoal 采集里(poll_nowgoal);
本 CLI 是链上的显式实体解析步骤:保证别名字典与 core 同步,并暴露待人工审核数量。

用法:
  .venv/bin/python -m backend.cli.resolve_entities
  .venv/bin/python -m backend.cli.resolve_entities --schedule-file rows.json
"""

import argparse
import json
import sys
from pathlib import Path

from backend.db.connections import connect_ro, connect_rw
from backend.ingest.entity_resolution import resolve_match, seed_team_aliases


def run(schedule_file: str | None = None) -> dict:
    conn_odds = connect_rw("odds")
    conn_core = connect_ro("core")
    try:
        added_aliases = seed_team_aliases(conn_odds, conn_core)
        resolved = {"auto_ok": 0, "needs_review": 0, "unresolved": 0}
        if schedule_file:
            rows = json.loads(Path(schedule_file).read_text(encoding="utf-8"))
            for row in rows:
                result = resolve_match(conn_odds, conn_core, row)
                status = result.get("review_status")
                if status in ("auto_ok", "confirmed"):
                    resolved["auto_ok"] += 1
                elif status == "needs_review":
                    resolved["needs_review"] += 1
                else:
                    resolved["unresolved"] += 1
        xref_stats = {
            r["review_status"]: r["n"]
            for r in conn_odds.execute(
                "SELECT review_status, COUNT(*) AS n FROM dim_match_xref GROUP BY review_status"
            )
        }
        alias_total = conn_odds.execute("SELECT COUNT(*) FROM dim_team_alias").fetchone()[0]
        return {
            "aliases_added": added_aliases,
            "aliases_total": alias_total,
            "resolved_now": resolved,
            "xref_by_status": xref_stats,
        }
    finally:
        conn_core.close()
        conn_odds.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="实体解析(别名种子 + xref 状态)")
    ap.add_argument("--schedule-file", default=None, help="离线日程行 JSON(测试/补录)")
    args = ap.parse_args(argv)
    result = run(schedule_file=args.schedule_file)
    print(f"[resolve_entities] 别名新增 {result['aliases_added']},总数 {result['aliases_total']}")
    print(f"  本次解析: {result['resolved_now']}")
    print(f"  xref 状态: {result['xref_by_status'] or '(空)'}")
    needs = result["xref_by_status"].get("needs_review", 0)
    if needs:
        print(f"  ⚠ {needs} 条映射待人工审核(/admin → xref 审核)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
