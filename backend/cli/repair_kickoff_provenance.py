"""安全修复由 legacy Gold 导入产生的"伪精确午夜"预测快照(P0-A 数据修复)。

背景:旧版 import_gold_predictions 把 canonical 比赛的自然日拼成
`<date>T00:00:00Z` 写入 kickoff_at_utc,并(在 provenance 迁移前)使这些
date-only 数据形式上看起来精确。本工具把这类**未锁定、非 official**的占位快照
重分类为诚实的 date_only 语义:

    kickoff_at_utc     → NULL(移除伪造的精确午夜;自然日仍在 core dim_match.Date)
    kickoff_precision  → 'date_only'
    kickoff_source     → 'legacy:gold_wdl_predictions:date_only'

安全边界(硬约束):
- 只修 locked_at IS NULL AND is_official=0 的快照——已锁定/official 永不改写;
- 识别依据是可证明的 provenance(占位午夜形状 + 来自 Gold 导入模型 + 未锁定 +
  尚未标 exact),不是"时间恰好等于午夜"就批量判错——真实 exact 的午夜开球
  (precision='exact' + 有来源)不在修复范围;
- 幂等:修复后 kickoff_at_utc 为 NULL,不再匹配占位形状,重跑无副作用;
- 支持 --dry-run:只报告将修改的行,不写库;
- 修复前后断言 official/locked 集合数量不变。

用法:
  python -m backend.cli.repair_kickoff_provenance --dry-run
  python -m backend.cli.repair_kickoff_provenance
"""

import argparse
import sys

from backend.db.connections import connect_rw, tx

GOLD_IMPORT_MODEL = "dc-baseline-1.M.2"
REPAIRED_SOURCE = "legacy:gold_wdl_predictions:date_only"

# 可修复目标:未锁定、非 official、来自 Gold 导入、且带午夜占位形状且尚未标 exact。
_TARGET_WHERE = (
    "locked_at IS NULL AND is_official=0 "
    "AND model_version_id=? "
    "AND kickoff_at_utc LIKE '%T00:00:00Z' "
    "AND (kickoff_precision IS NULL OR kickoff_precision <> 'exact')"
)


def _official_locked_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM prediction_snapshots WHERE is_official=1 AND locked_at IS NOT NULL"
    ).fetchone()[0]


def find_targets(conn) -> list[dict]:
    rows = conn.execute(
        f"SELECT id, match_id, kickoff_at_utc, kickoff_precision, status "
        f"FROM prediction_snapshots WHERE {_TARGET_WHERE} ORDER BY match_id",
        (GOLD_IMPORT_MODEL,),
    ).fetchall()
    return [dict(r) for r in rows]


def repair(conn, dry_run: bool = False) -> dict:
    before_official = _official_locked_count(conn)
    targets = find_targets(conn)
    result = {
        "candidates": len(targets),
        "repaired": 0,
        "dry_run": dry_run,
        "official_locked_before": before_official,
        "sample": [t["id"] for t in targets[:3]],
    }
    if dry_run or not targets:
        result["official_locked_after"] = before_official
        return result

    with tx(conn):
        cur = conn.execute(
            f"UPDATE prediction_snapshots "
            f"SET kickoff_at_utc=NULL, kickoff_precision='date_only', kickoff_source=? "
            f"WHERE {_TARGET_WHERE}",
            (REPAIRED_SOURCE, GOLD_IMPORT_MODEL),
        )
        result["repaired"] = cur.rowcount

    after_official = _official_locked_count(conn)
    result["official_locked_after"] = after_official
    if after_official != before_official:
        raise RuntimeError(
            f"数据完整性断言失败:official/locked 集合从 {before_official} 变为 {after_official}"
        )
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="修复 legacy Gold 导入的伪精确午夜快照")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写库")
    args = ap.parse_args(argv)
    conn = connect_rw("platform")
    try:
        result = repair(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}repair_kickoff_provenance: 候选 {result['candidates']} 条,"
          f"{'将修复' if args.dry_run else '已修复'} "
          f"{result['candidates'] if args.dry_run else result['repaired']} 条")
    print(f"  official/locked 集合:{result['official_locked_before']} → "
          f"{result['official_locked_after']}(必须不变)")
    if result["sample"]:
        print(f"  样本 id:{result['sample']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
