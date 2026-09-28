"""PREREG §3 的特征构造(纯函数,不触碰数据库;加载器在文件底部)。

约定:
- 输入 `matches` 按 kickoff 升序;所有滚动量只用 kickoff **严格早于** 本场的比赛。
- 单场值缺失 = None,不插补;窗口内非 None 值 <3 → None。
- 主窗口 8 场,权重 0.5^(k/4);敏感性 5/10 场同一权重式。
- 对手调整:adj_T(j) = v_T(j) − EWMA8(对手 O_j 在 j 之前"被对手打出的 v"),再 EWMA8。
- C9:球员赛后 rating 按 minutes 加权 EWMA8(≥3 场),球队值 = 上一场首发 11 人中有效
  (≥3 场)球员的均值,有效 <8 记缺失。
- C13:上一场首发 market_value 之和取 log,缺失 >3 人记缺失(去留由 PREREG §6.6 决定)。

本模块**不读取**比分/收盘线以外的目标信息,也不计算任何特征与目标的关系。
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

HALF_LIFE = 4.0
WINDOWS = (8, 5, 10)
MAIN_WINDOW = 8
MIN_PRIOR = 3
C9_MIN_PLAYER_MATCHES = 3
C9_MIN_VALID_PLAYERS = 8
C13_MAX_MISSING = 3
C11_CAP_DAYS = 14.0
C4_REL_TOL = 0.02

# ---- 队级 extra_json key(FotMob 原始 key)
K = dict(
    npxg="expected_goals_non_penalty", xgot="expected_goals_on_target", xg="expected_goals",
    big_chance="big_chance", sib="shots_inside_box",
    opp_half="opposition_half_passes", passes="passes", tackles="matchstats.headers.tackles",
    interceptions="interceptions", fouls="fouls", tob="touches_opp_box",
    acc_crosses="accurate_crosses", acc_passes="accurate_passes", long_balls="long_balls_accurate",
    gdw="ground_duels_won", aer="aerials_won", corners="corners", poss="BallPossesion",
)
TEAM_KEYS_USED = tuple(sorted(set(K.values())))

B_FEATURES = ("B1_npxgd", "B2_xgot_diff", "B3_att_luck", "B4_def_luck", "B5_big_chance_diff", "B6_sib_diff")
C_TEAM_FEATURES = ("C1_field_tilt", "C2_ppda", "C3_tob_diff", "C4_final_third_passes", "C5_cross_share",
                   "C6_long_share", "C7_duel_rate", "C8_corner_diff", "C12_possession")
C_LINEUP_FEATURES = ("C9_rolling_rating", "C10_xi_jaccard", "C11_rest_days", "C13_log_market_value")
STAT_FEATURES = B_FEATURES + C_TEAM_FEATURES            # 有单场值、可做窗口/对手调整/上半场
FH_FEATURES = tuple(f for f in STAT_FEATURES if f not in ("C4_final_third_passes",))  # C4 无半场球员数据
ALL_FEATURES = STAT_FEATURES + C_LINEUP_FEATURES


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _num(raw: dict, key: str):
    v = raw.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _sub(a, b):
    return None if a is None or b is None else a - b


def _ratio(a, b):
    return None if a is None or b is None or b == 0 else a / b


# ------------------------------------------------------------------ 单场值
def single_match_values(own_goals, own: dict, opp_goals, opp: dict, player_agg: dict | None,
                        c5_mode: str) -> dict:
    """一支球队在一场比赛里的 B/C 单场值(for=own, against=opp)。player_agg 为该队该场的球员
    汇总(None 表示无球员数据);c5_mode ∈ {'player_total','team_proxy'}。"""
    g = lambda k: _num(own, K[k])      # noqa: E731
    o = lambda k: _num(opp, K[k])      # noqa: E731
    v = {}
    v["B1_npxgd"] = _sub(g("npxg"), o("npxg"))
    v["B2_xgot_diff"] = _sub(g("xgot"), o("xgot"))
    v["B3_att_luck"] = _sub(None if own_goals is None else float(own_goals), g("xg"))
    v["B4_def_luck"] = _sub(None if opp_goals is None else float(opp_goals), o("xg"))
    v["B5_big_chance_diff"] = _sub(g("big_chance"), o("big_chance"))
    v["B6_sib_diff"] = _sub(g("sib"), o("sib"))
    oh, ooh = g("opp_half"), o("opp_half")
    v["C1_field_tilt"] = _ratio(oh, None if oh is None or ooh is None else oh + ooh)
    num = _sub(o("passes"), ooh)
    den = None
    if g("tackles") is not None and g("interceptions") is not None and g("fouls") is not None:
        den = g("tackles") + g("interceptions") + g("fouls")
    v["C2_ppda"] = _ratio(num, den)
    v["C3_tob_diff"] = _sub(g("tob"), o("tob"))
    v["C4_final_third_passes"] = None
    if player_agg is not None and player_agg.get("c4_valid"):
        v["C4_final_third_passes"] = player_agg["final_third_passes"]
    if c5_mode == "player_total":
        v["C5_cross_share"] = None
        if player_agg is not None and player_agg.get("crosses_total") is not None:
            v["C5_cross_share"] = _ratio(player_agg["crosses_total"], player_agg.get("passes_total"))
    else:
        v["C5_cross_share"] = _ratio(g("acc_crosses"), g("acc_passes"))
    v["C6_long_share"] = _ratio(g("long_balls"), g("acc_passes"))
    if None not in (g("gdw"), g("aer"), o("gdw"), o("aer")):
        tot = g("gdw") + g("aer") + o("gdw") + o("aer")
        v["C7_duel_rate"] = _ratio(g("gdw") + g("aer"), tot)
    else:
        v["C7_duel_rate"] = None
    v["C8_corner_diff"] = _sub(g("corners"), o("corners"))
    v["C12_possession"] = g("poss")
    return v


def ewma(values: list, window: int, min_n: int = MIN_PRIOR, weights: list | None = None):
    """values 按时间升序(最后一个最近);取最后 window 个,跳过 None,非 None <min_n → None。
    weights(可选)与 values 等长,乘到 0.5^(k/4) 上(C9 用 minutes)。"""
    tail = values[-window:]
    wt = None if weights is None else weights[-window:]
    num = den = 0.0
    n = 0
    L = len(tail)
    for idx, val in enumerate(tail):
        if val is None:
            continue
        k = L - 1 - idx
        w = 0.5 ** (k / HALF_LIFE)
        if wt is not None:
            if wt[idx] is None or wt[idx] <= 0:
                continue
            w *= wt[idx]
        num += w * val
        den += w
        n += 1
    if n < min_n or den == 0:
        return None
    return num / den


def jaccard(a: set, b: set):
    if not a and not b:
        return None
    return len(a & b) / len(a | b)


# ------------------------------------------------------------------ 主构造
def build_features(matches: list[dict], team_stats: dict, lineups: dict, player_rows: list[dict],
                   c5_mode: str = "player_total", windows=WINDOWS) -> dict[int, dict]:
    """返回 {match_id: {feature_variant_name: value}}。

    matches:   dim_match 行(含 Match_ID, kickoff_at_utc, Home_Team_ID, Away_Team_ID),任意顺序。
    team_stats:{(mid, tid, period): {"goals": int|None, "raw": dict}}
    lineups:   {(mid, tid): [ {Player_ID, is_starter, rating, market_value} ]}
    player_rows: fact_player_match_stats 行(Match_ID, Team_ID, Player_ID, minutes_played,
               passes_into_final_third, accurate_passes_total, accurate_crosses_total)
    """
    ms = sorted(matches, key=lambda m: (m["kickoff_at_utc"], m["Match_ID"]))
    mids = {m["Match_ID"] for m in ms}

    # 球员汇总(C4/C5 用) + 球员 minutes(C9 用)
    pagg: dict[tuple[int, int], dict] = {}
    pmin: dict[tuple[int, str], float | None] = {}
    tmp = defaultdict(lambda: dict(ft=0.0, ft_any=False, pt=0.0, pt_any=False, ct=0.0, ct_any=False, n=0))
    for r in player_rows:
        key = (r["Match_ID"], r["Team_ID"])
        if r["Match_ID"] not in mids:
            continue
        t = tmp[key]
        t["n"] += 1
        pmin[(r["Match_ID"], str(r["Player_ID"]))] = r["minutes_played"]
        if r["passes_into_final_third"] is not None:
            t["ft"] += float(r["passes_into_final_third"]); t["ft_any"] = True
        if r["accurate_passes_total"] is not None:
            t["pt"] += float(r["accurate_passes_total"]); t["pt_any"] = True
        if r["accurate_crosses_total"] is not None:
            t["ct"] += float(r["accurate_crosses_total"]); t["ct_any"] = True
    for key, t in tmp.items():
        team_passes = _num(team_stats.get((key[0], key[1], "All"), {}).get("raw", {}), K["passes"])
        c4_valid = False
        rel = None
        if t["pt_any"] and team_passes:
            rel = abs(t["pt"] - team_passes) / team_passes
            c4_valid = rel <= C4_REL_TOL and t["ft_any"]
        pagg[key] = dict(
            final_third_passes=t["ft"] if t["ft_any"] else None,
            passes_total=t["pt"] if t["pt_any"] else None,
            crosses_total=t["ct"] if t["ct_any"] else None,
            c4_valid=c4_valid, c4_rel_err=rel, n_players=t["n"],
        )

    # 单场值(All / FirstHalf)
    single: dict[tuple[int, int], dict] = {}
    single_fh: dict[tuple[int, int], dict] = {}
    for m in ms:
        mid, h, a = m["Match_ID"], m["Home_Team_ID"], m["Away_Team_ID"]
        for period, store in (("All", single), ("FirstHalf", single_fh)):
            th = team_stats.get((mid, h, period))
            ta = team_stats.get((mid, a, period))
            if th is None or ta is None:
                store[(mid, h)] = {f: None for f in STAT_FEATURES}
                store[(mid, a)] = {f: None for f in STAT_FEATURES}
                continue
            ph = pagg.get((mid, h)) if period == "All" else None
            pa = pagg.get((mid, a)) if period == "All" else None
            store[(mid, h)] = single_match_values(th["goals"], th["raw"], ta["goals"], ta["raw"], ph, c5_mode)
            store[(mid, a)] = single_match_values(ta["goals"], ta["raw"], th["goals"], th["raw"], pa, c5_mode)

    # 球队时间线
    hist: dict[int, list[dict]] = defaultdict(list)
    for m in ms:
        mid, h, a = m["Match_ID"], m["Home_Team_ID"], m["Away_Team_ID"]
        ko = parse_utc(m["kickoff_at_utc"])
        for tid, opp in ((h, a), (a, h)):
            lu = lineups.get((mid, tid), [])
            starters = [p for p in lu if p["is_starter"]]
            hist[tid].append(dict(
                mid=mid, ko=ko, opp=opp, v=single[(mid, tid)], vfh=single_fh[(mid, tid)],
                starters=starters, starter_ids={str(p["Player_ID"]) for p in starters},
            ))

    # 球员评分时间线(rating 来自 lineup,minutes 来自 player stats)
    phist: dict[str, list[tuple[datetime, float | None, float | None]]] = defaultdict(list)
    for m in ms:
        mid = m["Match_ID"]
        ko = parse_utc(m["kickoff_at_utc"])
        for tid in (m["Home_Team_ID"], m["Away_Team_ID"]):
            for p in lineups.get((mid, tid), []):
                pid = str(p["Player_ID"])
                mins = pmin.get((mid, pid))
                phist[pid].append((ko, p["rating"], None if mins is None else float(mins)))
    for pid in phist:
        phist[pid].sort(key=lambda x: x[0])

    def player_rolling(pid: str, before: datetime):
        seq = [(r, mins) for (ko, r, mins) in phist.get(pid, []) if ko < before and r is not None
               and mins is not None and mins > 0]
        if len(seq) < C9_MIN_PLAYER_MATCHES:
            return None
        return ewma([r for r, _ in seq], MAIN_WINDOW, min_n=C9_MIN_PLAYER_MATCHES,
                    weights=[mins for _, mins in seq])

    # 对手"被打出"的值:against_hist[tid] = [(ko, {f: v_opp(f)})]
    # 用 hist 反查:team O 在比赛 j 的对手 X,X 在 j 的 v 即 O 被打出的值
    v_by = {(e["mid"], tid): e["v"] for tid, es in hist.items() for e in es}
    against_cache: dict[tuple[int, int, str], float | None] = {}

    def ewma_against(tid: int, before_mid: int, before: datetime, f: str):
        """team tid 在 before(比赛 before_mid 的开球)之前"被对手打出"的 f 的 EWMA8;
        同一 (tid, before_mid, f) 只算一次(截断重算时缓存随子集重建,不跨调用)。"""
        key = (tid, before_mid, f)
        if key not in against_cache:
            vals = [v_by[(e["mid"], e["opp"])][f] for e in hist[tid] if e["ko"] < before]
            against_cache[key] = ewma(vals, MAIN_WINDOW)
        return against_cache[key]

    out: dict[int, dict] = {}
    for m in ms:
        mid, h, a = m["Match_ID"], m["Home_Team_ID"], m["Away_Team_ID"]
        ko = parse_utc(m["kickoff_at_utc"])
        team_vals = {}
        for tid in (h, a):
            prior = [e for e in hist[tid] if e["ko"] < ko]
            tv: dict[str, float | None] = {"n_prior": len(prior)}
            enough = len(prior) >= MIN_PRIOR
            for f in STAT_FEATURES:
                seq = [e["v"][f] for e in prior]
                for w in windows:
                    tv[f"{f}__w{w}"] = ewma(seq, w) if enough else None
                # 对手调整(主窗口)
                if enough:
                    adj = []
                    for e in prior:
                        base = e["v"][f]
                        ea = ewma_against(e["opp"], e["mid"], e["ko"], f)
                        adj.append(None if base is None or ea is None else base - ea)
                    tv[f"{f}__adj"] = ewma(adj, MAIN_WINDOW)
                else:
                    tv[f"{f}__adj"] = None
                if f in FH_FEATURES:
                    tv[f"{f}__fh"] = ewma([e["vfh"][f] for e in prior], MAIN_WINDOW) if enough else None
            # C9 / C10 / C11 / C13
            if enough:
                last = prior[-1]
                ratings = [player_rolling(pid, ko) for pid in last["starter_ids"]]
                valid = [r for r in ratings if r is not None]
                tv["C9_rolling_rating"] = (sum(valid) / len(valid)) if len(valid) >= C9_MIN_VALID_PLAYERS else None
                tv["C9_valid_players"] = len(valid)
                jac = [jaccard(prior[i]["starter_ids"], prior[i - 1]["starter_ids"]) for i in range(1, len(prior))]
                tv["C10_xi_jaccard"] = ewma(jac, MAIN_WINDOW)
                tv["C11_rest_days"] = min((ko - last["ko"]).total_seconds() / 86400.0, C11_CAP_DAYS)
                mvs = [p["market_value"] for p in last["starters"]]
                missing = sum(1 for x in mvs if x is None)
                tot = sum(float(x) for x in mvs if x is not None)
                tv["C13_log_market_value"] = math.log(tot) if (missing <= C13_MAX_MISSING and tot > 0) else None
            else:
                for f in C_LINEUP_FEATURES:
                    tv[f] = None
                tv["C9_valid_players"] = None
            team_vals[tid] = tv
        row = {}
        for k, vh in team_vals[h].items():
            va = team_vals[a][k]
            row[f"home__{k}"] = vh
            row[f"away__{k}"] = va
            if k in ("n_prior", "C9_valid_players"):
                continue
            row[f"diff__{k}"] = None if vh is None or va is None else vh - va
        out[mid] = row
    return out


# ------------------------------------------------------------------ 加载器(只读连接由调用方提供)
def load_team_stats(core: sqlite3.Connection, mids: set[int]) -> dict:
    rows = core.execute(
        "SELECT Match_ID, Team_ID, Period, Goals, extra_json FROM fact_team_match_stats "
        "WHERE Period IN ('All','FirstHalf')").fetchall()
    out = {}
    for mid, tid, period, goals, ej in rows:
        if mid not in mids:
            continue
        try:
            raw = json.loads(ej) if ej else {}
        except ValueError:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        out[(mid, tid, period)] = dict(goals=goals, raw=raw)
    return out


def load_lineups(core: sqlite3.Connection, mids: set[int]) -> dict:
    rows = core.execute(
        "SELECT Match_ID, Team_ID, Player_ID, is_starter, rating, market_value FROM fact_match_lineup").fetchall()
    out = defaultdict(list)
    for mid, tid, pid, st, rating, mv in rows:
        if mid not in mids:
            continue
        out[(mid, tid)].append(dict(Player_ID=pid, is_starter=int(st or 0), rating=rating, market_value=mv))
    return out


def load_player_rows(core: sqlite3.Connection, mids: set[int]) -> list[dict]:
    rows = core.execute(
        """SELECT Match_ID, Team_ID, Player_ID, minutes_played, passes_into_final_third,
                  accurate_passes_total, accurate_crosses_total, expected_goals, accurate_passes, goals
             FROM fact_player_match_stats""").fetchall()
    keys = ("Match_ID", "Team_ID", "Player_ID", "minutes_played", "passes_into_final_third",
            "accurate_passes_total", "accurate_crosses_total", "expected_goals", "accurate_passes", "goals")
    return [dict(zip(keys, r)) for r in rows if r[0] in mids]
