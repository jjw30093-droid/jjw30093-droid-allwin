"""T+7 赛程同步(数据管道重建 Phase 2)。

每天对 LEAGUE_META 全部联赛刷新未来 7 天赛程,捕捉改期/延期/新公布场次。
逐联赛 `league_matches(id)`(不传 season → 赛季发现),身份校验 discovery 口径,
反退化门禁保护 dim_match,逐行只更新"赛程拥有列"(不清裁判/天气/比分),
每联赛落一行 fixture_sync_ledger。poll_state 按 6 小时/联赛节流。

用法:
    python -m backend.cli.sync_fixtures_window --due            # 到期联赛才抓(worker 默认)
    python -m backend.cli.sync_fixtures_window --all            # 无视节流,全部抓
    python -m backend.cli.sync_fixtures_window --league-id 48   # 只抓单个
    python -m backend.cli.sync_fixtures_window --due --dry-run  # 不写库、不发网络之外一切照走
    python -m backend.cli.sync_fixtures_window --offline-fixture path.json --league-id 48
"""

import argparse
import json
import sys
from datetime import timedelta

from backend.db.connections import connect_ro, connect_rw, tx
from backend.db.util import new_uuid, parse_strict_utc, utc_now_iso
from backend.ingest.ingest_future_fixtures import (
    SeasonIdentityError,
    discover_season_identity,
    rows_from_payload,
    update_fixture_kickoff_only,
    upsert_fixture_row,
)
from backend.ingest.poll_windows import (
    FIXTURE_SYNC_INTERVAL_SECONDS,
    SOURCE_FOTMOB_FIXTURES,
    is_due,
    mark_polled,
)
from backend.queries.leagues import LEAGUE_META

HORIZON_DAYS = 7
REGRESSION_FLOOR_RATIO = 0.5   # 较上次成功抓取骤降到此比例以下 → 拒写


def _in_horizon(rows: list[dict], now, horizon) -> int:
    n = 0
    for r in rows:
        kt = r.get("kickoff_at_utc")
        if not kt:
            continue
        dt = parse_strict_utc(kt)
        if dt is not None and now < dt <= horizon:
            n += 1
    return n


def _prev_baseline(conn_odds, league_id: int, season: str) -> int | None:
    """上一次成功写入的抓取行数(同联赛同赛季),作为 G-A 反退化基线。"""
    row = conn_odds.execute(
        "SELECT fetched_rows FROM fixture_sync_ledger "
        "WHERE league_id=? AND season=? AND verdict='written' "
        "ORDER BY run_at DESC LIMIT 1",
        (league_id, season),
    ).fetchone()
    return row["fetched_rows"] if row else None


def _existing_finished_and_scored(conn_core, match_ids: list[int]) -> tuple[set, set]:
    """已在 dim_match 中标 Finish 的 Match_ID,以及已有比分的 Match_ID。"""
    if not match_ids:
        return set(), set()
    ph = ",".join("?" for _ in match_ids)
    finished = {r[0] for r in conn_core.execute(
        f"SELECT Match_ID FROM dim_match WHERE Match_ID IN ({ph}) AND status='Finish'",
        match_ids).fetchall()}
    scored = {r[0] for r in conn_core.execute(
        f"SELECT Match_ID FROM dim_match WHERE Match_ID IN ({ph}) "
        f"AND (home_score IS NOT NULL OR away_score IS NOT NULL)",
        match_ids).fetchall()}
    return finished, scored


def _write_ledger(conn_odds, dry_run: bool, **kw) -> None:
    """落一行 ledger。dry_run 下不写任何持久化状态(dry-run 应当零副作用)。"""
    if dry_run:
        return
    cols = ("run_at", "poll_run_id", "league_id", "season", "provider_selected_season",
            "fallback_season_used", "fetched_rows", "horizon7_rows", "written_rows",
            "prev_fetched_rows", "verdict", "detail")
    with tx(conn_odds):
        conn_odds.execute(
            f"INSERT INTO fixture_sync_ledger ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            [kw.get(c) for c in cols],
        )


