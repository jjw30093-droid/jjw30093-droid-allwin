"""Phase 2 之前的位置校验准备(只读本地参数 JSON,不连数据库)。

a. 自动检查:规则解码的 8 组映射回 GK/DEF/MID/FWD,与 usual_position_id 比对。
b. 按解码位置分层随机抽 60 名球员(每组 7–8 人),写 CSV 供人工核对。

用法:python3 scripts/simulator/position_check.py .local-data/simulator/simulator_params_YYYYMMDD.json
CSV 写到参数 JSON 同目录的 position_check.csv(.local-data/ 不进 git)。
"""
from __future__ import annotations

import csv
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

GROUPS = ("GK", "CB", "FB", "DM", "CM", "AM", "W", "ST")
TO_COARSE = {"GK": 0, "CB": 1, "FB": 1, "DM": 2, "CM": 2, "AM": 2, "W": 3, "ST": 3}
USUAL_LABEL = {0: "门将", 1: "后卫", 2: "中场", 3: "前锋"}
SAMPLE_TOTAL = 60
SEED = 20260929


def main() -> None:
    path = Path(sys.argv[1])
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
    pairs = Counter((p["main_position"], USUAL_LABEL[p["usual_position_id"]]) for p in comparable
                    if TO_COARSE[p["main_position"]] != p["usual_position_id"])
    print("不一致组合(解码 → usual):", ", ".join(f"{a}→{b}×{n}" for (a, b), n in pairs.most_common()))
    print("不一致名单(姓名|球队|解码|usual|最多的阵型/position_id/次数):")
    for p in sorted((p for p in comparable if p not in agree), key=lambda p: (p["main_position"], -p["minutes"])):
        t = teams.get(str(p["team_id"]), {}).get("name_zh", "")
        print(f"  {name(p)}|{t}|{p['main_position']}|{USUAL_LABEL[p['usual_position_id']]}|"
              f"{p['top_formation']}/{p['top_position_id']}/{p['top_position_starts']}")

    # ---- b. 分层抽样
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
