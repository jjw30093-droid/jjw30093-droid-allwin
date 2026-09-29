"""模拟器参数导出(只读)。口径:docs/simulator-model.md v0 + v0.1 变更记录。

只读打开三库之二(allwin.db / odds.db,sqlite URI mode=ro + query_only),
输出到仓库外目录。范围:英超 47、西甲 87,赛季 2025/2026 + 2026/2027。

用法(服务器):
  nice -n 19 python3 scripts/simulator/export_params.py \
      --data-dir /opt/allwin/shared/data --out-dir /opt/allwin/shared/exports/simulator

位置分组为规则解码(阵型字符串 + 格子行列),未经逐值校验,输出里标
position_unverified=true;Phase 2 之前换成校验过的解码表。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research" / "ah_signals"))

from common import load_xref, open_ro  # noqa: E402
from features_market import fit_poisson, implied_two, load_timelines, pick_close  # noqa: E402

MODEL_VERSION = "v0.1"
LEAGUES = (47, 87)
SEASONS = ("2025/2026", "2026/2027")
CURRENT_SEASON = "2026/2027"
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
FIXTURE_LOOKBACK_DAYS = 14
DEFAULT_STOPPAGE = (2.0, 5.0)
USUAL_FALLBACK = {0: "GK", 1: "CB", 2: "CM", 3: "ST"}
MIN_FORMATION_SAMPLES = 5


def parse_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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


# ------------------------------------------------------------------ 加载
def load_matches(core) -> list[dict]:
    rows = core.execute(
        f"""SELECT Match_ID, League_ID, Season, Date, kickoff_at_utc, kickoff_precision,
                   Home_Team_ID, Away_Team_ID, home_score, away_score, status
              FROM dim_match
             WHERE League_ID IN ({','.join(map(str, LEAGUES))}) AND Season IN (?,?)""",
        SEASONS,
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sort_key"] = d["kickoff_at_utc"] or f"{d['Date']}T00:00:00Z"
        out.append(d)
    out.sort(key=lambda d: (d["sort_key"], d["Match_ID"]))
    return out


def in_clause(ids) -> str:
    return ",".join(str(int(x)) for x in ids) or "NULL"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/opt/allwin/shared/data")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    if REPO in out_dir.resolve().parents or out_dir.resolve() == REPO:
        sys.exit("out-dir 必须在仓库外")
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    core = open_ro(data_dir / "allwin.db")
    odds = open_ro(data_dir / "odds.db")

    matches = load_matches(core)
    by_id = {m["Match_ID"]: m for m in matches}
    finished = [m for m in matches if m["status"] == "Finish"]
    fin_ids = [m["Match_ID"] for m in finished]
    fin_in = in_clause(fin_ids)
    diag: dict = {"excluded_own_goal_shot_rows": 0, "unknown_situations": Counter()}

    # ---- 射门(渠道 xG、球员射门)
    shots = core.execute(
        f"""SELECT Match_ID, Team_ID, Player_ID, Situation, Shot_Type, xG, Outcome, Is_Own_Goal
              FROM fact_shotmap WHERE Match_ID IN ({fin_in})"""
    ).fetchall()
    team_for = defaultdict(lambda: defaultdict(float))  # (mid, tid) -> c -> xG
    league_shot_xg = defaultdict(lambda: defaultdict(list))  # lid -> c -> [xG]
    pen_att = Counter()
    pen_goal = Counter()
    ply = defaultdict(lambda: Counter())  # pid -> stat -> value
    for r in shots:
        if r["Is_Own_Goal"] == 1 or r["xG"] is None:
            diag["excluded_own_goal_shot_rows"] += 1
            continue
        c = SITUATION_CHANNEL.get(r["Situation"])
        if c is None:
            diag["unknown_situations"][r["Situation"]] += 1
            continue
        m = by_id[r["Match_ID"]]
        lid = m["League_ID"]
        team_for[(r["Match_ID"], r["Team_ID"])][c] += r["xG"]
        league_shot_xg[lid][c].append(float(r["xG"]))
        pid = str(r["Player_ID"]) if r["Player_ID"] is not None else None
        if c == "penalty":
            pen_att[lid] += 1
            pen_goal[lid] += r["Outcome"] == "Goal"
            if pid:
                ply[pid]["pen_taken"] += 1
                ply[pid]["pen_scored"] += r["Outcome"] == "Goal"
        elif pid:
            ply[pid]["npxg"] += r["xG"]
            ply[pid]["shots"] += 1
            ply[pid]["headers"] += r["Shot_Type"] == "Header"

    # ---- 事件(红牌、乌龙、进球时间、补时)
    events = core.execute(
        f"""SELECT Match_ID, event_type, card_type, minute, overload_time, is_home,
                   minutes_added, extra_json
              FROM fact_match_events WHERE Match_ID IN ({fin_in})"""
    ).fetchall()
    red_team_matches = set()
    own_goals = Counter()
    goal_buckets = defaultdict(lambda: [0] * 6)
    added = defaultdict(lambda: {45: [], 90: []})
    for e in events:
        m = by_id[e["Match_ID"]]
        lid = m["League_ID"]
        et = e["event_type"]
        if et == "Card" and e["card_type"] in ("Red", "YellowRed"):
            red_team_matches.add((e["Match_ID"], e["is_home"]))
        elif et == "Goal":
            try:
                ex = json.loads(e["extra_json"] or "{}")
            except ValueError:
                ex = {}
            if ex.get("ownGoal") is True:
                own_goals[lid] += 1
            minute = e["minute"] or 0
            if minute <= 45:
                b = min(max(minute - 1, 0) // 15, 2)
            else:
                b = 3 + min((minute - 46) // 15, 2)
            goal_buckets[lid][b] += 1
        elif et == "AddedTime" and e["minute"] in (45, 90) and e["minutes_added"] is not None:
            added[lid][e["minute"]].append(e["minutes_added"])

    # ---- 联赛参数
    n_fin = Counter(m["League_ID"] for m in finished)
    red_by_league = Counter(by_id[mid]["League_ID"] for mid, _ in red_team_matches)
    leagues_out = {}
    for lid in LEAGUES:
        tm = 2 * n_fin[lid]
        mu = {c: sum(league_shot_xg[lid][c]) / tm for c in CHANNELS}
        qs, exs = {}, {}
        for c in CHANNELS:
            vals = sorted(league_shot_xg[lid][c])
            qs[c] = [rnd(quantile(vals, p)) for p in QUANTILE_PROBS]
            exs[c] = rnd(sum(vals) / len(vals)) if vals else None
        gb = goal_buckets[lid]
        mean_b = sum(gb) / 6
        a45, a90 = added[lid][45], added[lid][90]
        leagues_out[str(lid)] = {
            "league_id": lid,
            "finished_matches": n_fin[lid],
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
            # 每场模拟按此经验分布抽样补时(v0.2);键为补时分钟数,值为场次
            "stoppage_distribution": {
                "first_half": {str(k): v for k, v in sorted(Counter(a45).items())},
                "second_half": {str(k): v for k, v in sorted(Counter(a90).items())},
            },
        }

    # ---- 球队参数
    cur_teams = defaultdict(set)
    for m in matches:
        if m["Season"] == CURRENT_SEASON:
            cur_teams[m["League_ID"]].update((m["Home_Team_ID"], m["Away_Team_ID"]))
    team_league = {t: lid for lid, ts in cur_teams.items() for t in ts}
    team_hist = defaultdict(list)  # tid -> [(match, is_home)] 升序,只含本联赛
    for m in finished:
        for tid, is_home in ((m["Home_Team_ID"], True), (m["Away_Team_ID"], False)):
            if team_league.get(tid) == m["League_ID"]:
                team_hist[tid].append((m, is_home))

    teams_out = {}
    for tid, lid in team_league.items():
        mu = leagues_out[str(lid)]["mu"]
        window = list(reversed(team_hist[tid][-TEAM_WINDOW:]))
        ws = [0.5 ** (i / HALF_LIFE) for i in range(len(window))]
        sw, sw2 = sum(ws), sum(w * w for w in ws)
        n_eff = (sw * sw / sw2) if sw2 else 0.0
        A, D = {}, {}
        split = {"home": defaultdict(float), "away": defaultdict(float)}
        split_w = {"home": 0.0, "away": 0.0}
        split_n = Counter()
        for c in CHANNELS:
            fa = sum(w * team_for[(m["Match_ID"], tid)][c] for w, (m, _) in zip(ws, window))
            opp = lambda m, h: m["Away_Team_ID"] if h else m["Home_Team_ID"]  # noqa: E731
            fd = sum(w * team_for[(m["Match_ID"], opp(m, h))][c] for w, (m, h) in zip(ws, window))
            k = K_TEAM_PENALTY if c == "penalty" else K_TEAM
            A[c] = shrink(fa / sw, n_eff, k, mu[c]) if sw else mu[c]
            D[c] = shrink(fd / sw, n_eff, k, mu[c]) if sw else mu[c]
        for w, (m, h) in zip(ws, window):
            side = "home" if h else "away"
            split_w[side] += w
            split_n[side] += 1
            o = m["Away_Team_ID"] if h else m["Home_Team_ID"]
            for c in CHANNELS:
                split[side]["A_" + c] += w * team_for[(m["Match_ID"], tid)][c]
                split[side]["D_" + c] += w * team_for[(m["Match_ID"], o)][c]
        split_out = {
            side: {"matches": split_n[side],
                   **{key: rnd(v / split_w[side]) for key, v in split[side].items()}}
            if split_w[side] else {"matches": 0}
            for side in ("home", "away")
        }
        teams_out[str(tid)] = {
            "team_id": tid,
            "league_id": lid,
            "A": {c: rnd(v) for c, v in A.items()},
            "D": {c: rnd(v) for c, v in D.items()},
            "window_matches": len(window),
            "n_eff": rnd(n_eff, 3),
            "home_away_split_unshrunk": split_out,
        }

    tnames = {r["Team_ID"]: dict(r) for r in core.execute(
        f"SELECT Team_ID, name_zh, name_en FROM dim_team_i18n WHERE Team_ID IN ({in_clause(team_league)})")}
    for tid, t in teams_out.items():
        nm = tnames.get(int(tid), {})
        t["name_zh"] = nm.get("name_zh") or nm.get("name_en")
        t["name_en"] = nm.get("name_en")

    # ---- 阵容(位置映射表、阵型模板、最近一场首发、名单)
    lineup_rows = core.execute(
        f"""SELECT Match_ID, Team_ID, formation, Player_ID, player_name, shirt_number, position_id,
                   usual_position_id, is_starter, extra_json
              FROM fact_match_lineup WHERE Match_ID IN ({in_clause(by_id)})"""
    ).fetchall()
    team_match_lineup = defaultdict(list)
    for r in lineup_rows:
        team_match_lineup[(r["Match_ID"], r["Team_ID"])].append(r)

    formation_sets = defaultdict(Counter)
    formation_coords = defaultdict(list)
    player_pid_counts = defaultdict(Counter)  # player -> Counter[(formation, pid)]
    player_usual = {}
    player_shirt = {}
    player_lineup_name = {}
    player_last_seen = {}
    squads = defaultdict(set)
    for (mid, tid), rows in team_match_lineup.items():
        m = by_id[mid]
        starters = [r for r in rows if r["is_starter"] == 1 and r["position_id"] is not None]
        formation = rows[0]["formation"]
        pids = tuple(sorted(r["position_id"] for r in starters))
        if formation and len(pids) == 11:
            formation_sets[formation][pids] += 1
        for r in rows:
            p = str(r["Player_ID"])
            prev = player_last_seen.get(p)
            latest = prev is None or m["sort_key"] >= prev
            if latest:
                player_last_seen[p] = m["sort_key"]
            if r["usual_position_id"] is not None and (latest or p not in player_usual):
                player_usual[p] = r["usual_position_id"]
            if r["shirt_number"] not in (None, "") and (latest or p not in player_shirt):
                player_shirt[p] = r["shirt_number"]
            if r["player_name"] and (latest or p not in player_lineup_name):
                player_lineup_name[p] = r["player_name"]
            if m["Season"] == CURRENT_SEASON and team_league.get(tid) == m["League_ID"]:
                squads[tid].add(p)
        for r in starters:
            player_pid_counts[str(r["Player_ID"])][(formation, r["position_id"])] += 1
            try:
                v = json.loads(r["extra_json"] or "{}").get("verticalLayout") or {}
            except ValueError:
                v = {}
            if "x" in v and "y" in v:
                formation_coords[(formation, r["position_id"])].append((v["x"], v["y"]))

    # 单一映射表:(阵型, position_id) → 8 组。阵型模板、最近一场首发、球员主要位置全部只查这张表。
    position_map: dict[str, dict[int, str]] = {}
    undecodable_formations = []
    variant_sets = 0
    for formation, cnt in formation_sets.items():
        pids, _ = cnt.most_common(1)[0]
        groups = decode_formation(formation, list(pids))
        if groups is None:
            undecodable_formations.append(formation)
            continue
        position_map[formation] = groups
        variant_sets += len(cnt) - 1

    def slot_group(formation: str | None, pid: int) -> str | None:
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

    def top_start(p: str) -> tuple[str | None, int | None, int]:
        """出场次数最多的 position_id,及它最常出现的阵型、次数。"""
        cnt = player_pid_counts.get(p)
        if not cnt:
            return None, None, 0
        by_pid = Counter()
        for (_, pid), n in cnt.items():
            by_pid[pid] += n
        top_pid, n = by_pid.most_common(1)[0]
        forms = Counter({f: k for (f, pid), k in cnt.items() if pid == top_pid})
        return forms.most_common(1)[0][0], top_pid, n

    def main_position(p: str) -> tuple[str, str]:
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
        u = player_usual.get(p)
        if u in USUAL_FALLBACK:
            return USUAL_FALLBACK[u], "usual_position_fallback"
        return "CM", "default"

    last_lineup = {}
    for tid in team_league:
        hist = team_hist[tid]
        for m, _ in reversed(hist):
            rows = team_match_lineup.get((m["Match_ID"], tid)) or []
            starters = [r for r in rows if r["is_starter"] == 1 and r["position_id"] is not None]
            if len(starters) != 11:
                continue
            formation = rows[0]["formation"]
            last_lineup[tid] = {
                "match_id": m["Match_ID"],
                "date": m["Date"],
                "formation": formation,
                "formation_has_template": formation in formations_out,
                "formation_in_position_map": formation in position_map,
                "starters": [
                    {"player_id": str(r["Player_ID"]), "position_id": r["position_id"]}
                    for r in sorted(starters, key=lambda r: r["position_id"])
                ],
            }
            break
    for tid, t in teams_out.items():
        t["last_lineup"] = last_lineup.get(int(tid))
        t["squad"] = sorted(squads.get(int(tid), set()))

    # ---- 球员参数
    wanted = set()
    for s in squads.values():
        wanted |= s
    stat_rows = core.execute(
        f"""SELECT p.Match_ID, p.Player_ID, p.minutes_played, p.expected_assists, p.rating_title,
                   p.is_goalkeeper, p.expected_goals_on_target_faced, p.goals_conceded
              FROM fact_player_match_stats p
             WHERE p.Match_ID IN ({fin_in}) AND p.minutes_played > 0"""
    ).fetchall()
    rating_hist = defaultdict(list)
    for r in stat_rows:
        p = str(r["Player_ID"])
        mins = r["minutes_played"]
        s = ply[p]
        s["minutes"] += mins
        if r["expected_assists"] is not None:
            s["xa"] += r["expected_assists"]
            s["xa_minutes"] += mins
        if r["rating_title"] is not None:
            rating_hist[p].append((by_id[r["Match_ID"]]["sort_key"], r["rating_title"], mins))
        if r["is_goalkeeper"] == 1 and r["expected_goals_on_target_faced"] is not None \
                and r["goals_conceded"] is not None:
            s["gk_minutes"] += mins
            s["gk_xgot_faced"] += r["expected_goals_on_target_faced"]
            s["gk_conceded"] += r["goals_conceded"]

    player_league = {}
    player_team = {}
    for tid, ps in squads.items():
        for p in ps:
            player_league[p] = team_league[tid]
            player_team[p] = tid
    pos = {p: main_position(p) for p in wanted}

    def per90(v: float, mins: float) -> float | None:
        return v * 90.0 / mins if mins > 0 else None

    raw = {}
    for p in wanted:
        s = ply[p]
        hist = sorted(rating_hist.get(p, []), reverse=True)[:PLAYER_RATING_WINDOW]
        r_min = sum(h[2] for h in hist)
        raw[p] = {
            "npxg90": per90(s["npxg"], s["minutes"]),
            "xa90": per90(s["xa"], s["xa_minutes"]),
            "shots90": per90(s["shots"], s["minutes"]),
            "headers90": per90(s["headers"], s["minutes"]),
            "r_raw": (sum(h[1] * h[2] for h in hist) / r_min) if r_min else None,
            "r_minutes": r_min,
            "g_raw": per90(s["gk_xgot_faced"] - s["gk_conceded"], s["gk_minutes"]),
        }

    def group_mean(lid: int, group: str, key: str, wkey: str) -> float:
        num = den = 0.0
        for p in wanted:
            if player_league.get(p) != lid or pos[p][0] != group or raw[p][key] is None:
                continue
            w = raw[p]["r_minutes"] if wkey == "r_minutes" else ply[p][wkey]
            num += raw[p][key] * w
            den += w
        return num / den if den else 0.0

    gk_xgot90 = {}
    for lid in LEAGUES:
        mins = sum(ply[p]["gk_minutes"] for p in wanted if player_league.get(p) == lid)
        faced = sum(ply[p]["gk_xgot_faced"] for p in wanted if player_league.get(p) == lid)
        gk_xgot90[lid] = faced * 90.0 / mins if mins else None
        leagues_out[str(lid)]["gk_xgot_faced_per90"] = rnd(gk_xgot90[lid])

    means = {}
    for lid in LEAGUES:
        for g in ("GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"):
            means[(lid, g)] = {
                "npxg90": group_mean(lid, g, "npxg90", "minutes"),
                "xa90": group_mean(lid, g, "xa90", "xa_minutes"),
                "r": group_mean(lid, g, "r_raw", "r_minutes"),
                "g": group_mean(lid, g, "g_raw", "gk_minutes"),
            }

    pnames = {r["Player_ID"]: dict(r) for r in core.execute(
        f"""SELECT i.Player_ID, i.name_zh, i.name_zh_short, i.name_en, d.Player_Name
              FROM dim_player d LEFT JOIN dim_player_i18n i USING(Player_ID)
             WHERE d.Player_ID IN ({','.join("'" + p.replace("'", "''") + "'" for p in wanted) or "NULL"})""")}

    players_out = {}
    pos_source_count = Counter()
    for p in sorted(wanted):
        lid = player_league[p]
        g, src = pos[p]
        pos_source_count[src] += 1
        s, rw, mg = ply[p], raw[p], means[(lid, g)]
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
        nm = pnames.get(p, {})
        name_en = nm.get("name_en") or nm.get("Player_Name") or player_lineup_name.get(p)
        tf, tpid, tn = top_start(p)
        players_out[p] = {
            "player_id": p,
            "name_zh": nm.get("name_zh_short") or nm.get("name_zh"),
            "name_en": name_en,
            "team_id": player_team[p],
            "usual_position_id": player_usual.get(p),
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

    # ---- 一致性检查:最近一场首发放回它的实际阵型,主要位置 ≠ 所放位置(f_pos < 1)的比例
    consistency = []
    for tid, ll in last_lineup.items():
        rows_c = []
        for st in ll["starters"]:
            slot = slot_group(ll["formation"], st["position_id"])
            main = players_out[st["player_id"]]["main_position"] if st["player_id"] in players_out else None
            rows_c.append((st["player_id"], main, slot))
        mism = [r for r in rows_c if r[1] != r[2]]
        consistency.append({
            "team_id": tid,
            "name_zh": teams_out[str(tid)]["name_zh"],
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
    upcoming = [m for m in matches if m["status"] != "Finish" and m["kickoff_precision"] == "exact"
                and parse_utc(m["kickoff_at_utc"]) and parse_utc(m["kickoff_at_utc"]) > now]
    recent = [m for m in finished if m["kickoff_precision"] == "exact" and m["kickoff_at_utc"]
              and now - timedelta(days=FIXTURE_LOOKBACK_DAYS) <= parse_utc(m["kickoff_at_utc"]) <= now]
    cand = {m["Match_ID"]: m for m in upcoming + recent}
    ok_xref, _ = load_xref(odds, set(cand))
    skipped_inverted = [mid for mid, x in ok_xref.items() if x["home_away_inverted"]]
    pmid_to_mid = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items()
                   if not x["home_away_inverted"]}
    tl = load_timelines(odds, pmid_to_mid)
    fixtures_out = {}
    fixture_skip = Counter()
    for mid, m in cand.items():
        ko = parse_utc(m["kickoff_at_utc"])
        is_up = m["status"] != "Finish"
        snaps = {}
        for market in ("ah", "ou"):
            t = tl.get((mid, "Crown", market)) or []
            if is_up:
                past = [s for s in t if s["t"] <= now]
                snaps[market] = past[-1] if past else None
            else:
                snaps[market], _reason = pick_close(t, ko)
        if not snaps["ah"] and not snaps["ou"]:
            fixture_skip["no_crown_snap"] += 1
            continue
        entry = {
            "match_id": mid,
            "league_id": m["League_ID"],
            "kickoff_at_utc": m["kickoff_at_utc"],
            "home_team_id": m["Home_Team_ID"],
            "away_team_id": m["Away_Team_ID"],
            "status": "未开赛" if is_up else "已完赛（赛前盘口）",
            "final_score": None if is_up else [m["home_score"], m["away_score"]],
        }
        for market, (a, b) in (("ah", ("home", "away")), ("ou", ("over", "under"))):
            s = snaps[market]
            entry[market] = None if s is None else {
                "line": s["line"], a: s["v"][0], b: s["v"][1], "observed_at": fmt_utc(s["t"])}
        if snaps["ah"] and snaps["ou"]:
            p_ah = implied_two(*snaps["ah"]["v"])
            p_over = implied_two(*snaps["ou"]["v"])
            lh, la, ra, ro, p_win = fit_poisson(snaps["ah"]["line"], p_ah, snaps["ou"]["line"], p_over)
            entry["market_lambda"] = {"home": lh, "away": la, "resid_ah": rnd(ra), "resid_ou": rnd(ro),
                                      "p_ah_home": rnd(p_ah), "p_over": rnd(p_over)}
        else:
            entry["market_lambda"] = None
        fixtures_out[str(mid)] = entry

    # ---- 写出
    first_date = min((m["Date"] for m in finished), default=None)
    last_date = max((m["Date"] for m in finished), default=None)
    out = {
        "meta": {
            "generated_at": fmt_utc(now),
            "model_version": MODEL_VERSION,
            "uncalibrated": True,
            "spec": "docs/simulator-model.md (v0 + v0.1)",
            "leagues": list(LEAGUES),
            "seasons": list(SEASONS),
            "data_window": {"first_match_date": first_date, "last_match_date": last_date,
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
                "unknown_situations": dict(diag["unknown_situations"]),
                "position_source": dict(pos_source_count),
                "fixtures_skipped": dict(fixture_skip),
                "fixtures_skipped_inverted_xref": skipped_inverted,
                "players_without_minutes": sum(1 for p in players_out.values() if p["minutes"] == 0),
                "players_without_xa_minutes": sum(1 for p in players_out.values()
                                                  if p["minutes"] > 0 and p["xa_minutes"] == 0),
                "players_without_any_name": sum(1 for p in players_out.values()
                                                if not p["name_zh"] and not p["name_en"]),
                "undecodable_formations": undecodable_formations,
                "formation_variant_slot_sets": variant_sets,
                "lineup_consistency": {
                    "total_mismatch": total_mism,
                    "total_starters": total_n,
                    "share": rnd(total_mism / total_n) if total_n else None,
                    "teams": consistency,
                },
            },
        },
        "position_map": {f: {str(pid): g for pid, g in sorted(m.items())} for f, m in sorted(position_map.items())},
        "leagues": leagues_out,
        "formations": formations_out,
        "teams": teams_out,
        "players": players_out,
        "fixtures": fixtures_out,
    }
    path = out_dir / f"simulator_params_{now.strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size = path.stat().st_size
    print(json.dumps({
        "output": str(path),
        "bytes": size,
        "teams": len(teams_out),
        "teams_by_league": dict(Counter(t["league_id"] for t in teams_out.values())),
        "players": len(players_out),
        "formations": sorted(formations_out),
        "fixtures": dict(Counter(f["status"] for f in fixtures_out.values())),
        "fixtures_with_market_lambda": sum(1 for f in fixtures_out.values() if f["market_lambda"]),
        "diagnostics": out["meta"]["diagnostics"],
        "league_mu": {k: v["mu"] for k, v in leagues_out.items()},
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
