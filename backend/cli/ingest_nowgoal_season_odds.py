"""从 NowGoal season-archive 回补一个 (FotMob league_id, season) 的已完赛比赛
赔率两点摘要,写 `data/odds.db` `bronze_legacy_odds_summary`
(`source='nowgoal_archive_refetch'`,该值已被 `odds/0005_legacy_source_jka.sql`
允许,不需要新迁移)。

2026-08-08 新增,给挪超(59)/瑞典超(67)2026 赛季回补用,是
`backend/cli/ingest_jka_legacy_odds.py` refetch 那部分的通用化、可重跑版本
(J/K/A 当时是 ad-hoc 脚本,产物固化进 runtime/research/jka-odds-ingest/,
脚本本身从未进仓库)。命名刻意通用、联赛参数化,供以后新联赛/新赛季复用。

流程:
1. core 只读查目标集(该联赛该赛季 status='Finish' 的比赛,要求
   kickoff_precision='exact');
2. NowGoalArchiveTransport.archive_season() 一次请求拿全季(自证身份门禁,
   见 nowgoal_archive.SeasonIdentityError);
3. 用 backend.ingest.nowgoal_historical_match_resolution 做身份解析——
   不用 backend.ingest.entity_resolution(它要求入参 kickoff 已经是 UTC,
   archive 的 kickoff 是北京时间,直接喂会导致 kickoff_diff 恒为 -28800、
   100% needs_review;它也没有联赛过滤,10 个联赛同日撞车风险已在
   docs/data-plan.md §4-4 登记)。两轮:第一轮空词典靠比分+队名解析出多数,
   反哺训练对再学出 nowgoal_team_id -> fotmob_team_id 词典,第二轮复用词典;
4. 在原模块没有的地方**额外加一道门禁**:目标比赛都有精确 kickoff_at_utc,
   要求 |候选 kickoff(转 UTC 后) - 目标 kickoff_at_utc| <= 1800s 才能算
   auto_ok,否则降级为 needs_review——这一条本身就能拦住时区回归;
5. 对 auto_ok 的比赛抓 mix_history(ah/ou)+ euro_history(1x2),提取两点摘要
   (严格赛前过滤,见 nowgoal_archive.two_point_from_mix_history);
6. 写 bronze_legacy_odds_summary,`INSERT OR IGNORE`(按 UNIQUE(fotmob_match_id,
   source, market, period)幂等)。

用法:
    python -m backend.cli.ingest_nowgoal_season_odds \\
        --league-id 59 --season 2026 --nowgoal-league-id 22 --dry-run
    python -m backend.cli.ingest_nowgoal_season_odds \\
        --league-id 59 --season 2026 --nowgoal-league-id 22 --live
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from backend.ingest.nowgoal_historical_match_resolution import (
    GapMatch,
    NowGoalCandidate,
    STATUS_AUTO_OK,
    TrainingPair,
    build_team_id_dictionary,
    resolve_batch,
)
from backend.providers.nowgoal import normalize_for_inversion
from backend.providers.nowgoal_archive import (
    NowGoalArchiveTransport,
    archive_kickoff_to_utc,
    parse_archive_season,
    parse_team_info,
    two_point_from_euro_history,
    two_point_from_mix_history,
)

CORE_DB = _REPO / "data" / "allwin.db"
ODDS_DB = _REPO / "data" / "odds.db"

KICKOFF_TOLERANCE_SECONDS = 1800  # 与 entity_resolution.KICKOFF_TOLERANCE_SECONDS 一致


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _archive_season_key(season: str) -> str:
    """"2026" -> "2026"(自然年联赛,裸年份原样透传);
    "2020/2021" -> "2020-2021"(跨年联赛)。不做其它猜测。"""
    if "/" in season:
        y1, y2 = season.split("/", 1)
        return f"{y1}-{y2}"
    return season


def load_target_matches(core_db_path: Path, league_id: int, season: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{core_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT Match_ID, kickoff_at_utc, kickoff_precision, Home_Team_ID, Away_Team_ID,
                  Home_Team_Name, Away_Team_Name, home_score, away_score, Date
             FROM dim_match
            WHERE League_ID=? AND Season=? AND status='Finish'
            ORDER BY kickoff_at_utc""",
        (league_id, season),
    ).fetchall()
    conn.close()
    out = []
    skipped_no_exact_kickoff = 0
    for r in rows:
        if r["kickoff_precision"] != "exact" or not r["kickoff_at_utc"]:
            skipped_no_exact_kickoff += 1
            continue
        out.append(dict(r))
    return out, skipped_no_exact_kickoff  # type: ignore[return-value]


