#!/usr/bin/env python3
"""H-M2 纸面记录脚本(PREREG_hm2.md §5;只读数据库,零写入生产库,记录在仓库外)。

stdout 只打印:本次新增场次、累计场次、累计下注数、各不下注原因计数。**不打印命中率/ROI。**

用法(crontab,见 docs/deployment-aws-cloudflare.md):
    python3 research/ah_signals/hm2_log.py --data-dir /opt/allwin/shared/data \
        --out-dir /home/ubuntu/research_out/ah_signals/hm2
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import LEAGUES, load_xref, open_ro, parse_utc, settle_home  # noqa: E402
from features_market import load_timelines, pick_close, pick_t24  # noqa: E402

HM2_CUTOFF_UTC = "2026-09-28T07:20:00Z"          # PREREG_hm2.md 定稿 commit 时间戳
FROZEN_FILES = ("hm2_log.py", "hm2_evaluate.py", "PREREG_hm2.md")
COLUMNS = ["logged_at", "match_id", "league_id", "round", "kickoff_at_utc", "home", "away",
           "t24_at", "line_t24", "t24_home_w", "t24_away_w", "close_at", "closing_line",
           "close_home_w", "close_away_w", "dline", "side", "water_used", "home_score", "away_score",
           "settle", "payoff", "reason"]
DAY_COVERAGE_MIN = 0.5


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def load_state(out_dir: Path) -> dict | None:
    p = out_dir / "hm2_state.json"
    return json.loads(p.read_text()) if p.exists() else None


def freeze_check(out_dir: Path, state: dict | None) -> dict:
    """首次运行写入定稿哈希;之后比对,不一致 → 退出码 4。返回(可能新建的)state。"""
    now = {f: sha256(HERE / f) for f in FROZEN_FILES}
    if state is None:
        state = dict(frozen=now, frozen_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     frozen_git_sha=git_sha(), cutoff_utc=HM2_CUTOFF_UTC)
        return state
    bad = [f for f in FROZEN_FILES if state["frozen"].get(f) != now[f]]
    if bad:
        print(f"!! 规则冻结自检失败:文件已改动 {bad}(定稿哈希见 hm2_state.json)。拒绝运行。")
        sys.exit(4)
    if state.get("evaluated_at"):
        print(f"!! 评估已于 {state['evaluated_at']} 完成,记录脚本停止。")
        sys.exit(5)
    return state


def read_incidents(out_dir: Path) -> list[tuple[datetime, datetime, str]]:
    p = out_dir / "hm2_incidents.csv"
    out = []
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out.append((parse_utc(r["start_utc"]), parse_utc(r["end_utc"]), r.get("note", "")))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    state = freeze_check(out_dir, load_state(out_dir))
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    bets_path = out_dir / "hm2_bets.csv"
    logged: set[int] = set()
    if bets_path.exists():
        with open(bets_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                logged.add(int(r["match_id"]))

    core, odds = open_ro(data_dir / "allwin.db"), open_ro(data_dir / "odds.db")
    rows = core.execute(
        f"""SELECT Match_ID, League_ID, Season, Match_Round, kickoff_at_utc, Home_Team_Name, Away_Team_Name,
                   home_score, away_score
              FROM dim_match
             WHERE League_ID IN ({','.join(str(x) for x in LEAGUES)})
               AND status='Finish' AND kickoff_precision='exact'
               AND kickoff_at_utc > ? AND home_score IS NOT NULL AND away_score IS NOT NULL
             ORDER BY kickoff_at_utc, Match_ID""", (HM2_CUTOFF_UTC,)).fetchall()
    matches = [dict(r) for r in rows if r["Match_ID"] not in logged]
    ok_xref, _ = load_xref(odds, {m["Match_ID"] for m in matches})
    pmid_to_mid = {str(x["provider_match_id"]): mid for mid, x in ok_xref.items()}
    tl = load_timelines(odds, pmid_to_mid) if matches else {}
    incidents = read_incidents(out_dir)

    # 数据源变化(自动):按开球日统计 Crown 收盘 snap 覆盖率(用本次新增的全部候选场次)
    by_day = defaultdict(list)
    for m in matches:
        by_day[m["kickoff_at_utc"][:10]].append(m)
    bad_days = set()
    for day, ms in by_day.items():
        have = sum(1 for m in ms if m["Match_ID"] in ok_xref and pick_close(tl.get((m["Match_ID"], "Crown", "ah"), []), parse_utc(m["kickoff_at_utc"]))[0] is not None)
        if have / len(ms) < DAY_COVERAGE_MIN:
            bad_days.add(day)

    new_rows = []
    for m in matches:
        mid, ko = m["Match_ID"], parse_utc(m["kickoff_at_utc"])
        rec = dict.fromkeys(COLUMNS)
        rec.update(logged_at=run_at, match_id=mid, league_id=m["League_ID"], round=m["Match_Round"],
                   kickoff_at_utc=m["kickoff_at_utc"], home=m["Home_Team_Name"], away=m["Away_Team_Name"],
                   home_score=m["home_score"], away_score=m["away_score"])
        if mid not in ok_xref:
            rec["reason"] = "no_xref"
        elif m["kickoff_at_utc"][:10] in bad_days or any(a <= ko <= b for a, b, _ in incidents):
            rec["reason"] = "data_source_change"
        else:
            t = tl.get((mid, "Crown", "ah"), [])
            if not t:
                rec["reason"] = "no_crown_data"
            else:
                t24 = pick_t24(t, ko)
                close, creason = pick_close(t, ko)
                if t24 is None:
                    rec["reason"] = "no_t24"
                elif close is None:
                    rec["reason"] = "no_close"
                else:
                    rec.update(t24_at=t24["t"].strftime("%Y-%m-%dT%H:%M:%SZ"), line_t24=t24["line"],
                               t24_home_w=t24["v"][0], t24_away_w=t24["v"][1],
                               close_at=close["t"].strftime("%Y-%m-%dT%H:%M:%SZ"), closing_line=close["line"],
                               close_home_w=close["v"][0], close_away_w=close["v"][1])
                    dline = close["line"] - t24["line"]
                    rec["dline"] = round(dline / 0.25)
                    if abs(dline) < 1e-9:
                        rec["reason"] = "no_move"
                    else:
                        side = -1 if dline > 0 else 1
                        s = settle_home(m["home_score"] - m["away_score"], close["line"]) * side
                        w = close["v"][0] if side > 0 else close["v"][1]
                        rec.update(side="away" if side < 0 else "home", water_used=w, settle=s,
                                   payoff=(s * w) if s > 0 else s, reason="bet")
        new_rows.append(rec)

    write_header = not bets_path.exists()
    with open(bets_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            w.writeheader()
        w.writerows(new_rows)

    # 累计计数(重读全文件)
    total = Counter()
    with open(bets_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            total[r["reason"]] += 1
    n_all = sum(total.values())
    n_bets = total["bet"]
    state.update(last_run_at=run_at, last_git_sha=git_sha(), total_matches=n_all, total_bets=n_bets,
                 reasons=dict(total), cutoff_utc=HM2_CUTOFF_UTC)
    (out_dir / "hm2_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1))

    new_c = Counter(r["reason"] for r in new_rows)
    print(f"[hm2_log {run_at}] 本次新增场次={len(new_rows)}(下注={new_c['bet']},不下注={len(new_rows)-new_c['bet']})  "
          f"累计场次={n_all}  累计下注数={n_bets}  不下注原因累计={ {k: v for k, v in sorted(total.items()) if k != 'bet'} }")
    return 0


if __name__ == "__main__":
    sys.exit(main())
