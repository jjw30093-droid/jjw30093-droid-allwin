"""把 dim_match 的稳定身份(Match_ID)登记进 schedule_match_identity(Canonical v2 Phase 1)。

背景(2026-08-11 数据模型规范化,详见 docs/current-state.md 对应小节):
`backend/migrations/core/0003_schedule_state_v1.sql`(911 行 DDL)与
`backend/schedules/state.py`(1480 行命令实现)已经建好一套 provider-neutral、
append-only 的比赛身份/状态框架,但从未被任何生产代码写入过——`data/allwin.db`
里 schedule_match_identity / schedule_match_state_snapshot / schedule_match_observation
全部 0 行。

本脚本**只回填身份(identity),不回填状态快照(state snapshot)**。这不是偷懒,是
刻意的安全边界,原因见下面「为什么不回填状态」。

## 为什么身份回填是安全的

`dim_match.Match_ID` 本身就是 FotMob 的 provider match id(全仓所有 ingest 路径
共享这一约定),身份登记只是给这个既有事实建一份显式索引:
`(provider='fotmob', provider_match_id=str(Match_ID)) -> canonical_match_id=Match_ID`。
这条映射不依赖任何"这场比赛现在处于什么状态"的判断,纯粹是身份声明,
`schedule_match_identity` 表本身也不含任何比赛内容字段。

## 为什么不回填状态(state snapshot)

早期的 `schedule_state_migration_trial` 研究脚本的 `audit_legacy_dim_match()`
已经论证过这一点(该研究目录已于 2026-08-14 清理删除,结论见
`docs/audits/schedule-state-temp-copy-migration-trial.md`),本脚本延续同样的结论:

- `dim_match.kickoff_at_utc`/`status` 反映的是**当前**已知的最新值,不是"这场比赛
  在过去某个真实时刻被观测到是什么状态"——如果现在用一次性回填动作给 16,931 场
  比赛的 `first_observed_at`/`observed_at` 都盖上"回填运行时刻"这一个时间戳,
  就是用回填时间伪装成历史观测时间,直接违反 CLAUDE.md §6.2
  ("来源没有声明更新时间时必须为 NULL,不得用抓取时间伪装来源更新时间")。
- `schedule_rest_feature` 的 point-in-time 特征血缘依赖 `observed_at` 的真实性
  做"只能用当时已知信息"的防泄漏校验;一次性回填的假观测时间会让这层防护
  失去意义。
- legacy `status` 到 `finished`/`cancelled` 两个布尔的映射没有被验证过完整覆盖
  全部真实取值,贸然回填可能产生错误分类且无法追溯。

状态快照应当只在真正的 provider 响应到达时,通过未来的"实时双写"改造
(把 `backend/cli/sync_fixtures_window.py` 的 FotMob payload 接入
`backend/schedules/state.record_match_states_batch()`)自然产生——这样每条
`observed_at` 才是真实的观测时刻。这个实时双写改造需要先补一层
"FotMob 原始 API 响应 -> `fotmob_schedule.normalize_raw_schedule_payload()`
期望的精确 payload 形状"转换器(当前不存在,`normalize_raw_schedule_payload`
要求的 `{artifactProvenance, details, fixtures}` 严格键集与
`FotMobClient.league_matches()` 的原始响应形状不同),属于独立的后续任务,
不在本脚本范围内。

## 安全闸门

回填前对 dim_match 做一次性完整性核对(数量、非空、去重、类型),任何一项不通过
直接拒绝、不猜测、不部分回填。回填过程中 `dim_match` 全程只读,不做任何写入
或修改;`schedule_match_identity` 的 `UNIQUE(provider, provider_match_id)` 约束
保证脚本天然幂等——重复运行不会产生重复行。

用法:
  python -m backend.cli.backfill_schedule_identity --dry-run   # 默认,不写库
  python -m backend.cli.backfill_schedule_identity --commit    # 真正写入
"""

from __future__ import annotations

import argparse
import sys

from backend.db.connections import connect_ro, connect_rw
from backend.db.util import utc_now_iso
from backend.schedules import state as schedule

PROVIDER = "fotmob"
IDENTITY_PROVENANCE = "legacy_dim_match_backfill:repository_verified_fotmob_match_id:v1"


def audit_dim_match(conn) -> dict:
    """一次性完整性核对,返回是否可以安全回填(identity_gate_passed)。"""
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(Match_ID IS NOT NULL) AS non_null,
          COUNT(DISTINCT Match_ID) AS unique_count,
          SUM(
            Match_ID IS NULL
            OR typeof(Match_ID) <> 'integer'
            OR Match_ID <= 0
          ) AS invalid
        FROM dim_match
        """
    ).fetchone()
    total, non_null, unique_count, invalid = row
    gate_passed = total > 0 and non_null == total and unique_count == total and invalid == 0
    return {
        "total": total,
        "non_null_match_ids": non_null,
        "unique_match_ids": unique_count,
        "invalid_match_ids": invalid,
        "identity_gate_passed": gate_passed,
    }


def backfill(*, commit: bool) -> dict:
    """核对通过后逐行登记身份。dry-run 下用只读连接跑一遍相同的读取逻辑,
    统计"将会"插入/跳过多少行,但绝不触碰任何写连接。"""
    conn_audit = connect_ro("core")
    try:
        audit = audit_dim_match(conn_audit)
        match_ids = [
            int(r[0])
            for r in conn_audit.execute("SELECT Match_ID FROM dim_match ORDER BY Match_ID")
        ]
    finally:
        conn_audit.close()

    if not audit["identity_gate_passed"]:
        raise SystemExit(
            f"拒绝回填:dim_match 完整性核对未通过 ({audit})。"
            "不猜测、不部分回填、不静默跳过异常行。"
        )

    if not commit:
        # dry-run:核对已知有多少条会插入 vs 已存在(靠只读查询 schedule_match_identity
        # 是否已有对应 provider_match_id,而不是真的调用写路径)。
        conn_check = connect_ro("core")
        try:
            existing = {
                r[0]
                for r in conn_check.execute(
                    "SELECT provider_match_id FROM schedule_match_identity WHERE provider=?",
                    (PROVIDER,),
                )
            }
        finally:
            conn_check.close()
        would_insert = sum(1 for m in match_ids if str(m) not in existing)
        return {
            "audit": audit,
            "mode": "dry-run",
            "would_insert": would_insert,
            "would_skip": len(match_ids) - would_insert,
        }

    identity_created_at = utc_now_iso()
    conn_rw = connect_rw("core")
    inserted = 0
    skipped = 0
    try:
        for match_id in match_ids:
            result = schedule.record_match_identity(
                conn_rw,
                provider=PROVIDER,
                provider_match_id=match_id,
                canonical_match_id=match_id,
                identity_created_at=identity_created_at,
                identity_provenance=IDENTITY_PROVENANCE,
            )
            if result["inserted"]:
                inserted += 1
            else:
                skipped += 1
    finally:
        conn_rw.close()

    return {
        "audit": audit,
        "mode": "commit",
        "inserted": inserted,
        "skipped": skipped,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--commit", action="store_true", help="真正写入(默认 dry-run 不写库)")
    args = p.parse_args(argv)

    result = backfill(commit=args.commit)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
