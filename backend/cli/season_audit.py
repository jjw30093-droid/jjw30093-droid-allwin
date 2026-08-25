"""season_audit.py — dim_match 赛季标注差异报告(2026-08-25,CLAUDE.md §6.3)。

**只报不改**(站长决定):本工具没有 --commit,不提供任何写路径。存量错标的
修复是独立决策(牵涉 Silver 分区重建、SEO URL、模型切分等,见
docs/current-state.md §62 的前置清单),必须逐条确认后另行执行。

报告口径:
- 逐行比对 dim_match.Season 与制度表推导(backend/season_regime.py——与
  0011 触发器、质量门 G12 同一份逻辑);
- 按三类分组(与 2026-08-25 生产审计口径一致):
    current_season_opener  日期 ≥ 2026-07-01 的新赛季场次被标成旧赛季
                           (站长发现的"揭幕轮只查到 5 场"就是这类)
    calendar_league_mislabel  自然年联赛被标成跨年串
    historical_backfill    更早历史场次被盖上回填当时的"当前赛季"
- 未登记联赛的行单独列出(fail closed,不猜);
- 交叉证据:int_match_features.season 与 dim_match.Season 不一致的行
  (已证实是 dim_match 侧错——特征表反而保存了正确值)。

--provider-check(可选,需要网络与代理,通常在生产机上跑):对每个联赛发一次
不带 season 的 league_matches() 请求,用 provider 回报的当前赛季 +
fixtures.allMatches 的 match_id 集合,验证"当前赛季错标行"这一类——这正是
FotMob 自己的赛季语义(赛季 = 哪个联赛-赛季的赛程表包含这个 id)。历史赛季
不在本模式覆盖范围内,如实标 UNVERIFIED,不用日期规则冒充 provider 证据。

用法:
  python -m backend.cli.season_audit             # 文本报告
  python -m backend.cli.season_audit --json
  python -m backend.cli.season_audit --league-id 47
  python -m backend.cli.season_audit --provider-check   # 生产机上跑
"""

from __future__ import annotations

import argparse
import json
import sys

from backend.db.connections import connect_ro
from backend.season_regime import derived_season_sql


def _classify(row: dict) -> str:
    if row["date"] >= "2026-07-01":
        return "current_season_opener"
    if "/" in (row["stored"] or "") and "/" not in (row["derived"] or ""):
        return "calendar_league_mislabel"
    return "historical_backfill"


def collect(conn, league_id: int | None = None) -> dict:
    where_league = " AND m.League_ID = :lid" if league_id is not None else ""
    params = {"lid": league_id} if league_id is not None else {}
    drift_rows = [
        {
            "match_id": r["Match_ID"], "league_id": r["League_ID"],
            "date": r["Date"], "round": r["Match_Round"],
            "stored": r["Season"], "derived": r["derived"],
            "home": r["Home_Team_Name"], "away": r["Away_Team_Name"],
            "status": r["status"],
        }
        for r in conn.execute(
            "SELECT m.Match_ID, m.League_ID, m.Date, m.Match_Round, m.Season,"
            " m.Home_Team_Name, m.Away_Team_Name, m.status,"
            f" {derived_season_sql('m.Date', 'm.League_ID')} AS derived"
            " FROM dim_match m"
            " WHERE m.Season IS NOT NULL AND m.Date IS NOT NULL" + where_league,
            params,
        )
        if r["derived"] is not None and r["Season"] != r["derived"]
    ]
    for row in drift_rows:
        row["category"] = _classify(row)

    unregistered = [
        dict(r)
        for r in conn.execute(
            "SELECT m.League_ID, COUNT(*) AS rows FROM dim_match m"
            " WHERE m.League_ID IS NOT NULL AND NOT EXISTS ("
            "   SELECT 1 FROM dim_league_season_regime r"
            "   WHERE r.league_id = m.League_ID)" + where_league +
            " GROUP BY m.League_ID",
            params,
        )
    ]

    try:
        features_drift = conn.execute(
            "SELECT COUNT(*) FROM int_match_features f"
            " JOIN dim_match m ON m.Match_ID = f.match_id"
            " WHERE f.season IS NOT NULL AND m.Season IS NOT NULL"
            "   AND f.season <> m.Season",
        ).fetchone()[0]
    except Exception:
        features_drift = None

    by_category: dict[str, int] = {}
    by_league: dict[int, int] = {}
    for row in drift_rows:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
        by_league[row["league_id"]] = by_league.get(row["league_id"], 0) + 1

    return {
        "total_drift": len(drift_rows),
        "by_category": by_category,
        "by_league": by_league,
        "unregistered_leagues": unregistered,
        "features_cross_drift": features_drift,
        "historical_provider_evidence": "UNVERIFIED",  # 见模块 docstring
        "rows": sorted(
            drift_rows, key=lambda r: (r["league_id"], r["date"], r["match_id"])
        ),
    }


