"""模拟器参数计算(纯内存,可按开赛时点截止)。生产导出与 Phase 2 回测快照共用这一套函数。

口径:docs/simulator-model.md(v0 + v0.1 + v0.2;Phase 2 快照口径见 P2.1)。

- load_raw():只读一次性把所需行读进内存(按比赛分组)。
- truncate():模拟"删除 Date ≥ D 的全部比赛"——截断测试用,产生一份物理上不含未来数据的副本。
- build():只用 cutoff 之前(Date < cutoff)的已完赛比赛计算参数;cutoff=None 时用全部已完赛比赛。
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

CHANNELS = ("open", "counter", "setpiece", "penalty")
SITUATION_CHANNEL = {
    "RegularPlay": "open",
    "IndividualPlay": "open",
    "FastBreak": "counter",
    "FromCorner": "setpiece",
    "SetPiece": "setpiece",
    "FreeKick": "setpiece",
    "ThrowInSetPiece": "setpiece",
    "Penalty": "penalty",
}
TEAM_WINDOW = 10
HALF_LIFE = 5.0
K_TEAM = 5.0
K_TEAM_PENALTY = 20.0
K_PLAYER_MIN = 900.0
K_GK_MIN = 1800.0
XA_DISCOUNT = 0.7
PLAYER_RATING_WINDOW = 10
QUANTILE_PROBS = tuple(round(0.05 * i, 2) for i in range(1, 20))
DEFAULT_STOPPAGE = (2.0, 5.0)
USUAL_FALLBACK = {0: "GK", 1: "CB", 2: "CM", 3: "ST"}
MIN_FORMATION_SAMPLES = 5
# v0.3 第 7 条:强度回归的候选窗口(场数, 半衰期)与收缩 k;k=0 时强度为 0 取每场 0.05 下限
STRENGTH_WINDOWS = ((10, 5.0), (20, 10.0), (38, 19.0))
STRENGTH_KS = (0.0, 5.0)
STRENGTH_FLOOR = 0.05
POS_GROUPS = ("GK", "CB", "FB", "DM", "CM", "AM", "W", "ST")


def rnd(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(float(x), nd)


def quantile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    pos = p * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def shrink(x: float, n: float, k: float, mu: float) -> float:
    return (n * x + k * mu) / (n + k)


# ------------------------------------------------------------------ 位置规则解码
def _wide(pid: int) -> bool:
    return pid % 10 in (1, 2, 8, 9)


def decode_formation(formation: str | None, pids: list[int]) -> dict[int, str] | None:
    """(阵型, 该阵型下 11 个 position_id) → {position_id: 8 组之一}。

    行 = position_id // 10(纵深),末位 = 横向(5 居中,1/2、8/9 为边路)。
    外场球员按 position_id 升序后按阵型字符串切成若干条线,再按线的位置和宽度
    定组。4-2-3-1 的 64/66(双后腰,且只有它的第一条中场线恰好 2 人)定为 DM。
    """
    if not formation:
        return None
    try:
        lines = [int(x) for x in formation.split("-")]
    except ValueError:
        return None
    gk = [p for p in pids if p // 10 == 1]
    outfield = sorted(p for p in pids if p // 10 != 1)
    if len(gk) != 1 or sum(lines) != len(outfield):
        return None
    out = {gk[0]: "GK"}
    chunks, i = [], 0
    for n in lines:
        chunks.append(outfield[i:i + n])
        i += n
    k = len(chunks)
    def_n = len(chunks[0])
    last_n = len(chunks[-1])
    for idx, ch in enumerate(chunks):
        n = len(ch)
        if idx == 0:
            for p in ch:
                out[p] = "FB" if n >= 4 and _wide(p) else "CB"
        elif idx == k - 1:
            for p in ch:
                if n <= 2:
                    out[p] = "ST"
                else:
                    out[p] = "ST" if p % 10 == 5 else "W"
        elif idx == 1:
            central = [p for p in ch if not _wide(p)]
            for p in ch:
                if _wide(p):
                    out[p] = "FB" if def_n == 3 else "W"
                elif len(central) == 1 or (n == 2 and k >= 4):
                    out[p] = "DM"
                else:
                    out[p] = "CM"
        else:
            for p in ch:
                if _wide(p) or (n == 3 and p % 10 != 5):
                    out[p] = "W"
                elif n >= 4:
                    out[p] = "CM"
                elif n == 3 or n == 1:
                    out[p] = "AM"
                else:
                    out[p] = "AM" if last_n == 1 else "CM"
    return out


# ------------------------------------------------------------------ 数据
@dataclass
class Raw:
    """按比赛分组的原始行。元组字段顺序见 load_raw 里的 SELECT。"""
    matches: list[dict]
    shots: dict[int, list[tuple]] = field(default_factory=dict)
    events: dict[int, list[tuple]] = field(default_factory=dict)
    lineups: dict[int, list[tuple]] = field(default_factory=dict)
    pstats: dict[int, list[tuple]] = field(default_factory=dict)
    team_names: dict[int, tuple] = field(default_factory=dict)
    player_names: dict[str, tuple] = field(default_factory=dict)


def _in(ids) -> str:
    return ",".join(str(int(x)) for x in ids) or "NULL"


def load_raw(core, leagues, seasons) -> Raw:
    rows = core.execute(
        f"""SELECT Match_ID, League_ID, Season, Date, kickoff_at_utc, kickoff_precision,
                   Home_Team_ID, Away_Team_ID, home_score, away_score, status, Match_Round
              FROM dim_match
             WHERE League_ID IN ({_in(leagues)}) AND Season IN ({",".join("?" * len(seasons))})""",
        tuple(seasons),
    ).fetchall()
    matches = []
    for r in rows:
        d = dict(r)
        d["sort_key"] = d["kickoff_at_utc"] or f"{d['Date']}T00:00:00Z"
        matches.append(d)
    matches.sort(key=lambda d: (d["sort_key"], d["Match_ID"]))
    raw = Raw(matches=matches)
    ids = _in(m["Match_ID"] for m in matches)

    for r in core.execute(
        f"""SELECT Match_ID, Team_ID, Player_ID, Situation, Shot_Type, xG, Outcome, Is_Own_Goal,
                   Minute, Minute_Added, Period
              FROM fact_shotmap WHERE Match_ID IN ({ids})"""
    ):
        raw.shots.setdefault(r[0], []).append(tuple(r[1:]))
    for r in core.execute(
        f"""SELECT Match_ID, event_type, card_type, minute, overload_time, is_home, minutes_added, extra_json,
                   home_score, away_score, event_index
              FROM fact_match_events WHERE Match_ID IN ({ids}) ORDER BY Match_ID, event_index"""
    ):
        own = None
        if r[1] == "Goal":
            try:
                own = json.loads(r[7] or "{}").get("ownGoal") is True
            except ValueError:
                own = False
        # (event_type, card_type, minute, overload_time, is_home, minutes_added, own_goal, home_score, away_score)
        raw.events.setdefault(r[0], []).append((r[1], r[2], r[3], r[4], r[5], r[6], own, r[8], r[9]))
    for r in core.execute(
        f"""SELECT Match_ID, Team_ID, formation, Player_ID, player_name, shirt_number, position_id,
                   usual_position_id, is_starter, extra_json
              FROM fact_match_lineup WHERE Match_ID IN ({ids})"""
    ):
        try:
            v = json.loads(r[9] or "{}").get("verticalLayout") or {}
        except ValueError:
            v = {}
        raw.lineups.setdefault(r[0], []).append(
            (r[1], r[2], str(r[3]), r[4], r[5], r[6], r[7], r[8], v.get("x"), v.get("y")))
    for r in core.execute(
        f"""SELECT Match_ID, Player_ID, minutes_played, expected_assists, rating_title, is_goalkeeper,
                   expected_goals_on_target_faced, goals_conceded
              FROM fact_player_match_stats WHERE Match_ID IN ({ids}) AND minutes_played > 0"""
    ):
        raw.pstats.setdefault(r[0], []).append((str(r[1]),) + tuple(r[2:]))

    team_ids = {m["Home_Team_ID"] for m in matches} | {m["Away_Team_ID"] for m in matches}
    for r in core.execute(f"SELECT Team_ID, name_zh, name_en FROM dim_team_i18n WHERE Team_ID IN ({_in(team_ids)})"):
        raw.team_names[r[0]] = (r[1], r[2])
    pids = sorted({row[2] for rows in raw.lineups.values() for row in rows})
    for i in range(0, len(pids), 900):
        chunk = pids[i:i + 900]
        for r in core.execute(
            f"""SELECT d.Player_ID, i.name_zh, i.name_zh_short, i.name_en, d.Player_Name
                  FROM dim_player d LEFT JOIN dim_player_i18n i USING(Player_ID)
                 WHERE d.Player_ID IN ({",".join("?" * len(chunk))})""",
            chunk,
        ):
            raw.player_names[str(r[0])] = (r[1], r[2], r[3], r[4])
    return raw


def truncate(raw: Raw, cutoff: str) -> Raw:
    """物理删除 Date ≥ cutoff 的比赛及其全部子行(截断测试)。静态维表(名字)不变。"""
    keep = [m for m in raw.matches if m["Date"] < cutoff]
    ids = {m["Match_ID"] for m in keep}
    pick = lambda d: {k: v for k, v in d.items() if k in ids}  # noqa: E731
    return Raw(matches=keep, shots=pick(raw.shots), events=pick(raw.events), lineups=pick(raw.lineups),
               pstats=pick(raw.pstats), team_names=raw.team_names, player_names=raw.player_names)


# ------------------------------------------------------------------ 参数计算
def build(
    raw: Raw,
    *,
    leagues,
    current_season: str,
    cutoff: str | None = None,
    team_scope: dict[int, int] | None = None,
    k_grid=None,
    extras: bool = False,
    extra_players: dict[str, tuple[int, int | None]] | None = None,
) -> dict:
    """返回 {leagues, formations, position_map, teams, players, diag}。

    cutoff:只用 Date < cutoff 的比赛(None = 全部)。
    team_scope:{team_id: league_id},要输出的球队;None = 当前赛季(cutoff 之前出现过)的全部球队。
    k_grid:额外输出 A_by_k / D_by_k(回测用)。extras:额外输出 xg10(基准模型)与位置组均值。
    extra_players:{player_id: (team_id, usual_position_id)},赛前首发里没有历史的球员也输出参数
                   (分钟为 0,取同位置均值;位置先看历史,没有就用给定的 usual_position)。
    """
    visible = [m for m in raw.matches if cutoff is None or m["Date"] < cutoff]
    by_id = {m["Match_ID"]: m for m in visible}
    finished = [m for m in visible if m["status"] == "Finish"]
    diag: dict = {"excluded_own_goal_shot_rows": 0, "unknown_situations": Counter()}

    # ---- 射门
    team_for = defaultdict(lambda: defaultdict(float))
    league_shot_xg = defaultdict(lambda: defaultdict(list))
    pen_att, pen_goal = Counter(), Counter()
    ply = defaultdict(Counter)
    for m in finished:
        lid = m["League_ID"]
        for (tid, pid, situation, shot_type, xg, outcome, own, *_rest) in raw.shots.get(m["Match_ID"], ()):
            if own == 1 or xg is None:
                diag["excluded_own_goal_shot_rows"] += 1
                continue
            c = SITUATION_CHANNEL.get(situation)
            if c is None:
                diag["unknown_situations"][situation] += 1
                continue
            team_for[(m["Match_ID"], tid)][c] += xg
            league_shot_xg[lid][c].append(float(xg))
            p = str(pid) if pid is not None else None
            if c == "penalty":
                pen_att[lid] += 1
                pen_goal[lid] += outcome == "Goal"
                if p:
                    ply[p]["pen_taken"] += 1
                    ply[p]["pen_scored"] += outcome == "Goal"
            elif p:
                ply[p]["npxg"] += xg
                ply[p]["shots"] += 1
                ply[p]["headers"] += shot_type == "Header"

    # ---- 事件
    red_team_matches = set()
    own_goals = Counter()
    goal_buckets = defaultdict(lambda: [0] * 6)
    added = defaultdict(lambda: {45: [], 90: []})
    for m in finished:
        lid = m["League_ID"]
        for (et, card, minute, _ov, is_home, madd, own, *_score) in raw.events.get(m["Match_ID"], ()):
            if et == "Card" and card in ("Red", "YellowRed"):
                red_team_matches.add((m["Match_ID"], is_home))
            elif et == "Goal":
                if own:
                    own_goals[lid] += 1
                mm = minute or 0
                b = min(max(mm - 1, 0) // 15, 2) if mm <= 45 else 3 + min((mm - 46) // 15, 2)
                goal_buckets[lid][b] += 1
            elif et == "AddedTime" and minute in (45, 90) and madd is not None:
                added[lid][minute].append(madd)

    # ---- 联赛参数
    n_fin = Counter(m["League_ID"] for m in finished)
    red_by_league = Counter(by_id[mid]["League_ID"] for mid, _ in red_team_matches)
    leagues_out = {}
    for lid in leagues:
        tm = 2 * n_fin[lid]
        if tm == 0:
            continue
        mu = {c: sum(league_shot_xg[lid][c]) / tm for c in CHANNELS}
        qs, exs = {}, {}
        for c in CHANNELS:
            vals = sorted(league_shot_xg[lid][c])
            qs[c] = [rnd(quantile(vals, p)) for p in QUANTILE_PROBS]
            exs[c] = rnd(sum(vals) / len(vals)) if vals else None
        gb = goal_buckets[lid]
        mean_b = sum(gb) / 6
        a45, a90 = added[lid][45], added[lid][90]
        goals_total = sum((m["home_score"] or 0) + (m["away_score"] or 0) for m in finished if m["League_ID"] == lid)
        leagues_out[str(lid)] = {
            "league_id": lid,
            "finished_matches": n_fin[lid],
            "goals_per_team_match": rnd(goals_total / tm),
            "xg_per_team_match": rnd(sum(mu.values())),
            "mu": {c: rnd(v) for c, v in mu.items()} | {"owngoal": rnd(own_goals[lid] / tm)},
            "shot_xg_quantiles": {"probs": list(QUANTILE_PROBS), **qs},
            "shot_xg_mean": exs,
            "shots_per_team_match": {c: rnd(len(league_shot_xg[lid][c]) / tm) for c in CHANNELS},
            "red_card_rate": rnd(red_by_league[lid] / tm),
            "penalty_rate": rnd(pen_att[lid] / tm),
            "penalty_conversion": rnd(pen_goal[lid] / pen_att[lid]) if pen_att[lid] else None,
            "own_goal_count": own_goals[lid],
            "goal_timing": {
                "buckets": ["1-15", "16-30", "31-45+", "46-60", "61-75", "76-90+"],
                "goals": gb,
                "factor": [rnd(g / mean_b) for g in gb] if mean_b else [1.0] * 6,
            },
            "stoppage_mean": {
                "first_half": rnd(sum(a45) / len(a45), 2) if a45 else DEFAULT_STOPPAGE[0],
                "second_half": rnd(sum(a90) / len(a90), 2) if a90 else DEFAULT_STOPPAGE[1],
                "samples": [len(a45), len(a90)],
            },
            "stoppage_distribution": {
                "first_half": {str(k): v for k, v in sorted(Counter(a45).items())},
                "second_half": {str(k): v for k, v in sorted(Counter(a90).items())},
            },
        }

    # ---- 阵容(所有 cutoff 之前的比赛)
    lineup_matches = [m for m in visible if m["Match_ID"] in raw.lineups]
    formation_sets = defaultdict(Counter)
    formation_coords = defaultdict(list)
    player_pid_counts = defaultdict(Counter)
    player_usual, player_shirt, player_lineup_name, player_last_seen = {}, {}, {}, {}
    player_last_team = {}
    # 当前赛季(cutoff 之前)每队出现过的球员;回退:该联赛当季尚无比赛时用上一赛季
    season_squads = defaultdict(lambda: defaultdict(set))  # season -> tid -> {pid}
    season_team_league = defaultdict(dict)                 # season -> tid -> lid
    for m in lineup_matches:
        rows = raw.lineups[m["Match_ID"]]
        by_team = defaultdict(list)
        for row in rows:
            by_team[row[0]].append(row)
        for tid, trows in by_team.items():
            starters = [r for r in trows if r[7] == 1 and r[5] is not None]
            formation = trows[0][1]
            pids = tuple(sorted(r[5] for r in starters))
            if formation and len(pids) == 11:
                formation_sets[formation][pids] += 1
            season_team_league[m["Season"]][tid] = m["League_ID"]
            for r in trows:
                p = r[2]
                prev = player_last_seen.get(p)
                latest = prev is None or m["sort_key"] >= prev
                if latest:
                    player_last_seen[p] = m["sort_key"]
                    player_last_team[p] = tid
                if r[6] is not None and (latest or p not in player_usual):
                    player_usual[p] = r[6]
                if r[4] not in (None, "") and (latest or p not in player_shirt):
                    player_shirt[p] = r[4]
                if r[3] and (latest or p not in player_lineup_name):
                    player_lineup_name[p] = r[3]
                season_squads[m["Season"]][tid].add(p)
            for r in starters:
                player_pid_counts[r[2]][(formation, r[5])] += 1
                if r[8] is not None and r[9] is not None:
                    formation_coords[(formation, r[5])].append((r[8], r[9]))

    # 同一阵型字符串可能出现不止一种 position_id 组合(变体);逐个组合按同一规则解码进同一张表,
    # 出现次数最多的组合优先,变体里同一 position_id 解码不同的计入 variant_conflicts。
    position_map: dict[str, dict[int, str]] = {}
    undecodable = []
    conflicts = 0
    for formation, cnt in formation_sets.items():
        for pids, _n in cnt.most_common():
            groups = decode_formation(formation, list(pids))
            if groups is None:
                continue
            m = position_map.setdefault(formation, {})
            for pid, g in groups.items():
                if pid not in m:
                    m[pid] = g
                elif m[pid] != g:
                    conflicts += 1
        if formation not in position_map:
            undecodable.append(formation)
    diag["undecodable_formations"] = undecodable
    diag["formation_variant_slot_sets"] = sum(len(c) - 1 for f, c in formation_sets.items() if f in position_map)
    diag["variant_decode_conflicts"] = conflicts

    def slot_group(formation, pid):
        return position_map.get(formation or "", {}).get(pid)

    formations_out = {}
    for formation, cnt in formation_sets.items():
        total = sum(cnt.values())
        if total < MIN_FORMATION_SAMPLES or formation not in position_map:
            continue
        pids, _ = cnt.most_common(1)[0]
        slots = []
        for pid in pids:
            xy = formation_coords.get((formation, pid)) or []
            slots.append({
                "position_id": pid,
                "x": rnd(sum(a for a, _ in xy) / len(xy), 3) if xy else None,
                "y": rnd(sum(b for _, b in xy) / len(xy), 3) if xy else None,
            })
        formations_out[formation] = {"samples": total, "slots": slots}

    def top_start(p):
        cnt = player_pid_counts.get(p)
        if not cnt:
            return None, None, 0
        by_pid = Counter()
        for (_, pid), n in cnt.items():
            by_pid[pid] += n
        top_pid, n = by_pid.most_common(1)[0]
        forms = Counter({f: k for (f, pid), k in cnt.items() if pid == top_pid})
        return forms.most_common(1)[0][0], top_pid, n

    def main_position(p, fallback_usual=None):
        cnt = player_pid_counts.get(p)
        if cnt:
            by_pid = Counter()
            for (_, pid), n in cnt.items():
                by_pid[pid] += n
            top_pid = by_pid.most_common(1)[0][0]
            forms = Counter({f: n for (f, pid), n in cnt.items() if pid == top_pid})
            for f, _ in forms.most_common():
                g = slot_group(f, top_pid)
                if g:
                    return g, "rule_decoded"
        u = player_usual.get(p, fallback_usual)
        if u in USUAL_FALLBACK:
            return USUAL_FALLBACK[u], "usual_position_fallback"
        return "CM", "default"

    # ---- 球队范围与名单
    def season_for_league(lid):
        """当前赛季该联赛在 cutoff 之前已有比赛就用当前赛季,否则用最近一个有比赛的赛季。"""
        if any(l == lid for l in season_team_league[current_season].values()):
            return current_season
        seasons = sorted(s for s, tl in season_team_league.items() if any(l == lid for l in tl.values()))
        return seasons[-1] if seasons else current_season

    if team_scope is None:
        team_scope = dict(season_team_league[current_season])
    team_league = dict(team_scope)
    team_hist = defaultdict(list)
    for m in finished:
        for tid, is_home in ((m["Home_Team_ID"], True), (m["Away_Team_ID"], False)):
            if team_league.get(tid) == m["League_ID"]:
                team_hist[tid].append((m, is_home))

    teams_out = {}
    for tid, lid in team_league.items():
        if str(lid) not in leagues_out:
            continue
        mu = leagues_out[str(lid)]["mu"]
        window = list(reversed(team_hist[tid][-TEAM_WINDOW:]))
        ws = [0.5 ** (i / HALF_LIFE) for i in range(len(window))]
        sw, sw2 = sum(ws), sum(w * w for w in ws)
        n_eff = (sw * sw / sw2) if sw2 else 0.0
        opp = lambda m, h: m["Away_Team_ID"] if h else m["Home_Team_ID"]  # noqa: E731
        raw_a = {c: (sum(w * team_for[(m["Match_ID"], tid)][c] for w, (m, _) in zip(ws, window)) / sw) if sw else None
                 for c in CHANNELS}
        raw_d = {c: (sum(w * team_for[(m["Match_ID"], opp(m, h))][c] for w, (m, h) in zip(ws, window)) / sw) if sw else None
                 for c in CHANNELS}

        def shrunk(k_team):
            A, D = {}, {}
            for c in CHANNELS:
                k = K_TEAM_PENALTY if c == "penalty" else k_team
                A[c] = shrink(raw_a[c], n_eff, k, mu[c]) if sw else mu[c]
                D[c] = shrink(raw_d[c], n_eff, k, mu[c]) if sw else mu[c]
            return {c: rnd(v) for c, v in A.items()}, {c: rnd(v) for c, v in D.items()}

        A, D = shrunk(K_TEAM)
        split = {"home": defaultdict(float), "away": defaultdict(float)}
        split_w = {"home": 0.0, "away": 0.0}
        split_n = Counter()
        for w, (m, h) in zip(ws, window):
            side = "home" if h else "away"
            split_w[side] += w
            split_n[side] += 1
            o = m["Away_Team_ID"] if h else m["Home_Team_ID"]
            for c in CHANNELS:
                split[side]["A_" + c] += w * team_for[(m["Match_ID"], tid)][c]
                split[side]["D_" + c] += w * team_for[(m["Match_ID"], o)][c]
        t = {
            "team_id": tid,
            "league_id": lid,
            "A": A,
            "D": D,
            "window_matches": len(window),
            "n_eff": rnd(n_eff, 3),
            "home_away_split_unshrunk": {
                side: {"matches": split_n[side], **{key: rnd(v / split_w[side]) for key, v in split[side].items()}}
                if split_w[side] else {"matches": 0}
                for side in ("home", "away")
            },
        }
        if k_grid:
            t["A_by_k"], t["D_by_k"] = {}, {}
            for k in k_grid:
                t["A_by_k"][str(k)], t["D_by_k"][str(k)] = shrunk(float(k))
        if extras:
            last10 = window
            fx = sum(sum(team_for[(m["Match_ID"], tid)].values()) for m, _ in last10)
            fa = sum(sum(team_for[(m["Match_ID"], opp(m, h))].values()) for m, h in last10)
            t["xg10"] = {"for": rnd(fx / len(last10)) if last10 else None,
                         "against": rnd(fa / len(last10)) if last10 else None, "n": len(last10)}
        # v0.3 第 7 条:强度回归特征(按渠道合计的总量;xG 与实际进球;k ∈ {0, 5})
        lg = leagues_out[str(lid)]
        mu_tot = {"xg": sum(mu[c] for c in CHANNELS), "goals": lg["goals_per_team_match"]}
        strength = {}
        for n_win, hl in STRENGTH_WINDOWS:
            win = list(reversed(team_hist[tid][-n_win:]))
            wts = [0.5 ** (i / hl) for i in range(len(win))]
            s1, s2 = sum(wts), sum(x * x for x in wts)
            ne = (s1 * s1 / s2) if s2 else 0.0
            per = {"xg": ([], []), "goals": ([], [])}
            for (m, h) in win:
                o = opp(m, h)
                per["xg"][0].append(sum(team_for[(m["Match_ID"], tid)].values()))
                per["xg"][1].append(sum(team_for[(m["Match_ID"], o)].values()))
                gf, ga = (m["home_score"], m["away_score"]) if h else (m["away_score"], m["home_score"])
                per["goals"][0].append(gf or 0)
                per["goals"][1].append(ga or 0)
            feat = {"n": len(win), "n_eff": rnd(ne, 3)}
            for basis in ("xg", "goals"):
                feat[basis] = {}
                for side, vals in (("A", per[basis][0]), ("D", per[basis][1])):
                    rawv = sum(w * v for w, v in zip(wts, vals)) / s1 if s1 else mu_tot[basis]
                    out_k = []
                    for k in STRENGTH_KS:
                        v = shrink(rawv, ne, k, mu_tot[basis]) if s1 else mu_tot[basis]
                        if v < STRENGTH_FLOOR:
                            v = STRENGTH_FLOOR
                            diag["strength_floor_hits"] = diag.get("strength_floor_hits", 0) + 1
                        out_k.append(rnd(v))
                    feat[basis][side] = out_k
            strength[str(n_win)] = feat
        t["strength"] = strength
        nm = raw.team_names.get(tid, (None, None))
        t["name_zh"] = nm[0] or nm[1]
        t["name_en"] = nm[1]
        teams_out[str(tid)] = t

    last_lineup = {}
    for tid in team_league:
        for m, _ in reversed(team_hist[tid]):
            rows = [r for r in raw.lineups.get(m["Match_ID"], ()) if r[0] == tid]
            starters = [r for r in rows if r[7] == 1 and r[5] is not None]
            if len(starters) != 11:
                continue
            formation = rows[0][1]
            last_lineup[tid] = {
                "match_id": m["Match_ID"],
                "date": m["Date"],
                "formation": formation,
                "formation_has_template": formation in formations_out,
                "formation_in_position_map": formation in position_map,
                "starters": [{"player_id": r[2], "position_id": r[5]} for r in sorted(starters, key=lambda r: r[5])],
            }
            break

    squads = {}
    for tid, lid in team_league.items():
        s = season_for_league(lid)
        members = set(season_squads[s].get(tid, set())) if season_team_league[s].get(tid) == lid else set()
        if tid in last_lineup:
            members |= {st["player_id"] for st in last_lineup[tid]["starters"]}
        squads[tid] = members
    for tid, t in teams_out.items():
        t["last_lineup"] = last_lineup.get(int(tid))
        t["squad"] = sorted(squads.get(int(tid), set()))

    # ---- 球员参数
    wanted = set()
    player_league, player_team = {}, {}
    for tid, ps in squads.items():
        wanted |= ps
        for p in ps:
            # 同时出现在两队名单里(赛季中转会):归到最近一次出场的球队
            if p not in player_team or player_last_team.get(p) == tid:
                player_league[p] = team_league[tid]
                player_team[p] = tid
    extra_usual = {}
    for p, (tid, usual) in (extra_players or {}).items():
        wanted.add(p)
        player_league[p] = team_league.get(tid, player_league.get(p))
        player_team[p] = tid
        extra_usual[p] = usual

    # 位置组均值的样本池:各联赛"当前赛季(没有则上一赛季)在 cutoff 之前出场过的全部球员",
    # 不随当天有哪些球队比赛而变化。
    pool_league = {}
    for lid in leagues:
        s = season_for_league(lid)
        for tid, l in season_team_league[s].items():
            if l == lid:
                for p in season_squads[s][tid]:
                    pool_league[p] = lid

    rating_hist = defaultdict(list)
    for m in finished:
        for (p, mins, xa, rating, is_gk, xgot, conceded) in raw.pstats.get(m["Match_ID"], ()):
            s = ply[p]
            s["minutes"] += mins
            if xa is not None:
                s["xa"] += xa
                s["xa_minutes"] += mins
            if rating is not None:
                rating_hist[p].append((m["sort_key"], rating, mins))
            if is_gk == 1 and xgot is not None and conceded is not None:
                s["gk_minutes"] += mins
                s["gk_xgot_faced"] += xgot
                s["gk_conceded"] += conceded

    everyone = wanted | set(pool_league)
    pos = {p: main_position(p, extra_usual.get(p)) for p in everyone}

    def per90(v, mins):
        return v * 90.0 / mins if mins > 0 else None

    raw_p = {}
    for p in everyone:
        s = ply[p]
        hist = sorted(rating_hist.get(p, []), reverse=True)[:PLAYER_RATING_WINDOW]
        r_min = sum(h[2] for h in hist)
        raw_p[p] = {
            "npxg90": per90(s["npxg"], s["minutes"]),
            "xa90": per90(s["xa"], s["xa_minutes"]),
            "shots90": per90(s["shots"], s["minutes"]),
            "headers90": per90(s["headers"], s["minutes"]),
            "r_raw": (sum(h[1] * h[2] for h in hist) / r_min) if r_min else None,
            "r_minutes": r_min,
            "g_raw": per90(s["gk_xgot_faced"] - s["gk_conceded"], s["gk_minutes"]),
        }

    def group_mean(lid, group, key, wkey):
        num = den = 0.0
        for p, l in pool_league.items():
            if l != lid or pos[p][0] != group or raw_p[p][key] is None:
                continue
            w = raw_p[p]["r_minutes"] if wkey == "r_minutes" else ply[p][wkey]
            num += raw_p[p][key] * w
            den += w
        return num / den if den else 0.0

    for lid in leagues:
        if str(lid) not in leagues_out:
            continue
        mins = sum(ply[p]["gk_minutes"] for p, l in pool_league.items() if l == lid)
        faced = sum(ply[p]["gk_xgot_faced"] for p, l in pool_league.items() if l == lid)
        leagues_out[str(lid)]["gk_xgot_faced_per90"] = rnd(faced * 90.0 / mins) if mins else None

    means = {}
    for lid in leagues:
        for g in POS_GROUPS:
            means[(lid, g)] = {
                "npxg90": group_mean(lid, g, "npxg90", "minutes"),
                "xa90": group_mean(lid, g, "xa90", "xa_minutes"),
                "r": group_mean(lid, g, "r_raw", "r_minutes"),
                "g": group_mean(lid, g, "g_raw", "gk_minutes"),
            }
        if extras and str(lid) in leagues_out:
            leagues_out[str(lid)]["position_means"] = {
                g: {k: rnd(v) for k, v in means[(lid, g)].items()} for g in POS_GROUPS}

    players_out = {}
    pos_source_count = Counter()
    for p in sorted(wanted):
        lid = player_league[p]
        g, src = pos[p]
        pos_source_count[src] += 1
        s, rw, mg = ply[p], raw_p[p], means[(lid, g)]
        npxg90 = rw["npxg90"] if rw["npxg90"] is not None else mg["npxg90"]
        xa90 = rw["xa90"] if rw["xa90"] is not None else mg["xa90"]
        a_raw = npxg90 + XA_DISCOUNT * xa90
        a_mu = mg["npxg90"] + XA_DISCOUNT * mg["xa90"]
        a_p = shrink(a_raw, s["minutes"], K_PLAYER_MIN, a_mu)
        r_p = shrink(rw["r_raw"], rw["r_minutes"], K_PLAYER_MIN, mg["r"]) if rw["r_raw"] is not None else mg["r"]
        g_p = None
        if g == "GK" or s["gk_minutes"] > 0:
            g_p = shrink(rw["g_raw"], s["gk_minutes"], K_GK_MIN, means[(lid, "GK")]["g"]) \
                if rw["g_raw"] is not None else means[(lid, "GK")]["g"]
        nm = raw.player_names.get(p, (None, None, None, None))
        tf, tpid, tn = top_start(p)
        players_out[p] = {
            "player_id": p,
            "name_zh": nm[1] or nm[0],
            "name_en": nm[2] or nm[3] or player_lineup_name.get(p),
            "team_id": player_team[p],
            "usual_position_id": player_usual.get(p, extra_usual.get(p)),
            "top_formation": tf,
            "top_position_id": tpid,
            "top_position_starts": tn,
            "starts": sum(player_pid_counts.get(p, Counter()).values()),
            "shirt_number": player_shirt.get(p),
            "main_position": g,
            "position_source": src,
            "position_unverified": True,
            "minutes": s["minutes"],
            "xa_minutes": s["xa_minutes"],
            "a_p": rnd(a_p),
            "npxg90": rnd(rw["npxg90"]),
            "xa90": rnd(rw["xa90"]),
            "shots90": rnd(rw["shots90"]),
            "headers90": rnd(rw["headers90"]),
            "penalties_taken": s["pen_taken"],
            "penalties_scored": s["pen_scored"],
            "r_p": rnd(r_p, 3),
            "r_minutes": rw["r_minutes"],
            "g_p": rnd(g_p),
            "gk_minutes": s["gk_minutes"],
        }
    diag["position_source"] = dict(pos_source_count)
    diag["unknown_situations"] = dict(diag["unknown_situations"])

    return {
        "leagues": leagues_out,
        "formations": formations_out,
        "position_map": {f: {str(pid): g for pid, g in sorted(m.items())} for f, m in sorted(position_map.items())},
        "teams": teams_out,
        "players": players_out,
        "diag": diag,
    }
