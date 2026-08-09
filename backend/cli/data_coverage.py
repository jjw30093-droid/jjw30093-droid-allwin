"""data_coverage.py — 联赛×赛季数据覆盖真源(只读,deterministic)。

docs/data-plan.md §2 的覆盖矩阵此前是手工查询产物,动态数字随每轮 ingest
漂移。本 CLI 把"当前数据库里到底有什么"变成可重复命令:任何文档数字与
本命令输出冲突时,以本命令的重新运行为准。

只读纪律:三个库全部 `mode=ro` URI 打开,不写任何表。
确定性纪律:输出 JSON 不含生成时间、数据库路径、主机信息;所有 dict 按键
排序、列表按显式键排序;同一数据库状态重复运行必须逐字节一致
(`--json` 输出末尾打印 sha256 供比对)。

赔率覆盖三档(每场取最高档,档间不混淆;公司标签不静默合并):
- full_timeline:经 dim_match_xref(provider='nowgoal',review_status ∈
  auto_ok/confirmed)映射、bronze_ng_odds_snap 存在 market_phase='pre_match'
  的 1x2 快照。按 (company_id, company_name) 分开报告——历史回填的
  `281/bet 365` 与 live 轮询的 `8/Bet365` 是两条独立采集链路,合并会掩盖
  时间线密度的真实差异。
- open_close_only:bronze_legacy_odds_summary 存在 1x2 行。按 source 分开
  报告(asset_a_json / asset_b_footballdata / …),period 只有
  initial/latest 两点,latest 无精确时间戳,不得当 exact closing 使用。
- unavailable:两者皆无的已完赛比赛。

用法:
    python -m backend.cli.data_coverage \
        [--core data/allwin.db] [--odds data/odds.db] [--platform data/platform.db] \
        [--json out.json] [--md out.md]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

FIVE_LEAGUES = (47, 53, 54, 55, 87)

CORE_FACT_TABLES = (
    "fact_team_match_stats",
    "fact_shotmap",
    "fact_player_match_stats",
    "fact_match_events",
    "fact_match_lineup",
)


def _ro(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def build_coverage(
    conn_core: sqlite3.Connection,
    conn_odds: sqlite3.Connection,
    conn_platform: sqlite3.Connection,
) -> dict:
    """联赛×赛季覆盖矩阵(纯函数,只读三连接,输出可 JSON 序列化且确定)。"""

    # ── 比赛底表 ─────────────────────────────────────────────
    partitions: dict[tuple[int, str], dict] = {}
    match_partition: dict[int, tuple[int, str]] = {}
    finished_ids: dict[tuple[int, str], set[int]] = defaultdict(set)
    for r in conn_core.execute(
        """SELECT Match_ID, League_ID, Season, status,
                  kickoff_at_utc IS NOT NULL AND kickoff_precision='exact' AS has_exact
             FROM dim_match WHERE Season IS NOT NULL"""
    ):
        key = (int(r["League_ID"]), str(r["Season"]))
        p = partitions.setdefault(
            key,
            {
                "matches": 0,
                "finished": 0,
                "not_started": 0,
                "other_status": 0,
                "exact_kickoff": 0,
            },
        )
        p["matches"] += 1
        if r["status"] == "Finish":
            p["finished"] += 1
            finished_ids[key].add(int(r["Match_ID"]))
        elif r["status"] == "NotStarted":
            p["not_started"] += 1
        else:
            p["other_status"] += 1
        p["exact_kickoff"] += int(r["has_exact"])
        match_partition[int(r["Match_ID"])] = key

    # ── core fact / 特征覆盖(distinct match 数) ─────────────
    def _distinct_matches(table: str, col: str = "Match_ID") -> dict[tuple[int, str], int]:
        out: dict[tuple[int, str], int] = defaultdict(int)
        if not _table_exists(conn_core, table):
            return out
        for r in conn_core.execute(f"SELECT DISTINCT {col} AS mid FROM {table}"):
            key = match_partition.get(int(r["mid"]))
            if key:
                out[key] += 1
        return out

    fact_cov = {t: _distinct_matches(t) for t in CORE_FACT_TABLES}
    feature_cov = _distinct_matches("int_match_features", "Match_ID")
    gold_cov = _distinct_matches("gold_wdl_predictions", "match_id")

    # ── 赔率:full_timeline(经已验证 xref 的 1x2 赛前快照,按公司) ──
    xref: dict[str, int] = {}
    if _table_exists(conn_odds, "dim_match_xref"):
        for r in conn_odds.execute(
            """SELECT provider_match_id, fotmob_match_id FROM dim_match_xref
                WHERE provider='nowgoal' AND review_status IN ('auto_ok','confirmed')"""
        ):
            xref[str(r["provider_match_id"])] = int(r["fotmob_match_id"])

    full_by_company: dict[tuple[int, str], dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    full_any: dict[tuple[int, str], set[int]] = defaultdict(set)
    if _table_exists(conn_odds, "bronze_ng_odds_snap"):
        for r in conn_odds.execute(
            """SELECT DISTINCT provider_match_id, company_id, company_name
                 FROM bronze_ng_odds_snap
                WHERE market='1x2' AND market_phase='pre_match'"""
        ):
            fid = xref.get(str(r["provider_match_id"]))
            if fid is None:
                continue
            key = match_partition.get(fid)
            if key is None:
                continue
            label = f"{r['company_id']}:{(r['company_name'] or '').strip()}"
            full_by_company[key][label].add(fid)
            full_any[key].add(fid)

    # ── 赔率:open_close_only(legacy 1x2 两点摘要,按 source) ──
    legacy_by_source: dict[tuple[int, str], dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    legacy_any: dict[tuple[int, str], set[int]] = defaultdict(set)
    if _table_exists(conn_odds, "bronze_legacy_odds_summary"):
        for r in conn_odds.execute(
            """SELECT DISTINCT fotmob_match_id, source, provider
                 FROM bronze_legacy_odds_summary WHERE market='1x2'"""
        ):
            key = match_partition.get(int(r["fotmob_match_id"]))
            if key is None:
                continue
            label = f"{r['source']}:{(r['provider'] or '').strip()}"
            legacy_by_source[key][label].add(int(r["fotmob_match_id"]))
            legacy_any[key].add(int(r["fotmob_match_id"]))

    # ── platform:正式预测与评估 ─────────────────────────────
    official_cov: dict[tuple[int, str], int] = defaultdict(int)
    snapshot_status: dict[str, int] = defaultdict(int)
    eval_count = 0
    if _table_exists(conn_platform, "prediction_snapshots"):
        for r in conn_platform.execute(
            """SELECT match_id, status, is_official, locked_at
                 FROM prediction_snapshots"""
        ):
            snapshot_status[f"{r['status']}:official={int(bool(r['is_official']))}"] += 1
            if r["is_official"] and r["locked_at"] is not None:
                key = match_partition.get(int(r["match_id"]))
                if key:
                    official_cov[key] += 1
    if _table_exists(conn_platform, "prediction_evaluations"):
        eval_count = conn_platform.execute(
            "SELECT COUNT(*) FROM prediction_evaluations"
        ).fetchone()[0]

    # ── 汇总 ─────────────────────────────────────────────────
    rows = []
    for key in sorted(partitions):
        league_id, season = key
        p = partitions[key]
        fin = finished_ids[key]
        full = full_any[key] & fin
        legacy_only = (legacy_any[key] & fin) - full
        unavailable = fin - full - legacy_only
        rows.append(
            {
                "league_id": league_id,
                "season": season,
                **p,
                "facts": {t: fact_cov[t].get(key, 0) for t in CORE_FACT_TABLES},
                "features_int_match": feature_cov.get(key, 0),
                "gold_wdl_predictions": gold_cov.get(key, 0),
                "odds": {
                    "full_timeline_matches": len(full),
                    "full_timeline_by_company": {
                        label: len(ids & fin)
                        for label, ids in sorted(full_by_company[key].items())
                    },
                    "open_close_only_matches": len(legacy_only),
                    "legacy_by_source": {
                        label: len(ids & fin)
                        for label, ids in sorted(legacy_by_source[key].items())
                    },
                    "unavailable_matches": len(unavailable),
                },
                "official_locked_predictions": official_cov.get(key, 0),
            }
        )

    return {
        "schema_version": 1,
        "partitions": rows,
        "totals": {
            "leagues": len({r["league_id"] for r in rows}),
            "partitions": len(rows),
            "matches": sum(r["matches"] for r in rows),
            "finished": sum(r["finished"] for r in rows),
            "five_league_finished": sum(
                r["finished"] for r in rows if r["league_id"] in FIVE_LEAGUES
            ),
            "prediction_snapshot_status": dict(sorted(snapshot_status.items())),
            "prediction_evaluations": eval_count,
        },
    }


def to_markdown(cov: dict) -> str:
    lines = [
        "# 数据覆盖矩阵(data_coverage.py 生成,重跑以此为准)",
        "",
        "| 联赛 | 赛季 | 比赛(完/未) | exact KO | 特征 | full_timeline | open_close_only | 无赔率 | 正式预测 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in cov["partitions"]:
        o = r["odds"]
        lines.append(
            f"| {r['league_id']} | {r['season']} | {r['matches']}"
            f" ({r['finished']}/{r['not_started']}) | {r['exact_kickoff']}"
            f" | {r['features_int_match']} | {o['full_timeline_matches']}"
            f" | {o['open_close_only_matches']} | {o['unavailable_matches']}"
            f" | {r['official_locked_predictions']} |"
        )
    t = cov["totals"]
    lines += [
        "",
        f"合计:{t['partitions']} 个联赛-赛季分区,{t['matches']} 场"
        f"(完赛 {t['finished']};五大联赛完赛 {t['five_league_finished']});"
        f"正式评估 {t['prediction_evaluations']} 条。",
        "",
        "公司/来源明细在 JSON 的 full_timeline_by_company / legacy_by_source"
        "(标签形如 `company_id:name` / `source:provider`,不同标签绝不合并)。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core", default="data/allwin.db")
    ap.add_argument("--odds", default="data/odds.db")
    ap.add_argument("--platform", default="data/platform.db")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--md", dest="md_out", default=None)
    args = ap.parse_args(argv)

    conn_core, conn_odds, conn_platform = _ro(args.core), _ro(args.odds), _ro(args.platform)
    try:
        cov = build_coverage(conn_core, conn_odds, conn_platform)
    finally:
        conn_core.close(); conn_odds.close(); conn_platform.close()

    payload = json.dumps(cov, ensure_ascii=False, sort_keys=True, indent=1)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n")
    if args.md_out:
        Path(args.md_out).write_text(to_markdown(cov))
    if not args.json_out and not args.md_out:
        print(payload)
    print(f"coverage_sha256={digest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
