"""pit_dataset.py — 严格 point-in-time 研究数据集构建器(五大联赛)。

与生产特征表(int_match_features)的关键区别,逐条对应 2026-08-07 审计发现:

1. **排序 = kickoff_at_utc 严格时刻序**,不是 (match_date, match_id)。缺失精确
   kickoff 的完赛场次 fail-closed(整场剔除并计数上报,不猜测顺序)。
2. **同 kickoff 时刻互不进入对方历史**:历史窗口条件是 kickoff(历史场)
   **严格早于** kickoff(目标场),同刻并发场互相不可见。
3. **rolling 先排除 target match 本身**(按时刻严格早于,天然排除)。
4. **无未来锚点**:不产出依赖全表最大日期的 sample_weight;时间衰减权重留给
   训练侧按每个 fold 的 train 截止时刻现算(build_wdl_baseline 的全表锚点
   违反 PIT,见审计 #2)。
5. **附加赛隔离**:Serie A 2022/23 保级附加赛(Match_ID 4185671,
   Match_Round='final')标记 is_playoff=1,默认排除出常规赛研究集。
6. **数据质量过滤(TRUSTED_WITH_FILTERS)**:physical 全排除(本来就不取)、
   xGOT 原始值不取(NULL→0 断点)、shot_accuracy 不取(ShotsOnTarget 冗余)。
   本构建器只取 expected_goals / total_shots / ShotsOnTarget / BallPossesion
   四个 Period='All' 字段 + 比分,全部在过滤白名单内。
7. **lineage**:每场输出 input_cutoff_at(=kickoff)与输入历史场 Match_ID
   列表的稳定 hash;数据集整体 hash 确定(不含生成时间/路径)。

升班马/最小历史:不设隐藏门槛,如实输出 n_prior_*(总口径与分 venue 口径),
由训练侧决定 min_history 策略;跨赛季 rolling 与生产口径一致(允许跨季,
n_prior_season 单独给出本赛季内场次数供消融)。

只读纪律:传入的连接应指向 /tmp 一致性副本;本模块不含任何写库语句。

8. **J1/K1/澳超接入(2026-08-08 窄幅扩展)**:`leagues` 参数已支持任意联赛
   集合,不限于五大联赛。新增两处向后兼容扩展,默认参数下对五大联赛的
   `dataset_hash` 逐位不变(已用 `172d4428455465ac77bff6d57fa45e170938aa08edca24d8ce49fbbbf7cda0c0`
   验证):
   - **赛制阶段识别改用规则**:原来只认硬编码 `PLAYOFF_MATCH_IDS` 与
     `Match_Round=='final'`;现在任何非纯数字 `Match_Round`(等价于 SQL
     `GLOB '*[^0-9]*'`)都判定为赛制阶段(附加赛/季后赛/排位赛)。已实测
     该规则在五大联赛上推导出的集合与原硬编码集合 `{4185671}` 完全相同,
     故默认行为不变。真实覆盖:J1 `final`/`bronze`/`5..19/6..20`(20 场)、
     澳超 `1/4`/`1/2`/`final`(21 场)、K1(0 场,K1 没有淘汰赛,只有下面
     第二条讲的分组赛制)。
   - **target/history 两个独立开关**:`include_playoff`(默认 `False`)只控制
     赛制阶段比赛是否进入目标/评估样本;新增 `include_playoff_in_history`
     (默认 `True`,与原来的隐式行为一致)控制赛制阶段比赛是否可以作为
     后续比赛的滚动历史输入。两者可独立开关,用于敏感性分析。
   - **赛制结构性分裂检测(`detect_stage_splits`,默认关闭)**:K1 每季第
     34–38 轮切成 Final A/Final B 两组只组内对阵(不在 `Match_Round`
     字段里标记),J1 2026 过渡半赛季是东西两个 10 队分区(整季分区,
     无需切分轮次)。开启后用赛程图连通分量确定性识别,产出的
     `is_stage_split` 字段只在开启时才写入每行,默认关闭时行结构不变、
     hash 不变。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict, deque

FIVE_LEAGUES = (47, 53, 54, 55, 87)
ALL_EIGHT_LEAGUES = (47, 53, 54, 55, 87, 223, 9080, 113)
ROLL_WINDOWS = (5, 10)
ROLL_STATS = ("xg_for", "xg_against", "goals_for", "goals_against",
              "shots_for", "shots_on_target_for", "possession")
PLAYOFF_MATCH_IDS = {4185671}   # Serie A 2022/23 保级附加赛(审计 §4)
                                 # 已被下面的 _is_stage_round() 规则覆盖(逐位验证过
                                 # 两者在五大联赛上推导出同一个集合),继续保留只为
                                 # 防御性冗余,不再是唯一判据。


def _is_stage_round(match_round) -> bool:
    """判定 Match_Round 是否代表赛制阶段(附加赛/季后赛/排位赛)。

    等价于 SQL `Match_Round GLOB '*[^0-9]*'`(含任意非数字字符)。
    已实测:该规则在五大联赛上推导出的集合与硬编码 PLAYOFF_MATCH_IDS
    完全相同({4185671}),不改变五大联赛默认行为。
    真实场次(2026-08-08 只读实测):J1 `final`×2/`bronze`×2/`5..19`+`6..20`
    各×2(共 20 场)、澳超 `1/4`×6/`1/2`×12/`final`×3(共 21 场)、
    K1 0 场(K1 没有淘汰赛)。数据库中没有 NULL/空 Match_Round(已验证)。
    """
    if match_round is None:
        return False
    s = str(match_round)
    return not s.isdigit()


def _mean(values) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _connected_components(edges: list[tuple[int, int]]) -> list[set[int]]:
    adj: dict[int, set[int]] = defaultdict(set)
    nodes: set[int] = set()
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
        nodes.add(a)
        nodes.add(b)
    seen: set[int] = set()
    comps = []
    for n in nodes:
        if n in seen:
            continue
        stack, cur = [n], set()
        while stack:
            u = stack.pop()
            if u in cur:
                continue
            cur.add(u)
            stack.extend(adj[u] - cur)
        seen |= cur
        comps.append(cur)
    return comps


def detect_stage_splits(matches: list[dict]) -> set[int]:
    """按赛程图连通分量,确定性识别 (league_id, season) 内的结构性分组/分区
    (K1 每季第 34-38 轮 Final A/Final B、J1 2026 东西分区)。

    只在**纯数字轮次**(`_is_stage_round` 判定为 False)上计算——附加赛/
    季后赛/排位赛已由 `is_playoff` 单独处理,不参与这里的判断。

    算法(2026-08-08 对抗复核推翻了"从最后一轮开始逐轮累加、遇到 comps==1
    立即停"的初版——那个版本会把任何正常赛季的最后 1-2 轮误判成分裂,因为
    单独一轮天然就是不连通的图(N/2 场比赛=N/2 个不相交分量)。修正为:
    (a) 窗口最小 3 轮才开始判定,避免单轮/双轮的平凡不连通;
    (b) 要求每个分量至少 3 支队伍,排除巧合的小分量;
    (c) 从赛季末尾往前扩窗,分量数满足条件就持续记录,一旦不再满足就停止
        (单调性:窗口只加边不减边,分量数不会因为扩窗而增多,一旦合并成 1
        个分量,更早的比赛只会让图更连通,不需要继续往回找)。

    落地前必须对全部真实 league-season 空跑验证零误报,不能只信任构造用例。
    """
    by_ls: dict[tuple[int, str], list[tuple[int, dict]]] = defaultdict(list)
    for m in matches:
        if _is_stage_round(m.get("match_round")):
            continue
        try:
            rnd = int(m["match_round"])
        except (TypeError, ValueError):
            continue
        by_ls[(m["league_id"], m["season"])].append((rnd, m))

    split_ids: set[int] = set()
    for items in by_ls.values():
        edge_by_round: dict[int, list[tuple[int, int]]] = defaultdict(list)
        round_matches: dict[int, list[dict]] = defaultdict(list)
        for rnd, m in items:
            edge_by_round[rnd].append((m["home_team_id"], m["away_team_id"]))
            round_matches[rnd].append(m)
        rounds_desc = sorted(edge_by_round, reverse=True)

        window: list[int] = []
        best_window: set[int] | None = None
        for rnd in rounds_desc:
            window.append(rnd)
            if len(window) < 3:
                continue
            edges = [e for r in window for e in edge_by_round[r]]
            comps = _connected_components(edges)
            if len(comps) > 1 and all(len(c) >= 3 for c in comps):
                best_window = set(window)
            else:
                break
        if best_window:
            for rnd in best_window:
                for m in round_matches[rnd]:
                    split_ids.add(m["match_id"])
    return split_ids


def load_matches(conn_core: sqlite3.Connection, leagues=FIVE_LEAGUES) -> tuple[list[dict], dict]:
    """读取完赛场次(kickoff 严格时刻序)。返回 (matches, quality_report)。"""
    placeholders = ",".join("?" for _ in leagues)
    rows = conn_core.execute(
        f"""SELECT dm.Match_ID, dm.League_ID, dm.Season, dm.Date, dm.kickoff_at_utc,
                   dm.kickoff_precision, dm.Home_Team_ID, dm.Away_Team_ID,
                   dm.home_score, dm.away_score, dm.Match_Round
              FROM dim_match dm
             WHERE dm.status='Finish' AND dm.League_ID IN ({placeholders})""",
        tuple(leagues),
    ).fetchall()

    stats: dict[tuple[int, int], dict] = {}
    for r in conn_core.execute(
        f"""SELECT fts.Match_ID, fts.Team_ID, fts.extra_json
              FROM fact_team_match_stats fts
              JOIN dim_match dm ON dm.Match_ID = fts.Match_ID
             WHERE fts.Period='All' AND dm.status='Finish'
               AND dm.League_ID IN ({placeholders})""",
        tuple(leagues),
    ):
        d = json.loads(r["extra_json"])
        stats[(int(r["Match_ID"]), int(r["Team_ID"]))] = {
            "xg": d.get("expected_goals"),
            "shots": d.get("total_shots"),
            "sot": d.get("ShotsOnTarget"),
            "poss": d.get("BallPossesion"),
        }

    matches, dropped_no_kickoff, dropped_no_stats, playoff = [], 0, 0, 0
    for r in rows:
        mid = int(r["Match_ID"])
        if r["kickoff_at_utc"] is None or r["kickoff_precision"] != "exact":
            dropped_no_kickoff += 1
            continue
        is_playoff = mid in PLAYOFF_MATCH_IDS or _is_stage_round(r["Match_Round"])
        home_stats = stats.get((mid, int(r["Home_Team_ID"])))
        away_stats = stats.get((mid, int(r["Away_Team_ID"])))
        if home_stats is None or away_stats is None:
            dropped_no_stats += 1
            continue
        if is_playoff:
            playoff += 1
        matches.append({
            "match_id": mid,
            "league_id": int(r["League_ID"]),
            "season": str(r["Season"]),
            "kickoff": str(r["kickoff_at_utc"]),
            "home_team_id": int(r["Home_Team_ID"]),
            "away_team_id": int(r["Away_Team_ID"]),
            "home_score": int(r["home_score"]),
            "away_score": int(r["away_score"]),
            "is_playoff": int(is_playoff),
            "match_round": r["Match_Round"],
            "home_stats": home_stats,
            "away_stats": away_stats,
        })

    matches.sort(key=lambda m: (m["kickoff"], m["match_id"]))
    report = {
        "finished_rows": len(rows),
        "dropped_no_exact_kickoff": dropped_no_kickoff,
        "dropped_missing_team_stats": dropped_no_stats,
        "playoff_flagged": playoff,
        "usable": len(matches),
    }
    return matches, report


def build_dataset(conn_core: sqlite3.Connection, leagues=FIVE_LEAGUES,
                  include_playoff: bool = False,
                  include_playoff_in_history: bool = True,
                  detect_stage_splits_flag: bool = False) -> dict:
    """构建严格 PIT 数据集。

    返回 {"rows": [...], "quality": {...}, "dataset_hash": str, "manifest": {...}}。
    每行含目标(结果)、特征(仅 kickoff 前历史)、lineage(输入场 id hash)。

    `include_playoff`(默认 False)控制赛制阶段比赛(附加赛/季后赛/排位赛)
    是否进入目标/评估样本。`include_playoff_in_history`(默认 True,与
    2026-08-08 之前的隐式行为一致)控制这些比赛是否可作为后续比赛的滚动
    历史输入 —— 两者是独立开关,用于敏感性分析。`detect_stage_splits_flag`
    (默认 False)控制是否额外计算 `is_stage_split` 字段(K1 Final A/B 分组、
    J1 2026 东西分区);默认关闭时行结构与 `dataset_hash` 完全不变。
    """
    matches, quality = load_matches(conn_core, leagues)
    stage_split_ids = detect_stage_splits(matches) if detect_stage_splits_flag else None

    # 每队的历史(按 kickoff 序推进);deque 只保留最近 10 场即可
    overall: dict[int, deque] = defaultdict(lambda: deque(maxlen=max(ROLL_WINDOWS)))
    venue: dict[tuple[int, str], deque] = defaultdict(lambda: deque(maxlen=max(ROLL_WINDOWS)))
    season_count: dict[tuple[int, str], int] = defaultdict(int)
    last_kickoff: dict[int, str] = {}

    rows = []
    i = 0
    n = len(matches)
    while i < n:
        # 同 kickoff 时刻的比赛作为一个批次:先全部出特征,再统一入历史,
        # 保证同刻并发场互相不可见(不变量:历史 kickoff 严格 < 目标 kickoff)
        j = i
        while j < n and matches[j]["kickoff"] == matches[i]["kickoff"]:
            j += 1
        batch = matches[i:j]

        for m in batch:
            if m["is_playoff"] and not include_playoff:
                continue
            feat: dict = {}
            lineage_ids: list[int] = []
            for side, tid, opp_prefix in (("home", m["home_team_id"], "away"),
                                          ("away", m["away_team_id"], "home")):
                hist = list(overall[tid])
                lineage_ids.extend(h["match_id"] for h in hist)
                for w in ROLL_WINDOWS:
                    win = hist[-w:]
                    for stat in ROLL_STATS:
                        feat[f"{side}_{stat}_l{w}"] = _mean(h[stat] for h in win)
                    feat[f"{side}_n_matches_l{w}"] = len(win)
                vkey = (tid, side)
                vhist = list(venue[vkey])
                # venue 窗口可回溯到 overall-10 之外(真实数据 5,821 个案例,
                # 2026-08-07 对抗复核发现)——它们是真实特征输入,必须计入
                # lineage,否则 lineage_hash 低估输入集合
                lineage_ids.extend(h["match_id"] for h in vhist[-max(ROLL_WINDOWS):])
                for w in ROLL_WINDOWS:
                    vwin = vhist[-w:]
                    feat[f"{side}_xg_for_{side}_l{w}"] = _mean(h["xg_for"] for h in vwin)
                    feat[f"{side}_n_matches_{side}_l{w}"] = len(vwin)
                feat[f"{side}_n_prior_season"] = season_count[(tid, m["season"])]
                lk = last_kickoff.get(tid)
                feat[f"{side}_rest_days"] = (
                    round((_ts(m["kickoff"]) - _ts(lk)) / 86400.0, 3) if lk else None
                )
            row = {
                "match_id": m["match_id"],
                "league_id": m["league_id"],
                "season": m["season"],
                "kickoff": m["kickoff"],
                "input_cutoff_at": m["kickoff"],
                "home_team_id": m["home_team_id"],
                "away_team_id": m["away_team_id"],
                "target_home_goals": m["home_score"],
                "target_away_goals": m["away_score"],
                "is_playoff": m["is_playoff"],
                "features": feat,
                "lineage_hash": hashlib.sha256(
                    json.dumps(sorted(set(lineage_ids))).encode()
                ).hexdigest()[:16],
                "n_lineage_inputs": len(set(lineage_ids)),
            }
            if detect_stage_splits_flag:
                row["is_stage_split"] = int(m["match_id"] in stage_split_ids)
            rows.append(row)

        # 批次统一入历史(含附加赛:它们真实发生过,可以作为后续比赛的历史,
        # 除非 include_playoff_in_history=False 显式关闭做敏感性分析)
        for m in batch:
            if m["is_playoff"] and not include_playoff_in_history:
                continue
            for side, tid in (("home", m["home_team_id"]), ("away", m["away_team_id"])):
                opp = "away" if side == "home" else "home"
                entry = {
                    "match_id": m["match_id"],
                    "xg_for": m[f"{side}_stats"]["xg"],
                    "xg_against": m[f"{opp}_stats"]["xg"],
                    "goals_for": m[f"{side}_score"],
                    "goals_against": m[f"{opp}_score"],
                    "shots_for": m[f"{side}_stats"]["shots"],
                    "shots_on_target_for": m[f"{side}_stats"]["sot"],
                    "possession": m[f"{side}_stats"]["poss"],
                }
                overall[tid].append(entry)
                venue[(tid, side)].append(entry)
                season_count[(tid, m["season"])] += 1
                last_kickoff[tid] = m["kickoff"]
        i = j

    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    dataset_hash = hashlib.sha256(payload.encode()).hexdigest()

    missing = defaultdict(int)
    for r in rows:
        for k, v in r["features"].items():
            if v is None:
                missing[k] += 1
    manifest = {
        "leagues": sorted(leagues),
        "include_playoff": include_playoff,
        "include_playoff_in_history": include_playoff_in_history,
        "detect_stage_splits": detect_stage_splits_flag,
        "stage_split_rows": (sum(r.get("is_stage_split", 0) for r in rows)
                            if detect_stage_splits_flag else None),
        "roll_windows": list(ROLL_WINDOWS),
        "roll_stats": list(ROLL_STATS),
        "rows": len(rows),
        "dataset_hash": dataset_hash,
        "feature_missing_counts": dict(sorted(missing.items())),
        "quality": quality,
        "excluded_by_policy": {
            "physical_metrics": "全 NOT_READY(审计 §3)",
            "xgot_raw": "NULL→0 断点(审计 §2),原始值不入集",
            "shot_accuracy": "ShotsOnTarget 冗余复制列(审计 §1-2)",
        },
    }
    return {"rows": rows, "manifest": manifest, "dataset_hash": dataset_hash}


def _ts(iso: str) -> float:
    from datetime import datetime, timezone

    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
