"""Phase 2 回测:按比赛日计算开赛时点参数快照 + 赛果 + 截断测试(只读,在服务器上跑)。

口径:docs/simulator-model.md「Phase 2 回测与校准方法」P2.1。
- 对 25/26 五大联赛的每个比赛日 D,只用 Date < D 的比赛计算参数(与生产导出同一个 params_core.build)。
- 赛前信息单独存放:D 当天的实际首发(规则解码位置)、Crown 收盘 AH/OU 与反推 λ。
- 赛果(赛后数据)单独一份文件,只用于训练集校准与验证集评估。
- 截断测试:固定种子抽 20 个比赛日,物理删除 Date ≥ D 的全部比赛后重算,与快照逐字节比较。

用法:
  nice -n 19 python3 scripts/simulator/backtest_snapshots.py \
      --data-dir /opt/allwin/shared/data --out-dir /opt/allwin/shared/exports/simulator/backtest
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "research" / "ah_signals"))
sys.path.insert(0, str(HERE))

from common import load_xref, open_ro, parse_round, parse_utc  # noqa: E402
from features_market import load_timelines  # noqa: E402
from export_params import crown_entry  # noqa: E402
from params_core import SITUATION_CHANNEL, build, decode_formation, load_raw, truncate  # noqa: E402

LEAGUES = (47, 87, 55, 54, 53)
SEASONS = ("2024/2025", "2025/2026")
CURRENT = "2025/2026"
K_GRID = (3, 5, 8, 12)
TRUNCATION_DAYS = 20
SEED = 20260930


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def day_inputs(raw, day_matches):
    """当天比赛的球队范围、实际首发(规则解码)、首发球员的兜底信息。"""
    team_scope, lineups, extra = {}, {}, {}
    for m in day_matches:
        team_scope[m["Home_Team_ID"]] = m["League_ID"]
        team_scope[m["Away_Team_ID"]] = m["League_ID"]
        rows = raw.lineups.get(m["Match_ID"], ())
        sides = {}
        for side, tid in (("home", m["Home_Team_ID"]), ("away", m["Away_Team_ID"])):
            trows = [r for r in rows if r[0] == tid]
            starters = sorted((r for r in trows if r[7] == 1 and r[5] is not None), key=lambda r: r[5])
            formation = trows[0][1] if trows else None
            groups = decode_formation(formation, [r[5] for r in starters]) if len(starters) == 11 else None
            sides[side] = None if not groups else {
                "formation": formation,
                "starters": [{"player_id": r[2], "position_id": r[5], "group": groups[r[5]]} for r in starters],
            }
            for r in starters:
                extra[r[2]] = (tid, r[6])
        lineups[m["Match_ID"]] = sides
    return team_scope, lineups, extra


def outcomes_for(raw, m) -> dict:
    """赛后数据:进球时间线(受益方、是否乌龙、渠道)、红牌、点球射门、补时。"""
    mid = m["Match_ID"]
    ev = raw.events.get(mid, ())
    shots = raw.shots.get(mid, ())
    home, away = m["Home_Team_ID"], m["Away_Team_ID"]
    side_of = {home: 0, away: 1}
    # 非乌龙进球的渠道:按 (队, 分钟) 从 shotmap 进球里依次认领
    shot_goals = defaultdict(list)
    for (tid, _pid, situation, _st, xg, outcome, own, minute, madd, _period) in shots:
        if outcome == "Goal" and own != 1 and xg is not None and tid in side_of:
            shot_goals[(side_of[tid], minute)].append((madd, SITUATION_CHANNEL.get(situation, "unknown")))
    goals, reds = [], []
    prev = (0, 0)
    for (et, card, minute, ov, is_home, _madd, own, hs, as_) in ev:
        if et == "Goal":
            if hs is None or as_ is None:
                continue
            if (hs, as_) == prev:
                continue
            team = 0 if hs > prev[0] else 1
            prev = (hs, as_)
            channel = "owngoal"
            if not own:
                cands = shot_goals.get((team, minute)) or []
                channel = cands.pop(0)[1] if cands else "unknown"
            goals.append({"minute": minute, "added": ov or 0, "team": team, "own_goal": bool(own), "channel": channel})
        elif et == "Card" and card in ("Red", "YellowRed"):
            reds.append({"minute": minute, "added": ov or 0, "team": 0 if is_home else 1})
    announced = {45: None, 90: None}
    for (et, _c, minute, _ov, _h, madd, *_r) in ev:
        if et == "AddedTime" and minute in (45, 90) and madd is not None:
            announced[minute] = madd
    observed = {45: 0, 90: 0}
    for (_tid, _pid, _s, _st, _xg, _o, _own, minute, madd, period) in shots:
        if madd:
            observed[45 if period == "FirstHalf" else 90] = max(observed[45 if period == "FirstHalf" else 90], madd)
    for g in goals + reds:
        if g["added"] and g["minute"] in (45, 90):
            observed[g["minute"]] = max(observed[g["minute"]], g["added"])
    pens = [0, 0]
    for (tid, _pid, situation, *_r) in shots:
        if situation == "Penalty" and tid in side_of:
            pens[side_of[tid]] += 1
    return {
        "match_id": mid,
        "league_id": m["League_ID"],
        "round": parse_round(m["Match_Round"]),
        "date": m["Date"],
        "home_team_id": home,
        "away_team_id": away,
        "score": [m["home_score"], m["away_score"]],
        "goals": goals,
        "goals_consistent": [sum(1 for g in goals if g["team"] == 0), sum(1 for g in goals if g["team"] == 1)]
        == [m["home_score"], m["away_score"]],
        "reds": reds,
        "penalty_attempts": pens,
        "stoppage_announced": [announced[45], announced[90]],
        "stoppage_observed_max": [observed[45], observed[90]],
    }


def bet365_closes(tl, matches) -> dict:
    """Bet365 1x2 收盘去水概率:kickoff−10min 之前的最后一条(站长口径,不设距开球时长上限;
    距开球 >120min 的条数单独统计);去水 = 1/赔率 归一。"""
    out, missing, stale = {}, Counter(), 0
    for m in matches:
        if not m["kickoff_at_utc"]:
            missing["no_kickoff"] += 1
            continue
        ko = parse_utc(m["kickoff_at_utc"])
        cutoff = ko - timedelta(minutes=10)
        snaps = [x for x in (tl.get((m["Match_ID"], "Bet365", "1x2")) or []) if x["t"] <= cutoff]
        if not snaps:
            missing["no_snap_before_cutoff"] += 1
            continue
        snap = snaps[-1]
        gap = (ko - snap["t"]).total_seconds() / 60.0
        stale += gap > 120
        inv = [1.0 / v for v in snap["v"]]
        s = sum(inv)
        out[str(m["Match_ID"])] = {"odds": list(snap["v"]), "p": [x / s for x in inv], "gap_min": round(gap, 1),
                                   "observed_at": snap["t"].strftime("%Y-%m-%dT%H:%M:%SZ")}
    return {"matches": out, "missing": dict(missing), "gap_over_120min": stale}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/opt/allwin/shared/data")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--only-truncation", action="store_true")
    ap.add_argument("--only-bet365", action="store_true")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    if REPO in out_dir.resolve().parents or out_dir.resolve() == REPO:
        sys.exit("out-dir 必须在仓库外")
    (out_dir / "snapshots").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    core = open_ro(Path(args.data_dir) / "allwin.db")
    odds = open_ro(Path(args.data_dir) / "odds.db")
    raw = load_raw(core, LEAGUES, SEASONS)
    cur = [m for m in raw.matches if m["Season"] == CURRENT and m["status"] == "Finish"]
    dates = sorted({m["Date"] for m in cur})
    print(f"loaded {len(raw.matches)} matches ({len(cur)} in {CURRENT}, {len(dates)} dates) in {time.time() - t0:.1f}s",
          flush=True)

    if args.only_bet365:
        ok_xref, _ = load_xref(odds, {m["Match_ID"] for m in cur})
        p2m = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items() if not x["home_away_inverted"]}
        res = bet365_closes(load_timelines(odds, p2m), cur)
        (out_dir / "bet365_1x2.json").write_text(dumps(res), encoding="utf-8")
        print(json.dumps({"with_close": len(res["matches"]), "of": len(cur), "missing": res["missing"],
                          "gap_over_120min": res["gap_over_120min"]}), flush=True)
        return

    kwargs = dict(leagues=LEAGUES, current_season=CURRENT, k_grid=K_GRID, extras=True)

    # ---- 截断测试
    sample = sorted(random.Random(SEED).sample(dates, TRUNCATION_DAYS))
    trunc_rows = []
    for d in sample:
        day = [m for m in cur if m["Date"] == d]
        scope, _lineups, extra = day_inputs(raw, day)
        full = build(raw, cutoff=d, team_scope=scope, extra_players=extra, **kwargs)
        cut = build(truncate(raw, d), cutoff=None, team_scope=scope, extra_players=extra, **kwargs)
        a, b = dumps(full), dumps(cut)
        diff_keys = [] if a == b else sorted(k for k in full if dumps(full[k]) != dumps(cut.get(k)))
        trunc_rows.append({"date": d, "matches_that_day": len(day), "teams": len(full["teams"]),
                           "players": len(full["players"]), "bytes": len(a), "identical": a == b,
                           "differing_sections": diff_keys})
        print(f"truncation {d}: identical={a == b} bytes={len(a)} teams={len(full['teams'])} "
              f"players={len(full['players'])} diff={diff_keys}", flush=True)
    (out_dir / "truncation_test.json").write_text(dumps({"seed": SEED, "days": trunc_rows}), encoding="utf-8")
    if args.only_truncation:
        return

    # ---- Crown 收盘
    ok_xref, _ = load_xref(odds, {m["Match_ID"] for m in cur})
    pmid_to_mid = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items() if not x["home_away_inverted"]}
    tl = load_timelines(odds, pmid_to_mid)
    now = datetime.now(timezone.utc)

    # ---- 逐日快照
    crown_with_lambda = 0
    for i, d in enumerate(dates):
        day = [m for m in cur if m["Date"] == d]
        scope, lineups, extra = day_inputs(raw, day)
        params = build(raw, cutoff=d, team_scope=scope, extra_players=extra, **kwargs)
        prematch = []
        for m in day:
            prematch.append({
                "match_id": m["Match_ID"],
                "league_id": m["League_ID"],
                "round": parse_round(m["Match_Round"]),
                "date": d,
                "kickoff_at_utc": m["kickoff_at_utc"],
                "home_team_id": m["Home_Team_ID"],
                "away_team_id": m["Away_Team_ID"],
                "lineups": lineups[m["Match_ID"]],
                "crown": crown_entry(tl, m, now, is_up=False) if m["kickoff_at_utc"] else None,
            })
            crown_with_lambda += bool(prematch[-1]["crown"] and prematch[-1]["crown"].get("market_lambda"))
        snap = {"date": d, "params": params, "prematch": prematch}
        (out_dir / "snapshots" / f"{d}.json").write_text(dumps(snap), encoding="utf-8")
        if i % 20 == 0:
            print(f"snapshot {i + 1}/{len(dates)} {d} ({time.time() - t0:.0f}s)", flush=True)

    # ---- 赛果
    outcomes = [outcomes_for(raw, m) for m in cur]
    (out_dir / "outcomes.json").write_text(dumps(outcomes), encoding="utf-8")
    bad = [o["match_id"] for o in outcomes if not o["goals_consistent"]]
    print(json.dumps({
        "snapshots": len(dates),
        "matches": len(cur),
        "by_league": dict(Counter(m["League_ID"] for m in cur)),
        "crown_close_with_lambda": crown_with_lambda,
        "goal_timeline_inconsistent": len(bad),
        "truncation_identical": sum(r["identical"] for r in trunc_rows),
        "seconds": round(time.time() - t0),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