def sync_one_league(
    league_id: int, *, client, conn_core_rw, conn_odds, poll_run_id: str,
    now_iso: str, dry_run: bool, offline_payload: dict | None = None,
) -> dict:
    """同步单个联赛的 T+7 赛程,返回结果摘要 dict,并落一行 ledger。"""
    now = parse_strict_utc(now_iso)
    horizon = now + timedelta(days=HORIZON_DAYS)
    result = {"league_id": league_id, "verdict": None, "fetched": 0, "written": 0}

    # 1. 抓取 + 身份发现
    try:
        data = offline_payload if offline_payload is not None \
            else client.league_matches(league_id)
        season = discover_season_identity(data, league_id)
        rows = rows_from_payload(data, league_id, season)
    except SeasonIdentityError as e:
        result["verdict"] = "refused_identity"
        result["detail"] = str(e)
        _write_ledger(conn_odds, dry_run, run_at=now_iso, poll_run_id=poll_run_id,
                      league_id=league_id, season=None, provider_selected_season=None,
                      fallback_season_used=0, fetched_rows=0, horizon7_rows=0,
                      written_rows=0, prev_fetched_rows=None,
                      verdict="refused_identity", detail=str(e))
        return result
    except Exception as e:  # 网络/解析失败:如实记录 fetch_failed,不伪装
        result["verdict"] = "fetch_failed"
        result["detail"] = f"{type(e).__name__}: {e}"
        _write_ledger(conn_odds, dry_run, run_at=now_iso, poll_run_id=poll_run_id,
                      league_id=league_id, season=None, provider_selected_season=None,
                      fallback_season_used=0, fetched_rows=0, horizon7_rows=0,
                      written_rows=0, prev_fetched_rows=None,
                      verdict="fetch_failed", detail=result["detail"])
        return result

    fetched = len(rows)
    horizon7 = _in_horizon(rows, now, horizon)
    result["fetched"] = fetched
    baseline = _prev_baseline(conn_odds, league_id, season)

    # 2. off_season:无未来赛程且无基线冲突 → 合法空,不告警、不拒写
    if fetched == 0:
        result["verdict"] = "off_season"
        _write_ledger(conn_odds, dry_run, run_at=now_iso, poll_run_id=poll_run_id,
                      league_id=league_id, season=season,
                      provider_selected_season=season, fallback_season_used=0,
                      fetched_rows=0, horizon7_rows=0, written_rows=0,
                      prev_fetched_rows=baseline, verdict="off_season",
                      detail="来源无未开赛场次(季外或赛程未公布)")
        return result

    # 3. G-A 反退化:较上次成功抓取骤降 >50% → 拒写、保留旧数据(事故 #5/#7)
    if baseline is not None and baseline >= 4 and fetched < baseline * REGRESSION_FLOOR_RATIO:
        detail = f"骤降 {baseline}→{fetched}(<{REGRESSION_FLOOR_RATIO:.0%}),疑似部分数据,拒写保留旧数据"
        result["verdict"] = "refused_regression"
        result["detail"] = detail
        _write_ledger(conn_odds, dry_run, run_at=now_iso, poll_run_id=poll_run_id,
                      league_id=league_id, season=season,
                      provider_selected_season=season, fallback_season_used=0,
                      fetched_rows=fetched, horizon7_rows=horizon7, written_rows=0,
                      prev_fetched_rows=baseline, verdict="refused_regression",
                      detail=detail)
        return result

    # 4. G-B/G-C:已完赛/已有比分行不得被赛程行覆盖(事故 #2 清列同型)
    conn_core_ro = connect_ro("core")
    try:
        finished, scored = _existing_finished_and_scored(
            conn_core_ro, [r["Match_ID"] for r in rows])
    finally:
        conn_core_ro.close()
    conflict = [r["Match_ID"] for r in rows
                if (r["Match_ID"] in finished or r["Match_ID"] in scored)
                and r["status"] != "Finish"]

    # 2026-09-07:冲突行**逐行剔除**,不再整批拒写。
    #
    # 安全性一字未变——已完赛/已有比分的行仍然绝不会被未完赛行覆盖,只是从
    # "整个联赛拒写"改成"把这几行从写入集合里排除"。
    #
    # 改的原因是真实事故:荷甲(57)自 2026-09-06T06:06 起连续 4 次同步(每 ~6h)
    # 全部 refused_downgrade、written_rows=0,**整个联赛的赛程同步停摆 24 小时**,
    # 265 行一行没写进去。肇事的是 FotMob 一条自相矛盾的记录(match 5781733):
    # 同一个对象里既带比分 home 1 / away 3,又标 notStarted:true、
    # finished:false,并把开球从 09-05 16:45Z 改挂到 09-08 12:00Z。
    # 这种脏数据不会自愈,旧写法会让该联赛**无限期**冻结。
    #
    # 一条坏行不该有能力冻结整个联赛;但也绝不能静默丢弃(CLAUDE.md §13),
    # 所以剔除的 Match_ID 全部写进 ledger.detail,verdict 用独立取值
    # written_with_conflicts,由 pipeline_gates 的 G2 照常暴露给人。
    safe_rows = rows
    conflict_rows: list = []
    verdict = "written"
    detail = None
    if conflict:
        conflict_set = set(conflict)
        safe_rows = [r for r in rows if r["Match_ID"] not in conflict_set]
        conflict_rows = [r for r in rows if r["Match_ID"] in conflict_set]
        verdict = "written_with_conflicts"

    # 5. 写入(只更新赛程拥有列,不碰裁判/天气/比分/kickoff 回填)
    #
    # 冲突行走 update_fixture_kickoff_only:**只放行开球时刻**,status/比分
    # 一律不动(2026-09-07 站长选定的方案 B)。理由见该函数 docstring——被守卫
    # 拒掉的那一行恰恰是唯一携带新开球时刻的行,整行丢弃会让改期永远进不了库。
    kickoff_only_updated = 0
    if not dry_run:
        with tx(conn_core_rw):
            for r in safe_rows:
                upsert_fixture_row(conn_core_rw, r)
            for r in conflict_rows:
                if update_fixture_kickoff_only(conn_core_rw, r):
                    kickoff_only_updated += 1
    if conflict:
        skipped_entirely = len(conflict_rows) - kickoff_only_updated
        detail = (f"{len(conflict)} 行已完赛/已有比分,保留原 status 与比分;"
                  f"其中 {kickoff_only_updated} 行只更新了开球时刻,"
                  f"{skipped_entirely} 行因赛季标签会变而整行未动: {conflict[:10]}")
    result["kickoff_only_updated"] = kickoff_only_updated
    result["written"] = 0 if dry_run else len(safe_rows)
    result["verdict"] = verdict
    result["season"] = season
    if detail:
        result["detail"] = detail
    _write_ledger(conn_odds, dry_run, run_at=now_iso, poll_run_id=poll_run_id,
                  league_id=league_id, season=season,
                  provider_selected_season=season, fallback_season_used=0,
                  fetched_rows=fetched, horizon7_rows=horizon7,
                  written_rows=result["written"], prev_fetched_rows=baseline,
                  verdict=verdict,
                  detail=detail or ("dry_run" if dry_run else None))
    return result


