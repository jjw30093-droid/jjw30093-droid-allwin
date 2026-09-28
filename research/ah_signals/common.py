"""research/ah_signals 公共常量、只读加载器与结算函数。

所有数据库访问必须经 `open_ro()`(sqlite3 URI `mode=ro` + PRAGMA query_only),
零写入生产库。

Phase 0 之后的锁定口径(任务书补充决定,2026-09-28):
- 主口径公司 Crown;Bet365 为收盘线稳健性口径;Macauslot 只在 Phase 4 公司间
  分歧中使用。
- 收盘线主定义:observed_at ≤ kickoff−10min 的最后一条 AH snap;该 snap 距
  kickoff > 120min 记为缺失。敏感性定义:开球前真实最后一条 snap。
- T-24h:observed_at ≤ kickoff−24h 的最后一条;第一条 snap 晚于 kickoff−24h 记缺失。
- 符号:库内 line>0 = 主让;margin = (home−away) − line。
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

LEAGUES = (47, 53, 54, 55, 87)
LEAGUE_NAME = {47: "英超", 53: "法甲", 54: "德甲", 55: "意甲", 87: "西甲"}
SEASONS = ("2025/2026", "2026/2027")
MARKETS = ("ah", "ou", "1x2")

COMPANIES = {"1": "Macauslot", "80": "Macauslot", "8": "Bet365", "281": "Bet365", "3": "Crown"}
COMPANY_IDS = {"Macauslot": ("1", "80"), "Bet365": ("8", "281"), "Crown": ("3",)}
PRIMARY = "Crown"

CLOSE_CUTOFF_MIN = 10       # 主定义:kickoff−10min 之前的最后一条
CLOSE_MAX_GAP_MIN = 120     # 超过则收盘线记缺失
T24_HOURS = 24

# Bet365 单场数据异常(Phase 1 核验:Malaga vs Deportivo 26/27,latest 1.75@5.25/0.12,
# 来源侧异常,见 docs/data-sources.md §2.6);只从 Bet365 稳健性检验剔除,Crown 主分析保留
BET365_EXCLUDE_MIDS = frozenset({5868027})

# 水位合理性(PREREG 数据清洗项):亚盘两边水位之和正常在 1.7~2.0,单边 <0.3 视为异常
WATER_SUM_RANGE = (1.60, 2.10)
WATER_MIN = 0.30

# 25/26 升班马常量(任务书给定,按库内英文队名匹配,匹配不上的列出)
PROMOTED_2526 = {
    47: ("Leeds United", "Burnley", "Sunderland"),
    87: ("Levante", "Elche", "Real Oviedo"),
    55: ("Sassuolo", "Pisa", "Cremonese"),
    54: ("1. FC Köln", "Hamburger SV"),
    53: ("Lorient", "Paris FC", "Metz"),
}


# ------------------------------------------------------------------ 基础
def open_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def quantiles(values, probs=(0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)) -> list[float]:
    vals = sorted(values)
    if not vals:
        return [math.nan] * len(probs)
    out = []
    n = len(vals)
    for p in probs:
        k = (n - 1) * p
        f = int(math.floor(k))
        c = min(f + 1, n - 1)
        out.append(vals[f] if f == c else vals[f] + (vals[c] - vals[f]) * (k - f))
    return out


def mean_ci95(values) -> tuple[float, float, float, int]:
    """返回 (mean, lo, hi, n),正态近似;n<2 时 CI 为 NaN。"""
    n = len(values)
    if n == 0:
        return math.nan, math.nan, math.nan, 0
    m = sum(values) / n
    if n < 2:
        return m, math.nan, math.nan, n
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    se = math.sqrt(var / n)
    return m, m - 1.96 * se, m + 1.96 * se, n


def pearson(xs, ys) -> float:
    n = len(xs)
    if n < 3:
        return math.nan
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else math.nan


# ------------------------------------------------------------------ 结算
def settle_home(margin_raw: int, line: float) -> float:
    """本研究独立实现的亚盘结算(主队视角),返回 ∈ {-1,-0.5,0,0.5,1}。
    line>0 = 主让。四分之一盘拆成相邻两个半仓各判一次再平均。
    与 backend/commands/reco_settlement_math.resolve_leg_result 逐场比对见 Phase 1。"""
    q = round(line / 0.25)
    if abs(line / 0.25 - q) > 1e-6:
        raise ValueError(f"line={line} 不是 0.25 的整数倍")

    def single(l: float) -> float:
        adj = margin_raw - l
        if abs(adj) < 1e-9:
            return 0.0
        return 1.0 if adj > 0 else -1.0

    if q % 2 == 0:          # 整数盘或半球盘
        return single(line)
    return (single(line - 0.25) + single(line + 0.25)) / 2.0


RECO_TO_NUM = {"win": 1.0, "half_win": 0.5, "push": 0.0, "half_loss": -0.5, "lose": -1.0}


# ------------------------------------------------------------------ 加载
def load_matches(core: sqlite3.Connection) -> list[dict]:
    rows = core.execute(
        f"""SELECT Match_ID, League_ID, Season, Match_Round, kickoff_at_utc, Date,
                   Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name,
                   home_score, away_score
              FROM dim_match
             WHERE League_ID IN ({','.join(str(x) for x in LEAGUES)})
               AND Season IN (?,?) AND status='Finish'
               AND kickoff_precision='exact' AND kickoff_at_utc IS NOT NULL
             ORDER BY kickoff_at_utc, Match_ID""",
        SEASONS,
    ).fetchall()
    return [dict(r) for r in rows]


def load_xref(odds: sqlite3.Connection, mids: set[int]) -> tuple[dict[int, dict], list[dict]]:
    """返回 (ok_xref: mid -> row, needs_review: [row...]),只取 provider='nowgoal'。"""
    rows = odds.execute("SELECT * FROM dim_match_xref WHERE provider='nowgoal'").fetchall()
    ok, nr = {}, []
    for r in rows:
        d = dict(r)
        mid = int(d["fotmob_match_id"])
        if mid not in mids:
            continue
        if d["review_status"] in ("auto_ok", "confirmed"):
            ok[mid] = d
        else:
            nr.append(d)
    return ok, nr


def _parse_payload(pj: str):
    """返回 (shape, latest_line, latest_home, latest_away, init_line, init_home, init_away)。"""
    try:
        p = json.loads(pj)
    except (TypeError, ValueError):
        return None
    if not isinstance(p, dict):
        return None
    if "initial" in p or "latest" in p:
        lat = p.get("latest") if isinstance(p.get("latest"), dict) else {}
        ini = p.get("initial") if isinstance(p.get("initial"), dict) else {}
        return ("nested", lat.get("line"), lat.get("home"), lat.get("away"),
                ini.get("line"), ini.get("home"), ini.get("away"))
    return ("flat", p.get("line"), p.get("home"), p.get("away"), None, None, None)


def load_ah_timelines(odds: sqlite3.Connection, pmid_to_mid: dict[str, int],
                      market: str = "ah") -> dict[tuple[int, str], list[dict]]:
    """(mid, company_key) -> 按 observed_at 升序的 snap 列表。
    每条: observed_at, shape, line, home, away, init_line, init_home, init_away。
    只收 line 为数值的行;Macauslot 两个 id 合并,Bet365 8/281 合并。"""
    rows = odds.execute(
        """SELECT provider_match_id, company_id, observed_at, payload_json
             FROM bronze_ng_odds_snap
            WHERE market=? AND company_id IN ('1','80','8','281','3')
            ORDER BY observed_at ASC, id ASC""",
        (market,),
    ).fetchall()
    out: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for pmid, cid, obs, pj in rows:
        mid = pmid_to_mid.get(str(pmid))
        if mid is None:
            continue
        ck = COMPANIES.get(str(cid))
        if ck is None:
            continue
        parsed = _parse_payload(pj)
        if parsed is None:
            continue
        shape, line, home, away, il, ih, ia = parsed
        if not isinstance(line, (int, float)):
            continue
        out[(mid, ck)].append(dict(
            observed_at=obs, shape=shape, line=round(float(line), 2),
            home=float(home) if isinstance(home, (int, float)) else None,
            away=float(away) if isinstance(away, (int, float)) else None,
            init_line=round(float(il), 2) if isinstance(il, (int, float)) else None,
            init_home=float(ih) if isinstance(ih, (int, float)) else None,
            init_away=float(ia) if isinstance(ia, (int, float)) else None,
        ))
    return out


# ------------------------------------------------------------------ 取点
def snap_at_or_before(timeline: list[dict], cutoff: datetime):
    best = None
    for s in timeline:
        if parse_utc(s["observed_at"]) <= cutoff:
            best = s
        else:
            break
    return best


def pick_close_main(timeline: list[dict], kickoff: datetime):
    """主定义收盘:kickoff−10min 之前最后一条;距 kickoff >120min 记缺失。
    返回 (snap|None, gap_min|None, reason)。"""
    s = snap_at_or_before(timeline, kickoff - timedelta(minutes=CLOSE_CUTOFF_MIN))
    if s is None:
        return None, None, "no_snap_before_cutoff"
    gap = (kickoff - parse_utc(s["observed_at"])).total_seconds() / 60.0
    if gap > CLOSE_MAX_GAP_MIN:
        return None, gap, "gap_gt_120min"
    return s, gap, "ok"


def pick_close_last(timeline: list[dict], kickoff: datetime):
    """敏感性收盘:开球前真实最后一条(observed_at < kickoff)。"""
    best = None
    for s in timeline:
        if parse_utc(s["observed_at"]) < kickoff:
            best = s
        else:
            break
    return best


def pick_t24(timeline: list[dict], kickoff: datetime):
    """T-24h:kickoff−24h 之前最后一条;第一条 snap 晚于 kickoff−24h 记缺失。"""
    if not timeline:
        return None
    if parse_utc(timeline[0]["observed_at"]) > kickoff - timedelta(hours=T24_HOURS):
        return None
    return snap_at_or_before(timeline, kickoff - timedelta(hours=T24_HOURS))


def pick_open(timeline: list[dict]):
    """开盘:第一条嵌套行的 initial(若存在),否则 None;另返回第一条 snap 本身。"""
    if not timeline:
        return None, None
    first = timeline[0]
    ini = None
    for s in timeline:
        if s["init_line"] is not None:
            ini = dict(line=s["init_line"], home=s["init_home"], away=s["init_away"],
                       observed_at=s["observed_at"])
            break
    return ini, first


def parse_round(text) -> int | None:
    import re
    if text is None:
        return None
    m = re.search(r"\d+", str(text))
    return int(m.group(0)) if m else None


def seeded_sample(items: list, k: int, seed: int = 20260928) -> list:
    rng = random.Random(seed)
    return rng.sample(items, min(k, len(items)))
