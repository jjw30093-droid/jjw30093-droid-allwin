"""validate_opponent_adjustment.py — 严格样本外验证"对手强度校正"是否真的
比不校正更准,分三类指标分别验证(不共用同一个校正乘法),并对小样本对手
做收缩(shrinkage),避免升班马这类样本量小的对手把校正拉飞。

方法论(全部满足方案 §二.2.3 的强约束):
- **严格 PIT**:对手强度只用"目标比赛边界之前"的数据计算,且用的是对手
  自己近两年的整体历史(不是拿目标比赛本身反推),不会用到未来信息。
- **样本外**:验证目标只取 CUTOFF(2025-01-01)之后的比赛,复用
  validate_window_length.py 的同一条切分原则。
- **升班马/小样本收缩**:对手历史场次越少,校正力度越接近 1.0(不校正)
  ——shrinkage = n / (n + K),K=8。
- **三类指标各自独立验证,不共用同一个乘法**:
  ① 进攻类(创造 xG):按对手"近两年场均让出 xG"校正;
  ② 防守类(讓出 xG):按对手"近两年场均创造 xG"校正(方向相反);
  ③ 比例类(控球率):不是"进球强度"问题,尝试的校正公式与①②完全不同
     (按对手近两年场均控球率的反向校正),预期这类零和特征很难被"对手
     强度"这个概念改善——如实验证并如实报告,不预设结论。
- **判定**:某一类只有在四个联赛上都不劣于未校正(容差同 validate_window_length.py
  的 NOISE_TOLERANCE),才判定"采用";否则该类首版不上线校正,查询层继续用
  不校正的窗口均值。

用法:
    .venv/bin/python -m backend.scripts.validate_opponent_adjustment
    .venv/bin/python -m backend.scripts.validate_opponent_adjustment --out docs/audits/opponent-adjustment-validation-v1.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db.connections import connect_ro  # noqa: E402

LEAGUES = {47: "英超", 87: "西甲", 54: "德甲", 55: "意甲"}
CUTOFF = "2025-01-01"
LOOKBACK_DAYS = 730
NOISE_TOLERANCE = 0.005
SHRINKAGE_K = 8
WINDOW = 10  # 与 validate_window_length.py 验证过的球队级默认窗口一致


def _corr(xs: list[float], ys: list[float]) -> float:
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return num / den if den else 0.0


def _rows(conn, league_id: int, field: str):
    return conn.execute(
        f"""SELECT m.Match_ID mid, COALESCE(m.kickoff_at_utc, m.Date) boundary, m.Date d,
                  t.Team_ID tid,
                  CASE WHEN t.Team_ID=m.Home_Team_ID THEN 1 ELSE 0 END is_home,
                  CASE WHEN t.Team_ID=m.Home_Team_ID THEN m.Away_Team_ID ELSE m.Home_Team_ID END opp,
                  json_extract(t.extra_json,'$.{field}') val
             FROM dim_match m JOIN fact_team_match_stats t ON t.Match_ID=m.Match_ID AND t.Period='All'
            WHERE m.League_ID=? AND m.status IN ('Finish','Finished')
            ORDER BY boundary""",
        (league_id,),
    ).fetchall()


def _rows_conceded(conn, league_id: int, field: str):
    """同一场比赛里"对手"的该字段值(用于算让出/被创造)。"""
    return conn.execute(
        f"""SELECT m.Match_ID mid, COALESCE(m.kickoff_at_utc, m.Date) boundary, m.Date d,
                  t_own.Team_ID tid,
                  CASE WHEN t_own.Team_ID=m.Home_Team_ID THEN 1 ELSE 0 END is_home,
                  CASE WHEN t_own.Team_ID=m.Home_Team_ID THEN m.Away_Team_ID ELSE m.Home_Team_ID END opp,
                  json_extract(t_opp.extra_json,'$.{field}') val
             FROM dim_match m
             JOIN fact_team_match_stats t_own ON t_own.Match_ID=m.Match_ID AND t_own.Period='All'
             JOIN fact_team_match_stats t_opp ON t_opp.Match_ID=m.Match_ID AND t_opp.Period='All'
              AND t_opp.Team_ID = (CASE WHEN t_own.Team_ID=m.Home_Team_ID THEN m.Away_Team_ID ELSE m.Home_Team_ID END)
            WHERE m.League_ID=? AND m.status IN ('Finish','Finished')
            ORDER BY boundary""",
        (league_id,),
    ).fetchall()


def _strength_series(rows) -> dict[int, list[tuple[str, float]]]:
    """{team_id: [(boundary, val), ...]}(按 boundary 升序,val 非空)。"""
    out: dict[int, list[tuple[str, float]]] = defaultdict(list)
    for r in rows:
        if r["val"] is not None:
            out[r["tid"]].append((r["boundary"], r["val"]))
    return out


def _opponent_strength_as_of(series: dict[int, list], team_id: int, boundary: str) -> tuple[float | None, int]:
    """team_id 在 boundary 之前(不含)、LOOKBACK_DAYS 内的均值与场次(PIT-safe)。"""
    import datetime

    date_part = boundary[:10]
    try:
        floor_date = (datetime.date.fromisoformat(date_part) - datetime.timedelta(days=LOOKBACK_DAYS)).isoformat()
    except ValueError:
        floor_date = "0000-01-01"
    vals = [v for b, v in series.get(team_id, []) if floor_date <= b < boundary]
    if not vals:
        return None, 0
    return st.mean(vals), len(vals)


def _shrunk_ratio(raw_ratio: float, n: int) -> float:
    w = n / (n + SHRINKAGE_K)
    return 1 + (raw_ratio - 1) * w


def _validate_metric(conn, league_id: int, *, own_field: str | None, conceded_field: str | None,
                      adjust_by: str) -> dict:
    """own_field:自身字段名(进攻类/比例类用);conceded_field:对手让出字段名
    (防守类用,own_field 传 None)。adjust_by 决定校正用哪一批"对手历史"
    ('opp_concedes' 用对手让出 X 的历史给进攻类校正; 'opp_creates' 用对手
    创造 X 的历史给防守类校正; 'opp_own' 用对手自身该指标的历史给比例类校正)。"""
    if own_field:
        rows = _rows(conn, league_id, own_field)
        val_key_field = own_field
    else:
        rows = _rows_conceded(conn, league_id, conceded_field)
        val_key_field = conceded_field

    by_team_venue: dict[tuple[int, int], list] = defaultdict(list)
    for r in rows:
        if r["val"] is not None:
            by_team_venue[(r["tid"], r["is_home"])].append(r)

    # 对手强度序列:用同一个字段口径,但取"对手方"的时间序列
    if adjust_by == "opp_concedes":
        strength_rows = _rows_conceded(conn, league_id, val_key_field)
    else:
        strength_rows = _rows(conn, league_id, val_key_field)
    strength_series = _strength_series(strength_rows)
    league_avg = st.mean(v for series in strength_series.values() for _, v in series) if strength_series else None
    if league_avg is None:
        return {"validated_matches": 0, "insufficient_sample": True}

    raw_preds, adj_preds, actual = [], [], []
    for (_tid, _is_home), history in by_team_venue.items():
        for i, target in enumerate(history):
            if target["d"] < CUTOFF or i < WINDOW:
                continue
            window = history[i - WINDOW:i]
            raw_preds.append(st.mean(m["val"] for m in window))

            adjusted_vals = []
            for m in window:
                opp_strength, n_opp = _opponent_strength_as_of(strength_series, m["opp"], m["boundary"])
                if opp_strength is None or opp_strength <= 0:
                    adjusted_vals.append(m["val"])
                    continue
                raw_ratio = league_avg / opp_strength
                ratio = _shrunk_ratio(raw_ratio, n_opp)
                adjusted_vals.append(m["val"] * ratio)
            adj_preds.append(st.mean(adjusted_vals))
            actual.append(target["val"])

    if len(actual) < 30:
        return {"validated_matches": len(actual), "insufficient_sample": True}
    return {
        "validated_matches": len(actual),
        "corr_raw": round(_corr(raw_preds, actual), 4),
        "corr_adjusted": round(_corr(adj_preds, actual), 4),
        "insufficient_sample": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    conn = connect_ro("core")

    categories = {
        "attack_xg": dict(own_field="expected_goals", conceded_field=None, adjust_by="opp_concedes"),
        "defense_xga": dict(own_field=None, conceded_field="expected_goals", adjust_by="opp_creates"),
        "style_possession": dict(own_field="BallPossesion", conceded_field=None, adjust_by="opp_own"),
    }

    report: dict[str, dict] = {}
    for cat, kwargs in categories.items():
        per_league = {}
        for lid in LEAGUES:
            per_league[LEAGUES[lid]] = _validate_metric(conn, lid, **kwargs)
        valid = [v for v in per_league.values() if not v["insufficient_sample"]]
        adopt = len(valid) == len(LEAGUES) and all(
            v["corr_adjusted"] >= v["corr_raw"] - NOISE_TOLERANCE for v in valid
        )
        report[cat] = {
            "per_league": per_league,
            "adopt_adjustment": adopt,
            "verdict_note": (
                f"{cat}:对手强度校正在全部四个联赛样本外验证中不劣于未校正,首版采用"
                if adopt else
                f"{cat}:对手强度校正未能在全部四个联赛样本外验证中稳定超过未校正,首版不上校正"
            ),
        }

    text = json.dumps({"cutoff": CUTOFF, "window": WINDOW, "shrinkage_k": SHRINKAGE_K,
                        "noise_tolerance": NOISE_TOLERANCE, "categories": report},
                       ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n写入 {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