def run_sync(*, due_only: bool, only_league: int | None, dry_run: bool,
             now_iso: str | None = None, offline_payload: dict | None = None) -> dict:
    now_iso = now_iso or utc_now_iso()
    poll_run_id = new_uuid()
    league_ids = [only_league] if only_league else list(LEAGUE_META)
    conn_odds = connect_rw("odds")
    conn_core_rw = connect_rw("core")
    client = None
    summary = {"poll_run_id": poll_run_id, "now": now_iso, "leagues": []}
    try:
        for lid in league_ids:
            if due_only and offline_payload is None:
                if not is_due(conn_odds, SOURCE_FOTMOB_FIXTURES, str(lid),
                              FIXTURE_SYNC_INTERVAL_SECONDS, now_iso):
                    summary["leagues"].append({"league_id": lid, "verdict": "not_due"})
                    continue
            if client is None and offline_payload is None:
                from backend.fotmob_client import FotMobClient
                client = FotMobClient()
            res = sync_one_league(
                lid, client=client, conn_core_rw=conn_core_rw, conn_odds=conn_odds,
                poll_run_id=poll_run_id, now_iso=now_iso, dry_run=dry_run,
                offline_payload=offline_payload)
            summary["leagues"].append(res)
            # 只有真正拿到来源的结论性答案(written/off_season/身份或反退化拒写)
            # 才计入 6 小时节流;fetch_failed(网络/解析失败,没拿到任何答案)不
            # mark_polled——否则一次偶发 SSL/超时会让该联赛静默拉黑 6 小时,
            # 且期间每次 tick 因 not_due 直接跳过、被 CLI 判为"无 fetch_failed"
            # 而报退出码 0,把真实网络失败伪装成成功(真实运行中复现过)。
            # 不 mark 时下一次 tick(runner 重试的 60s 后,或 15 分钟链)会
            # is_due()==True 立即重试,如实反映"还没成功过"。
            if offline_payload is None and res.get("verdict") != "fetch_failed":
                mark_polled(conn_odds, SOURCE_FOTMOB_FIXTURES, str(lid), now_iso, poll_run_id,
                            tier="fixture_sync",
                            ok=res.get("verdict") in ("written", "off_season"))
    finally:
        conn_odds.close()
        conn_core_rw.close()
    # 汇总
    by_verdict = {}
    for r in summary["leagues"]:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
    summary["by_verdict"] = by_verdict
    summary["written_total"] = sum(r.get("written", 0) for r in summary["leagues"])
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--due", action="store_true", help="只抓到期联赛(6h 节流)")
    p.add_argument("--all", action="store_true", help="无视节流全部抓")
    p.add_argument("--league-id", type=int, default=None)
    p.add_argument("--dry-run", action="store_true", help="不写 dim_match")
    p.add_argument("--offline-fixture", type=str, default=None,
                   help="离线 league_matches JSON(需配 --league-id),不发网络")
    p.add_argument("--now", type=str, default=None)
    args = p.parse_args(argv)

    offline_payload = None
    if args.offline_fixture:
        if not args.league_id:
            print("--offline-fixture 需配 --league-id", file=sys.stderr)
            return 2
        offline_payload = json.loads(open(args.offline_fixture, encoding="utf-8").read())

    summary = run_sync(
        due_only=args.due and not args.all,
        only_league=args.league_id, dry_run=args.dry_run,
        now_iso=args.now, offline_payload=offline_payload)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    # 有 refused_* / fetch_failed → 非零退出(供 worker 感知)。
    # written_with_conflicts **刻意不在这里**:那是部分成功(联赛照常同步,只是
    # 剔了几行脏数据),让它把任务判失败会在数据源脏数据持续期间一直报错;
    # 按 CLAUDE.md §13,这类"发现了问题"由 pipeline_gates 的告警表达,不由
    # 任务失败表达。refused_downgrade 保留在集合里只为兼容历史 ledger 取值,
    # 写入端 2026-09-07 起已不再产生它。
    bad = {"refused_regression", "refused_downgrade", "refused_identity", "fetch_failed"}
    return 1 if bad & set(summary["by_verdict"]) else 0


if __name__ == "__main__":
    sys.exit(main())
