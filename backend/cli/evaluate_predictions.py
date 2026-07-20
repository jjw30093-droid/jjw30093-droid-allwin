"""离线评估命令:结算 → 计算 Accuracy/Brier/LogLoss/RPS/Calibration → 写 prediction_evaluations。

样本口径(CLAUDE.md §9.1 永久资格不变量):全部已结算的正式样本
(is_official + 曾锁定 + 赛前发布),含撤回与被取代版本——任何后续状态变化
都不能把已成为正式样本的记录移出评估分母。
用法:python -m backend.cli.evaluate_predictions [--model <id>] [--notes ...]
"""

import argparse
import json
import sys

from backend.commands.predictions import settle_outcomes
from backend.db.connections import connect_ro, connect_rw, tx
from backend.db.util import new_uuid, utc_now_iso
from backend.eval.metrics import evaluate_all
from backend.queries.track_record import evaluation_samples


def evaluate(conn_platform, conn_core, model_version_id=None, notes="") -> dict:
    with tx(conn_platform):
        settled = settle_outcomes(conn_platform, conn_core)
    samples = evaluation_samples(conn_platform, model_version_id)
    result = evaluate_all(samples)
    result["settled_now"] = settled
    if result["sample_size"] == 0:
        return result
    with tx(conn_platform):
        conn_platform.execute(
            """INSERT INTO prediction_evaluations
               (id, model_version_id, scope_json, sample_size, accuracy, brier, log_loss, rps,
                calibration_json, evaluated_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_uuid(),
                model_version_id,
                json.dumps({
                    "official_only": True,
                    "model_version_id": model_version_id,
                    # 永久资格口径:撤回/被取代的正式样本仍在分母内(CLAUDE.md §9.1)
                    "includes_retracted": True,
                    "includes_superseded": True,
                }),
                result["sample_size"],
                result["accuracy"],
                result["brier"],
                result["log_loss"],
                result["rps"],
                json.dumps(result["calibration"], ensure_ascii=False),
                utc_now_iso(),
                notes,
            ),
        )
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--notes", default="")
    args = ap.parse_args(argv)
    conn_core = connect_ro("core")
    conn_platform = connect_rw("platform")
    try:
        result = evaluate(conn_platform, conn_core, args.model, args.notes)
    finally:
        conn_core.close()
        conn_platform.close()
    if result["sample_size"] == 0:
        print("暂无符合口径的正式样本(official+曾锁定+赛前发布+已结算;含撤回/被取代),未写入评估。")
    else:
        printable = {k: v for k, v in result.items() if k != "calibration"}
        print(f"evaluation: {printable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