def provider_check(report: dict) -> dict:
    """对"当前赛季错标"类做 provider 实证(每联赛一次请求,fail-soft 记录)。"""
    from backend.fotmob_client import FotMobClient
    from backend.ingest.season_identity import (
        SeasonIdentityError,
        available_provider_seasons,
        discover_season,
    )

    client = FotMobClient()
    checks = {}
    leagues = sorted({
        r["league_id"] for r in report["rows"]
        if r["category"] == "current_season_opener"
    })
    for lid in leagues:
        try:
            data = client.league_matches(lid)
            season = discover_season(data, lid)
            ids = {
                int(m["id"])
                for m in (data.get("fixtures", {}) or {}).get("allMatches") or []
                if m.get("id") is not None
            }
            confirmed = [
                r["match_id"] for r in report["rows"]
                if r["league_id"] == lid
                and r["category"] == "current_season_opener"
                and r["match_id"] in ids and r["derived"] == season
            ]
            checks[lid] = {
                "provider_current_season": season,
                "advertised_seasons": available_provider_seasons(data),
                "confirmed_mislabeled": confirmed,
                "confirmed_count": len(confirmed),
            }
        except (SeasonIdentityError, Exception) as e:  # noqa: BLE001 — 审计工具:单联赛失败如实记录,不中止
            checks[lid] = {"error": f"{type(e).__name__}: {e}"}
    report["provider_check"] = checks
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--league-id", type=int, default=None)
    ap.add_argument("--limit", type=int, default=50,
                    help="文本模式逐行明细的最大行数(JSON 模式恒输出全部)")
    ap.add_argument("--provider-check", action="store_true",
                    help="对当前赛季错标类发起 provider 实证(需网络/代理)")
    args = ap.parse_args(argv)

    conn = connect_ro("core")
    try:
        report = collect(conn, args.league_id)
    finally:
        conn.close()
    if args.provider_check:
        report = provider_check(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    print(f"season_audit: 存量赛季标注差异 {report['total_drift']} 行(只报不改)")
    print(f"  按类别: {report['by_category']}")
    print(f"  按联赛: {report['by_league']}")
    print(f"  未登记联赛: {report['unregistered_leagues'] or '无'}")
    print(f"  features 交叉不一致: {report['features_cross_drift']}"
          f"(已证实为 dim_match 侧错)")
    print(f"  历史赛季 provider 证据: {report['historical_provider_evidence']}")
    for row in report["rows"][: args.limit]:
        print(f"  [{row['category']}] match={row['match_id']} league={row['league_id']}"
              f" {row['date']} 轮次={row['round']}"
              f" 库内={row['stored']} 推导={row['derived']}"
              f" {row['home']} vs {row['away']} ({row['status']})")
    remaining = report["total_drift"] - min(args.limit, report["total_drift"])
    if remaining > 0:
        print(f"  ... 其余 {remaining} 行见 --json 输出")
    if "provider_check" in report:
        print("  provider 实证(当前赛季错标类):")
        for lid, c in report["provider_check"].items():
            print(f"    league {lid}: {json.dumps(c, ensure_ascii=False)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
