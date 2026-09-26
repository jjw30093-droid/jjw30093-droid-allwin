#!/usr/bin/env python3
"""B费"下滑有多严重"数据分析——一键刷新脚本。

每次运行都会通过 SSH 重新从生产库拉取最新数据（不读任何本地缓存的 JSON），
因此下一轮比赛结束、生产库更新后，直接重跑本脚本即可拿到最新数字。用
--json-out 额外写一份结构化 JSON，供 render_bruno_series.py 出图时注入
数值——图上任何数字都不手写。

口径（2026-09-23 与站长逐条核对过）：
- 场均 = 赛季合计 ÷ 出场场次（`sum(非空值)/apps`，数学上等价于"NULL按0处理再
  除以出场场次"）；分母永远是出场场次，不是"该字段非空的场次数"——后者会
  系统性高估，因为它把"贡献可忽略、字段干脆不报"的比赛整场排除在分母外。
  见 docs/data-sources.md §1.4.2。
- 球员赛季位置 = 该球员本赛季全部出场场次 usual_position 的众数
  （0=GK,1=DEF,2=MID,3=FWD），众数并列时取数值更小的一档（如实披露，不隐藏）。
- 基准池 = 位置众数 ∈ {MID(2), FWD(3)} 且出场场次 ≥ 对应赛季的 --min-apps 阈值。
- `big_chance_created_team_title` 已从全部计算中移除：docs/data-sources.md
  §1.4.2 证实该字段 NULL≠0、约43%场次无法与球队口径对账，不适合做球员级
  排名/场均对比。P2 象限图改用 x=关键传球(chances_created)、
  y=xA(expected_assists)——这两个字段已核实：外场球员范围内 NULL 恒为 0
  （门将专属缺失），可以直接用于排名。

用法示例（下一轮比赛结束后一键刷新）：
    python3 bruno_refresh.py
    python3 bruno_refresh.py --json-out bruno_data.json
"""
import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

SSH_HOST = "vip-lightsail"
DB_PATH = "/opt/allwin/shared/data/allwin.db"

BRUNO_BIRTH_DATE = date(1994, 9, 8)  # 来源：公开资料

STAT_COLS = [
    "chances_created", "expected_assists",
    "expected_goals_non_penalty", "assists", "goals",
    "accurate_passes", "accurate_passes_total", "passes_into_final_third",
    "dispossessed", "minutes_played",
]

STARS = {
    "萨卡 Bukayo Saka": "Bukayo Saka",
    "切尔基 Rayan Cherki": "Rayan Cherki",
    "索博斯洛伊 Dominik Szoboszlai": "Dominik Szoboszlai",
    "赖斯 Declan Rice": "Declan Rice",
    "帕尔默 Cole Palmer": "Cole Palmer",
    "厄德高 Martin Ødegaard": "Martin Ødegaard",
}
# P2 需要头像+名字标注的（其余星标只画灰点）
LABELED_STARS = {"帕尔默 Cole Palmer", "厄德高 Martin Ødegaard",
                  "切尔基 Rayan Cherki", "索博斯洛伊 Dominik Szoboszlai"}

POS_NAME = {"0": "GK", "1": "DEF", "2": "MID", "3": "FWD"}


