#!/usr/bin/env python3
"""crown_ext — Crown 与 Bet365 AH 主盘口的领先-滞后(纯描述;只读;只用 25/26 回填数据,来源时间戳分辨率到秒;
26/27 为 5 分钟轮询、时间分辨率不足,不使用)。

- 变动:相邻两条赛前 snap(水位过滤后)之间 line 变化,或任一边水位变化 >0.02。
- 方向:line 上升(主让加深)= 向主队(+1);line 不变时主队水位下降或客队水位上升 = 向主队(+1);反之 −1;
  两边水位同向移动且 line 不变 = 无方向(0)。
- 对每次 Bet365 变动,统计其后 (0, 15/30/60 分钟] 内 Crown 是否有同方向变动(以及任意变动);反向同样。
  同一秒(lag=0)的变动单独计为"同时",不计入"随后"。
- 基准:每场在 [Crown 首条 snap, kickoff−10min] 内随机取 5 个时点(seed 固定),统计其后窗口内 Crown / Bet365
  发生任意变动的比例。
- 领先时间分布:被跟随的变动到首次同方向跟随变动的分钟数 p10/p50/p90;分联赛拆分。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ah_signals"))
from common import CLOSE_CUTOFF_MIN, LEAGUE_NAME, LEAGUES, load_matches, load_xref, open_ro, parse_utc, quantiles  # noqa: E402
from features_market import load_timelines  # noqa: E402

WATER_EPS = 0.02
WINDOWS = (15, 30, 60)
RANDOM_PER_MATCH = 5
SEED = 20260928


def changes(tl: list[dict], ko) -> list[tuple]:
    """返回 [(t, direction, kind)],kind ∈ {line, water};只用 observed_at < kickoff−10min 的 snap。"""
    cutoff = ko - timedelta(minutes=CLOSE_CUTOFF_MIN)
    seq = [s for s in tl if s["t"] <= cutoff]
    out = []
    for a, b in zip(seq, seq[1:]):
        dl = b["line"] - a["line"]
        dh, da = b["v"][0] - a["v"][0], b["v"][1] - a["v"][1]
        if abs(dl) > 1e-9:
            out.append((b["t"], 1 if dl > 0 else -1, "line"))
        elif abs(dh) > WATER_EPS or abs(da) > WATER_EPS:
            toward_home = (dh < -WATER_EPS) or (da > WATER_EPS)
            toward_away = (dh > WATER_EPS) or (da < -WATER_EPS)
            d = 1 if (toward_home and not toward_away) else (-1 if (toward_away and not toward_home) else 0)
            out.append((b["t"], d, "water"))
    return out


def follow_stats(src: list[tuple], dst: list[tuple]):
    """对 src 每次变动:dst 在 (0, w] 分钟内是否有同方向变动 / 任意变动;同时(lag=0)单独计;首次同方向跟随的 lag。"""
    res = {w: Counter() for w in WINDOWS}
    lags = []
    simult = 0
    for t, d, _ in src:
        if any(abs((t2 - t).total_seconds()) < 1 for t2, _, _ in dst):
            simult += 1
        first_same = None
        for t2, d2, _ in dst:
            dt = (t2 - t).total_seconds() / 60
            if dt <= 0:
                continue
            for w in WINDOWS:
                if dt <= w:
                    res[w]["any"] += 1 if not res[w].get(("any_done", t)) else 0
                    res[w][("any_done", t)] = 1
                    if d != 0 and d2 == d and not res[w].get(("same_done", t)):
                        res[w]["same"] += 1
                        res[w][("same_done", t)] = 1
            if d != 0 and d2 == d and first_same is None:
                first_same = dt
        if first_same is not None:
            lags.append(first_same)
    n = len(src)
    n_dir = sum(1 for _, d, _ in src if d != 0)
    return dict(n=n, n_dir=n_dir, simult=simult,
                any={w: res[w]["any"] for w in WINDOWS}, same={w: res[w]["same"] for w in WINDOWS}, lags=lags)


def merge(acc, r):
    acc["n"] += r["n"]; acc["n_dir"] += r["n_dir"]; acc["simult"] += r["simult"]
    for w in WINDOWS:
        acc["any"][w] += r["any"][w]; acc["same"][w] += r["same"][w]
    acc["lags"] += r["lags"]


def new_acc():
    return dict(n=0, n_dir=0, simult=0, any={w: 0 for w in WINDOWS}, same={w: 0 for w in WINDOWS}, lags=[])


def pct(a, b):
    return 100.0 * a / b if b else float("nan")


def show(label, acc, base):
    print(f"  {label}: 变动数={acc['n']}(有方向 {acc['n_dir']};同时 lag=0 的 {acc['simult']})")
    for w in WINDOWS:
        print(f"     {w:2d} 分钟内:对方任意变动 {pct(acc['any'][w], acc['n']):5.1f}%  同方向变动 {pct(acc['same'][w], acc['n_dir']):5.1f}%   基准(随机时点后对方任意变动) {pct(base['any'][w], base['n']):5.1f}%")
    if acc["lags"]:
        q = quantiles(acc["lags"], probs=(0.1, 0.5, 0.9))
        print(f"     首次同方向跟随的 lag(分钟,60 分钟内)N={len([l for l in acc['lags'] if l <= 60])} p10/p50/p90={q[0]:.1f}/{q[1]:.1f}/{q[2]:.1f}(全部跟随,不限 60 分钟)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    core, odds = open_ro(data_dir / "allwin.db"), open_ro(data_dir / "odds.db")
    matches = [m for m in load_matches(core) if m["Season"] == "2025/2026"]
    ok_xref, _ = load_xref(odds, {m["Match_ID"] for m in matches})
    matches = [m for m in matches if m["Match_ID"] in ok_xref]
    pmid_to_mid = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items()}
    tl = load_timelines(odds, pmid_to_mid)
    print(f"25/26 比赛={len(matches)}(只用 25/26:回填数据 observed_at 为来源时间戳,秒级;26/27 是 5 分钟轮询,分辨率不足,不使用)")
    print(f"变动定义:line 变化,或任一边水位变化 >{WATER_EPS};只用 observed_at ≤ kickoff−{CLOSE_CUTOFF_MIN}min 的赛前 snap(水位过滤后)")

    rng = random.Random(SEED)
    acc_b2c, acc_c2b = defaultdict(new_acc), defaultdict(new_acc)     # league -> acc;key 0 = ALL
    base_c, base_b = defaultdict(new_acc), defaultdict(new_acc)
    n_used = 0
    kinds = Counter()
    for m in matches:
        mid, ko, lid = m["Match_ID"], parse_utc(m["kickoff_at_utc"]), m["League_ID"]
        c_tl, b_tl = tl.get((mid, "Crown", "ah"), []), tl.get((mid, "Bet365", "ah"), [])
        if len(c_tl) < 2 or len(b_tl) < 2:
            continue
        n_used += 1
        cc, bc = changes(c_tl, ko), changes(b_tl, ko)
        for _, d, k in cc:
            kinds[("Crown", k, d)] += 1
        for _, d, k in bc:
            kinds[("Bet365", k, d)] += 1
        r1, r2 = follow_stats(bc, cc), follow_stats(cc, bc)
        for key in (0, lid):
            merge(acc_b2c[key], r1); merge(acc_c2b[key], r2)
        # 基准:随机时点
        t0 = max(c_tl[0]["t"], b_tl[0]["t"]); t1 = ko - timedelta(minutes=CLOSE_CUTOFF_MIN)
        span = (t1 - t0).total_seconds()
        if span <= 0:
            continue
        for _ in range(RANDOM_PER_MATCH):
            t = t0 + timedelta(seconds=rng.random() * span)
            for dst, base in ((cc, base_c), (bc, base_b)):
                for key in (0, lid):
                    base[key]["n"] += 1
                    for w in WINDOWS:
                        if any(0 < (t2 - t).total_seconds() / 60 <= w for t2, _, _ in dst):
                            base[key]["any"][w] += 1
    print(f"两家都有 ≥2 条赛前 snap 的比赛={n_used}")
    print("变动计数(公司, 类型, 方向 +1=向主队/−1=向客队/0=无方向):")
    for k, v in sorted(kinds.items()):
        print(f"   {k}: {v}")
    print("\n" + "=" * 12, "领先-滞后(全体)", "=" * 12)
    show("Bet365 变动 → Crown 跟随", acc_b2c[0], base_c[0])
    show("Crown 变动 → Bet365 跟随", acc_c2b[0], base_b[0])
    print("\n" + "=" * 12, "分联赛", "=" * 12)
    for l in LEAGUES:
        print(f"[{LEAGUE_NAME[l]}]")
        show("Bet365 → Crown", acc_b2c[l], base_c[l])
        show("Crown → Bet365", acc_c2b[l], base_b[l])
    out = dict(n_used=n_used, b2c={str(k): {kk: (vv if kk != "lags" else len(vv)) for kk, vv in v.items()} for k, v in acc_b2c.items()},
               c2b={str(k): {kk: (vv if kk != "lags" else len(vv)) for kk, vv in v.items()} for k, v in acc_c2b.items()},
               base_c={str(k): v["any"] | {"n": v["n"]} for k, v in base_c.items()}, base_b={str(k): v["any"] | {"n": v["n"]} for k, v in base_b.items()})
    (out_dir / "lead_lag_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print(f"\n[written] {out_dir/'lead_lag_summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
