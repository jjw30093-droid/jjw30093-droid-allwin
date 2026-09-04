"""清理 kickoff 严重偏差的孤儿 dim_match_xref 行(2026-08-24,站长拍板)。

## 背景

2026-08-17 之前,单边模糊匹配 bug(已在 entity_resolution.py 修复,见
`_pair_score` 616-620 行注释)让若干 NowGoal 场次被错配到**完全不相关**的比赛
(常见形式:配到了青年队/预备队/女足场次,kickoff 偏差 30–51 小时)。这些错配
行写入时 `review_status='needs_review'`,但当时的 `resolve_match` 永不重新评估
`needs_review` 行,导致它们永久占死 `UNIQUE(provider, fotmob_match_id)` 名额,
把真正正确的场次挡在候选池之外——对应的 FotMob 比赛因此永远抓不到赔率。

`entity_resolution.py` 现已加入 `HARD_REJECT_KICKOFF_SECONDS` 门槛,从源头拒绝
写入这类严重偏差的新行;本脚本是清理历史遗留的一次性维护动作,不是常驻任务、
不注册进 worker。

## 范围与安全边界

只删除同时满足以下全部条件的行(与新增门槛用同一个 `HARD_REJECT_KICKOFF_SECONDS`
常量,删的正是新守卫今后会拒绝写入的那一类):

    provider='nowgoal' AND review_status='needs_review' AND verified=0
    AND method='auto' AND kickoff_diff_seconds IS NOT NULL
    AND abs(kickoff_diff_seconds) > HARD_REJECT_KICKOFF_SECONDS

绝不触碰 `confirmed` / `rejected` / `verified=1` 的行——这些是人工判断过的结果,
即使 kickoff_diff_seconds 字段恰好也很大也不属于本脚本的清理范围。删除前后各查
一次这三类行的计数并断言不变,作为写后不变量核对(与 repair_kickoff_provenance.py
的既有约定一致)。

删除(而不是改指向)是刻意选择:删掉才能同时释放 `UNIQUE(provider,
fotmob_match_id)` 名额、并让该场次重新进入 `_candidate_matches` 候选池,由正常
解析流程(`allwin-odds.timer` 每 5 分钟到期判断内联的实体解析)自己重新发现
正确的 NowGoal 场次——不需要本脚本替人工判断"应该指向哪个 titan_id"。

## 2026-09-04 追加 `--unpromotable`

上面那次(2026-08-24)只把阈值定在 HARD_REJECT_KICKOFF_SECONDS(6 小时),挡住并
清掉了时差特别大的一批。但同一个根因还会产生**时差在 6 小时以内**的错配:
`_canonical_form` 当时把整个括号内容一律剥掉,"Arsenal (W)" 归一成 "arsenal",
于是 NowGoal 的**女足/青年队**场次成为男足正赛的候选。它们开球时刻通常只差
1.5~5.75 小时,全在 6 小时阈值以内,照样写成 needs_review 并占死名额。
2026-09-04 生产实测:48h 内 9 场因此永久无赔率,pipeline_gates 的
xref_unmapped_upcoming 自 08-24 起连报 CRITICAL 548 次。

根因已在 entity_resolution.py 修复(`_SQUAD_QUALIFIER_TOKENS` + 双边限定词
否决),但**存量占位行必须清掉才能释放名额**——`_rescore_existing_needs_review`
按设计只原地确认同一个 fotmob_match_id、绝不改指向。

`--unpromotable` 把阈值降到 KICKOFF_TOLERANCE_SECONDS(30 分钟),判据是一条
可证明的性质:**auto_ok 要求 |kickoff_diff| ≤ 容差,所以超出容差的 needs_review
行永远不可能被提升,只会占着名额**。删除是安全且幂等的——若某行其实是正确
场次(只是真的改期了),下一轮解析会原样重建成 needs_review,状态不变。

用法:
  python -m backend.cli.purge_stale_xref            # 默认,dry-run 不写库
  python -m backend.cli.purge_stale_xref --commit    # 真正删除
  python -m backend.cli.purge_stale_xref --unpromotable           # 看更宽的一批
  python -m backend.cli.purge_stale_xref --unpromotable --commit  # 真正删除

执行前必须已手动跑过 `bash deploy/scripts/backup_sqlite.sh`
(本脚本不自动触发备份——CLAUDE.md 的写路径与运维动作分离约定)。
"""

from __future__ import annotations

import argparse
import sys

from backend.db.connections import connect_ro, connect_rw
from backend.ingest.entity_resolution import (
    HARD_REJECT_KICKOFF_SECONDS,
    KICKOFF_TOLERANCE_SECONDS,
)

_TARGET_PREDICATE = """
    provider='nowgoal' AND review_status='needs_review' AND verified=0
    AND method='auto' AND kickoff_diff_seconds IS NOT NULL
    AND ABS(kickoff_diff_seconds) > ?
"""