def run_sql(sql: str, timeout: int = 420):
    p = subprocess.run(
        ["ssh", SSH_HOST, f"sqlite3 -json {DB_PATH}"],
        input=sql, capture_output=True, text=True, timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError(f"sqlite3 失败: {p.stderr.strip()}")
    out = p.stdout.strip()
    return json.loads(out) if out else []


def pull_position_rows(league_id: int, seasons: list[str]):
    season_list = ",".join(f"'{s}'" for s in seasons)
    sql = f"""
    SELECT f.Player_ID AS pid, f.player_name AS name, f.Team_ID AS team_id,
           m.Season AS season, f.Match_ID AS match_id,
           f.usual_position AS up, f.minutes_played AS minutes
    FROM fact_player_match_stats f
    JOIN dim_match m ON m.Match_ID = f.Match_ID
    WHERE m.League_ID = {league_id} AND m.Season IN ({season_list});
    """
    return run_sql(sql)


def pull_stat_rows(league_id: int, seasons: list[str], player_ids: list[str]):
    """只拉池内球员（+B费本人）的完整宽表字段——全联赛全字段太宽太慢，
    先用 pull_position_rows 的窄表定好池子成员，再按 pid 白名单精确拉取。"""
    season_list = ",".join(f"'{s}'" for s in seasons)
    cols = ", ".join(f"f.{c}" for c in STAT_COLS)
    pid_list = ",".join(f"'{p}'" for p in player_ids)
    sql = f"""
    SELECT f.Player_ID AS pid, f.player_name AS name, f.Team_ID AS team_id,
           m.Season AS season, f.Match_ID AS match_id, {cols}
    FROM fact_player_match_stats f
    JOIN dim_match m ON m.Match_ID = f.Match_ID
    WHERE m.League_ID = {league_id} AND m.Season IN ({season_list}) AND f.Player_ID IN ({pid_list});
    """
    return run_sql(sql)


def pull_bruno_all_seasons(player_id: str, league_id: int):
    cols = ", ".join(STAT_COLS)
    sql = f"""
    SELECT m.Season AS season, f.Match_ID AS match_id, {cols}
    FROM fact_player_match_stats f
    JOIN dim_match m ON m.Match_ID = f.Match_ID
    WHERE f.Player_ID = '{player_id}' AND m.League_ID = {league_id}
    ORDER BY m.Season;
    """
    return run_sql(sql)


def pull_bruno_match_info(player_id: str, league_id: int, season: str):
    sql = f"""
    SELECT m.Match_ID AS match_id, m.Date AS date, m.Match_Round AS round,
           m.Home_Team_ID AS home_id, m.Away_Team_ID AS away_id,
           m.Home_Team_Name AS home_name, m.Away_Team_Name AS away_name,
           m.home_score AS home_score, m.away_score AS away_score
    FROM fact_player_match_stats f
    JOIN dim_match m ON m.Match_ID = f.Match_ID
    WHERE f.Player_ID = '{player_id}' AND m.League_ID = {league_id} AND m.Season = '{season}'
    ORDER BY m.Date;
    """
    return run_sql(sql)


def build_pool(position_rows, min_apps: int):
    """usual_position 众数 in {MID,FWD} 且出场 >= min_apps 的球员名单（按 pid 去重）。"""
    by_player = defaultdict(list)
    for r in position_rows:
        by_player[r["pid"]].append(r)
    pool = {}
    for pid, rows in by_player.items():
        apps = len(rows)
        if apps < min_apps:
            continue
        counts = Counter(r["up"] for r in rows if r["up"] is not None)
        if not counts:
            continue
        top = max(counts.values())
        tied = sorted(k for k, v in counts.items() if v == top)
        mode_up = tied[0]  # 并列取数值更小的一档
        if mode_up not in ("2", "3"):
            continue
        pool[pid] = {
            "pid": pid, "name": rows[0]["name"], "team_id": rows[0]["team_id"],
            "apps": apps, "up_mode": mode_up, "up_tie": len(tied) > 1,
        }
    return pool


def season_avgs(stat_rows, pool: dict):
    """按 pid 聚合：场均 = sum(非空)/apps；NULL 不当 0，缺失率过高时单独标注。"""
    by_player = defaultdict(list)
    for r in stat_rows:
        by_player[r["pid"]].append(r)
    out = {}
    for pid, info in pool.items():
        rows = by_player.get(pid, [])
        apps = info["apps"]
        rec = dict(info)
        for col in STAT_COLS:
            if col == "minutes_played":
                continue
            vals = [r[col] for r in rows if r.get(col) is not None]
            rec[f"{col}_avg"] = (sum(vals) / apps) if apps else None
            rec[f"{col}_cov"] = f"{len(vals)}/{apps}"
        out[pid] = rec
    return out


def rank_of(pool: dict, pid: str, key: str):
    """竞赛排名：比 pid 严格更大的人数 + 1。并列同名次。返回 (rank, pool_size)。"""
    bruno_val = pool[pid][key]
    better = sum(1 for p in pool.values() if p.get(key) is not None and p[key] > bruno_val)
    return better + 1, len(pool)


def compute_age(as_of: date) -> int:
    return as_of.year - BRUNO_BIRTH_DATE.year - (
        (as_of.month, as_of.day) < (BRUNO_BIRTH_DATE.month, BRUNO_BIRTH_DATE.day)
    )


def fmt(v, nd=4):
    return "NULL(缺失)" if v is None else f"{v:.{nd}f}"


def print_item1(bruno_rows):
    by_season = defaultdict(list)
    for r in bruno_rows:
        by_season[r["season"]].append(r)
    print("=== 1. B费历史全部EPL赛季：场均关键传球/xA/助攻 ===")
    print(f"{'赛季':<10}{'场均关键传球':<14}{'场均xA':<10}{'场均助攻':<10}")
    table = {}
    for season in sorted(by_season):
        rs = by_season[season]
        apps = len(rs)
        cc_avg = sum(r["chances_created"] for r in rs if r["chances_created"] is not None) / apps
        xa_avg = sum(r["expected_assists"] for r in rs if r["expected_assists"] is not None) / apps
        ast_avg = sum(r["assists"] for r in rs if r["assists"] is not None) / apps
        table[season] = {"apps": apps, "chances_created_avg": cc_avg, "expected_assists_avg": xa_avg, "assists_avg": ast_avg}
        print(f"{season:<10}{cc_avg:<14.4f}{xa_avg:<10.4f}{ast_avg:<10.4f}(apps={apps})")
    print()
    return table


def print_xa_formula_check(stats_ref, pool_ref_ids, bruno_pid):
    """按你的要求核验 xA 场均口径：贴出一个 xA 部分缺失（cov<apps）的池内球员，
    对比"sum(非空)/apps"（当前用的、正确的口径）跟"sum(非空)/非空场次数"
    （错误口径，若之前用了这个会导致偏高），证明二者确实会给出不同数字、
    确实需要用 apps 做分母。"""
    by_player = defaultdict(list)
    for r in stats_ref:
        by_player[r["pid"]].append(r)
    print("=== 3-验证. xA 场均口径核对（sum(非空)/apps vs 错误口径 sum(非空)/非空场次） ===")
    print(f"season_avgs() 实际代码（{__file__}）：")
    print('    vals = [r[col] for r in rows if r.get(col) is not None]')
    print('    rec[f"{col}_avg"] = (sum(vals) / apps) if apps else None')
    print("  —— 分母恒为 apps（出场场次），不是 len(vals)（非空场次数）。已经是正确口径，无需修正。")
    print("  用一个 xA 部分缺失的池内球员实际验证两种口径确实不同：")
    shown = 0
    for pid, info in pool_ref_ids.items():
        if pid == bruno_pid:
            continue
        rows = by_player.get(pid, [])
        apps = info["apps"]
        vals = [r["expected_assists"] for r in rows if r.get("expected_assists") is not None]
        if 0 < len(vals) < apps:
            correct = sum(vals) / apps
            wrong = sum(vals) / len(vals)
            print(f"    {info['name']}: apps={apps}, xA非空场次={len(vals)}, "
                  f"正确(÷apps)={correct:.4f}, 错误(÷非空场次)={wrong:.4f}, "
                  f"{'一致' if abs(correct-wrong)<1e-9 else f'相差{wrong-correct:+.4f}（错误口径会偏高）'}")
            shown += 1
        if shown >= 5:
            break
    print()


def print_item2(pool_cur: dict, pool_ref: dict, bruno_pid: str, season_cur_label: str, season_ref_label: str):
    print("=== 2. 池内排名（关键传球/xA/助攻，两季对比） ===")
    ranks = {}
    for key, label in [("chances_created_avg", "关键传球"), ("expected_assists_avg", "xA"), ("assists_avg", "助攻")]:
        r_ref, n_ref = rank_of(pool_ref, bruno_pid, key)
        r_cur, n_cur = rank_of(pool_cur, bruno_pid, key)
        ranks[key] = {"ref_rank": r_ref, "ref_pool": n_ref, "cur_rank": r_cur, "cur_pool": n_cur}
        print(f"  {label}: {season_ref_label} 第{r_ref}名(池{n_ref}人) → {season_cur_label} 第{r_cur}名(池{n_cur}人)")
    print()

    print(f"=== 2b. {season_ref_label} 池内第2名 + B费倍数（关键传球、xA） ===")
    seconds = {}
    for key, label in [("chances_created_avg", "关键传球"), ("expected_assists_avg", "xA")]:
        vals = [(p["name"], p[key]) for p in pool_ref.values() if p.get(key) is not None]
        vals.sort(key=lambda t: -t[1])
        bruno_val = pool_ref[bruno_pid][key]
        second = vals[1] if vals[0][0] == pool_ref[bruno_pid]["name"] else vals[0]
        mult = bruno_val / second[1] if second[1] else None
        seconds[key] = {"name": second[0], "value": second[1], "bruno_value": bruno_val, "multiple": mult}
        print(f"  {label}: 第1名 {vals[0][0]}={vals[0][1]:.4f}  第2名 {second[0]}={second[1]:.4f}  "
              f"B费={bruno_val:.4f}  B费/第2名={mult:.3f}倍" if mult else f"  {label}: 第2名值为0，无法计算倍数")
    print()
    return ranks, seconds


def print_item3(match_info, bruno_current_rows, season_label: str):
    stats_by_mid = {r["match_id"]: r for r in bruno_current_rows}
    print(f"=== 3. {season_label} B费逐场：进球/助攻/关键传球 ===")
    print(f"{'轮次':<6}{'日期':<12}{'对阵':<32}{'进球':<6}{'助攻':<6}{'关键传球'}")
    total_goals = 0
    goal_matches = []
    rows_out = []
    for m in match_info:
        s = stats_by_mid.get(m["match_id"])
        if not s:
            continue
        goals = s["goals"] or 0
        total_goals += goals
        if goals:
            goal_matches.append(m)
        opp_str = f"{m['home_name']} {m['home_score']}-{m['away_score']} {m['away_name']}"
        assists = s["assists"] or 0
        cc = s["chances_created"] or 0
        print(f"{m['round']:<6}{m['date']:<12}{opp_str:<32}{goals:.0f}{'':<5}{assists:.0f}{'':<5}{cc:.0f}")
        rows_out.append({
            "round": m["round"], "date": m["date"], "home_name": m["home_name"], "away_name": m["away_name"],
            "home_score": m["home_score"], "away_score": m["away_score"],
            "goals": int(goals), "assists": int(assists), "chances_created": int(cc),
        })
    print(f"  本赛季合计进球: {total_goals:.0f}；发生在: "
          f"{', '.join(f'{m['date']}({m['home_name']} vs {m['away_name']})' for m in goal_matches) if goal_matches else '无'}")
    print()
    return rows_out


def absence_reason(en_name: str, position_rows_for_season, min_apps: int):
    rows = [r for r in position_rows_for_season if r["name"] == en_name]
    if not rows:
        return "本赛季在该联赛无任何出场记录（0 apps，转会/伤缺/未登场，具体原因需人工核实）"
    apps = len(rows)
    counts = Counter(r["up"] for r in rows if r["up"] is not None)
    if not counts:
        return f"出场{apps}场但位置字段缺失，无法判定位置众数"
    top = max(counts.values())
    mode_up = sorted(k for k, v in counts.items() if v == top)[0]
    if mode_up not in ("2", "3"):
        return f"出场{apps}场，位置众数={POS_NAME.get(mode_up, mode_up)}，不属于MID/FWD"
    if apps < min_apps:
        return f"出场{apps}场，低于该赛季阈值{min_apps}场"
    return "满足条件但未在池字典中命中（异常，需人工核查）"


def print_item4(pool_cur: dict, bruno_pid: str, pool_ref: dict, season_cur_label: str, season_ref_label: str,
                 pos_cur, pos_ref, min_apps_current: int, min_apps_reference: int):
    """组合（x=关键传球, y=xA），两季分别列出。"""
    result = {}
    for season_label, pool in [(season_ref_label, pool_ref), (season_cur_label, pool_cur)]:
        bruno = pool[bruno_pid]
        bx, by = bruno["chances_created_avg"], bruno["expected_assists_avg"]
        print(f"=== 4. 组合（x=场均关键传球, y=场均xA）—— {season_label} ===")
        print(f"  B费坐标: x={bx:.4f}, y={by:.4f}, apps={bruno['apps']}")
        ahead = [p for p in pool.values()
                 if p.get("chances_created_avg") is not None and p.get("expected_assists_avg") is not None
                 and p["pid"] != bruno_pid
                 and p["chances_created_avg"] > bx and p["expected_assists_avg"] > by]
        ahead.sort(key=lambda p: -p["expected_assists_avg"])
        print(f"  两维都领先B费的球员（{len(ahead)}人）：")
        for p in ahead:
            print(f"    {p['name']}: x={p['chances_created_avg']:.4f}, y={p['expected_assists_avg']:.4f}, apps={p['apps']}")
        result[season_label] = {
            "bruno": {"x": bx, "y": by, "apps": bruno["apps"]},
            "ahead": [{"name": p["name"], "team_id": p["team_id"], "pid": p["pid"],
                       "x": p["chances_created_avg"], "y": p["expected_assists_avg"], "apps": p["apps"]} for p in ahead],
        }
        print()

    print(f"  池内知名球员坐标（{season_ref_label} / {season_cur_label}）：")
    stars_out = {}
    for zh, en in STARS.items():
        stars_out[zh] = {}
        print(f"    {zh}:")
        for label, pool, pos_rows, min_apps in [
            (season_ref_label, pool_ref, pos_ref, min_apps_reference),
            (season_cur_label, pool_cur, pos_cur, min_apps_current),
        ]:
            hit = next((p for p in pool.values() if p["name"] == en), None)
            if hit:
                x, y = hit["chances_created_avg"], hit["expected_assists_avg"]
                print(f"      {label}: x={x:.4f}, y={y:.4f}, apps={hit['apps']}")
                stars_out[zh][label] = {"present": True, "x": x, "y": y, "apps": hit["apps"], "team_id": hit["team_id"], "pid": hit["pid"]}
            else:
                reason = absence_reason(en, pos_rows, min_apps)
                print(f"      {label}: 不在池内 —— {reason}")
                stars_out[zh][label] = {"present": False, "reason": reason}
    print()
    return result, stars_out


def print_item5(bruno_career: dict, season_cur_label: str, season_ref_label: str):
    """P3 用：7季关键传球柱状图数据 + '打回原形'判定 + xA 迷你柱状图数据。"""
    print("=== 5. P3 柱状图数据：7季关键传球 + '打回原形'判定 + xA 迷你图 ===")
    cc_series = [(s, v["chances_created_avg"]) for s, v in sorted(bruno_career.items())]
    xa_series = [(s, v["expected_assists_avg"]) for s, v in sorted(bruno_career.items())]
    best_season, best_val = max(cc_series, key=lambda t: t[1])
    cur_val = bruno_career[season_cur_label]["chances_created_avg"]
    echoes = [(s, v) for s, v in cc_series if s != season_cur_label and abs(v - cur_val) < 0.15]
    print(f"  生涯最佳(关键传球): {best_season} = {best_val:.4f}")
    print(f"  本赛季({season_cur_label})关键传球 = {cur_val:.4f}（虚线基准）")
    print(f"  与本赛季差值<0.15的历史赛季（'打回原形'标记）：")
    for s, v in echoes:
        print(f"    {s}: {v:.4f}（差值{v-cur_val:+.4f}）")
    print(f"  xA 7季序列（迷你图）: " + " / ".join(f"{s}={v:.4f}" for s, v in xa_series))
    print()
    return {
        "cc_series": cc_series, "xa_series": xa_series,
        "best_season": best_season, "best_val": best_val,
        "cur_val": cur_val, "echo_seasons": [s for s, v in echoes],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league-id", type=int, default=47, help="联赛ID，默认英超47")
    ap.add_argument("--player-id", default="422685", help="B费 Player_ID")
    ap.add_argument("--season-current", default="2026/2027", help="进行中的本赛季")
    ap.add_argument("--season-reference", default="2025/2026", help="用于对比的参照赛季（已完整踢完）")
    ap.add_argument("--min-apps-current", type=int, default=3, help="本赛季基准池最少出场场次阈值")
    ap.add_argument("--min-apps-reference", type=int, default=15, help="参照赛季基准池最少出场场次阈值")
    ap.add_argument("--json-out", default=None, help="额外写一份结构化 JSON 供出图脚本注入数值")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    print(f"# 刷新时间: {now.isoformat()}Z （数据直接来自生产库 {DB_PATH}，非缓存）")
    print(f"# 联赛={args.league_id}  本赛季={args.season_current}(min_apps>={args.min_apps_current})  "
          f"参照赛季={args.season_reference}(min_apps>={args.min_apps_reference})")
    age = compute_age(now.date())
    print(f"# B费出生日期: {BRUNO_BIRTH_DATE.isoformat()}（来源：公开资料，常量写在脚本头部）  当前年龄: {age}岁")
    print()

    seasons = [args.season_current, args.season_reference]
    print("# 拉取全联赛位置窄表(用于定池)...", file=sys.stderr)
    position_rows = pull_position_rows(args.league_id, seasons)

    pos_cur = [r for r in position_rows if r["season"] == args.season_current]
    pos_ref = [r for r in position_rows if r["season"] == args.season_reference]

    pool_cur_ids = build_pool(pos_cur, args.min_apps_current)
    pool_ref_ids = build_pool(pos_ref, args.min_apps_reference)
    bruno_pos_cur = [r for r in pos_cur if r["pid"] == args.player_id]
    if args.player_id not in pool_cur_ids and bruno_pos_cur:
        pool_cur_ids[args.player_id] = {
            "pid": args.player_id, "name": bruno_pos_cur[0]["name"],
            "team_id": bruno_pos_cur[0]["team_id"], "apps": len(bruno_pos_cur),
            "up_mode": "2", "up_tie": False,
        }
    bruno_pos_ref = [r for r in pos_ref if r["pid"] == args.player_id]
    if args.player_id not in pool_ref_ids and bruno_pos_ref:
        pool_ref_ids[args.player_id] = {
            "pid": args.player_id, "name": bruno_pos_ref[0]["name"],
            "team_id": bruno_pos_ref[0]["team_id"], "apps": len(bruno_pos_ref),
            "up_mode": "2", "up_tie": False,
        }

    pid_whitelist = sorted(set(pool_cur_ids) | set(pool_ref_ids) | {args.player_id})
    print(f"# 拉取{len(pid_whitelist)}名池内球员的完整统计宽表...", file=sys.stderr)
    stat_rows = pull_stat_rows(args.league_id, seasons, pid_whitelist)
    stats_cur = [r for r in stat_rows if r["season"] == args.season_current]
    stats_ref = [r for r in stat_rows if r["season"] == args.season_reference]

    pool_cur = season_avgs(stats_cur, pool_cur_ids)
    pool_ref = season_avgs(stats_ref, pool_ref_ids)

    if args.player_id not in pool_cur or args.player_id not in pool_ref:
        print("!! B费本人在生产库中缺少对应赛季数据，无法计算，脚本终止。", file=sys.stderr)
        sys.exit(1)

    bruno_all_rows = pull_bruno_all_seasons(args.player_id, args.league_id)
    career_table = print_item1(bruno_all_rows)

    print_xa_formula_check(stats_ref, pool_ref_ids, args.player_id)

    ranks, seconds = print_item2(pool_cur, pool_ref, args.player_id, args.season_current, args.season_reference)

    match_info = pull_bruno_match_info(args.player_id, args.league_id, args.season_current)
    bruno_current_rows = [r for r in stats_cur if r["pid"] == args.player_id]
    matches_out = print_item3(match_info, bruno_current_rows, args.season_current)

    combo, stars_out = print_item4(pool_cur, args.player_id, pool_ref, args.season_current, args.season_reference,
                                    pos_cur, pos_ref, args.min_apps_current, args.min_apps_reference)

    bar_chart = print_item5(career_table, args.season_current, args.season_reference)

    print(f"# 基准池规模: 本赛季({args.season_current})={len(pool_cur)}人  参照赛季({args.season_reference})={len(pool_ref)}人")

    if args.json_out:
        payload = {
            "generated_at": now.isoformat(),
            "league_id": args.league_id,
            "season_current": args.season_current,
            "season_reference": args.season_reference,
            "bruno_birth_date": BRUNO_BIRTH_DATE.isoformat(),
            "bruno_age": age,
            "pool_size_current": len(pool_cur),
            "pool_size_reference": len(pool_ref),
            "career_table": career_table,
            "ranks": ranks,
            "seconds": seconds,
            "matches": matches_out,
            "combo": combo,
            "stars": stars_out,
            "bar_chart": bar_chart,
            "bruno_team_id": pool_cur[args.player_id]["team_id"],
            "bruno_pid": args.player_id,
            "pool_scatter": {
                args.season_reference: [
                    {"pid": p["pid"], "x": p["chances_created_avg"], "y": p["expected_assists_avg"],
                     "name": p["name"], "team_id": p["team_id"], "apps": p["apps"]}
                    for p in pool_ref.values() if p["pid"] != args.player_id
                    and p.get("chances_created_avg") is not None and p.get("expected_assists_avg") is not None
                ],
                args.season_current: [
                    {"pid": p["pid"], "x": p["chances_created_avg"], "y": p["expected_assists_avg"],
                     "name": p["name"], "team_id": p["team_id"], "apps": p["apps"]}
                    for p in pool_cur.values() if p["pid"] != args.player_id
                    and p.get("chances_created_avg") is not None and p.get("expected_assists_avg") is not None
                ],
            },
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"# JSON 已写入: {args.json_out}")


if __name__ == "__main__":
    main()
