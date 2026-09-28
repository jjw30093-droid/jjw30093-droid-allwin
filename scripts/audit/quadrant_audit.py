#!/usr/bin/env python3
"""只读审计:miaomiaodi.vip 三处象限图(球队页/球员页/比赛页)视角是否重复。

不修改任何代码、不写库。直接调用生产代码里真正驱动这三张图的函数
(backend.queries.league_stats.team_season_stats / backend.queries.player_quadrant.
player_quadrant_stats / backend.queries.team_style_preview.league_style_views),
保证取到的数字与线上完全同源,不是重新猜一遍 SQL。

用法(在仓库根目录,只读连接生产库或本地库):
    python3 scripts/audit/quadrant_audit.py --data-dir /opt/allwin/shared/data

2026-09-28 首次运行(生产库,只读)结论存档见 docs/current-state.md。此后维护:
新增/删除/改轴的视角时同步更新本文件的 TEAM_VIEWS / MATCH_VIEWS / PLAYER_VIEWS,
不要再复制脚本单独跑一份、跑完就扔——这份脚本本身进 git,历史可比对。
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from backend.queries.league_stats import team_season_stats  # noqa: E402
from backend.queries.player_quadrant import player_quadrant_stats  # noqa: E402
from backend.queries.team_style_preview import league_style_views  # noqa: E402

LEAGUES = {47: "英超", 53: "法甲", 54: "德甲", 55: "意甲", 87: "西甲"}
SEASON = "2025/2026"
SEASON_END_BEFORE = "2026-12-31T00:00:00Z"  # 足够晚,配合 window=42 取到该赛季全部已完赛比赛
FULL_WINDOW = 42  # > 38 轮顶级联赛全季场次,不会截断


def open_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


# ------------------------------------------------------------------ Spearman(纯 Python,服务器无 numpy/scipy)
def rank(vals: list[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]):
    n = len(a)
    if n < 5:
        return None, n
    ra, rb = rank(a), rank(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    if va <= 0 or vb <= 0:
        return None, n
    return cov / math.sqrt(va * vb), n


def paired(dict_a: dict, dict_b: dict):
    """两个 {key: value} 按公共 key 配对,丢掉任一缺失的。"""
    keys = [k for k in dict_a if k in dict_b and dict_a[k] is not None and dict_b[k] is not None]
    return [dict_a[k] for k in keys], [dict_b[k] for k in keys], keys


# ------------------------------------------------------------------ 团队级指标定义(与 frontend/components/league/teamMetrics.ts 逐字对应)
def team_metrics(row: dict) -> dict[str, float | None]:
    r = row.get("ratios") or {}

    def rv(key):
        d = r.get(key)
        return num(d.get("value")) if d else None

    xg = num(row.get("avg_expected_goals"))
    shots = num(row.get("avg_total_shots"))
    xga = num(row.get("avg_expected_goals_conceded"))
    non_pen_xg = num(row.get("avg_expected_goals_non_penalty"))
    m: dict[str, float | None] = {
        "shotsOnTarget": num(row.get("avg_shots_on_target")),
        "cleanSheets": num(row.get("clean_sheets")),
        "bttsPct": num(row.get("btts_pct")),
        "nonPenXg": non_pen_xg,
        "corners": num(row.get("avg_corners")),
        "xgot": num(row.get("avg_expected_goals_on_target")),
        "possession": num(row.get("avg_possession")),
        "fouls": num(row.get("avg_fouls")),
        "penXg": (max(0.0, xg - non_pen_xg) if xg is not None and non_pen_xg is not None else None),
        "xgPerShot": (xg / shots if xg is not None and shots not in (None, 0) else None),
        "xg": xg,
        "xga": xga,
        "openPlayXg": num(row.get("avg_expected_goals_open_play")),
        "setPlayXg": num(row.get("avg_expected_goals_set_play")),
        "totalShots": shots,
        "oppHalfPassShare": rv("opp_half_pass_share"),
        "setPieceXgShare": rv("set_piece_xg_share"),
        "setPieceXgaShare": rv("set_piece_xga_share"),
        "oppXgPerShot": rv("opp_xg_per_shot"),
        "shotAccuracy": rv("shot_accuracy"),
        "boxShotShare": rv("box_shot_share"),
        "bigChanceConversion": rv("big_chance_conversion"),
        "aerialWinShare": rv("aerial_win_share"),
        "fastBreakXgShare": rv("fast_break_xg_share"),
        "boxTouchShare": rv("box_touch_share"),
        "npxgPerBoxTouch": rv("npxg_per_box_touch"),
        "defActionDensity": rv("def_action_density"),
        "oppTerritoryShare": rv("opp_territory_share"),
        "cornerShotRate": rv("corner_shot_rate"),
        "finishingDelta": rv("finishing_delta"),
        "gkSavesAboveExpected": rv("gk_saves_above_expected"),
    }
    fd = r.get("finishing_delta")
    mp = num(row.get("matches_played"))
    sc = num(fd.get("sample_count")) if fd else None
    m["nonPenaltyShotsPerMatch"] = (sc / mp) if (sc is not None and mp not in (None, 0)) else None
    return m


TEAM_VIEWS = [
    # (id, x_key, y_key, tab, title)
    ("both-ends", "xg", "xga", "攻守 xG", "预期进球 × 预期失球"),
    ("tactics", "openPlayXg", "setPlayXg", "战术", "运动战 × 定位球"),
    # 2026-09-28 修复对角线化(原 y=xg 与 x=totalShots 曾 r=0.849):
    # y 改为每脚 xG(比值,不随射门量机械上升)。
    ("volume", "totalShots", "xgPerShot", "多射还是精射", "射门数量 × 每脚质量"),
    ("possession-passing", "oppHalfPassShare", "totalShots", "推进方式", "前场传球占比"),
    ("set-piece-both-ends", "setPieceXgShare", "setPieceXgaShare", "定位球攻防", "定位球攻防"),
    ("attack-defence-quality", "xgPerShot", "oppXgPerShot", "攻防质量", "攻防质量"),
    ("shot-quality-accuracy", "xgPerShot", "shotAccuracy", "质量与准星", "质量与准星"),
    ("box-shots", "boxShotShare", "totalShots", "禁区内外", "禁区内外"),
    ("fast-break-possession", "fastBreakXgShare", "possession", "反击与控球", "反击与控球"),
    ("chance-conversion", "xgPerShot", "bigChanceConversion", "机会转化", "机会转化"),
    ("aerial-physicality", "aerialWinShare", "fouls", "空中对抗", "空中对抗"),
    ("box-pressure", "boxTouchShare", "npxgPerBoxTouch", "禁区压制", "禁区压制"),
    ("territory-pressing", "oppTerritoryShare", "defActionDensity", "阵地与拼抢", "阵地与拼抢"),
    ("corner-quality", "cornerShotRate", "corners", "角球成色", "角球成色"),
    ("finishing-record", "nonPenaltyShotsPerMatch", "finishingDelta", "终结记录", "终结记录"),
    ("defence-goalkeeping", "oppXgPerShot", "gkSavesAboveExpected", "防线与门将", "防线与门将"),
]

# 比赛页视角:x/y 来自 league_style_views 直接返回的 x/y(已在函数内算好)。
# 2026-09-28:原第三个视角"xg-for-against"(攻守 xG)已删除对应的 team_style_
# preview.py 实现,改为比赛页直接复用球队页 both-ends 视角的组件与
# team_season_stats 数据(TeamStyleQuadrant.tsx)——不再是 league_style_views
# 产出的独立视角,因此这里也不再列出;它现在已经等同于 TEAM_VIEWS 里的
# both-ends,由那一行覆盖。
MATCH_VIEWS = [
    ("poss-fastbreak", "控球 × 快攻", "控球率 % × 快攻射门占比 %"),
    ("cross-box", "传中 × 禁区触球", "场均成功传中 × 场均禁区触球"),
]

PLAYER_VIEWS = [
    # (id, x_key, y_key, tab, title, positions)
    ("player-shooting-finishing", "npxgPer90", "finishingDeltaPer90", "射门与终结", "每90分钟非点球xG × 终结超额", (2, 3)),
    # 2026-09-28 修复对角线化(原 y=xaPer90 与 x=chancesCreatedPer90 曾
    # r=0.896):y 改为 xaPerChanceCreated = xA 累计 ÷ 创造机会累计(比值,
    # 创造机会<5 次记 None——与前端 PLAYER_METRICS.xaPerChanceCreated 同一
    # 口径,见 player_metric_value_special() 里对这个 key 的特殊处理)。
    ("player-creativity", "chancesCreatedPer90", "xaPerChanceCreated", "进攻创造力", "每90分钟创造机会数 × 每次创造的xA", (1, 2, 3)),
    ("player-defensive-contribution", "defensiveActionsPer90", "duelWinRate", "防守贡献", "每90分钟防守动作 × 对抗成功率", (1, 2)),
    ("player-progression", "touchesPer90", "progressionRate", "持球推进", "每90分钟触球数 × 每百次触球送进前场传球数", (1, 2)),
    ("player-goalkeeping", "xgotFacedPer90", "goalsPreventedPer90", "门将扑救", "每90分钟面对射正预期进球 × 扑救超额", (0,)),
    ("player-goalkeeper-distribution", "passCompletionRate", "longBallShare", "门将出球", "传球成功率 × 长传占比", (0,)),
]

# 与 frontend/components/league/quadrantViews.ts::VIEW_GROUPS 的 label 逐字对应
# (Python 侧无法直接 import TS 常量,靠这份注释人工保持同步——改动分组名时
# 记得同步改这里)。both-ends 的 tab 已改名,不再是这些类别名的子串。
VIEW_GROUP_LABELS = ["攻防总览", "进攻构成", "射门质量", "控球与推进", "防守承压"]


def player_metric_value(ratios: dict | None, key: str) -> float | None:
    if key == "xaPerChanceCreated":
        return _xa_per_chance_created(ratios)
    if not ratios:
        return None
    d = ratios.get(key)
    return num(d.get("value")) if d else None


def _xa_per_chance_created(ratios: dict | None) -> float | None:
    """与 frontend/components/league/playerMetrics.ts::xaPerChanceCreated
    同一口径:xA 累计 numerator ÷ 创造机会累计 numerator,创造机会<5 次记 None。"""
    if not ratios:
        return None
    xa = ratios.get("xa_per90")
    ch = ratios.get("chances_created_per90")
    xa_num = num(xa.get("numerator")) if xa else None
    ch_num = num(ch.get("numerator")) if ch else None
    if xa_num is None or ch_num is None or ch_num < 5:
        return None
    return xa_num / ch_num


PLAYER_RATIO_KEY_MAP = [
    ("npxgPer90", "npxg_per90"), ("finishingDeltaPer90", "finishing_delta_per90"),
    ("chancesCreatedPer90", "chances_created_per90"), ("xaPer90", "xa_per90"),
    ("xaPerChanceCreated", "xaPerChanceCreated"),  # 见 player_metric_value() 的特殊处理
    ("defensiveActionsPer90", "defensive_actions_per90"), ("duelWinRate", "duel_win_rate"),
    ("touchesPer90", "touches_per90"), ("progressionRate", "progression_rate"),
    ("xgotFacedPer90", "xgot_faced_per90"), ("goalsPreventedPer90", "goals_prevented_per90"),
    ("passCompletionRate", "pass_completion_rate"), ("longBallShare", "long_ball_share"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    core = open_ro(data_dir / "allwin.db")

    # ---------------------------------------------------------------- 取数(全部只读)
    team_metric_pool: dict[str, dict[str, float | None]] = {}  # "L{lid}:T{tid}" -> {metric: value}
    for lid in LEAGUES:
        res = team_season_stats(core, lid, SEASON)
        for row in res["rows"]:
            tid = row["team"]["team_id"]
            key = f"L{lid}:T{tid}"
            team_metric_pool[key] = team_metrics(row)
    n_teams = len(team_metric_pool)
    print(f"取到球队(全部 5 联赛,{SEASON}):{n_teams} 支")

    match_metric_pool: dict[str, dict[str, float | None]] = {}
    for lid in LEAGUES:
        views = league_style_views(core, lid, SEASON, SEASON_END_BEFORE, window=FULL_WINDOW)
        for v in views:
            for p in v["points"]:
                key = f"L{lid}:T{p['team_id']}"
                match_metric_pool.setdefault(key, {})[f"{v['id']}__x"] = num(p["x"])
                match_metric_pool.setdefault(key, {})[f"{v['id']}__y"] = num(p["y"])
    print(f"取到比赛页象限图球队点(全部 5 联赛):{len(match_metric_pool)} 支")

    player_pool: dict[int, dict[str, dict[str, float | None]]] = defaultdict(dict)  # pos -> key -> {metric: v}
    excluded_total = 0
    for lid in LEAGUES:
        res = player_quadrant_stats(core, lid, SEASON)
        excluded_total += res.get("excluded_below_floor", 0)
        for row in res["rows"]:
            share = row.get("minutes_share")
            if share is None or share < 0.4:
                continue
            pos = row.get("usual_position")
            if pos is None:
                continue
            pid = row["player"]["player_id"]
            key = f"L{lid}:P{pid}"
            m = {vid_x: player_metric_value(row["ratios"], mk) for (vid_x, mk) in PLAYER_RATIO_KEY_MAP}
            player_pool[pos][key] = m
    for pos in (0, 1, 2, 3):
        print(f"  达 40% 出场占比门槛的球员(位置={pos}):{len(player_pool[pos])} 人")

    # ---------------------------------------------------------------- 1. 配置表
    print("\n" + "=" * 20 + " 1. 视角配置表 " + "=" * 20)
    rows_cfg = []
    for vid, xk, yk, tab, title in TEAM_VIEWS:
        rows_cfg.append((vid, "球队页", tab, title, xk, yk,
                         "y 反转" if vid in ("both-ends", "attack-defence-quality", "defence-goalkeeping") else "均不反转",
                         "场均/比率(见下)", "本赛季全部"))
    for vid, tab, title in MATCH_VIEWS:
        rows_cfg.append((vid, "比赛页", tab, title, "x(见下)", "y(见下)",
                         "y 反转(仅 xg-for-against)" if vid == "xg-for-against" else "不反转",
                         "场均(近N场滚动)", f"近{FULL_WINDOW}场≈整季"))
    for vid, xk, yk, tab, title, poss in PLAYER_VIEWS:
        rows_cfg.append((vid, "球员页", tab, title, xk, yk,
                         "x 反转" if vid == "player-goalkeeping" else "不反转",
                         "每90分钟/比率", f"位置池{poss}"))
    print(f"{'视角key':30s} {'页面':6s} {'显示名':10s} {'x轴':30s} {'y轴':30s} {'轴方向':22s} {'聚合口径':10s} {'默认样本窗口':14s}")
    for vid, page, tab, title, xk, yk, dirn, agg, win in rows_cfg:
        print(f"{vid:30s} {page:6s} {tab:10s} {xk:30s} {yk:30s} {dirn:22s} {agg:10s} {win:14s}")

    # ---------------------------------------------------------------- 团队级 metric 值池(id -> array 配对用字典)
    team_axis_values: dict[str, dict[str, float]] = {}
    for vid, xk, yk, tab, title in TEAM_VIEWS:
        team_axis_values[f"{vid}:x"] = {k: v.get(xk) for k, v in team_metric_pool.items() if v.get(xk) is not None}
        team_axis_values[f"{vid}:y"] = {k: v.get(yk) for k, v in team_metric_pool.items() if v.get(yk) is not None}
    for vid, tab, title in MATCH_VIEWS:
        team_axis_values[f"{vid}:x"] = {k: v.get(f"{vid}__x") for k, v in match_metric_pool.items() if v.get(f"{vid}__x") is not None}
        team_axis_values[f"{vid}:y"] = {k: v.get(f"{vid}__y") for k, v in match_metric_pool.items() if v.get(f"{vid}__y") is not None}

    player_axis_values: dict[str, dict[str, float]] = {}
    for vid, xk, yk, tab, title, poss in PLAYER_VIEWS:
        pool: dict[str, dict[str, float | None]] = {}
        for pos in poss:
            pool.update(player_pool[pos])
        player_axis_values[f"{vid}:x"] = {k: v.get(xk) for k, v in pool.items() if v.get(xk) is not None}
        player_axis_values[f"{vid}:y"] = {k: v.get(yk) for k, v in pool.items() if v.get(yk) is not None}

    # ---------------------------------------------------------------- 2a. 视角内相关性
    print("\n" + "=" * 20 + " 2a. 视角内部 x×y Spearman " + "=" * 20)
    print(f"{'视角key':30s} {'页面':6s} {'N':>5s} {'r':>8s}  标记")
    all_views_team = [(vid, "球队页") for vid, *_ in TEAM_VIEWS] + [(vid, "比赛页") for vid, *_ in MATCH_VIEWS]
    for vid, page in all_views_team:
        a, b, keys = paired(team_axis_values[f"{vid}:x"], team_axis_values[f"{vid}:y"])
        r, n = spearman(a, b)
        flag = "对角线化，象限无效" if (r is not None and abs(r) > 0.7) else ""
        print(f"{vid:30s} {page:6s} {n:5d} {('%.3f' % r) if r is not None else 'n/a':>8s}  {flag}")
    for vid, xk, yk, tab, title, poss in PLAYER_VIEWS:
        a, b, keys = paired(player_axis_values[f"{vid}:x"], player_axis_values[f"{vid}:y"])
        r, n = spearman(a, b)
        flag = "对角线化，象限无效" if (r is not None and abs(r) > 0.7) else ""
        print(f"{vid:30s} {'球员页':6s} {n:5d} {('%.3f' % r) if r is not None else 'n/a':>8s}  {flag}")

    # ---------------------------------------------------------------- 2b/2c. 视角间重复
    print("\n" + "=" * 20 + " 2b/2c. 视角间重复对 " + "=" * 20)
    team_view_ids = [vid for vid, *_ in TEAM_VIEWS] + [vid for vid, *_ in MATCH_VIEWS]
    team_axis_metric = {vid: (xk, yk) for vid, xk, yk, *_ in TEAM_VIEWS}
    for vid, tab, title in MATCH_VIEWS:
        team_axis_metric[vid] = (f"{vid}__x", f"{vid}__y")

    dup_count = 0
    print("-- 团队级视角两两比较(球队页 16 + 比赛页 3 = 19 个,C(19,2)=171 对) --")
    for i in range(len(team_view_ids)):
        for j in range(i + 1, len(team_view_ids)):
            va, vb = team_view_ids[i], team_view_ids[j]
            same_metric = team_axis_metric[va] == team_axis_metric[vb] or team_axis_metric[va] == team_axis_metric[vb][::-1]
            xa, ya = team_axis_values[f"{va}:x"], team_axis_values[f"{va}:y"]
            xb, yb = team_axis_values[f"{vb}:x"], team_axis_values[f"{vb}:y"]
            r_xx, n_xx = spearman(*paired(xa, xb)[:2])
            r_yy, n_yy = spearman(*paired(ya, yb)[:2])
            r_xy, n_xy = spearman(*paired(xa, yb)[:2])
            r_yx, n_yx = spearman(*paired(ya, xb)[:2])
            match1 = (r_xx is not None and abs(r_xx) > 0.8) and (r_yy is not None and abs(r_yy) > 0.8)
            match2 = (r_xy is not None and abs(r_xy) > 0.8) and (r_yx is not None and abs(r_yx) > 0.8)
            if same_metric or match1 or match2:
                dup_count += 1
                reason = "轴指标完全相同" if same_metric else ("x↔x,y↔y 均>0.8" if match1 else "x↔y,y↔x 均>0.8")
                print(f"  [重复] {va} × {vb}  原因={reason}")
                print(f"      x-x r={r_xx if r_xx is None else round(r_xx,3)}(n={n_xx})  y-y r={r_yy if r_yy is None else round(r_yy,3)}(n={n_yy})  "
                      f"x-y r={r_xy if r_xy is None else round(r_xy,3)}(n={n_xy})  y-x r={r_yx if r_yx is None else round(r_yx,3)}(n={n_yx})")
    print(f"  团队级重复对总数:{dup_count}")

    print("-- 球员级视角两两比较(6 个,只在位置池有重叠时比较) --")
    dup_count_p = 0
    for i in range(len(PLAYER_VIEWS)):
        for j in range(i + 1, len(PLAYER_VIEWS)):
            va = PLAYER_VIEWS[i]
            vb = PLAYER_VIEWS[j]
            overlap = set(va[5]) & set(vb[5])
            if not overlap:
                print(f"  {va[0]} × {vb[0]}: 位置池不重叠({va[5]} vs {vb[5]}),跳过")
                continue
            same_metric = {va[1], va[2]} == {vb[1], vb[2]}
            xa, ya = player_axis_values[f"{va[0]}:x"], player_axis_values[f"{va[0]}:y"]
            xb, yb = player_axis_values[f"{vb[0]}:x"], player_axis_values[f"{vb[0]}:y"]
            r_xx, n_xx = spearman(*paired(xa, xb)[:2])
            r_yy, n_yy = spearman(*paired(ya, yb)[:2])
            r_xy, n_xy = spearman(*paired(xa, yb)[:2])
            r_yx, n_yx = spearman(*paired(ya, xb)[:2])
            match1 = (r_xx is not None and abs(r_xx) > 0.8) and (r_yy is not None and abs(r_yy) > 0.8)
            match2 = (r_xy is not None and abs(r_xy) > 0.8) and (r_yx is not None and abs(r_yx) > 0.8)
            tag = "[重复]" if (same_metric or match1 or match2) else "[不重复]"
            if same_metric or match1 or match2:
                dup_count_p += 1
            print(f"  {tag} {va[0]} × {vb[0]}(重叠位置池 {sorted(overlap)})  "
                  f"x-x r={r_xx if r_xx is None else round(r_xx,3)}(n={n_xx})  y-y r={r_yy if r_yy is None else round(r_yy,3)}(n={n_yy})  "
                  f"x-y r={r_xy if r_xy is None else round(r_xy,3)}(n={n_xy})  y-x r={r_yx if r_yx is None else round(r_yx,3)}(n={n_yx})")
    print(f"  球员级重复对总数:{dup_count_p}")

    # ---------------------------------------------------------------- 命名问题
    print("\n" + "=" * 20 + " 3. 命名审计:视角间 + 视角与类别名 " + "=" * 20)
    all_tabs = [(vid, tab, team_axis_metric.get(vid)) for vid, xk, yk, tab, title in TEAM_VIEWS]
    all_tabs += [(vid, tab, team_axis_metric.get(vid)) for vid, tab, title in MATCH_VIEWS]
    all_tabs += [(vid, tab, (xk, yk)) for vid, xk, yk, tab, title, poss in PLAYER_VIEWS]
    found_name = False
    print("-- 3a. 视角两两之间(显示名相同/互为子串,且轴不同) --")
    for i in range(len(all_tabs)):
        for j in range(i + 1, len(all_tabs)):
            vid_a, tab_a, ax_a = all_tabs[i]
            vid_b, tab_b, ax_b = all_tabs[j]
            if tab_a == tab_b and ax_a != ax_b:
                found_name = True
                print(f"  「{tab_a}」同名:{vid_a}({ax_a}) vs {vid_b}({ax_b})")
            elif (tab_a in tab_b or tab_b in tab_a) and ax_a != ax_b:
                found_name = True
                print(f"  「{tab_a}」⊂「{tab_b}」高度相似:{vid_a}({ax_a}) vs {vid_b}({ax_b})")
    if not found_name:
        print("  无:未发现显示名相同/互为子串但轴不同的视角对。")

    print("-- 3b. 视角名 vs 类别名(VIEW_GROUPS) --")
    found_group = False
    for vid, tab, _ax in all_tabs:
        for glabel in VIEW_GROUP_LABELS:
            if tab == glabel:
                found_group = True
                print(f"  视角「{tab}」({vid}) 与类别「{glabel}」同名")
            elif tab in glabel or glabel in tab:
                found_group = True
                print(f"  视角「{tab}」({vid}) 与类别「{glabel}」互为子串")
    if not found_group:
        print("  无:未发现视角名与类别名相同或互为子串。")

    print(f"\n[数据说明] 球员池已按站点自身逻辑排除出场占比<40%的球员(汇总 excluded_below_floor={excluded_total},"
          f"含低于后端 15% 下限已不下发的部分)。团队级样本为 5 联赛全部球队(N≈{n_teams}),未额外施加各视角自身的"
          f"minVolume 门槛(只按 x/y 双非空配对),比 UI 实际展示时的样本可能略宽松。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