def _protected_counts(conn) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM dim_match_xref"
        " WHERE provider='nowgoal' AND (review_status IN ('confirmed','rejected') OR verified=1)"
    ).fetchone()
    return {"protected_count": row["n"]}


def _targets(conn_odds, conn_core, threshold: int) -> list[dict]:
    rows = conn_odds.execute(
        f"SELECT id, fotmob_match_id, provider_match_id, confidence, kickoff_diff_seconds,"
        f" created_at FROM dim_match_xref WHERE {_TARGET_PREDICATE} ORDER BY id",
        (threshold,),
    ).fetchall()
    out = []
    for r in rows:
        cand = conn_core.execute(
            "SELECT Home_Team_Name, Away_Team_Name, kickoff_at_utc, League_ID"
            " FROM dim_match WHERE Match_ID=?",
            (r["fotmob_match_id"],),
        ).fetchone()
        out.append({
            "xref_id": r["id"],
            "fotmob_match_id": r["fotmob_match_id"],
            "fotmob_teams": (
                f"{cand['Home_Team_Name']} vs {cand['Away_Team_Name']}" if cand else "(core 里查不到)"
            ),
            "league_id": cand["League_ID"] if cand else None,
            "kickoff_at_utc": cand["kickoff_at_utc"] if cand else None,
            "provider_match_id": r["provider_match_id"],
            "confidence": r["confidence"],
            "kickoff_diff_seconds": r["kickoff_diff_seconds"],
            "created_at": r["created_at"],
        })
    return out


def purge(commit: bool = False, threshold: int = HARD_REJECT_KICKOFF_SECONDS) -> dict:
    conn_check = connect_rw("odds")
    conn_core = connect_ro("core")
    try:
        before = _protected_counts(conn_check)
        targets = _targets(conn_check, conn_core, threshold)
    finally:
        conn_core.close()
        conn_check.close()

    if not commit:
        conn_check2 = connect_ro("odds")
        try:
            after = _protected_counts(conn_check2)
        finally:
            conn_check2.close()
        return {
            "mode": "dry-run", "threshold_seconds": threshold,
            "would_delete": len(targets), "targets": targets,
            "protected_before": before["protected_count"], "protected_after": after["protected_count"],
        }

    conn_rw = connect_rw("odds")
    try:
        conn_rw.execute("BEGIN IMMEDIATE")
        ids = [t["xref_id"] for t in targets]
        deleted = 0
        for xref_id in ids:
            cur = conn_rw.execute(
                f"DELETE FROM dim_match_xref WHERE id=? AND {_TARGET_PREDICATE}",
                (xref_id, threshold),
            )
            deleted += cur.rowcount
        after = _protected_counts(conn_rw)
        conn_rw.execute("COMMIT")
    except Exception:
        conn_rw.execute("ROLLBACK")
        raise
    finally:
        conn_rw.close()

    if after["protected_count"] != before["protected_count"]:
        raise RuntimeError(
            f"不变量被破坏:confirmed/rejected/verified=1 计数从 {before['protected_count']}"
            f" 变成 {after['protected_count']}——本脚本只应删除 needs_review+method=auto+"
            "verified=0 的行,已回滚。"
        )

    return {
        "mode": "commit", "threshold_seconds": threshold, "deleted": deleted, "targets": targets,
        "protected_before": before["protected_count"], "protected_after": after["protected_count"],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--commit", action="store_true", help="真正删除(默认 dry-run 不写库)")
    p.add_argument(
        "--unpromotable", action="store_true",
        help=f"把阈值从 HARD_REJECT({HARD_REJECT_KICKOFF_SECONDS}s)降到 "
             f"KICKOFF_TOLERANCE({KICKOFF_TOLERANCE_SECONDS}s):清理**永远不可能**"
             "被提升为 auto_ok 的占位行(见模块头注 2026-09-04 段)",
    )
    args = p.parse_args(argv)

    threshold = KICKOFF_TOLERANCE_SECONDS if args.unpromotable else HARD_REJECT_KICKOFF_SECONDS
    result = purge(commit=args.commit, threshold=threshold)
    tag = "[dry-run] " if result["mode"] == "dry-run" else ""
    count = result.get("would_delete", result.get("deleted"))
    print(f"{tag}purge_stale_xref: {'将删除' if result['mode'] == 'dry-run' else '已删除'} {count} 条")
    print(f"  阈值: |kickoff_diff| > {result['threshold_seconds']}s"
          f"{'(unpromotable 模式)' if args.unpromotable else ''}")
    print(f"  受保护(confirmed/rejected/verified=1)计数:"
          f" {result['protected_before']} → {result['protected_after']}(必须不变)")
    for t in result["targets"]:
        print(f"  xref_id={t['xref_id']} fotmob_match_id={t['fotmob_match_id']}"
              f" ({t['fotmob_teams']}, league={t['league_id']}, kickoff={t['kickoff_at_utc']})"
              f" <- provider_match_id={t['provider_match_id']}"
              f" confidence={t['confidence']} kickoff_diff={t['kickoff_diff_seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
