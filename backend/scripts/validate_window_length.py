"""validate_window_length.py — 样本外验证"同主客场近 N 场"该用 5 场还是
10 场,不是在同一批数据上直接比相关系数(那属于"同集选择",上一轮的方法论
缺陷)。

做法:
1. 按时间切一刀(CUTOFF),CUTOFF 之前的比赛只能被当"历史窗口的原料",
   CUTOFF 之后(含)的比赛才作为**验证目标**——即只在样本外(out-of-sample)
   的预测上比较 window=5 与 window=10 的表现,不用调参时看过的同一批目标。
2. 对每场验证目标比赛:用该队自己"同主客场、该场之前"的近 N 场均值,
   预测该场真实创造 xG;分别算 N=5 与 N=10 两组预测值与真实值的相关系数。
3. 四个联赛(英超47/西甲87/德甲54/意甲55)分别跑,不看单一联赛就下结论。
4. 判定:只有 N=10 在**全部四个联赛**上相关系数都不低于 N=5(允许极小的
   噪声容差),才采用 10;否则维持现状 window=5。

用法:
    .venv/bin/python -m backend.scripts.validate_window_length
    .venv/bin/python -m backend.scripts.validate_window_length --out docs/audits/window-length-validation-v1.json
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
# 验证目标只取这个日期(含)之后的比赛——早于此的比赛只能当窗口原料,
# 不能同时既是"调参时看过的目标"又是"验证集目标"。选 2025-01-01 是因为
# 它在全部四个联赛的完赛历史中都留出了至少一个完整赛季的"纯验证期"
# (2025-01 至今),同时给窗口原料留了 2020 年至今的历史可用。
CUTOFF = "2025-01-01"
NOISE_TOLERANCE = 0.005  # 允许的相关系数噪声容差,防止因四舍五入判"刚好打平"为不过关


def _corr(xs: list[float], ys: list[float]) -> float:
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return num / den if den else 0.0


def _rows(conn, league_id: int):
    return conn.execute(
        """SELECT m.Match_ID mid, COALESCE(m.kickoff_at_utc, m.Date) boundary, m.Date d,
                  t.Team_ID tid,
                  CASE WHEN t.Team_ID=m.Home_Team_ID THEN 1 ELSE 0 END is_home,
                  json_extract(t.extra_json,'$.expected_goals') xg
             FROM dim_match m JOIN fact_team_match_stats t ON t.Match_ID=m.Match_ID AND t.Period='All'
            WHERE m.League_ID=? AND m.status IN ('Finish','Finished')
            ORDER BY boundary""",
        (league_id,),
    ).fetchall()


def validate_league(conn, league_id: int) -> dict:
    rows = [r for r in _rows(conn, league_id) if r["xg"] is not None]
    by_team_venue: dict[tuple[int, int], list] = defaultdict(list)
    for r in rows:
        by_team_venue[(r["tid"], r["is_home"])].append(r)

    preds = {5: [], 10: []}
    actual = []
    validated_matches = 0
    for (_tid, _is_home), history in by_team_venue.items():
        for i, target in enumerate(history):
            if target["d"] < CUTOFF:
                continue  # 只在 CUTOFF 之后的比赛上做验证目标
            if i < 10:
                continue  # 两组窗口都要求凑满,不足 10 场的直接跳过(两组用同一批目标才可比)
            window10 = history[i - 10:i]
            window5 = history[i - 5:i]
            preds[5].append(st.mean(m["xg"] for m in window5))
            preds[10].append(st.mean(m["xg"] for m in window10))
            actual.append(target["xg"])
            validated_matches += 1

    if validated_matches < 30:
        return {"league_id": league_id, "league_name": LEAGUES[league_id],
                "validated_matches": validated_matches, "insufficient_sample": True}

    return {
        "league_id": league_id, "league_name": LEAGUES[league_id],
        "validated_matches": validated_matches,
        "corr_window_5": round(_corr(preds[5], actual), 4),
        "corr_window_10": round(_corr(preds[10], actual), 4),
        "insufficient_sample": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    conn = connect_ro("core")
    results = [validate_league(conn, lid) for lid in LEAGUES]

    valid = [r for r in results if not r["insufficient_sample"]]
    ten_wins_everywhere = all(
        r["corr_window_10"] >= r["corr_window_5"] - NOISE_TOLERANCE for r in valid
    ) and len(valid) == len(LEAGUES)

    verdict = {
        "cutoff": CUTOFF,
        "noise_tolerance": NOISE_TOLERANCE,
        "per_league": results,
        "adopt_window_10": ten_wins_everywhere,
        "verdict_note": (
            "N=10 在全部四个联赛的样本外验证中都不弱于 N=5,采用 10 场作为球队级窗口默认值"
            if ten_wins_everywhere else
            "N=10 未能在全部四个联赛的样本外验证中稳定超过 N=5,维持现状 window=5,不采用 10"
        ),
    }
    text = json.dumps(verdict, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n写入 {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
