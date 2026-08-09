"""market_baseline.py — 分公司真实市场基线(只读,可复现)。

预先指定的口径(model-api-contract.md §2.1b 要求的六要素,全部固定于此):

1. **公司集合**:full_timeline 档按 (company_id, company_name) 独立报告——
   `281:bet 365`(NowGoal euro 历史)、`177:pinnacle`、`80:macauslot`;
   live 轮询的 `8:Bet365`/`31:Sbobet` 样本极少(≤17 场)只报数量不作基线。
   两点摘要档按 source 报告(provider 均为 Bet365),概率点命名
   **summary_latest**——它没有精确时间戳,绝不称为 exact closing。
2. **快照选择(full_timeline)**:该场该公司 market='1x2' 且
   market_phase='pre_match' 且 observed_at **严格早于** kickoff_at_utc 的
   最后一条(observed_at 最大;并列取 id 最大)。in_play/unknown 一律不进。
   xref 只认 review_status ∈ (auto_ok, confirmed);无法安全映射 fail closed。
3. **去水公式(主基准)**:proportional(比例归一)——
   p_i = (1/odds_i) / Σ_j (1/odds_j)。若研究其它去水法必须另列条目,
   不得事后择优冒充预先指定。
4. **缺失规则**:任一赔率缺失/≤1.01 → 该场该公司剔除(计数上报);
   overround=Σ(1/odds) 不在 [1.01, 1.35] → 剔除(计数上报)。
5. **聚合方式**:不跨公司聚合;每公司独立成一条基线。
6. **RPS 公式**:backend/eval 与训练脚本共用的 Σ(CDF差)²/(K-1),K=3。

时间纪律:legacy summary_latest 无 observed_at,只能与"赛前存在过的某一时点"
配对,不得参与"临场收盘"类结论;full_timeline 的 last-pre-kickoff 快照才可
称为 archive-closing(observed_at 有证据)。
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

FULL_COMPANIES = ("281", "177", "80")   # bet 365 / pinnacle / macauslot(历史链路)
OVERROUND_BOUNDS = (1.01, 1.35)
MIN_ODDS = 1.01


def proportional_devig(home: float, draw: float, away: float) -> tuple[float, float, float, float]:
    inv = (1.0 / home, 1.0 / draw, 1.0 / away)
    s = sum(inv)
    return inv[0] / s, inv[1] / s, inv[2] / s, s


def _kickoffs(conn_core: sqlite3.Connection, leagues) -> dict[int, str]:
    ph = ",".join("?" for _ in leagues)
    return {
        int(r[0]): str(r[1])
        for r in conn_core.execute(
            f"""SELECT Match_ID, kickoff_at_utc FROM dim_match
                 WHERE status='Finish' AND League_ID IN ({ph})
                   AND kickoff_at_utc IS NOT NULL AND kickoff_precision='exact'""",
            tuple(leagues),
        )
    }


def build_full_timeline_baseline(
    conn_core: sqlite3.Connection, conn_odds: sqlite3.Connection, leagues=(47, 53, 54, 55, 87)
) -> dict:
    """每公司:match_id → (p_home,p_draw,p_away,overround,observed_at)。"""
    kick = _kickoffs(conn_core, leagues)
    xref = {
        str(r[0]): int(r[1])
        for r in conn_odds.execute(
            """SELECT provider_match_id, fotmob_match_id FROM dim_match_xref
                WHERE provider='nowgoal' AND review_status IN ('auto_ok','confirmed')"""
        )
    }

    # 每 (fotmob, company) 保留 observed_at 严格早于 kickoff 的最后一条
    best: dict[tuple[int, str], tuple[str, int, dict]] = {}
    stats = defaultdict(int)
    for r in conn_odds.execute(
        """SELECT id, provider_match_id, company_id, payload_json, observed_at
             FROM bronze_ng_odds_snap
            WHERE market='1x2' AND market_phase='pre_match'"""
    ):
        fid = xref.get(str(r["provider_match_id"]))
        if fid is None:
            stats["skipped_unmapped"] += 1
            continue
        ko = kick.get(fid)
        if ko is None:
            stats["skipped_no_kickoff"] += 1
            continue
        cid = str(r["company_id"])
        if cid not in FULL_COMPANIES:
            stats[f"skipped_company_{cid}"] += 1
            continue
        obs = str(r["observed_at"])
        if obs >= ko:                       # ISO-UTC 字典序 = 时间序;严格早于
            stats["skipped_at_or_after_kickoff"] += 1
            continue
        key = (fid, cid)
        cur = best.get(key)
        if cur is None or (obs, int(r["id"])) > (cur[0], cur[1]):
            best[key] = (obs, int(r["id"]), json.loads(r["payload_json"]))

    out: dict[str, dict[int, dict]] = {c: {} for c in FULL_COMPANIES}
    excl = defaultdict(int)
    over_dist: dict[str, list[float]] = defaultdict(list)
    for (fid, cid), (obs, _id, payload) in sorted(best.items()):
        h, d, a = payload.get("home"), payload.get("draw"), payload.get("away")
        if not all(isinstance(v, (int, float)) and v > MIN_ODDS for v in (h, d, a)):
            excl[f"{cid}:bad_odds"] += 1
            continue
        ph_, pd_, pa_, over = proportional_devig(h, d, a)
        if not (OVERROUND_BOUNDS[0] <= over <= OVERROUND_BOUNDS[1]):
            excl[f"{cid}:overround_out_of_bounds"] += 1
            continue
        out[cid][fid] = {
            "p": (round(ph_, 6), round(pd_, 6), round(pa_, 6)),
            "overround": round(over, 4),
            "observed_at": obs,
        }
        over_dist[cid].append(over)

    def _pct(vals, q):
        if not vals:
            return None
        vals = sorted(vals)
        return round(vals[min(len(vals) - 1, int(q * len(vals)))], 4)

    report = {
        "companies": {
            cid: {
                "paired_matches": len(out[cid]),
                "overround_p50": _pct(over_dist[cid], 0.5),
                "overround_p90": _pct(over_dist[cid], 0.9),
            }
            for cid in FULL_COMPANIES
        },
        "selection_stats": dict(sorted(stats.items())),
        "exclusions": dict(sorted(excl.items())),
        "devig": "proportional",
        "snapshot_rule": "last pre_match snapshot with observed_at strictly before kickoff",
    }
    return {"per_company": out, "report": report}


def build_summary_latest_baseline(
    conn_core: sqlite3.Connection, conn_odds: sqlite3.Connection, leagues=(47, 53, 54, 55, 87)
) -> dict:
    """legacy 两点摘要的 summary_latest 档(无时间戳;不称 closing)。"""
    kick = _kickoffs(conn_core, leagues)
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    excl = defaultdict(int)
    for r in conn_odds.execute(
        """SELECT fotmob_match_id, source, home_or_over, draw, away_or_under
             FROM bronze_legacy_odds_summary
            WHERE market='1x2' AND period='latest'"""
    ):
        fid = int(r["fotmob_match_id"])
        if fid not in kick:
            continue
        h, d, a = r["home_or_over"], r["draw"], r["away_or_under"]
        if not all(isinstance(v, (int, float)) and v > MIN_ODDS for v in (h, d, a)):
            excl[f"{r['source']}:bad_odds"] += 1
            continue
        ph_, pd_, pa_, over = proportional_devig(h, d, a)
        if not (OVERROUND_BOUNDS[0] <= over <= OVERROUND_BOUNDS[1]):
            excl[f"{r['source']}:overround_out_of_bounds"] += 1
            continue
        out[str(r["source"])][fid] = {
            "p": (round(ph_, 6), round(pd_, 6), round(pa_, 6)),
            "overround": round(over, 4),
        }
    report = {
        "sources": {s: len(v) for s, v in sorted(out.items())},
        "exclusions": dict(sorted(excl.items())),
        "timestamp_evidence": "none — summary_latest 无 observed_at,不得称 exact closing",
    }
    return {"per_source": dict(out), "report": report}
