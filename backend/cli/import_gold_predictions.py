"""Adapter:把现有 gold_wdl_predictions 导入预测登记簿(不重写模型)。

导入口径(docs/prediction-integrity.md,依据 docs/current-state.md 的审计事实):
- gold 表只有批次 updated_at(2026-07-09),无 generated_at/模型版本列;
- 2025/2026 赛季 380 行:比赛早已完赛,写入时间在赛后 → 无法证明赛前生成,
  只能导入 legacy_unverified(internal 可见,永不 official,不入公开战绩);
- 2026/2027 赛季 380 行:写入时间早于全部开球 → 导入 draft,由管理员在
  开球前发布并锁定后才进入 track record。

幂等:同 (match_id, model_version) 已存在快照则跳过。

用法:python -m backend.cli.import_gold_predictions [--dry-run]
"""

import argparse
import sys

from backend.commands.predictions import (
    finish_run,
    get_or_create_model_version,
    register_snapshot,
    start_run,
)
from backend.db.connections import connect_ro, connect_rw, tx
from backend.db.util import sha256_hex

MODEL_ID = "dc-baseline-1.M.2"


def _norm_ts(sqlite_ts: str | None) -> str | None:
    """SQLite datetime('now') 的 'YYYY-MM-DD HH:MM:SS'(UTC)→ ISO Z。"""
    if not sqlite_ts:
        return None
    return sqlite_ts.replace(" ", "T") + ("" if sqlite_ts.endswith("Z") else "Z")


def import_gold(conn_platform, conn_core, dry_run: bool = False) -> dict:
    get_or_create_model_version(
        conn_platform,
        MODEL_ID,
        algorithm="dixon-coles-poisson + isotonic calibration",
        description="极简 baseline:l10 滚动 xG 估 λ,DC 低比分修正 ρ=-0.005274,isotonic 按类校准",
        params={"rho": -0.005274, "features": ["xg_for_l10", "xg_against_l10"], "n_goals": 10},
        train_range="2020/21–2024/25(EPL)",
        metrics={
            "test_season": "2025/26",
            "rps": 0.2143,
            "baseline": "历史主/平/客频率基线",
            "baseline_rps": 0.2281,
            "note": "对照为历史频率基线,不是博彩公司收盘赔率共识(市场基线 UNVERIFIED)",
        },
    )

    rows = conn_core.execute(
        """SELECT g.match_id, g.season, g.p_home, g.p_draw, g.p_away,
                  g.lambda_home, g.lambda_away, g.confidence, g.reason, g.updated_at,
                  m.Date AS match_date
           FROM gold_wdl_predictions g JOIN dim_match m ON m.Match_ID = g.match_id
           ORDER BY g.match_id"""
    ).fetchall()

    existing = {
        r[0]
        for r in conn_platform.execute(
            "SELECT match_id FROM prediction_snapshots WHERE model_version_id=?", (MODEL_ID,)
        )
    }

    stats = {"total": len(rows), "skipped": 0, "legacy_unverified": 0, "draft": 0}
    run_id = None
    if not dry_run:
        run_id = start_run(conn_platform, MODEL_ID, triggered_by="import",
                           notes="import legacy gold_wdl_predictions")

    for r in rows:
        if r["match_id"] in existing:
            stats["skipped"] += 1
            continue
        # 概率归一(gold 表由校准后归一,仍夹一层防浮点残差)
        total_p = r["p_home"] + r["p_draw"] + r["p_away"]
        probs = (r["p_home"] / total_p, r["p_draw"] / total_p, r["p_away"] / total_p)
        legacy = r["season"] == "2025/2026"
        status = "legacy_unverified" if legacy else "draft"
        stats["legacy_unverified" if legacy else "draft"] += 1
        if dry_run:
            continue
        register_snapshot(
            conn_platform,
            match_id=r["match_id"],
            kickoff_at_utc=f"{r['match_date']}T00:00:00Z",
            model_version_id=MODEL_ID,
            home_win=probs[0], draw=probs[1], away_win=probs[2],
            generated_at=_norm_ts(r["updated_at"]),
            run_id=run_id,
            input_snapshot_hash=sha256_hex(
                f"{r['match_id']}|{r['lambda_home']}|{r['lambda_away']}|{r['updated_at']}"
            ),
            expected_home_goals=r["lambda_home"],
            expected_away_goals=r["lambda_away"],
            confidence=r["confidence"] or None,
            status=status,
            visibility="internal" if legacy else "public",
            is_official=0,
        )
    if not dry_run and run_id:
        finish_run(conn_platform, run_id, "succeeded", stats["legacy_unverified"] + stats["draft"])
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    conn_core = connect_ro("core")
    conn_platform = connect_rw("platform")
    try:
        if args.dry_run:
            stats = import_gold(conn_platform, conn_core, dry_run=True)
        else:
            with tx(conn_platform):
                stats = import_gold(conn_platform, conn_core)
    finally:
        conn_core.close()
        conn_platform.close()
    print(f"import_gold_predictions: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
