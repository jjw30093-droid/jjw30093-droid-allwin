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
from backend.db.util import utc_now_iso
from backend.ingest.entity_resolution import (
    resolve_match,
    seed_ascii_fold_aliases,
    seed_canonical_form_aliases,
    seed_manual_alias_overrides,
    seed_team_aliases,
)


def run(schedule_file: str | None = None) -> dict:
    conn_odds = connect_rw("odds")
    conn_core = connect_ro("core")
    try:
        added_aliases = seed_team_aliases(conn_odds, conn_core)
        # 人工核实过的不可约别名(2026-08-24):见 provider_alias_overrides.py 顶部
        # 说明——归一化算法故意不覆盖这批,只信人工逐条核实过的表。**必须排在
        # ascii_fold/canonical_form 之前**:这样这批人工别名本身也能在同一轮被
        # 归一化折叠,不必等下一次运行才收敛(纯顺序问题,不影响最终一致性,
        # 但排在后面会让"再跑一次 added 应为 0"这条幂等性断言多等一轮才成立)。
        manual_override_added = seed_manual_alias_overrides(conn_odds)
        # 变音符 → ASCII 折叠别名(数据管道重建 Phase 3;Phase 6 接入自动化):
        # NowGoal 发 ASCII、FotMob 发变音符原文,不折叠则荷甲/葡超/巴甲等整批
        # needs_review。撞名 fail-closed(歧义折叠串整批拒绝,列 rejected)。
        ascii_fold = seed_ascii_fold_aliases(conn_odds)
        # 跨源品牌书写差异折叠(数据管道重建 Phase 3 续;2026-08-24 起接入):NowGoal
        # "AC Milan"/FotMob "Milan"、NowGoal "Groningen"/FotMob "FC Groningen" 这类
        # 差异不撞名解决不了,不折叠则新赛季一批比赛卡在 needs_review 永久冻结。
        # 撞名 fail-closed(归一化串撞名整批拒绝,列 rejected)。
        canonical_form = seed_canonical_form_aliases(conn_odds)
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
        needs_review_count = xref_stats.get("needs_review", 0)
        notify_result = None
        if needs_review_count:
            # 2026-08-24:此前这个数字每天算出来只 print() 到 job_runs 的 stdout,
            # 没有任何人看到,静默积压了一周。G11 质量门(pipeline_gates.py,每
            # 30 分钟)是主要告警路径;这里额外接一条最低成本的兜底,即使 G11
            # 因为某种原因没跑,每日巡检自己也会喊一声(WARNING 配额+24h 去重
            # 由 notify() 统一处理,不会刷屏)。
            from backend import notify as notify_mod

            notify_result = notify_mod.notify(
                level="WARNING",
                source="resolve_entities_backlog",
                title=f"实体解析待人工审核 {needs_review_count} 条",
                body=f"dim_match_xref.review_status='needs_review' 共 {needs_review_count} 条,"
                     f"详情见 /admin → 映射审核 或质量门 G11(xref_unmapped_upcoming)。",
                dedup_key="resolve_entities:needs_review",
            )
        return {
            "aliases_added": added_aliases,
            "ascii_fold_added": ascii_fold.get("added", 0),
            "ascii_fold_rejected": ascii_fold.get("rejected", []),
            "canonical_form_added": canonical_form.get("added", 0),
            "canonical_form_rejected": canonical_form.get("rejected", []),
            "manual_override_added": manual_override_added,
            "aliases_total": alias_total,
            "resolved_now": resolved,
            "xref_by_status": xref_stats,
            "notify_result": notify_result,
        }
    finally:
        conn_core.close()
        conn_odds.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="实体解析(别名种子 + xref 状态)")
    ap.add_argument("--schedule-file", default=None, help="离线日程行 JSON(测试/补录)")
    args = ap.parse_args(argv)
    result = run(schedule_file=args.schedule_file)
    print(f"[resolve_entities] 别名新增 {result['aliases_added']},"
          f"ASCII 折叠新增 {result['ascii_fold_added']},"
          f"跨源归一化新增 {result['canonical_form_added']},"
          f"人工校正新增 {result['manual_override_added']},总数 {result['aliases_total']}")
    if result["ascii_fold_rejected"]:
        print(f"  ⚠ 折叠撞名整串拒绝(需人工复核): {result['ascii_fold_rejected']}")
    if result["canonical_form_rejected"]:
        print(f"  ⚠ 归一化撞名整串拒绝(需人工复核): {result['canonical_form_rejected']}")
    print(f"  本次解析: {result['resolved_now']}")
    print(f"  xref 状态: {result['xref_by_status'] or '(空)'}")
    needs = result["xref_by_status"].get("needs_review", 0)
    if needs:
        print(f"  ⚠ {needs} 条映射待人工审核(/admin → xref 审核);"
              f"notify: {result['notify_result']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