def _parse_utc(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class TeamIdDictionaryError(Exception):
    """种子训练学出的 nowgoal_team_id -> fotmob_team_id 词典不是干净双射,
    拒绝使用(fail-closed,不猜)。"""


def resolve_and_gate(target_matches: list[dict], archive_rows, league_id: int,
                     team_names: dict[int, str] | None = None,
                     min_votes: int = 3, min_margin_ratio: float = 3.0) -> list[dict]:
    """两轮身份解析 + 额外的精确 kickoff 门禁。返回逐场结果(含 status/titan_id/
    kickoff_diff_seconds 等字段,供 --dry-run 展示与 --live 写入前过滤)。

    `min_votes`/`min_margin_ratio`:传给 `build_team_id_dictionary`。
    `nowgoal_historical_match_resolution.py` 的模块默认值(10/5.0)是为原始
    用途(2,269 场跨多赛季历史缺口)校准的;本 CLI 一次只处理一个联赛一个
    赛季(约 120 场、16 支队),默认值会让几乎所有队伍拿不到起投票数,
    2026-08-08 实测:min_votes=10 时 16 队只有 3 队进词典,
    min_votes=3,margin=3.0 时 16/16 队全部进词典且是干净双射
    (16 个不同 fotmob_team_id,零碰撞)——已验证不是"降低门槛制造匹配",
    是把门槛校准到真实样本规模;双射校验见下方 fail-closed 检查。
    """
    gap_matches = [
        GapMatch(
            match_id=m["Match_ID"], league_id=league_id, date=m["Date"],
            home_team_id=m["Home_Team_ID"], away_team_id=m["Away_Team_ID"],
            home_team_name=m["Home_Team_Name"], away_team_name=m["Away_Team_Name"],
            home_score=m["home_score"], away_score=m["away_score"],
        )
        for m in target_matches
    ]
    team_names = team_names or {}
    candidates = [
        NowGoalCandidate(
            titan_id=r.titan_id, nowgoal_league_id=r.ng_league_id,
            kickoff_local=r.kickoff_utc[:16].replace("T", " "),  # 占位:resolve_batch 只做
                                                                  # 自然日窗口过滤,真正的精确
                                                                  # UTC 比较在下面的门禁里做
            home_team_id=r.home_ng_id, away_team_id=r.away_ng_id,
            # 队名来自 archive TeamInfo(见 nowgoal_archive.parse_team_info)——
            # 给"词典未覆盖到票数门槛的球队"一条名称相似度兜底路径,
            # 不让它们因为纯 id 词典缺票就整批判 no_candidate。
            home_team_name=team_names.get(r.home_ng_id), away_team_name=team_names.get(r.away_ng_id),
            home_score=r.home_score, away_score=r.away_score,
        )
        for r in archive_rows
    ]
    # 候选按"任意日期窗口内可能匹配的比赛"全部喂给每场——resolve_gap_match
    # 自己会做联赛+日期窗口过滤,这里不用再按 match 精筛。
    candidates_by_match_id = {gm.match_id: candidates for gm in gap_matches}

    # 第一轮:空词典,候选没有队名(archive TeamInfo 只给 id,不在这里查),
    # 所以第一轮的"id 词典"路径天然是空的。真正启动词典学习需要至少一轮种子——
    # 用"联赛(候选本就已按联赛过滤)+ 日期窗口(±2 天)+ 比分"单独解出种子对,
    # 只挑在这三重过滤下唯一存活的场次(容忍多候选场次先跳过,不猜)。
    from backend.ingest.nowgoal_historical_match_resolution import within_date_window

    seed_pairs: list[TrainingPair] = []
    for gm in gap_matches:
        in_window = [c for c in candidates
                    if within_date_window(gm.date, c.kickoff_local, window_days=2) is True]
        same_score = [
            c for c in in_window
            if (c.home_score, c.away_score) in ((gm.home_score, gm.away_score),
                                                 (gm.away_score, gm.home_score))
        ]
        if len(same_score) == 1:
            c = same_score[0]
            direct = (c.home_score, c.away_score) == (gm.home_score, gm.away_score)
            seed_pairs.append(TrainingPair(
                ng_home_id=c.home_team_id, ng_away_id=c.away_team_id,
                ng_home_score=c.home_score, ng_away_score=c.away_score,
                fm_home_id=gm.home_team_id if direct else gm.away_team_id,
                fm_away_id=gm.away_team_id if direct else gm.home_team_id,
                fm_home_score=gm.home_score if direct else gm.away_score,
                fm_away_score=gm.away_score if direct else gm.home_score,
            ))

    team_id_dict = build_team_id_dictionary(seed_pairs, min_votes=min_votes,
                                            min_margin_ratio=min_margin_ratio)
    fm_ids = list(team_id_dict.values())
    if len(fm_ids) != len(set(fm_ids)):
        dupes = {v for v in fm_ids if fm_ids.count(v) > 1}
        raise TeamIdDictionaryError(
            f"team_id_dict 不是干净双射,{len(dupes)} 个 fotmob_team_id 被多个 "
            f"nowgoal_team_id 同时映射(dupes={sorted(dupes)});拒绝继续解析,"
            "不猜哪个映射对"
        )
    results = resolve_batch(gap_matches, candidates_by_match_id, team_id_dict)

    archive_by_titan = {r.titan_id: r for r in archive_rows}
    target_by_id = {m["Match_ID"]: m for m in target_matches}
    out = []
    for res in results:
        row: dict = {
            "match_id": res.match_id, "status": res.status, "titan_id": res.titan_id,
            "direction": res.direction, "evidence_kind": res.evidence_kind, "detail": res.detail,
        }
        if res.status == STATUS_AUTO_OK and res.titan_id:
            arch = archive_by_titan.get(res.titan_id)
            target = target_by_id[res.match_id]
            if arch is None:
                row["status"] = "needs_review"
                row["detail"] = "titan_not_found_in_archive_rows"
            else:
                diff = abs((_parse_utc(arch.kickoff_utc) - _parse_utc(target["kickoff_at_utc"]))
                          .total_seconds())
                row["kickoff_diff_seconds"] = diff
                if diff > KICKOFF_TOLERANCE_SECONDS:
                    row["status"] = "needs_review"
                    row["detail"] = f"kickoff_diff_{diff:.0f}s_exceeds_{KICKOFF_TOLERANCE_SECONDS}s"
        out.append(row)
    return out


def fetch_two_point_rows(transport: NowGoalArchiveTransport, match_id: int, titan_id: str,
                         kickoff_utc: str, inverted: bool, company: str) -> list[dict]:
    mix_cid, euro_cid = ("8", "281") if company == "bet365" else ("1", "80")
    provider_name = "Bet365" if company == "bet365" else "Macauslot"

    mix = transport.mix_history(titan_id, cid=mix_cid)
    two_pt = two_point_from_mix_history(mix, kickoff_utc)
    euro = transport.euro_history(titan_id, cid=euro_cid)
    tp_1x2 = two_point_from_euro_history(euro, kickoff_utc)

    out = []
    for market, tp in (("ah", two_pt.get("ah")), ("ou", two_pt.get("ou")), ("1x2", tp_1x2)):
        if tp is None:
            continue
        for period in ("initial", "latest"):
            group = tp["opening"] if period == "initial" else tp["closing"]
            record = {"market": market, "company_id": mix_cid if market != "1x2" else euro_cid,
                      "company_name": provider_name, "initial": group, "latest": group}
            normalized = normalize_for_inversion(record, inverted)
            g = normalized["initial"]
            if market == "1x2":
                line, home_or_over, draw, away_or_under = None, g["home"], g["draw"], g["away"]
            elif market == "ah":
                line, home_or_over, draw, away_or_under = g["line"], g["home"], None, g["away"]
            else:
                line, home_or_over, draw, away_or_under = g["line"], g["over"], None, g["under"]
            out.append({
                "fotmob_match_id": match_id, "source": "nowgoal_archive_refetch",
                "provider": provider_name, "market": market, "period": period,
                "line": line, "home_or_over": home_or_over, "draw": draw,
                "away_or_under": away_or_under, "orientation_fixed": 0,
                "source_file": f"nowgoal_archive:titan_{titan_id}",
            })
    return out


def backup_db(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst_path = db_path.with_name(f"{db_path.name}.backup-pre-nowgoal-season-odds-{stamp}")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dst_path))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return dst_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", type=int, required=True)
    parser.add_argument("--season", type=str, required=True)
    parser.add_argument("--nowgoal-league-id", type=int, required=True,
                        help="不给默认值,不猜——见 nowgoal_archive.py 模块 docstring 的双重确证记录")
    parser.add_argument("--archive-season-key", type=str, default=None)
    parser.add_argument("--company", choices=("bet365", "macauslot"), default="bet365")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-votes", type=int, default=3,
                        help="build_team_id_dictionary 的最少票数门槛,默认按单赛季"
                             "单联赛(~120 场/16 队)校准,见 resolve_and_gate 文档")
    parser.add_argument("--min-margin-ratio", type=float, default=3.0)
    parser.add_argument("--sleep-min", type=float, default=0.3)
    parser.add_argument("--sleep-max", type=float, default=0.8)
    parser.add_argument("--proxy-env", type=str, default="THORDATA_PROXY")
    parser.add_argument("--core-db", type=Path, default=CORE_DB, help="仅测试用")
    parser.add_argument("--db-path", type=Path, default=ODDS_DB, help="仅测试用临时库覆盖")
    parser.add_argument("--skip-backup", action="store_true", help="仅测试用")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)

    import os
    proxy = os.environ.get(args.proxy_env)
    if not proxy:
        from dotenv import load_dotenv
        load_dotenv()
        proxy = os.environ.get(args.proxy_env)
    if not proxy:
        print(f"FAILED: {args.proxy_env} not set", file=sys.stderr)
        return 1

    season_key = args.archive_season_key or _archive_season_key(args.season)

    target_matches, skipped_no_kickoff = load_target_matches(
        args.core_db, args.league_id, args.season)
    if args.limit:
        target_matches = target_matches[: args.limit]

    transport = NowGoalArchiveTransport(proxy=proxy, sleep_min=args.sleep_min,
                                        sleep_max=args.sleep_max)
    archive_payload = transport.archive_season(args.nowgoal_league_id, season_key)
    archive_rows = parse_archive_season(archive_payload, ng_league_id=args.nowgoal_league_id)
    team_names = parse_team_info(archive_payload)

    resolved = resolve_and_gate(target_matches, archive_rows, args.league_id,
                                team_names=team_names,
                                min_votes=args.min_votes,
                                min_margin_ratio=args.min_margin_ratio)
    by_status: dict[str, int] = {}
    for r in resolved:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    summary = {
        "league_id": args.league_id, "season": args.season,
        "nowgoal_league_id": args.nowgoal_league_id, "archive_season_key": season_key,
        "target_matches": len(target_matches), "skipped_no_exact_kickoff": skipped_no_kickoff,
        "archive_rows": len(archive_rows), "resolution_by_status": by_status,
    }

    if args.dry_run:
        print(json.dumps({"mode": "DRY_RUN", **summary}, ensure_ascii=False, indent=1))
        return 0

    backup_path = None if args.skip_backup else backup_db(args.db_path)

    target_by_id = {m["Match_ID"]: m for m in target_matches}
    all_rows: list[dict] = []
    fetch_errors: list[dict] = []
    for r in resolved:
        if r["status"] != STATUS_AUTO_OK:
            continue
        match = target_by_id[r["match_id"]]
        try:
            all_rows.extend(fetch_two_point_rows(
                transport, r["match_id"], r["titan_id"], match["kickoff_at_utc"],
                inverted=(r["direction"] == "inverted"), company=args.company))
        except Exception as e:  # noqa: BLE001 - 单场失败不阻塞整批,如实记录
            fetch_errors.append({"match_id": r["match_id"], "titan_id": r["titan_id"],
                                 "error": type(e).__name__})

    conn = sqlite3.connect(str(args.db_path), timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    now = _utc_now()
    inserted = 0
    with conn:
        for row in all_rows:
            cur = conn.execute("""
                INSERT OR IGNORE INTO bronze_legacy_odds_summary
                  (fotmob_match_id, source, provider, market, period, line,
                   home_or_over, draw, away_or_under, orientation_fixed,
                   source_file, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["fotmob_match_id"], row["source"], row["provider"], row["market"],
                 row["period"], row["line"], row["home_or_over"], row["draw"],
                 row["away_or_under"], row["orientation_fixed"], row["source_file"], now))
            inserted += cur.rowcount
    check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    print(json.dumps({
        "mode": "LIVE", "backup_path": str(backup_path) if backup_path else None,
        "rows_inserted": inserted, "rows_prepared": len(all_rows),
        "fetch_errors": fetch_errors, "integrity_check": check, **summary,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
