"""run_multileague_research.py — 多联赛 PIT 建模研究的永久端到端入口。

一条命令,从三个只读数据库副本完整跑出全部研究产物,不依赖任何预先存在的
中间文件(如手工生成过一次的 pit_dataset.json/market_probs.json),不依赖
仓库绝对路径或固定 /tmp 目录:

    python -m backend.cli.run_multileague_research \\
        --core /tmp/xxx/allwin.db --odds /tmp/xxx/odds.db \\
        --platform /tmp/xxx/platform.db --output-dir /tmp/xxx/results

一次运行做完:coverage → PIT dataset → market baseline(full_timeline +
summary_latest)→ 五折模型研究(freq/dc/lr/hgb + market 配对)→ 消融 →
写 manifest.json(汇总每个业务产物的确定性内容哈希)。

契约(2026-08-07 窄幅收口新增,替代此前 gitignored 的
runtime/research/multileague-model-v1/run_full_research.py 硬编码脚本):

- 三个输入库全部 `mode=ro` 打开,本 CLI 不写任何数据库;
- 缺少必要表 fail closed(明确报错退出,不产出任何文件);
- 中途异常时不留下"看起来完成"的 manifest——manifest 最后一步原子写入
  (先写临时文件再 rename),异常路径下 output-dir 里只有已完成阶段的
  产物,没有 manifest.json;
- **对已有一次成功产物的 output-dir 重跑同样成立**(2026-08-07 补丁):
  输入库校验通过后、开始覆写任何 artifact 前,先原子撤销旧
  manifest.json——之后无论在哪个阶段失败,目录里都不会出现"旧成功
  manifest + 新一轮部分 artifact"的混合态;
- **联赛集合与折叠方式可配置(2026-08-08 J1/K1/澳超接入)**:`--leagues big5`
  (默认,五大联赛+赛季字符串折叠,产物与此前完全一致、`dataset_hash` 逐位
  不变)或 `--leagues all8`(八联赛+按 kickoff_at_utc 绝对时间的
  rolling-origin 折叠,summary 市场来源额外含 J/K/A 的真实来源)。两种配置
  产出的 fold_metrics 用不同的 fold 标签前缀(F/T)区分,不得混写同一张表;
- 所有写出的 JSON 内容里不含生成时间戳/主机名/绝对路径(business content
  only);manifest.json 单独记录"运行环境"字段(解释器版本等)供人工核对
  可复现性边界,但该字段不参与任何 artifact_hash 的计算;
- 相同输入(同一进程/环境)重复运行,每个产物文件与 manifest 里记录的
  artifact_hash 逐位一致。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REQUIRED_TABLES = {
    "core": ("dim_match", "fact_team_match_stats"),
    "odds": ("dim_match_xref", "bronze_ng_odds_snap", "bronze_legacy_odds_summary"),
    "platform": ("prediction_snapshots", "prediction_evaluations"),
}


class ResearchInputError(RuntimeError):
    """输入数据库缺少必要表结构,fail closed。"""


def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _check_required_tables(conn: sqlite3.Connection, tables: tuple[str, ...], label: str) -> None:
    existing = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = [t for t in tables if t not in existing]
    if missing:
        raise ResearchInputError(
            f"{label} 库缺少必要表 {missing}(fail closed,未产出任何文件)"
        )


def _content_hash(obj) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, obj) -> str:
    """写 JSON(sort_keys,确定性),返回内容哈希。原子写:tmp → rename。"""
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload + "\n")
    tmp.replace(path)
    return _content_hash(obj)


def run(core_path: str, odds_path: str, platform_path: str, output_dir: str,
       leagues_profile: str = "big5") -> dict:
    """核心编排逻辑,纯函数式(除了写文件),供 CLI 与测试共用。

    `leagues_profile`:"big5"(默认,五大联赛 + 赛季字符串折叠,产物与
    2026-08-07 之前完全一致)或 "all8"(八联赛 + 按 kickoff_at_utc 绝对
    时间的 rolling-origin 折叠,J/K/A 市场来源额外启用)。

    返回写入的 manifest dict。异常时不写 manifest.json(调用方已用 tmp+rename
    保证这一点,本函数在写 manifest 之前的任何异常都会让 output_dir 里
    没有 manifest.json)。
    """
    from backend.cli.data_coverage import build_coverage, to_markdown
    from backend.models.research.market_baseline import (
        build_full_timeline_baseline, build_summary_latest_baseline,
    )
    from backend.models.research.pit_dataset import (
        ALL_EIGHT_LEAGUES, FIVE_LEAGUES, build_dataset,
    )
    from backend.models.research.run_research import (
        ALL_SUMMARY_SOURCES, DEFAULT_SUMMARY_SOURCES, SEASONS,
        assert_market_coverage, build_folds_by_time, run_full_study,
    )

    if leagues_profile not in ("big5", "all8"):
        raise ValueError(f"unknown leagues_profile: {leagues_profile!r}")
    leagues = FIVE_LEAGUES if leagues_profile == "big5" else ALL_EIGHT_LEAGUES
    source_priority = (DEFAULT_SUMMARY_SOURCES if leagues_profile == "big5"
                       else ALL_SUMMARY_SOURCES)
    # 2024-07-01 起每 6 个月一个 origin,直到覆盖当前数据末端(J1/K1/澳超
    # 只有 2-3 个赛季历史,更早的 origin 训练集过小,见审计文档 §样本量)
    TIME_ORIGINS = ["2024-07-01", "2025-01-01", "2025-07-01", "2026-01-01"]

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    conn_core = _ro(core_path)
    conn_odds = _ro(odds_path)
    conn_platform = _ro(platform_path)
    try:
        _check_required_tables(conn_core, REQUIRED_TABLES["core"], "core")
        _check_required_tables(conn_odds, REQUIRED_TABLES["odds"], "odds")
        _check_required_tables(conn_platform, REQUIRED_TABLES["platform"], "platform")

        # 真 P1(2026-08-07 用户复核确认):对已有一次成功产物的 output-dir
        # 重跑,若这一轮在写入部分 artifact 后失败,旧 manifest.json 此前
        # 会继续留在目录里,把"旧成功 manifest + 新一轮部分 artifact + 本轮
        # 失败"的混合态说成"已完成"。安全契约(方案 B,最小 fail-closed):
        # 输入库校验通过后、修改任何 artifact 前,先原子撤销旧 manifest——
        # 之后无论在哪个阶段失败,output-dir 在成功之前必然没有 manifest.json,
        # 不会出现旧 manifest 与新产物混合的状态。校验失败的分支(上面三行)
        # 还没到这一步,不会误删一次真正成功的旧 manifest。
        (out / "manifest.json").unlink(missing_ok=True)

        artifact_hashes: dict[str, str] = {}

        coverage = build_coverage(conn_core, conn_odds, conn_platform)
        artifact_hashes["coverage"] = _atomic_write_json(out / "coverage.json", coverage)
        (out / "coverage.md").write_text(to_markdown(coverage))

        dataset = build_dataset(conn_core, leagues=leagues)
        artifact_hashes["dataset_manifest"] = _atomic_write_json(
            out / "dataset_manifest.json", dataset["manifest"])
        # 逐场数据体量大,不建议入 git;写到 output-dir(调用方自行决定该目录
        # 是否为 gitignored /tmp 路径),供研究步骤复用、不重复查询数据库
        artifact_hashes["dataset_rows"] = _atomic_write_json(
            out / "dataset_rows.json", dataset["rows"])

        full_mb = build_full_timeline_baseline(conn_core, conn_odds, leagues=leagues)
        summary_mb = build_summary_latest_baseline(conn_core, conn_odds, leagues=leagues)
        market_report = {"full_timeline": full_mb["report"], "summary_latest": summary_mb["report"]}
        artifact_hashes["market_baseline_report"] = _atomic_write_json(
            out / "market_baseline_report.json", market_report)
        market_probs = {"full": full_mb["per_company"], "summary": summary_mb["per_source"]}
        artifact_hashes["market_probs"] = _atomic_write_json(
            out / "market_probs.json", market_probs)

        if leagues_profile == "big5":
            # 冻结路径:不传 folds,内部走 build_folds(seasons)——
            # 与 2026-08-07 之前逐位一致。
            study = run_full_study(dataset["rows"], full_mb["per_company"],
                                   summary_mb["per_source"], seasons=SEASONS,
                                   leagues=leagues, source_priority=source_priority)
        else:
            # J/K/A 扩展路径:按绝对时间折叠,fail-closed 检查市场配对覆盖。
            mk_sum_check: dict[int, tuple] = {}
            for src in source_priority:
                for mid, v in summary_mb["per_source"].get(src, {}).items():
                    mk_sum_check.setdefault(int(mid), tuple(v["p"]))
            assert_market_coverage(dataset["rows"], mk_sum_check, leagues=leagues, min_paired=1)
            folds = build_folds_by_time(dataset["rows"], TIME_ORIGINS, test_days=183)
            study = run_full_study(dataset["rows"], full_mb["per_company"],
                                   summary_mb["per_source"], folds=folds,
                                   leagues=leagues, source_priority=source_priority,
                                   calibration="temperature_per_league",
                                   use_stratified_calib=True)
        artifact_hashes["fold_metrics"] = _atomic_write_json(
            out / "fold_metrics.json", study["fold_metrics"])
        artifact_hashes["paired_market_metrics"] = _atomic_write_json(
            out / "paired_market_metrics.json", study["paired_market_metrics"])
        artifact_hashes["feature_ablation"] = _atomic_write_json(
            out / "feature_ablation.json", study["feature_ablation"])
    finally:
        conn_core.close()
        conn_odds.close()
        conn_platform.close()

    manifest = {
        "schema_version": 1,
        "leagues_profile": leagues_profile,
        "dataset_hash": dataset["dataset_hash"],
        "artifact_hashes": artifact_hashes,
        "manifest_hash": _content_hash({"dataset_hash": dataset["dataset_hash"],
                                        "artifact_hashes": artifact_hashes}),
        "run_environment": {
            "python_version": sys.version.split()[0],
            "sklearn_version": _sklearn_version(),
        },
    }
    # manifest.json 最后写、原子替换——它是"运行完整完成"的信号;
    # 中途任何异常都不会走到这里,output_dir 里不会出现看起来完成的 manifest。
    _atomic_write_json(out / "manifest.json", manifest)
    return manifest


def _sklearn_version() -> str:
    try:
        import sklearn

        return sklearn.__version__
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core", required=True, help="core(allwin.db)只读路径")
    ap.add_argument("--odds", required=True, help="odds.db 只读路径")
    ap.add_argument("--platform", required=True, help="platform.db 只读路径")
    ap.add_argument("--output-dir", required=True, help="产物输出目录(建议 gitignored /tmp 路径)")
    ap.add_argument("--leagues", choices=("big5", "all8"), default="big5",
                    help="big5(默认,冻结路径,与 2026-08-07 之前逐位一致)"
                         "或 all8(J1/K1/澳超接入,按绝对时间折叠)")
    args = ap.parse_args(argv)

    try:
        manifest = run(args.core, args.odds, args.platform, args.output_dir,
                       leagues_profile=args.leagues)
    except ResearchInputError as e:
        print(f"FAILED(fail-closed): {e}", file=sys.stderr)
        return 1

    print(json.dumps({
        "dataset_hash": manifest["dataset_hash"],
        "manifest_hash": manifest["manifest_hash"],
        "artifact_hashes": manifest["artifact_hashes"],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
