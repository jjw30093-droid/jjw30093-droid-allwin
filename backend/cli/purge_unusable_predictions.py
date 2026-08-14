"""清空未经真实训练、不可用的历史预测快照(用户拍板,2026-08-11)。

背景:`platform.db` 的 `prediction_snapshots` 现有 760 行,全部是
`draft`(380)或 `legacy_unverified`(380),`is_official` 全为 0。实测
`/api/v1/track-record` 已对这批数据返回 `total: 0` + 诚实空态文案——它们
在公开面本来就不可见,纯属死重。模型需要用真实数据重新训练,现有产出
不可用,用户已明确要求清空。

## 红线核对(CLAUDE.md §9.1)

宪法禁止的是"物理删除**正式**预测":`is_official=1 且 locked_at 非空` 的样本
一旦合格就永久属于公开正式样本集合,不得物理删除。本脚本删除的 760 行
`is_official` 全为 0、`locked_at` 全为空,不满足"正式样本"判据,删除它们
**不触碰该红线**。

数据库层本身也已经有这条红线的可执行保护:`prediction_snapshots` 上的
`trg_pred_snap_no_delete` 触发器会在 `locked_at IS NOT NULL OR is_official=1`
时拒绝 DELETE——本脚本执行的 DELETE 语句必须真正经过这个触发器(不得用
任何绕过触发器的写路径,如直接操作 WAL 文件),触发器本身就是执行时的
双重保险,不是本脚本自证清白的唯一依据。

## 范围

只删除 `prediction_snapshots` 表中 `is_official=0` 的行(本次即全部 760 行)。
不删除:
- `prediction_outcomes`(真实赛果结算记录,以 match_id 为键,不依赖
  prediction_snapshots 的 FK,是客观比赛结果不是模型产出);
- `prediction_evaluations`(当前 0 行,且不含 snapshot 级 FK);
- `prediction_snapshot_edits`(append-only,`trg_pred_snap_edits_no_delete`
  触发器无条件拒绝任何 DELETE,当前 0 行,无需处理);
- `prediction_runs`(当前 4 行,其中 1 行的 760 个子快照会被本脚本清空,
  但保留这条"曾经跑过一次批量登记"的运行记录本身是无害的审计留痕,
  不属于"未经真实训练不可用的预测数据"这个删除范围)。

用法:
  python -m backend.cli.purge_unusable_predictions --dry-run   # 默认,不写库
  python -m backend.cli.purge_unusable_predictions --commit    # 真正删除

执行前必须已手动跑过 `bash deploy/scripts/backup_sqlite.sh`
(本脚本不自动触发备份——CLAUDE.md 的写路径与运维动作分离约定)。
"""

from __future__ import annotations

import argparse
import sys

from backend.db.connections import connect_ro, connect_rw


def _audit(conn) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM prediction_snapshots WHERE is_official=1"
    ).fetchone()
    official_count = row["n"]
    row2 = conn.execute(
        "SELECT status, COUNT(*) AS n FROM prediction_snapshots"
        " WHERE is_official=0 GROUP BY status"
    ).fetchall()
    by_status = {r["status"]: r["n"] for r in row2}
    return {"official_count": official_count, "unofficial_by_status": by_status}


def purge(commit: bool = False) -> dict:
    conn_check = connect_ro("platform")
    try:
        audit = _audit(conn_check)
    finally:
        conn_check.close()

    # 安全闸门:非官方样本判据只看 is_official,不看 status——防止将来
    # 表结构或状态取值变化后本脚本在没人注意的情况下删到正式样本。
    if audit["official_count"] != 0:
        raise RuntimeError(
            f"检测到 {audit['official_count']} 条 is_official=1 的正式预测,"
            "本脚本只清理未经训练的不可用数据,不会删除任何正式样本——中止执行。"
            "如果确实需要处理正式样本,那是另一套流程(CLAUDE.md §9.1 的"
            "retract/supersede),不是本脚本的职责。"
        )

    to_delete = sum(audit["unofficial_by_status"].values())
    if not commit:
        return {"audit": audit, "mode": "dry-run", "would_delete": to_delete}

    conn_rw = connect_rw("platform")
    try:
        conn_rw.execute("BEGIN IMMEDIATE")
        cur = conn_rw.execute(
            "DELETE FROM prediction_snapshots WHERE is_official=0"
        )
        deleted = cur.rowcount
        conn_rw.execute("COMMIT")
    except Exception:
        conn_rw.execute("ROLLBACK")
        raise
    finally:
        conn_rw.close()

    return {"audit": audit, "mode": "commit", "deleted": deleted}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--commit", action="store_true", help="真正删除(默认 dry-run 不写库)")
    args = p.parse_args(argv)

    result = purge(commit=args.commit)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
