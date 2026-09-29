"""模拟器参数导出(只读)。口径:docs/simulator-model.md(最新变更记录见文末)。

只读打开 allwin.db / odds.db(sqlite URI mode=ro + query_only),输出到仓库外目录。
范围:英超 47、西甲 87,赛季 2025/2026 + 2026/2027。参数计算在 params_core.build()
(与 Phase 2 回测快照共用同一套函数)。

用法(服务器):
  nice -n 19 python3 scripts/simulator/export_params.py \
      --data-dir /opt/allwin/shared/data --out-dir /opt/allwin/shared/exports/simulator

位置分组为规则解码(阵型字符串 + 格子行列),输出里标 position_unverified=true。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research" / "ah_signals"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import load_xref, open_ro  # noqa: E402
from features_market import fit_poisson, implied_two, load_timelines, pick_close  # noqa: E402
from params_core import CHANNELS, SITUATION_CHANNEL, build, load_raw, rnd  # noqa: E402

# 生效版本:导出参数实际对应的模型版本。显式维护,不从规格文档标题推导;
# v0.3 校准值正式启用(生产导出写入 calibration)之前保持 v0.2。
EFFECTIVE_VERSION = "v0.2"
LEAGUES = (47, 87)
SEASONS = ("2025/2026", "2026/2027")
CURRENT_SEASON = "2026/2027"
FIXTURE_LOOKBACK_DAYS = 14


def parse_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def crown_entry(tl, m: dict, now: datetime, is_up: bool) -> dict | None:
    """一场比赛的 Crown AH/OU:未开赛取当前最新一条,已完赛取研究口径收盘(kickoff−10min 之前最后一条)。"""
    ko = parse_utc(m["kickoff_at_utc"])
    snaps = {}
    for market in ("ah", "ou"):
        t = tl.get((m["Match_ID"], "Crown", market)) or []
        if is_up:
            past = [s for s in t if s["t"] <= now]
            snaps[market] = past[-1] if past else None
        else:
            snaps[market], _reason = pick_close(t, ko)
    if not snaps["ah"] and not snaps["ou"]:
        return None
    entry = {}
    for market, (a, b) in (("ah", ("home", "away")), ("ou", ("over", "under"))):
        s = snaps[market]
        entry[market] = None if s is None else {
            "line": s["line"], a: s["v"][0], b: s["v"][1], "observed_at": fmt_utc(s["t"])}
    if snaps["ah"] and snaps["ou"]:
        p_ah = implied_two(*snaps["ah"]["v"])
        p_over = implied_two(*snaps["ou"]["v"])
        lh, la, ra, ro, _p_win = fit_poisson(snaps["ah"]["line"], p_ah, snaps["ou"]["line"], p_over)
        entry["market_lambda"] = {"home": lh, "away": la, "resid_ah": rnd(ra), "resid_ou": rnd(ro),
                                  "p_ah_home": rnd(p_ah), "p_over": rnd(p_over)}
    else:
        entry["market_lambda"] = None
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/opt/allwin/shared/data")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--out-name", default=None, help="输出文件名(默认 simulator_params_YYYYMMDD.json)")
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    if REPO in out_dir.resolve().parents or out_dir.resolve() == REPO:
        sys.exit("out-dir 必须在仓库外")
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    core = open_ro(data_dir / "allwin.db")
    odds = open_ro(data_dir / "odds.db")

    raw = load_raw(core, LEAGUES, SEASONS)
    p = build(raw, leagues=LEAGUES, current_season=CURRENT_SEASON)
    leagues_out, teams_out, players_out = p["leagues"], p["teams"], p["players"]
    position_map = p["position_map"]
    diag = p["diag"]
    finished = [m for m in raw.matches if m["status"] == "Finish"]

    # ---- 一致性检查:最近一场首发放回它的实际阵型,主要位置 ≠ 所放位置(f_pos < 1)的比例
    consistency = []
    for tid, t in teams_out.items():
        ll = t["last_lineup"]
        if not ll:
            continue
        rows_c = []
        for st in ll["starters"]:
            slot = position_map.get(ll["formation"], {}).get(str(st["position_id"]))
            main_pos = players_out[st["player_id"]]["main_position"] if st["player_id"] in players_out else None
            rows_c.append((st["player_id"], main_pos, slot))
        mism = [r for r in rows_c if r[1] != r[2]]
        consistency.append({
            "team_id": int(tid),
            "name_zh": t["name_zh"],
            "formation": ll["formation"],
            "mismatch": len(mism),
            "n": len(rows_c),
            "detail": [f"{players_out.get(pid, {}).get('name_zh') or players_out.get(pid, {}).get('name_en')}:{a}→{b}"
                       for pid, a, b in mism],
        })
    consistency.sort(key=lambda r: (-r["mismatch"], r["team_id"]))
    total_mism = sum(r["mismatch"] for r in consistency)
    total_n = sum(r["n"] for r in consistency)

    # ---- fixtures:未开赛最新 Crown 盘口 + 最近 14 天已完赛赛前收盘
    upcoming = [m for m in raw.matches if m["status"] != "Finish" and m["kickoff_precision"] == "exact"
                and parse_utc(m["kickoff_at_utc"]) and parse_utc(m["kickoff_at_utc"]) > now]
    recent = [m for m in finished if m["kickoff_precision"] == "exact" and m["kickoff_at_utc"]
              and now - timedelta(days=FIXTURE_LOOKBACK_DAYS) <= parse_utc(m["kickoff_at_utc"]) <= now]
    cand = {m["Match_ID"]: m for m in upcoming + recent}
    ok_xref, _ = load_xref(odds, set(cand))
    skipped_inverted = [mid for mid, x in ok_xref.items() if x["home_away_inverted"]]
    pmid_to_mid = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items() if not x["home_away_inverted"]}
    tl = load_timelines(odds, pmid_to_mid)
    fixtures_out = {}
    fixture_skip = Counter()
    for mid, m in cand.items():
        is_up = m["status"] != "Finish"
        crown = crown_entry(tl, m, now, is_up)
        if crown is None:
            fixture_skip["no_crown_snap"] += 1
            continue
        fixtures_out[str(mid)] = {
            "match_id": mid,
            "league_id": m["League_ID"],
            "kickoff_at_utc": m["kickoff_at_utc"],
            "home_team_id": m["Home_Team_ID"],
            "away_team_id": m["Away_Team_ID"],
            "status": "未开赛" if is_up else "已完赛（赛前盘口）",
            "final_score": None if is_up else [m["home_score"], m["away_score"]],
            **crown,
        }

    # ---- 写出
    out = {
        "meta": {
            "generated_at": fmt_utc(now),
            "effective_version": EFFECTIVE_VERSION,
            "model_version": EFFECTIVE_VERSION,
            "uncalibrated": True,
            "spec": "docs/simulator-model.md",
            "leagues": list(LEAGUES),
            "seasons": list(SEASONS),
            "data_window": {"first_match_date": min((m["Date"] for m in finished), default=None),
                            "last_match_date": max((m["Date"] for m in finished), default=None),
                            "finished_matches": len(finished)},
            "channels": list(CHANNELS) + ["owngoal"],
            "situation_to_channel": SITUATION_CHANNEL,
            "position_decoding": "rule-based (formation string + grid row/col); unverified",
            "notes": [
                "球队/球员参数用截至导出时的全部已完赛数据,已完赛 fixtures 的参数不是赛前时点值(有前视)。",
                "xA 为 NULL 的出场行从 xA/90 的分子和分母中排除(见 players.xa_minutes),不按 0 处理。",
                "乌龙球不计入任何射门渠道;频率来自 fact_match_events 的 ownGoal 标记。",
            ],
            "diagnostics": {
                "excluded_own_goal_shot_rows": diag["excluded_own_goal_shot_rows"],
                "unknown_situations": diag["unknown_situations"],
                "position_source": diag["position_source"],
                "fixtures_skipped": dict(fixture_skip),
                "fixtures_skipped_inverted_xref": skipped_inverted,
                "players_without_minutes": sum(1 for q in players_out.values() if q["minutes"] == 0),
                "players_without_xa_minutes": sum(1 for q in players_out.values()
                                                  if q["minutes"] > 0 and q["xa_minutes"] == 0),
                "players_without_any_name": sum(1 for q in players_out.values()
                                                if not q["name_zh"] and not q["name_en"]),
                "undecodable_formations": diag["undecodable_formations"],
                "formation_variant_slot_sets": diag["formation_variant_slot_sets"],
                "lineup_consistency": {
                    "total_mismatch": total_mism,
                    "total_starters": total_n,
                    "share": rnd(total_mism / total_n) if total_n else None,
                    "teams": consistency,
                },
            },
        },
        "position_map": position_map,
        "leagues": leagues_out,
        "formations": p["formations"],
        "teams": teams_out,
        "players": players_out,
        "fixtures": fixtures_out,
    }
    path = out_dir / (args.out_name or f"simulator_params_{now.strftime('%Y%m%d')}.json")
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({
        "output": str(path),
        "bytes": path.stat().st_size,
        "teams": len(teams_out),
        "teams_by_league": dict(Counter(t["league_id"] for t in teams_out.values())),
        "players": len(players_out),
        "formations": sorted(p["formations"]),
        "fixtures": dict(Counter(f["status"] for f in fixtures_out.values())),
        "fixtures_with_market_lambda": sum(1 for f in fixtures_out.values() if f["market_lambda"]),
        "diagnostics": {k: v for k, v in out["meta"]["diagnostics"].items() if k != "lineup_consistency"},
        "lineup_consistency_share": out["meta"]["diagnostics"]["lineup_consistency"]["share"],
        "league_mu": {k: v["mu"] for k, v in leagues_out.items()},
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
