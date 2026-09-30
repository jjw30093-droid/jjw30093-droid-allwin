"""Phase 2 之前的位置校验准备(只读本地参数 JSON,不连数据库)。

a. 自动检查:规则解码的 8 组映射回 GK/DEF/MID/FWD,与 usual_position_id 比对。
b. 分层随机抽样写 CSV 供人工核对:
   - 默认:按解码位置分层抽 60 名(每组 7–8 人),写 position_check.csv;
   - --per-league N:每个联赛抽 N 名、联赛内按 8 个位置组分层(N=16 即每组 2 人,某组人数不足时由同联赛人数最多的组补足),
     写 position_check_leagues.csv(带联赛列);自动检查另按联赛分别报告。

用法:python3 scripts/simulator/position_check.py .local-data/simulator/simulator_params_YYYYMMDD.json [--per-league 16]
CSV 写到参数 JSON 同目录(.local-data/ 不进 git)。
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

GROUPS = ("GK", "CB", "FB", "DM", "CM", "AM", "W", "ST")
TO_COARSE = {"GK": 0, "CB": 1, "FB": 1, "DM": 2, "CM": 2, "AM": 2, "W": 3, "ST": 3}
USUAL_LABEL = {0: "门将", 1: "后卫", 2: "中场", 3: "前锋"}
LEAGUE_NAME = {47: "英超", 87: "西甲", 55: "意甲", 54: "德甲", 53: "法甲"}
SAMPLE_TOTAL = 60
SEED = 20260929


def expected_mismatch(p: dict) -> str | None:
    """已知的、预期内的解码差异(站长 2026-09-30 认定,当日追加 5-4-1、4-4-1-1),单独列出,不计入不一致。"""
    f, pid, g, u = p["top_formation"] or "", p["top_position_id"], p["main_position"], p["usual_position_id"]
    if f in ("4-4-2", "5-4-1", "4-4-1-1") and pid in (72, 78) and g == "W" and u == 2:
        return "4-4-2 / 5-4-1 / 4-4-1-1 边前卫 72/78:解码 W,FotMob 中场"
    if (f.startswith("3-4-") or f == "3-5-2" or f.startswith("5-")) and g == "FB" and u in (2, 3):
        return "三中卫/五后卫体系翼卫:解码 FB,FotMob 中场或前锋"
    if f in ("4-2-3-1", "3-4-2-1") and pid in (84, 85) and g == "AM" and u == 3:
        return "4-2-3-1 / 3-4-2-1 的 84/85:解码 AM,FotMob 前锋"
    return None


def league_of(teams: dict, p: dict) -> int | None:
    return teams.get(str(p["team_id"]), {}).get("league_id")


def per_league_report(teams: dict, players: list[dict]) -> None:
    print("按联赛(一致 / 可比;扣除预期内不一致后):")
    for lid in sorted({league_of(teams, p) for p in players} - {None}, key=lambda x: list(LEAGUE_NAME).index(x) if x in LEAGUE_NAME else 99):
        ps = [p for p in players if league_of(teams, p) == lid and p["usual_position_id"] in USUAL_LABEL]
        ok = [p for p in ps if TO_COARSE[p["main_position"]] == p["usual_position_id"]]
        exp = [p for p in ps if p not in ok and expected_mismatch(p)]
        denom = len(ps) - len(exp)
        print(f"  {LEAGUE_NAME.get(lid, lid)}:{len(ok)}/{len(ps)} = {len(ok) / len(ps):.1%};"
              f"扣除预期内 {len(exp)} 人后 {len(ok)}/{denom} = {len(ok) / denom:.1%}(剩余不一致 {denom - len(ok)} 人)")


def per_league_sample(path: Path, teams: dict, players: list[dict], per_league: int, name) -> None:
    rng = random.Random(SEED)
    out = path.parent / "position_check_leagues.csv"
    rows, quotas = [], {}
    for lid in LEAGUE_NAME:
        lp = [p for p in players if league_of(teams, p) == lid]
        pools = {g: sorted((p for p in lp if p["main_position"] == g), key=lambda p: p["player_id"]) for g in GROUPS}
        quota = {g: min(per_league // len(GROUPS), len(pools[g])) for g in GROUPS}
        rest = per_league - sum(quota.values())
        for g in sorted(GROUPS, key=lambda g: -(len(pools[g]) - quota[g])):
            add = min(rest, len(pools[g]) - quota[g])
            quota[g] += add
            rest -= add
        quotas[LEAGUE_NAME[lid]] = quota
        for g in GROUPS:
            for p in rng.sample(pools[g], quota[g]):
                u = p["usual_position_id"]
                rows.append([LEAGUE_NAME[lid], name(p), teams.get(str(p["team_id"]), {}).get("name_zh", ""), g,
                             f"{u} {USUAL_LABEL.get(u, '')}".strip() if u is not None else "",
                             p["top_formation"], p["top_position_id"], "预期内" if expected_mismatch(p) else "", ""])
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["联赛", "中文名", "球队", "解码位置", "usual_position", "出场次数最多的阵型", "position_id", "预期内差异", "人工核对结论"])
        w.writerows(rows)
    print(f"CSV 已写出:{out}({len(rows)} 人;每联赛配额 {quotas})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("params")
    ap.add_argument("--per-league", type=int, default=0, help="每个联赛抽样人数(联赛内按位置组分层);0 = 旧的全局 60 人抽样")
    args = ap.parse_args()
    path = Path(args.params)
    d = json.loads(path.read_text(encoding="utf-8"))
    teams = d["teams"]
    players = [p for p in d["players"].values() if p["position_source"] == "rule_decoded"]

    def name(p: dict) -> str:
        return p["name_zh"] or p["name_en"] or "未知球员"

    # ---- a. 自动检查
    comparable = [p for p in players if p["usual_position_id"] in USUAL_LABEL]
    agree = [p for p in comparable if TO_COARSE[p["main_position"]] == p["usual_position_id"]]
    by_group = defaultdict(lambda: [0, 0])
    for p in comparable:
        by_group[p["main_position"]][1] += 1
        by_group[p["main_position"]][0] += TO_COARSE[p["main_position"]] == p["usual_position_id"]
    print(f"规则解码球员 {len(players)} 人,其中有 usual_position 可比 {len(comparable)} 人")
    print(f"一致 {len(agree)} / {len(comparable)} = {len(agree) / len(comparable):.1%}")
    print("按解码位置分组(一致/可比):")
    for g in GROUPS:
        a, n = by_group[g]
        if n:
            print(f"  {g:<2} → {USUAL_LABEL[TO_COARSE[g]]}:{a}/{n} = {a / n:.1%}")
    mismatched = [p for p in comparable if p not in agree]
    expected = [p for p in mismatched if expected_mismatch(p)]
    remaining = [p for p in mismatched if not expected_mismatch(p)]
    denom = len(comparable) - len(expected)
    print(f"扣除预期内的不一致 {len(expected)} 人后:一致 {len(agree)} / {denom} = {len(agree) / denom:.1%}"
          f"(剩余不一致 {len(remaining)} 人)")
    print("预期内的不一致(按类别):")
    for cat, n in Counter(expected_mismatch(p) for p in expected).most_common():
        print(f"  {cat}:{n} 人")
        for p in sorted((q for q in expected if expected_mismatch(q) == cat), key=lambda q: -q["minutes"]):
            t = teams.get(str(p["team_id"]), {}).get("name_zh", "")
            print(f"    {name(p)}|{t}|{p['main_position']}|{USUAL_LABEL[p['usual_position_id']]}|"
                  f"{p['top_formation']}/{p['top_position_id']}/{p['top_position_starts']}")
    pairs = Counter((p["main_position"], USUAL_LABEL[p["usual_position_id"]]) for p in remaining)
    print("剩余不一致组合(解码 → usual):", ", ".join(f"{a}→{b}×{n}" for (a, b), n in pairs.most_common()))
    print("剩余不一致名单(姓名|球队|解码|usual|最多的阵型/position_id/次数):")
    for p in sorted(remaining, key=lambda p: (p["main_position"], -p["minutes"])):
        t = teams.get(str(p["team_id"]), {}).get("name_zh", "")
        print(f"  {name(p)}|{t}|{p['main_position']}|{USUAL_LABEL[p['usual_position_id']]}|"
              f"{p['top_formation']}/{p['top_position_id']}/{p['top_position_starts']}")

    per_league_report(teams, players)

    # ---- b. 分层抽样
    if args.per_league:
        per_league_sample(path, teams, players, args.per_league, name)
        return
    rng = random.Random(SEED)
    pools = {g: sorted((p for p in players if p["main_position"] == g), key=lambda p: p["player_id"]) for g in GROUPS}
    order = sorted(GROUPS, key=lambda g: -len(pools[g]))
    quota = {g: SAMPLE_TOTAL // len(GROUPS) for g in GROUPS}
    for g in order[: SAMPLE_TOTAL - sum(quota.values())]:
        quota[g] += 1
    out = path.parent / "position_check.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["中文名", "球队", "解码位置", "usual_position", "出场次数最多的阵型", "position_id"])
        for g in GROUPS:
            for p in rng.sample(pools[g], min(quota[g], len(pools[g]))):
                u = p["usual_position_id"]
                w.writerow([name(p), teams.get(str(p["team_id"]), {}).get("name_zh", ""), g,
                            f"{u} {USUAL_LABEL.get(u, '')}".strip() if u is not None else "",
                            p["top_formation"], p["top_position_id"]])
    print(f"CSV 已写出:{out}(配额 {quota})")


if __name__ == "__main__":
    main()
