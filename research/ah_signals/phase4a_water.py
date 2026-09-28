#!/usr/bin/env python3
"""Phase 4 前置:水位格式核验(PREREG_phase4 §2.1;只看赔率本身,不看结果,只读)。

对 Crown / Bet365 的 AH、OU 与 Bet365 的 1x2:25/26 样本比赛的收盘 snap(主定义:
observed_at ≤ kickoff−10min 的最后一条,距 kickoff >120min 记缺失)的原始水位分布,以及
港式假设下两边隐含概率之和(1x2 用 Σ1/odds)的分布。
判定:p50 ∈ [1.02,1.08] 且 99% 的 snap ∈ [1.00,1.15]。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CLOSE_CUTOFF_MIN, CLOSE_MAX_GAP_MIN, COMPANIES, load_matches, load_xref, open_ro, parse_utc,
    quantiles,
)

FIELDS = {"ah": ("home", "away"), "ou": ("over", "under"), "1x2": ("home", "draw", "away")}
CHECKS = [("Crown", "ah"), ("Crown", "ou"), ("Bet365", "ah"), ("Bet365", "ou"), ("Bet365", "1x2")]


def load_close_snaps(odds, pmid_to_mid, kickoff_by_mid):
    """(company, market, mid) -> 收盘 snap 的 latest dict(或 None)。"""
    rows = odds.execute(
        """SELECT provider_match_id, company_id, market, observed_at, payload_json
             FROM bronze_ng_odds_snap
            WHERE market IN ('ah','ou','1x2') AND company_id IN ('8','281','3')
            ORDER BY observed_at ASC, id ASC"""
    ).fetchall()
    best = {}
    for pmid, cid, market, obs, pj in rows:
        mid = pmid_to_mid.get(str(pmid))
        if mid is None:
            continue
        ck = COMPANIES.get(str(cid))
        ko = kickoff_by_mid[mid]
        t = parse_utc(obs)
        if t > ko - timedelta(minutes=CLOSE_CUTOFF_MIN):
            continue
        try:
            p = json.loads(pj)
        except ValueError:
            continue
        lat = p.get("latest") if isinstance(p, dict) and isinstance(p.get("latest"), dict) else (p if isinstance(p, dict) else None)
        if lat is None:
            continue
        vals = [lat.get(f) for f in FIELDS[market]]
        if any(not isinstance(v, (int, float)) or v <= 0 for v in vals):
            continue
        best[(ck, market, mid)] = (t, vals)  # 升序遍历,最后一条即最晚
    out = {}
    for (ck, market, mid), (t, vals) in best.items():
        gap = (kickoff_by_mid[mid] - t).total_seconds() / 60.0
        out[(ck, market, mid)] = None if gap > CLOSE_MAX_GAP_MIN else vals
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    core, odds = open_ro(data_dir / "allwin.db"), open_ro(data_dir / "odds.db")
    matches = [m for m in load_matches(core) if m["Season"] == "2025/2026"]
    ok_xref, _ = load_xref(odds, {m["Match_ID"] for m in matches})
    matches = [m for m in matches if m["Match_ID"] in ok_xref]
    pmid_to_mid = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items()}
    kickoff = {m["Match_ID"]: parse_utc(m["kickoff_at_utc"]) for m in matches}
    print(f"25/26 样本比赛(xref ok)={len(matches)};收盘 snap 主定义:observed_at ≤ kickoff−10min 最后一条,gap>120min 记缺失")
    snaps = load_close_snaps(odds, pmid_to_mid, kickoff)
    print(f"\n{'公司/市场':14s} {'有收盘':>6s} {'缺失':>5s} | 原始水位/赔率 p1/p50/p99(各边) | 隐含概率之和 p1/p50/p99 | ∈[1.00,1.15]% | 判定")
    verdicts = {}
    for ck, market in CHECKS:
        vals = [snaps.get((ck, market, m["Match_ID"])) for m in matches]
        have = [v for v in vals if v is not None]
        miss = len(vals) - len(have)
        if not have:
            print(f"{ck+'/'+market:14s} {0:6d} {miss:5d} | 无数据 → 不进入 Phase 4")
            verdicts[(ck, market)] = False
            continue
        sides = list(zip(*have))
        side_q = [quantiles(list(sd), probs=(0.01, 0.5, 0.99)) for sd in sides]
        if market == "1x2":
            sums = [sum(1.0 / v for v in vs) for vs in have]
        else:
            sums = [sum(1.0 / (1.0 + v) for v in vs) for vs in have]
        sq = quantiles(sums, probs=(0.01, 0.5, 0.99))
        in_range = sum(1 for x in sums if 1.00 <= x <= 1.15) / len(sums)
        ok = 1.02 <= sq[1] <= 1.08 and in_range >= 0.99
        verdicts[(ck, market)] = ok
        side_txt = " ".join(f"{FIELDS[market][i]}={q[0]:.2f}/{q[1]:.2f}/{q[2]:.2f}" for i, q in enumerate(side_q))
        print(f"{ck+'/'+market:14s} {len(have):6d} {miss:5d} | {side_txt} | {sq[0]:.4f}/{sq[1]:.4f}/{sq[2]:.4f} | "
              f"{100*in_range:6.2f} | {'通过' if ok else '不通过 → 停'}")
        # 区间分布明细
        bins = [(0, 1.00), (1.00, 1.02), (1.02, 1.04), (1.04, 1.06), (1.06, 1.08), (1.08, 1.15), (1.15, 99)]
        dist = {f"[{a},{b})": sum(1 for x in sums if a <= x < b) for a, b in bins}
        print(f"{'':14s}   隐含概率之和分布: {dist}")
        extreme = [vs for vs in have if not (1.00 <= (sum(1.0/v for v in vs) if market == '1x2' else sum(1.0/(1.0+v) for v in vs)) <= 1.15)]
        if extreme:
            print(f"{'':14s}   区间外 snap 例(最多 5 条): {extreme[:5]}")
    print("\n判定汇总:")
    for (ck, market), ok in verdicts.items():
        print(f"  {ck}/{market}: {'通过' if ok else '不通过'}")
    return 0 if all(verdicts.values()) else 3


if __name__ == "__main__":
    sys.exit(main())
