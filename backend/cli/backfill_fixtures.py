"""赛程行缺失补采:按 (league_id, season) 全量枚举 provider 赛程,补齐当前
不完整的赛程骨架,再逐场补明细(比分/射门/球队统计等)。

背景(2026-08-27,西甲象限图缺队排查的延伸;CLAUDE.md §6.3):
backend/ingest/ingest_future_fixtures.py::rows_from_payload() 明确跳过
status='Finish' 的行:

    if status == "Finish":
        # 已完赛场次交给现有流程(ingest_league.py)按完整比赛处理。
        continue

常规 6 小时赛程同步(schedule_sync_multi)因此只写"尚未完赛"的比赛。任何在
赛季中途才接入的联赛,接入那一刻已经踢完的比赛永久缺失,且没有任何定时
任务补上——生产实测:巴甲(268)缺 215/380 场,荷甲(57)、葡超(61) 各缺 9
场,全部是"整轮消失",不是随机丢失(轮次 1..N 里某几轮完全为空)。这个
形状与本模块的 pipeline_gates.py::G15 fixture_round_gap 门检测的信号
完全对应。

本 CLI 就是那条缺失的"可按 (league_id, season) 重跑的补采路径"(§6.3 要求
每个"只在事件发生那一刻写入"的任务都要有)。复用
backend/ingest/ingest_league.py 的 enumerate_fixtures()/
seed_fixture_skeletons()/ingest_matches_sequential(),不重新实现赛季回声
校验或逐场重试逻辑。

⚠️ 只补骨架会造出 status='Finish' 但比分为 NULL 的行——比缺失更糟(页面
显示"已完赛"却没有比分)。seed_fixture_skeletons 走的 upsert_fixture_row
是列作用域的,FIXTURE_OWNED_COLUMNS 不含 home_score/away_score。本 CLI
因此把"补骨架"和"补明细"绑在同一次 --commit 里做,不提供只补骨架的模式;
--commit 结束时会显式核对该 league-season 是否还残留 Finish+NULL 比分的
行,残留就大声警告(不静默放过)。

⚠️ --match-ids(ingest_league.py / reingest_matches.py 那个)不是补采路径:
ingest_match() 对不存在的 dim_match 行 fail closed(2026-08-25 赛季修复的
一部分),无法创建缺失的行。只有本 CLI 走的 enumeration + skeleton 能创建行。

用法:
  # 先看损伤,零写入,只发 1 次 HTTP(league_matches,含赛季回声校验)
  python -m backend.cli.backfill_fixtures --league-id 268 --season 2026

  # 真正执行,--detail-limit 限制这一次补多少场明细(骨架不受限,一次写完)
  python -m backend.cli.backfill_fixtures --league-id 268 --season 2026 \
      --commit --detail-limit 50
  # 重复运行直到 detail_targets=0(已有比分的场次不会被重复请求明细)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# ingest_league.py 是 script 风格模块(内部 `from db import ...`),不可直接
# 包导入;沿用 backend/cli/backfill_season_tables.py 头部同款 sys.path 桥接,
# 不复制其函数体。
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_BACKEND_DIR, os.path.join(_BACKEND_DIR, "ingest")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ingest_league import (  # noqa: E402
    _acquire_lock,
    _release_lock,
    enumerate_fixtures,
    ingest_matches_sequential,
    seed_fixture_skeletons,
)

from backend.db.connections import connect_ro, connect_rw  # noqa: E402
from backend.fotmob_client import FotMobClient  # noqa: E402
from backend.season_regime import season_for_match  # noqa: E402


def _round_gap(fixtures: list) -> dict:
    """数字轮次的缺口——与 pipeline_gates.py::G15 fixture_round_gap 同一
    算法,这里只是给 dry-run 报告用,不产生告警、不落库。"""
    rounds = set()
    for f in fixtures:
        r = f.get("round")
        if r is not None and str(r).isdigit():
            rounds.add(int(r))
    if not rounds:
        return {"max_round": None, "missing_rounds": []}
    mx = max(rounds)
    missing = sorted(set(range(1, mx + 1)) - rounds)
    return {"max_round": mx, "missing_rounds": missing[:15]}


def plan(client: FotMobClient, conn, league_id: int, season: str) -> dict:
    """只读:枚举 provider 全量赛程,与库内现状比对,不写任何东西。
    发起且仅发起 1 次 HTTP 请求(enumerate_fixtures 内部的 league_matches,
    含赛季回声校验——响应与 league_id/season 不符会直接抛异常,不返回半页)。
    """
    fixtures = enumerate_fixtures(client, league_id, season)

    existing_rows = conn.execute(
        "SELECT Match_ID, status, home_score, away_score FROM dim_match"
        " WHERE League_ID=? AND Season=?",
        (league_id, season),
    ).fetchall()
    existing = {r["Match_ID"]: dict(r) for r in existing_rows}

    to_create, to_update, unchanged = [], [], []
    for f in fixtures:
        cur = existing.get(f["match_id"])
        if cur is None:
            to_create.append(f)
        elif cur["status"] != f["status"]:
            to_update.append(f)
        else:
            unchanged.append(f)

    # 需要补明细(比分等)的场次:provider 说已完赛(非取消),且要么库里还没有
    # 这一行,要么行在但比分是 NULL(此前只补过骨架、明细没补完)。
    detail_targets = [
        f for f in fixtures
        if f["finished"] and not f["cancelled"]
        and existing.get(f["match_id"], {}).get("home_score") is None
    ]

    # provider 场次数低于库中已有 → 反退化,不猜、直接拒绝(commit 阶段生效;
    # 这里只是把判断结果带出去,dry-run 模式下如实报告但不影响退出码)。
    regression = len(fixtures) < len(existing)

    # 赛季触发器预检:每个将新建的行按 (League_ID, Date) 推导赛季,与
    # --season 比对——不一致时 migrations/core/0011 的触发器会在写入时
    # ABORT。这里先算一遍报出来,不要写到一半才崩、留下部分提交的批次。
    season_mismatches = []
    for f in to_create:
        utc = f.get("utc")
        date = utc[:10] if isinstance(utc, str) and len(utc) >= 10 else None
        if date is None:
            continue
        derived = season_for_match(conn, league_id, date)
        if derived is not None and derived != season:
            season_mismatches.append(
                {"match_id": f["match_id"], "date": date, "derived_season": derived}
            )

    return {
        "league_id": league_id,
        "season": season,
        "provider_fixture_count": len(fixtures),
        "existing_row_count": len(existing),
        "to_create": len(to_create),
        "to_update": len(to_update),
        "unchanged": len(unchanged),
        "detail_targets": len(detail_targets),
        "round_gap_before": _round_gap([f for f in fixtures if f["match_id"] in existing]),
        "round_gap_after": _round_gap(fixtures),
        "regression": regression,
        "season_mismatches": season_mismatches,
        # 内部字段,commit 阶段复用;报告输出前会被过滤掉。
        "_fixtures": fixtures,
        "_detail_targets": detail_targets,
    }


def run_commit(
    conn_rw,
    plan_result: dict,
    *,
    detail_limit: int | None,
    sleep: float,
    sleep_jitter: float,
    cooldown_window: int,
    cooldown_fail_rate: float,
    cooldown_seconds: float,
) -> dict:
    league_id = plan_result["league_id"]
    season = plan_result["season"]
    fixtures = plan_result["_fixtures"]

    # 骨架:一次性全部写(seed_fixture_skeletons 本身是列作用域 upsert,
    # 对已存在且未变化的行是空操作,重复调用安全)。
    seeded = seed_fixture_skeletons(league_id, season, fixtures)

    detail_targets = plan_result["_detail_targets"]
    if detail_limit is not None:
        detail_targets = detail_targets[:detail_limit]

    if detail_targets:
        ok, fail = ingest_matches_sequential(
            detail_targets,
            league_id=league_id,
            sleep=sleep,
            sleep_jitter=sleep_jitter,
            cooldown_window=cooldown_window,
            cooldown_fail_rate=cooldown_fail_rate,
            cooldown_seconds=cooldown_seconds,
        )
    else:
        ok, fail = [], []

    # 收尾核对:该 league-season 是否还残留"已完赛但比分为 NULL"的行——
    # 正常情况下不该有,出现了要大声说,不能悄悄放过(§2.2)。
    leftover = conn_rw.execute(
        "SELECT COUNT(*) FROM dim_match WHERE League_ID=? AND Season=?"
        " AND status='Finish' AND (home_score IS NULL OR away_score IS NULL)",
        (league_id, season),
    ).fetchone()[0]

    return {
        "seeded_rows": seeded,
        "detail_attempted": len(detail_targets),
        "detail_ok": len(ok),
        "detail_failed": len(fail),
        "detail_fail_ids": fail,
        "finish_rows_with_null_score_remaining": leftover,
    }


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _print_report(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, default=str))
        return
    mode = report.get("mode", "?")
    print(f"[{mode}] league_id={report['league_id']} season={report['season']}")
    print(f"  provider 场次: {report['provider_fixture_count']}"
          f"  库中已有: {report['existing_row_count']}"
          f"  将新建: {report['to_create']}"
          f"  将更新: {report['to_update']}"
          f"  不变: {report['unchanged']}")
    print(f"  需要补明细(含已建骨架但比分为空的): {report['detail_targets']}")
    gap_before = report["round_gap_before"]
    gap_after = report["round_gap_after"]
    print(f"  轮次缺口 修复前: max_round={gap_before['max_round']} "
          f"missing={gap_before['missing_rounds']}")
    print(f"  轮次缺口 修复后: max_round={gap_after['max_round']} "
          f"missing={gap_after['missing_rounds']}")
    if report.get("regression"):
        print("  ⚠️ provider 场次数低于库中已有行数——拒绝写入,不猜、不 DELETE")
    if report.get("season_mismatches"):
        print(f"  ⚠️ {len(report['season_mismatches'])} 场按日期推导的赛季与 --season 不符"
              "(写入会被存储层触发器 ABORT):")
        for m in report["season_mismatches"][:10]:
            print(f"     match_id={m['match_id']} date={m['date']} "
                  f"derived_season={m['derived_season']}")
    if "seeded_rows" in report:
        print(f"  骨架落库: {report['seeded_rows']} 行")
        print(f"  明细: 尝试 {report['detail_attempted']}, "
              f"成功 {report['detail_ok']}, 失败 {report['detail_failed']}")
        if report["detail_fail_ids"]:
            fail_str = ",".join(str(x) for x in report["detail_fail_ids"])
            print(f"     失败 match_ids: {fail_str}")
            print(f"     重试: python -m backend.cli.backfill_fixtures "
                  f"--league-id {report['league_id']} --season {report['season']} --commit")
        remaining = report.get("finish_rows_with_null_score_remaining", 0)
        if remaining:
            print(f"  ⚠️⚠️ 仍有 {remaining} 行 status='Finish' 但比分为 NULL——"
                  "重跑本命令补齐,不要让这类行留在库里")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--league-id", type=int, required=True)
    ap.add_argument("--season", type=str, required=True)
    ap.add_argument("--commit", action="store_true", help="真正执行(默认 dry-run 不写库)")
    ap.add_argument(
        "--detail-limit", type=int, default=None,
        help="本次最多补多少场明细(骨架不受此限制,一次性全部写);"
        "留空=不限制,一次补完",
    )
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--sleep-jitter", type=float, default=0.5)
    ap.add_argument("--cooldown-window", type=int, default=10)
    ap.add_argument("--cooldown-fail-rate", type=float, default=0.8)
    ap.add_argument("--cooldown-seconds", type=float, default=90.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    lock_path = _acquire_lock(args.league_id)
    try:
        client = FotMobClient()
        conn_ro = connect_ro("core")
        try:
            result = plan(client, conn_ro, args.league_id, args.season)
        finally:
            conn_ro.close()

        if not args.commit:
            report = _clean(result)
            report["mode"] = "dry-run"
            _print_report(report, args.json)
            return 0

        if result["regression"]:
            report = _clean(result)
            report["mode"] = "commit"
            report["action"] = "refused_regression"
            _print_report(report, args.json)
            return 1

        if result["season_mismatches"]:
            report = _clean(result)
            report["mode"] = "commit"
            report["action"] = "refused_season_mismatch"
            _print_report(report, args.json)
            return 1

        conn_rw = connect_rw("core")
        try:
            commit_result = run_commit(
                conn_rw,
                result,
                detail_limit=args.detail_limit,
                sleep=args.sleep,
                sleep_jitter=args.sleep_jitter,
                cooldown_window=args.cooldown_window,
                cooldown_fail_rate=args.cooldown_fail_rate,
                cooldown_seconds=args.cooldown_seconds,
            )
        finally:
            conn_rw.close()

        report = _clean(result)
        report.update(commit_result)
        report["mode"] = "commit"
        _print_report(report, args.json)
        return 0
    finally:
        _release_lock(lock_path)


if __name__ == "__main__":
    sys.exit(main())
