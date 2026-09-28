#!/usr/bin/env python3
"""crown_ext — 大小球(OU)Phase 0–1:数据审计 + 目标表 + 市场基线(只读;只用 25/26;不做任何特征与结果的关系分析)。

- Crown OU 收盘线:observed_at ≤ kickoff−10min 最后一条,距 kickoff >120min 记缺失(同 ah_signals);
  先检查同一 observed_at 是否有多条线(主盘口判定)。
- margin_ou = 总进球 − 收盘 OU 线;settle_over 独立实现(含 .25/.75 拆半仓),与
  backend/commands/reco_settlement_math.resolve_leg_result('ou', line, 'over') 逐场比对。
- 市场基线:margin_ou 均值/95% CI;全买大/全买小 ROI 与保本胜率;按联赛、盘口档位(≤2.25/2.5/2.75/≥3)、
  赛季前 5 轮/末 5 轮分组。水位核验复用 ah_signals phase4a(Crown OU 已通过)。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ah_signals"))
sys.path.insert(0, str(HERE.parents[1]))
from common import LEAGUE_NAME, LEAGUES, load_matches, load_xref, mean_ci95, open_ro, parse_round, parse_utc, quantiles, seeded_sample  # noqa: E402
from features_market import load_timelines, pick_close, pick_initial, pick_t24  # noqa: E402
from backend.commands.reco_settlement_math import resolve_leg_result  # noqa: E402

RECO_TO_NUM = {"win": 1.0, "half_win": 0.5, "push": 0.0, "half_loss": -0.5, "lose": -1.0}


def settle_over(total: int, line: float) -> float:
    """独立实现:买大(over)视角,返回 {-1,-0.5,0,0.5,1};四分之一线拆成相邻两个半仓平均。"""
    q = round(line / 0.25)
    if abs(line / 0.25 - q) > 1e-6:
        raise ValueError(f"line={line} 不是 0.25 的整数倍")

    def single(l):
        d = total - l
        return 0.0 if abs(d) < 1e-9 else (1.0 if d > 0 else -1.0)

    if q % 2 == 0:
        return single(line)
    return (single(line - 0.25) + single(line + 0.25)) / 2.0


def line_bucket(line: float) -> str:
    if line <= 2.25:
        return "≤2.25"
    if line == 2.5:
        return "2.5"
    if line == 2.75:
        return "2.75"
    return "≥3"


def group_stats(rows):
    n = len(rows)
    m, lo, hi, _ = mean_ci95([r["margin_ou"] for r in rows]) if rows else (math.nan,) * 4
    roi_o = roi_u = 0.0
    w_o = w_u = 0.0
    over_pure = under_pure = 0
    for r in rows:
        s = r["settle_over"]
        roi_o += (s * r["over_w"]) if s > 0 else s
        su = -s
        roi_u += (su * r["under_w"]) if su > 0 else su
        w_o += r["over_w"]; w_u += r["under_w"]
        over_pure += s == 1.0; under_pure += s == -1.0
    return dict(n=n, mean=m, lo=lo, hi=hi, roi_over=100 * roi_o / n if n else math.nan, roi_under=100 * roi_u / n if n else math.nan,
                be_over=100 / (1 + w_o / n) if n else math.nan, be_under=100 / (1 + w_u / n) if n else math.nan,
                over_pure=100 * over_pure / n if n else math.nan, under_pure=100 * under_pure / n if n else math.nan)


def print_table(title, groups):
    print(f"\n--- {title} ---")
    print(f"{'组':14s} {'N':>5s} {'mean margin_ou':>15s} {'95%CI':>18s} {'纯大%':>6s} {'纯小%':>6s} {'ROI大%':>7s} {'保本大%':>7s} {'ROI小%':>7s} {'保本小%':>7s}  备注")
    for name, rows in groups.items():
        g = group_stats(rows)
        ci = f"[{g['lo']:+.3f},{g['hi']:+.3f}]" if not math.isnan(g["lo"]) else "n/a"
        print(f"{name:14s} {g['n']:5d} {g['mean']:+15.3f} {ci:>18s} {g['over_pure']:6.1f} {g['under_pure']:6.1f} "
              f"{g['roi_over']:+7.2f} {g['be_over']:7.1f} {g['roi_under']:+7.2f} {g['be_under']:7.1f}  {'样本不足,不可靠' if g['n'] < 100 else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    core, odds = open_ro(data_dir / "allwin.db"), open_ro(data_dir / "odds.db")
    matches = [m for m in load_matches(core) if m["Season"] == "2025/2026"]
    ok_xref, nr = load_xref(odds, {m["Match_ID"] for m in matches})
    matches = [m for m in matches if m["Match_ID"] in ok_xref]
    pmid_to_mid = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items()}
    print(f"25/26 完赛且精确开球比赛={len(matches)}(needs_review 剔除 {len(nr)};26/27 未载入)")

    # ================================================================ Phase 0:OU 审计
    print("\n" + "=" * 12, "Phase 0:Crown OU 快照审计", "=" * 12)
    raw = odds.execute(
        """SELECT provider_match_id, observed_at, COUNT(*) c, COUNT(DISTINCT json_extract(payload_json,'$.latest.line')) dl
             FROM bronze_ng_odds_snap WHERE market='ou' AND company_id='3'
            GROUP BY provider_match_id, observed_at HAVING c>1""").fetchall()
    multi = [r for r in raw if str(r[0]) in pmid_to_mid]
    multi_lines = [r for r in multi if r[3] > 1]
    print(f"  同一 (比赛, observed_at) 有多条 Crown OU 行的时点数={len(multi)};其中 latest.line 不同(多线)的时点数={len(multi_lines)}"
          + ("  → 单一主盘口,无需选线" if not multi_lines else "  !! 存在多线,需定主盘口规则"))
    for r in multi_lines[:10]:
        print(f"     pmid={r[0]} at={r[1]} rows={r[2]} distinct_lines={r[3]}")
    tl = load_timelines(odds, pmid_to_mid)   # 已过滤水位异常;含 ah/ou/1x2
    n_snaps = Counter()
    for (mid, ck, mk), v in tl.items():
        if ck == "Crown" and mk == "ou":
            n_snaps[mid] = len(v)
    cnts = [n_snaps.get(m["Match_ID"], 0) for m in matches]
    q = quantiles(cnts, probs=(0, 0.1, 0.5, 0.9, 1))
    print(f"  每场 Crown OU 赛前 snap 数(水位过滤后)p0/p10/p50/p90/p100 = {q[0]:.0f}/{q[1]:.0f}/{q[2]:.0f}/{q[3]:.0f}/{q[4]:.0f};0 条的场次={sum(1 for c in cnts if c == 0)}")
    ini_ne = 0; gaps = []
    rows_out = []
    reasons = Counter()
    mismatch = 0
    for m in matches:
        mid, ko = m["Match_ID"], parse_utc(m["kickoff_at_utc"])
        t = tl.get((mid, "Crown", "ou"), [])
        close, reason = pick_close(t, ko)
        ini = pick_initial(t)
        if t and ini is not None and abs(ini["line"] - t[0]["line"]) > 1e-9:
            ini_ne += 1
        if close is None:
            reasons[reason] += 1
            continue
        gaps.append((ko - close["t"]).total_seconds() / 60)
        total = m["home_score"] + m["away_score"]
        s = settle_over(total, close["line"])
        reco = RECO_TO_NUM[resolve_leg_result("ou", close["line"], "over", home_score=m["home_score"], away_score=m["away_score"])]
        if abs(s - reco) > 1e-9:
            mismatch += 1
        rows_out.append(dict(match_id=mid, league_id=m["League_ID"], league=LEAGUE_NAME[m["League_ID"]], round=parse_round(m["Match_Round"]),
                             kickoff_at_utc=m["kickoff_at_utc"], home=m["Home_Team_Name"], away=m["Away_Team_Name"],
                             home_score=m["home_score"], away_score=m["away_score"], total=total,
                             ou_open_line=ini["line"] if ini else None, ou_t24_line=(pick_t24(t, ko) or {}).get("line"),
                             ou_close_at=close["t"].strftime("%Y-%m-%dT%H:%M:%SZ"), ou_close_line=close["line"],
                             over_w=close["v"][0], under_w=close["v"][1], margin_ou=total - close["line"],
                             settle_over=s, settle_reco=reco))
    gq = quantiles(gaps, probs=(0.5, 0.9, 1.0))
    print(f"  收盘线(主定义)有={len(rows_out)} 缺失={dict(reasons)};收盘 snap 距开球分钟 p50/p90/max={gq[0]:.0f}/{gq[1]:.0f}/{gq[2]:.0f}")
    print(f"  initial.line ≠ 首条 snap.line 的场次={ini_ne}(25/26 回填,预期 0)")
    print(f"  收盘 OU 线分布: {dict(sorted(Counter(r['ou_close_line'] for r in rows_out).items()))}")

    # ================================================================ Phase 1:目标与结算
    print("\n" + "=" * 12, "Phase 1:目标变量与结算比对", "=" * 12)
    print(f"  settle_over(独立实现) vs reco_settlement_math('ou','over'):比对场次={len(rows_out)} 不一致={mismatch}")
    print("  随机 5 场(seed=20260928):")
    for r in seeded_sample(rows_out, 5):
        print(f"    {r['league']} {r['home']} vs {r['away']} {r['home_score']}-{r['away_score']} 总进球={r['total']} "
              f"OU线={r['ou_close_line']:.2f} margin_ou={r['margin_ou']:+.2f} settle_over={r['settle_over']:+.1f} (reco={r['settle_reco']:+.1f}) 水位 大={r['over_w']} 小={r['under_w']}")
    margins = [r["margin_ou"] for r in rows_out]
    m, lo, hi, n = mean_ci95(margins)
    q = quantiles(margins, probs=(0.05, 0.25, 0.5, 0.75, 0.95))
    print(f"  margin_ou:N={n} mean={m:+.4f} 95%CI=[{lo:+.4f},{hi:+.4f}] p5/p25/p50/p75/p95={q[0]:+.2f}/{q[1]:+.2f}/{q[2]:+.2f}/{q[3]:+.2f}/{q[4]:+.2f}")
    tot = [r["total"] for r in rows_out]; lines = [r["ou_close_line"] for r in rows_out]
    from common import pearson
    print(f"  总进球 mean={sum(tot)/len(tot):.3f};收盘线 mean={sum(lines)/len(lines):.3f};corr(收盘线, 总进球)={pearson(lines, tot):+.4f} corr(收盘线, margin_ou)={pearson(lines, margins):+.4f}")
    print(f"  settle_over 分布: {dict(sorted(Counter(r['settle_over'] for r in rows_out).items()))}")

    # ================================================================ 市场基线
    print("\n" + "=" * 12, "市场基线(Crown 收盘 OU 线/水位;ROI=平注 1 单位;保本胜率=1/(1+平均水位))", "=" * 12)
    print_table("全体", {"ALL": rows_out})
    print_table("按联赛", {LEAGUE_NAME[l]: [r for r in rows_out if r["league_id"] == l] for l in LEAGUES})
    print_table("按盘口档位", {b: [r for r in rows_out if line_bucket(r["ou_close_line"]) == b] for b in ("≤2.25", "2.5", "2.75", "≥3")})
    max_round = {}
    for r in rows_out:
        if r["round"] is not None:
            max_round[r["league_id"]] = max(max_round.get(r["league_id"], 0), r["round"])
    print_table("按赛季阶段", {"前 5 轮": [r for r in rows_out if r["round"] is not None and r["round"] <= 5],
                          "末 5 轮": [r for r in rows_out if r["round"] is not None and r["round"] >= max_round[r["league_id"]] - 4],
                          "中段": [r for r in rows_out if r["round"] is not None and 5 < r["round"] < max_round[r["league_id"]] - 4]})

    with open(out_dir / "ou_phase1_target.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys())); w.writeheader(); w.writerows(rows_out)
    print(f"\n[written] {out_dir/'ou_phase1_target.csv'} ({len(rows_out)} 行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
