"""PREREG_phase4 的 M 市场族特征与 T 族目标(纯函数;加载器在底部)。

约定(与 PREREG_phase4 §2 一致):
- 各市场收盘 snap = observed_at ≤ kickoff−10min 的最后一条,距 kickoff >120min 记缺失;
  T-24h = ≤ kickoff−24h 最后一条;T-60min = ≤ kickoff−60min 最后一条;开盘 = initial。
- 水位异常(AH/OU:home+away ∉ [1.60,2.10] 或单边 <0.30;1x2:Σ1/odds ∉ [1.00,1.15])的 snap
  不参与取点。
- 档 = 0.25;线变正 = 向主队方向。
- 市场侧去水:p = 1/(1+w) 归一(1x2:1/odds 归一);模型侧覆盖概率用 EV 中性 p_eff。
- M6 的球员赛前滚动评分与 PREREG C9 同一定义(只用 kickoff 严格早于本场的比赛)。
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from common import CLOSE_CUTOFF_MIN, CLOSE_MAX_GAP_MIN, COMPANIES, WATER_MIN, WATER_SUM_RANGE
from features import C9_MIN_PLAYER_MATCHES, C9_MIN_VALID_PLAYERS, MAIN_WINDOW, ewma, parse_utc

TICK = 0.25
ALIGN_MIN = 15
MAX_GOALS = 10
LAMBDA_LO, LAMBDA_HI, LAMBDA_COARSE, LAMBDA_FINE = 0.10, 4.00, 0.10, 0.01

M_FEATURES = ("M1_open_to_close", "M2_t24_to_close", "M3_water_move", "M4a_line_gap", "M4b_prob_gap",
              "M5_poisson_vs_1x2", "M6_lineup_surprise")
FIELDS = {"ah": ("home", "away"), "ou": ("over", "under"), "1x2": ("home", "draw", "away")}


# ------------------------------------------------------------------ 概率工具
def water_ok(market: str, vals: tuple) -> bool:
    if market == "1x2":
        s = sum(1.0 / v for v in vals)
        return 1.00 <= s <= 1.15
    return WATER_SUM_RANGE[0] <= vals[0] + vals[1] <= WATER_SUM_RANGE[1] and min(vals) >= WATER_MIN


def implied_two(w_a: float, w_b: float) -> float:
    pa, pb = 1.0 / (1.0 + w_a), 1.0 / (1.0 + w_b)
    return pa / (pa + pb)


def implied_1x2_home(o_h: float, o_d: float, o_a: float) -> float:
    ph, pd, pa = 1.0 / o_h, 1.0 / o_d, 1.0 / o_a
    return ph / (ph + pd + pa)


def _pmf(lam: float) -> list[float]:
    out = [math.exp(-lam)]
    for k in range(1, MAX_GOALS + 1):
        out.append(out[-1] * lam / k)
    return out


_PMF_CACHE: dict[int, list[float]] = {}


def pmf_cached(lam: float) -> list[float]:
    key = round(lam * 100)
    if key not in _PMF_CACHE:
        _PMF_CACHE[key] = _pmf(key / 100.0)
    return _PMF_CACHE[key]


def margin_dist(ph: list[float], pa: list[float]) -> dict[int, float]:
    d: dict[int, float] = defaultdict(float)
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            d[i - j] += x * y
    return d


def total_dist(ph: list[float], pa: list[float]) -> dict[int, float]:
    d: dict[int, float] = defaultdict(float)
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            d[i + j] += x * y
    return d


def _single(v: float, line: float) -> int:
    adj = v - line
    return 0 if abs(adj) < 1e-9 else (1 if adj > 0 else -1)


def p_eff(dist: dict[int, float], line: float) -> float:
    """EV 中性覆盖概率:(Wf + Wh/2)/(Wf + Wh/2 + Lf + Lh/2);dist 为整数结果(净胜球或总进球)分布,
    line 为 0.25 整数倍;'覆盖' = 结果 − line > 0。"""
    q = round(line / TICK)
    if q % 2 == 0:
        lines = (line,)
    else:
        lines = (line - TICK, line + TICK)
    wf = wh = lf = lh = 0.0
    for v, p in dist.items():
        r = [_single(v, l) for l in lines]
        if len(r) == 1:
            if r[0] > 0:
                wf += p
            elif r[0] < 0:
                lf += p
        else:
            s = r[0] + r[1]
            if s == 2:
                wf += p
            elif s == 1:
                wh += p
            elif s == -1:
                lh += p
            elif s == -2:
                lf += p
    den = wf + wh / 2 + lf + lh / 2
    return (wf + wh / 2) / den if den > 0 else float("nan")


_FIT_CACHE: dict[tuple, tuple] = {}


def fit_poisson(ah_line: float, p_ah_home: float, ou_line: float, p_over: float):
    """纯函数,按输入元组缓存(截断测试重算时同一输入必然同一输出;缓存只省时间,不改变语义)。"""
    key = (ah_line, p_ah_home, ou_line, p_over)
    if key not in _FIT_CACHE:
        _FIT_CACHE[key] = _fit_poisson(ah_line, p_ah_home, ou_line, p_over)
    return _FIT_CACHE[key]


def _fit_poisson(ah_line: float, p_ah_home: float, ou_line: float, p_over: float):
    """网格搜索 λ_h, λ_a ∈ [0.10,4.00]:先 0.10 粗网格,再在最优点 ±0.10 内 0.01 细网格
    (目标函数光滑单峰,等价于全 0.01 网格)。返回 (λ_h, λ_a, resid_ah, resid_ou, p_home_win)。"""
    def obj(lh, la):
        ph, pa = pmf_cached(lh), pmf_cached(la)
        md, td = margin_dist(ph, pa), total_dist(ph, pa)
        ra = p_eff(md, ah_line) - p_ah_home
        ro = p_eff(td, ou_line) - p_over
        return ra * ra + ro * ro, ra, ro, md

    best = None
    n_coarse = int(round((LAMBDA_HI - LAMBDA_LO) / LAMBDA_COARSE)) + 1
    for i in range(n_coarse):
        lh = round(LAMBDA_LO + i * LAMBDA_COARSE, 2)
        for j in range(n_coarse):
            la = round(LAMBDA_LO + j * LAMBDA_COARSE, 2)
            val = obj(lh, la)
            if best is None or val[0] < best[0]:
                best = (val[0], lh, la)
    _, ch, ca = best
    best = None
    steps = int(round(LAMBDA_COARSE / LAMBDA_FINE))
    for i in range(-steps, steps + 1):
        lh = round(ch + i * LAMBDA_FINE, 2)
        if lh < LAMBDA_LO or lh > LAMBDA_HI:
            continue
        for j in range(-steps, steps + 1):
            la = round(ca + j * LAMBDA_FINE, 2)
            if la < LAMBDA_LO or la > LAMBDA_HI:
                continue
            val = obj(lh, la)
            if best is None or val[0] < best[0]:
                best = (val[0], lh, la, val[1], val[2], val[3])
    _, lh, la, ra, ro, md = best
    p_home_win = sum(p for d, p in md.items() if d > 0)
    return lh, la, ra, ro, p_home_win


# ------------------------------------------------------------------ 取点
def _last_at_or_before(tl: list[dict], cutoff: datetime):
    best = None
    for s in tl:
        if s["t"] <= cutoff:
            best = s
        else:
            break
    return best


def pick_close(tl, ko):
    s = _last_at_or_before(tl, ko - timedelta(minutes=CLOSE_CUTOFF_MIN))
    if s is None:
        return None, "no_snap"
    if (ko - s["t"]).total_seconds() / 60.0 > CLOSE_MAX_GAP_MIN:
        return None, "gap_gt_120"
    return s, "ok"


def pick_t24(tl, ko):
    if not tl or tl[0]["t"] > ko - timedelta(hours=24):
        return None
    return _last_at_or_before(tl, ko - timedelta(hours=24))


def pick_t60(tl, ko):
    return _last_at_or_before(tl, ko - timedelta(minutes=60))


def pick_initial(tl):
    for s in tl:
        if s.get("init") is not None:
            return s["init"]
    return None


def aligned(*snaps) -> bool:
    ts = [s["t"] for s in snaps]
    return all(abs((a - b).total_seconds()) <= ALIGN_MIN * 60 for a in ts for b in ts)


# ------------------------------------------------------------------ 主构造
def build_market_features(matches: list[dict], tl: dict, lineups: dict, player_rows: list[dict]) -> dict[int, dict]:
    """tl: {(mid, company, market): [snap...]},snap = dict(t=datetime, v=tuple(values), line=float|None, init=...)
    返回 {mid: {feature: value, ..., '_reason__X': str}}。"""
    ms = sorted(matches, key=lambda m: (m["kickoff_at_utc"], m["Match_ID"]))
    mids = {m["Match_ID"] for m in ms}

    # ---- 球员赛前滚动评分(与 features.py C9 同定义)
    pmin = {}
    for r in player_rows:
        if r["Match_ID"] in mids:
            pmin[(r["Match_ID"], str(r["Player_ID"]))] = r["minutes_played"]
    phist: dict[str, list] = defaultdict(list)
    prev_xi: dict[int, list] = defaultdict(list)   # tid -> [(ko, mid, starter_ids)]
    for m in ms:
        mid, ko = m["Match_ID"], parse_utc(m["kickoff_at_utc"])
        for tid in (m["Home_Team_ID"], m["Away_Team_ID"]):
            lu = lineups.get((mid, tid), [])
            for p in lu:
                pid = str(p["Player_ID"])
                mins = pmin.get((mid, pid))
                phist[pid].append((ko, p["rating"], None if mins is None else float(mins)))
            prev_xi[tid].append((ko, mid, {str(p["Player_ID"]) for p in lu if p["is_starter"]}))
    for pid in phist:
        phist[pid].sort(key=lambda x: x[0])

    def player_rolling(pid, before):
        seq = [(r, mins) for (ko, r, mins) in phist.get(pid, []) if ko < before and r is not None
               and mins is not None and mins > 0]
        if len(seq) < C9_MIN_PLAYER_MATCHES:
            return None
        return ewma([r for r, _ in seq], MAIN_WINDOW, min_n=C9_MIN_PLAYER_MATCHES, weights=[mn for _, mn in seq])

    def xi_mean(ids, before):
        vals = [player_rolling(pid, before) for pid in ids]
        vals = [v for v in vals if v is not None]
        return (sum(vals) / len(vals), len(vals)) if len(vals) >= C9_MIN_VALID_PLAYERS else (None, len(vals))

    out = {}
    for m in ms:
        mid, ko = m["Match_ID"], parse_utc(m["kickoff_at_utc"])
        raw = m["home_score"] - m["away_score"]
        f: dict = {}
        ca = tl.get((mid, "Crown", "ah"), [])
        ba = tl.get((mid, "Bet365", "ah"), [])
        bo = tl.get((mid, "Bet365", "ou"), [])
        b1 = tl.get((mid, "Bet365", "1x2"), [])
        c_close, c_reason = pick_close(ca, ko)
        c_t24, c_t60, c_init = pick_t24(ca, ko), pick_t60(ca, ko), pick_initial(ca)
        b_close, b_reason = pick_close(ba, ko)
        b_t24 = pick_t24(ba, ko)
        o_close, o_reason = pick_close(bo, ko)
        x_close, x_reason = pick_close(b1, ko)

        f["crown_close_line"] = c_close["line"] if c_close else None
        f["crown_close_at"] = c_close["t"].strftime("%Y-%m-%dT%H:%M:%SZ") if c_close else None
        f["line_T24"] = c_t24["line"] if c_t24 else None
        f["line_T60"] = c_t60["line"] if c_t60 else None
        f["b365_close_line"] = b_close["line"] if b_close else None
        f["b365_t24_line"] = b_t24["line"] if b_t24 else None
        f["margin"] = raw - c_close["line"] if c_close else None
        f["margin_b365"] = raw - b_close["line"] if b_close else None
        f["margin_T24"] = raw - c_t24["line"] if c_t24 else None
        f["margin_b365_T24"] = raw - b_t24["line"] if b_t24 else None
        f["dline_raw"] = (c_close["line"] - c_t24["line"]) if (c_close and c_t24) else None
        f["dline"] = f["dline_raw"] / TICK if f["dline_raw"] is not None else None
        f["dline_b365"] = ((b_close["line"] - b_t24["line"]) / TICK) if (b_close and b_t24) else None
        f["dline_60"] = ((c_close["line"] - c_t60["line"]) / TICK) if (c_close and c_t60) else None
        f["t24_home_w"], f["t24_away_w"] = (c_t24["v"][0], c_t24["v"][1]) if c_t24 else (None, None)
        f["close_home_w"], f["close_away_w"] = (c_close["v"][0], c_close["v"][1]) if c_close else (None, None)

        # M1 / M2
        f["M1_open_to_close"] = ((c_close["line"] - c_init["line"]) / TICK) if (c_close and c_init) else None
        f["_reason__M1"] = "ok" if f["M1_open_to_close"] is not None else ("no_close:" + c_reason if not c_close else "no_initial")
        f["M2_t24_to_close"] = f["dline"]
        f["_reason__M2"] = "ok" if f["dline"] is not None else ("no_close:" + c_reason if not c_close else "no_t24")
        # M3
        if c_close and c_t24:
            moved = abs(c_close["line"] - c_t24["line"]) > 1e-9
            f["I_move"] = 1.0 if moved else 0.0
            f["M3_water_move"] = 0.0 if moved else implied_two(*c_close["v"]) - implied_two(*c_t24["v"])
            f["_reason__M3"] = "ok" if not moved else "ok(线变,Δp=0)"
        else:
            f["I_move"] = None; f["M3_water_move"] = None
            f["_reason__M3"] = f["_reason__M2"]
        # M4
        if c_close and b_close:
            if aligned(c_close, b_close):
                f["M4a_line_gap"] = (c_close["line"] - b_close["line"]) / TICK
                f["_reason__M4a"] = "ok"
                if abs(f["M4a_line_gap"]) < 1e-9:
                    f["M4b_prob_gap"] = implied_two(*c_close["v"]) - implied_two(*b_close["v"])
                    f["_reason__M4b"] = "ok"
                else:
                    f["M4b_prob_gap"] = None; f["_reason__M4b"] = "not_same_line"
            else:
                f["M4a_line_gap"] = None; f["M4b_prob_gap"] = None
                f["_reason__M4a"] = f["_reason__M4b"] = "not_aligned_15min"
        else:
            f["M4a_line_gap"] = None; f["M4b_prob_gap"] = None
            f["_reason__M4a"] = f["_reason__M4b"] = ("no_crown_close:" + c_reason) if not c_close else ("no_b365_close:" + b_reason)
        # M5
        if b_close and o_close and x_close:
            if aligned(b_close, o_close, x_close):
                p_ah = implied_two(*b_close["v"])
                p_over = implied_two(*o_close["v"])
                lh, la, ra, ro, p_win = fit_poisson(b_close["line"], p_ah, o_close["line"], p_over)
                f["M5_poisson_vs_1x2"] = p_win - implied_1x2_home(*x_close["v"])
                f["M5_lambda_h"], f["M5_lambda_a"], f["M5_resid_ah"], f["M5_resid_ou"] = lh, la, ra, ro
                f["_reason__M5"] = "ok" if max(abs(ra), abs(ro)) <= 0.02 else "ok(拟合不佳>0.02)"
            else:
                f["M5_poisson_vs_1x2"] = None; f["_reason__M5"] = "not_aligned_15min"
        else:
            f["M5_poisson_vs_1x2"] = None
            f["_reason__M5"] = "no_b365_ah:" + b_reason if not b_close else ("no_b365_ou:" + o_reason if not o_close else "no_b365_1x2:" + x_reason)
        # M6
        vals = {}
        for side, tid in (("home", m["Home_Team_ID"]), ("away", m["Away_Team_ID"])):
            actual = {str(p["Player_ID"]) for p in lineups.get((mid, tid), []) if p["is_starter"]}
            prev = [x for x in prev_xi[tid] if x[0] < ko]
            if not actual:
                vals[side] = (None, "no_lineup")
                continue
            if not prev:
                vals[side] = (None, "no_prev_match")
                continue
            a_mean, a_n = xi_mean(actual, ko)
            p_mean, p_n = xi_mean(prev[-1][2], ko)
            if a_mean is None or p_mean is None:
                vals[side] = (None, f"valid_lt_8(actual={a_n},prev={p_n})")
            else:
                vals[side] = (a_mean - p_mean, "ok")
        if vals["home"][0] is not None and vals["away"][0] is not None:
            f["M6_lineup_surprise"] = vals["home"][0] - vals["away"][0]
            f["M6_home"], f["M6_away"] = vals["home"][0], vals["away"][0]
            f["_reason__M6"] = "ok"
        else:
            f["M6_lineup_surprise"] = None
            f["_reason__M6"] = ";".join(f"{s}:{v[1]}" for s, v in vals.items() if v[0] is None)
        out[mid] = f
    return out


# ------------------------------------------------------------------ 加载器
def load_timelines(odds: sqlite3.Connection, pmid_to_mid: dict[str, int]) -> dict:
    """{(mid, company, market): [snap]},snap=dict(t, v=tuple, line, init=dict|None);已过滤水位异常与非数值。"""
    rows = odds.execute(
        """SELECT provider_match_id, company_id, market, observed_at, payload_json
             FROM bronze_ng_odds_snap
            WHERE market IN ('ah','ou','1x2') AND company_id IN ('8','281','3')
            ORDER BY observed_at ASC, id ASC"""
    ).fetchall()
    out = defaultdict(list)
    for pmid, cid, market, obs, pj in rows:
        mid = pmid_to_mid.get(str(pmid))
        ck = COMPANIES.get(str(cid))
        if mid is None or ck is None:
            continue
        try:
            p = json.loads(pj)
        except ValueError:
            continue
        if not isinstance(p, dict):
            continue
        lat = p.get("latest") if isinstance(p.get("latest"), dict) else p
        ini = p.get("initial") if isinstance(p.get("initial"), dict) else None
        vals = tuple(lat.get(k) for k in FIELDS[market])
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 for v in vals):
            continue
        vals = tuple(float(v) for v in vals)
        if not water_ok(market, vals):
            continue
        line = lat.get("line") if market != "1x2" else None
        if market != "1x2" and not isinstance(line, (int, float)):
            continue
        init = None
        if ini is not None:
            iv = tuple(ini.get(k) for k in FIELDS[market])
            il = ini.get("line") if market != "1x2" else None
            if all(isinstance(v, (int, float)) for v in iv) and (market == "1x2" or isinstance(il, (int, float))):
                init = dict(line=None if il is None else round(float(il), 2), v=tuple(float(v) for v in iv))
        out[(mid, ck, market)].append(dict(t=parse_utc(obs), v=vals, line=None if line is None else round(float(line), 2), init=init))
    return out
