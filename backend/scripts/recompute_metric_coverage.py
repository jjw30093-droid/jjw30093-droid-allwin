"""recompute_metric_coverage.py — 重算 backend/metrics/registry.py 里声明的
每个球队级字段在四大联赛(英超47/西甲87/德甲54/意甲55)的真实覆盖率,
产出可重跑、可核对的 JSON 产物,不允许任何覆盖率数字凭记忆/旧文档编造
(CLAUDE.md §2.2 真实输出纪律)。

口径:上赛季起(2025-08-01)、Period='All' 的已完赛球队场,与
docs/design-brief-*.md / plan 里引用的口径一致。

用法(只读连接,不改库):
    .venv/bin/python -m backend.scripts.recompute_metric_coverage
    .venv/bin/python -m backend.scripts.recompute_metric_coverage --out docs/audits/prematch-metric-coverage-v1.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db.connections import connect_ro  # noqa: E402

LEAGUES = {47: "英超", 87: "西甲", 54: "德甲", 55: "意甲"}
SINCE = "2025-08-01"

TEAM_EXTRA_JSON_FIELDS = [
    "BallPossesion", "passes", "accurate_passes", "opposition_half_passes",
    "touches_opp_box", "total_shots", "ShotsOnTarget", "expected_goals",
    "expected_goals_on_target", "big_chance", "shots_inside_box",
    "matchstats.headers.tackles", "interceptions", "clearances", "shot_blocks",
    "accurate_crosses", "duel_won", "ground_duels_won", "aerials_won",
]


def team_field_coverage(conn) -> dict:
    league_filter = ",".join(str(k) for k in LEAGUES)
    total = conn.execute(
        f"""SELECT COUNT(*) n FROM fact_team_match_stats t JOIN dim_match m ON m.Match_ID=t.Match_ID
             WHERE t.Period='All' AND m.League_ID IN ({league_filter}) AND m.Date>=?""",
        (SINCE,),
    ).fetchone()["n"]
    out = {"denominator_team_matches": total, "fields": {}}
    for field in TEAM_EXTRA_JSON_FIELDS:
        row = conn.execute(
            f"""SELECT SUM(CASE WHEN json_extract(t.extra_json,'$."{field}"') IS NOT NULL
                                 THEN 1 ELSE 0 END) h
                  FROM fact_team_match_stats t JOIN dim_match m ON m.Match_ID=t.Match_ID
                 WHERE t.Period='All' AND m.League_ID IN ({league_filter}) AND m.Date>=?""",
            (SINCE,),
        ).fetchone()
        pct = round(100.0 * row["h"] / total, 1) if total else None
        out["fields"][field] = {"non_null": row["h"], "pct": pct}
    return out


def shotmap_coverage(conn) -> dict:
    league_filter = ",".join(str(k) for k in LEAGUES)
    total_matches = conn.execute(
        f"""SELECT COUNT(*) n FROM dim_match WHERE League_ID IN ({league_filter})
             AND status IN ('Finish','Finished') AND Date>=?""",
        (SINCE,),
    ).fetchone()["n"]
    matches_with_shots = conn.execute(
        f"""SELECT COUNT(DISTINCT m.Match_ID) n FROM fact_shotmap f
              JOIN dim_match m ON m.Match_ID=f.Match_ID
             WHERE m.League_ID IN ({league_filter}) AND m.Date>=?""",
        (SINCE,),
    ).fetchone()["n"]
    row = conn.execute(
        f"""SELECT COUNT(*) shots,
                   SUM(CASE WHEN Situation IS NOT NULL THEN 1 ELSE 0 END) sit,
                   SUM(CASE WHEN xG IS NOT NULL THEN 1 ELSE 0 END) xg
              FROM fact_shotmap f JOIN dim_match m ON m.Match_ID=f.Match_ID
             WHERE m.League_ID IN ({league_filter}) AND m.Date>=?""",
        (SINCE,),
    ).fetchone()
    return {
        "matches_total": total_matches,
        "matches_with_shots": matches_with_shots,
        "matches_with_shots_pct": round(100.0 * matches_with_shots / total_matches, 1) if total_matches else None,
        "shots": row["shots"],
        "situation_non_null_pct": round(100.0 * row["sit"] / row["shots"], 1) if row["shots"] else None,
        "xg_non_null_pct": round(100.0 * row["xg"] / row["shots"], 1) if row["shots"] else None,
    }


def goalkeeper_coverage(conn) -> dict:
    """全部联赛(不限四大联赛)——门将样本本身就少,门将覆盖率口径不按
    "四大联赛上赛季"限定,按 backend/queries/player_form.py 文档口径:
    is_goalkeeper=1 且 minutes_played>0 的全量球员场。"""
    row = conn.execute(
        """SELECT COUNT(*) n,
                  SUM(CASE WHEN expected_goals_on_target_faced IS NOT NULL THEN 1 ELSE 0 END) xgot,
                  SUM(CASE WHEN goals_prevented IS NOT NULL THEN 1 ELSE 0 END) gp,
                  SUM(CASE WHEN keeper_sweeper IS NOT NULL THEN 1 ELSE 0 END) sweeper,
                  SUM(CASE WHEN keeper_high_claim IS NOT NULL THEN 1 ELSE 0 END) high_claim,
                  MIN(m.Date) d0, MAX(m.Date) d1, COUNT(DISTINCT m.League_ID) lg
             FROM fact_player_match_stats p JOIN dim_match m ON m.Match_ID=p.Match_ID
            WHERE p.is_goalkeeper=1 AND COALESCE(p.minutes_played,0)>0""",
    ).fetchone()
    n = row["n"]
    return {
        "denominator_gk_rows": n,
        "leagues": row["lg"], "date_from": row["d0"], "date_to": row["d1"],
        "expected_goals_on_target_faced_pct": round(100.0 * row["xgot"] / n, 1) if n else None,
        "goals_prevented_pct": round(100.0 * row["gp"] / n, 1) if n else None,
        "keeper_sweeper_pct": round(100.0 * row["sweeper"] / n, 1) if n else None,
        "keeper_high_claim_pct": round(100.0 * row["high_claim"] / n, 1) if n else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None, help="写入 JSON 产物的路径;不传则只打印到 stdout")
    args = parser.parse_args()

    conn = connect_ro("core")
    result = {
        "since": SINCE,
        "leagues": LEAGUES,
        "team_extra_json_fields": team_field_coverage(conn),
        "shotmap": shotmap_coverage(conn),
        "goalkeeper": goalkeeper_coverage(conn),
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n写入 {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
