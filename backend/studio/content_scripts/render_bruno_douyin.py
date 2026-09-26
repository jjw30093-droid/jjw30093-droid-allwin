"""render_bruno_douyin：B费"下滑有多严重" NO.02 抖音版（1080x1920）。

安全区规则（无 skill 文件可读，按站长本轮消息里的详细规格直接实现，
2026-09-24 已用 AskUserQuestion 向站长确认过）：信息元素只放在
x 64-880、y 240-1440；1440-1920 只放背景色+渐变；右侧 x>=900 且 y>=880
不放任何信息。复用 render_series.py 的 PLATFORMS["douyin"]["safezone"]
（同一份坐标）和 _chrome.html.j2 的 douyin_brand/identity_bar/douyin_caveat/
safezone_debug 宏——这几个宏已经是这份安全区规则的既有实现，不重新发明。

中文名/球队名一律来自 bruno_names.py（逐个 Player_ID/Team_ID 查
dim_player_i18n/dim_team_i18n 核对过，不是手工音译）。
"""
from __future__ import annotations

import json
from pathlib import Path

from render_series import CACHE_DIR, OUT_DIR, PLATFORMS, render_page, shoot, text_px_width
from render_bruno_series import avatar_abs, badge_abs, fit_font
from bruno_names import CHINESE_SHORT, CHINESE_FULL, TEAM_ZH

DATA_PATH = CACHE_DIR / "bruno_data.json"

HULL_PROMOTED = True
IPSWICH_PROMOTED = True
BOTH_PROMOTED = HULL_PROMOTED and IPSWICH_PROMOTED
MU_RESULTS = {"Hull City": "负", "Ipswich Town": "胜", "Everton": "平",
              "Manchester City": "负", "Fulham": "平"}
ZERO_MATCHES_RECORD = "两平两负，一胜未取"

SAFEZONE = PLATFORMS["douyin"]["safezone"]
CONTENT_X0, CONTENT_Y0, CONTENT_X1, CONTENT_Y1 = SAFEZONE["content"]  # 64,240,880,1440
CONTENT_W = CONTENT_X1 - CONTENT_X0  # 816


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug-safezone", action="store_true", dest="debug_safezone")
    args = ap.parse_args()

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    season_cur, season_ref = data["season_current"], data["season_reference"]
    age = data["bruno_age"]

    plat_cfg = PLATFORMS["douyin"]
    CANVAS_W, CANVAS_H = plat_cfg["w"], plat_cfg["h"]
    plat_out_dir = OUT_DIR / "bruno" / "douyin"
    suffix = "_debug" if args.debug_safezone else ""
    print(f"=== 平台: douyin  画布: {CANVAS_W}x{CANVAS_H}  debug_safezone={args.debug_safezone}  输出目录: {plat_out_dir} ===")

    COLUMN_NAME, ISSUE_NO = "喵弟球员体检", "NO.02"
    bruno_team_id = data["bruno_team_id"]
    bruno_avatar, bruno_badge = avatar_abs("422685"), badge_abs(bruno_team_id)
    last_date = data["matches"][-1]["date"]
    caveat = f"数据：英超，截至 {last_date}"

    common_ctx = dict(
        logo_path=str(CACHE_DIR / "logo-mark.png"), column_name=COLUMN_NAME, issue_no=ISSUE_NO,
        platform="douyin", canvas_w=CANVAS_W, canvas_h=CANVAS_H, safezone=SAFEZONE,
        debug_safezone=args.debug_safezone,
        bruno_avatar=bruno_avatar, bruno_badge=bruno_badge, bruno_age=age,
        footer_note=caveat,
    )

    ranks = data["ranks"]
    career = dict(data["career_table"])
    cc_ref, cc_cur = career[season_ref]["chances_created_avg"], career[season_cur]["chances_created_avg"]
    xa_ref, xa_cur = career[season_ref]["expected_assists_avg"], career[season_cur]["expected_assists_avg"]
    ast_ref, ast_cur = career[season_ref]["assists_avg"], career[season_cur]["assists_avg"]
    goals_cur = sum(m["goals"] for m in data["matches"]) / len(data["matches"])

    def pct(ref, cur):
        return round((cur - ref) / ref * 100)

    pct_cc, pct_xa, pct_ast = pct(cc_ref, cc_cur), pct(xa_ref, xa_cur), pct(ast_ref, ast_cur)

    # ======================= P1：封面 =======================
    title1, title2 = "3项英超第一", "一个没保住"
    title1_fs = fit_font(title1, 118, CONTENT_W)
    title2_fs = fit_font(title2, 128, CONTENT_W)

    rows = [
        dict(name="关键传球", rank_cur=ranks["chances_created_avg"]["cur_rank"], pct=pct_cc),
        dict(name="预期助攻", rank_cur=ranks["expected_assists_avg"]["cur_rank"], pct=pct_xa),
        dict(name="助攻", rank_cur=ranks["assists_avg"]["cur_rank"], pct=pct_ast),
    ]

    p1_ctx = dict(
        **common_ctx, page_no="1 / 4",
        title1=title1, title1_fs=title1_fs, title2=title2, title2_fs=title2_fs,
        rows=rows,
    )

    # ======================= P2：残影散点 =======================
    bruno_cur = data["combo"][season_cur]["bruno"]
    bruno_ref = data["combo"][season_ref]["bruno"]
    ahead3 = data["combo"][season_cur]["ahead"]  # 两项都超过B费(3人)

    second_cc = data["seconds"]["chances_created_avg"]
    over_pct = round((second_cc["bruno_value"] - second_cc["value"]) / second_cc["value"] * 100)
    over_cheng = round(over_pct / 10)  # spec 原文是"近{X}成"，不是"近{X}%"

    pool_cur_scatter = data["pool_scatter"][season_cur]
    all_x = [bruno_cur["x"], bruno_ref["x"]] + [a["x"] for a in ahead3] + [p["x"] for p in pool_cur_scatter]
    all_y = [bruno_cur["y"], bruno_ref["y"]] + [a["y"] for a in ahead3] + [p["y"] for p in pool_cur_scatter]
    x_min, x_max = 0, max(4.0, max(all_x) * 1.05)
    y_min, y_max = 0, max(0.4, max(all_y) * 1.05) + 0.07  # 顶部留白给箭头标签，同小红书版

    # spec 给的散点区 y420-1150(730高)加上下面两行"排在他前面"头像横排后，
    # 剩余空间不够两行(每行 标题+头像+文字 至少116px，两行+间距要248px)在
    # 1150到1400(口径行)之间只剩250px，太紧——散点区压缩到680(420-1100)，
    # 给下面两行留 1120-1400 共280px，才能两行都在28px最小字号下不挤。
    SCATTER_W, SCATTER_H = CONTENT_W, 568
    MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 56, 16, 16, 34
    PX0, PX1 = MARGIN_L, SCATTER_W - MARGIN_R
    PY0, PY1 = MARGIN_T, SCATTER_H - MARGIN_B

    def to_x(v):
        return PX0 + (PX1 - PX0) * (v - x_min) / (x_max - x_min)

    def to_y(v):
        return PY1 - (PY1 - PY0) * (v - y_min) / (y_max - y_min)

    x_ticks = [dict(x=to_x(v), label=str(v)) for v in (0, 1, 2, 3, 4)]
    y_ticks = [dict(y=to_y(v), label=f"{v:.1f}") for v in (0, 0.1, 0.2, 0.3, 0.4)]
    gray_dots = [{"x": to_x(p["x"]), "y": to_y(p["y"])} for p in pool_cur_scatter]

    BRUNO_CUR_PX, BRUNO_CUR_PY = to_x(bruno_cur["x"]), to_y(bruno_cur["y"])
    BRUNO_CUR_R = 48  # 96px

    ahead_pids = {a["pid"] for a in ahead3}

    def build_labeled(r):
        out = []
        for a in ahead3:
            out.append(dict(pid=a["pid"], x=to_x(a["x"]), y=to_y(a["y"]), r=r,
                             avatar=avatar_abs(a["pid"]), badge=badge_abs(a["team_id"]),
                             label=CHINESE_SHORT.get(a["name"], a["name"]),
                             team_zh=TEAM_ZH.get(a["team_id"], str(a["team_id"])), is_ahead=True))
        return out

    LABEL_H = 40

    def gap_xy(p, q):
        return p["r"] + q["r"] + 8, p["r"] + q["r"] + LABEL_H

    import random

    def run_collision(r):
        rnd = random.Random(11)
        labeled = build_labeled(r)
        for a in labeled:
            a["true_x"], a["true_y"] = a["x"], a["y"]
        fixed = [dict(x=BRUNO_CUR_PX, y=BRUNO_CUR_PY, r=BRUNO_CUR_R),
                 dict(x=to_x(bruno_ref["x"]), y=to_y(bruno_ref["y"]), r=57)]
        for _ in range(200):
            moved = False
            pts = labeled + fixed
            for i in range(len(labeled)):
                for j in range(len(pts)):
                    if pts[j] is labeled[i]:
                        continue
                    p, q = labeled[i], pts[j]
                    gx, gy = gap_xy(p, q)
                    dx, dy = p["x"] - q["x"], p["y"] - q["y"]
                    if (dx / gx) ** 2 + (dy / gy) ** 2 < 1:
                        dist = (dx * dx + dy * dy) ** 0.5
                        if dist < 0.5:
                            dx, dy = rnd.uniform(-1, 1), rnd.uniform(-1, 1)
                            dist = (dx * dx + dy * dy) ** 0.5
                        ux, uy = dx / dist, dy / dist
                        need = 1 / (((ux / gx) ** 2 + (uy / gy) ** 2) ** 0.5)
                        push = (need - dist) + 0.5
                        pdx, pdy = ux * push, uy * push
                        qdx, qdy = -ux * push, -uy * push
                        if p["is_ahead"]:
                            pdx = max(pdx, 0.0); pdy = min(pdy, 0.0)
                        p["x"] += pdx; p["y"] += pdy
                        if q in labeled:
                            if q["is_ahead"]:
                                qdx = max(qdx, 0.0); qdy = min(qdy, 0.0)
                            q["x"] += qdx; q["y"] += qdy
                        moved = True
            if not moved:
                break
        for a in labeled:
            a["x"] = min(max(a["x"], a["r"]), SCATTER_W - a["r"])
            a["y"] = min(max(a["y"], a["r"]), SCATTER_H - a["r"])
            ddx, ddy = a["x"] - a["true_x"], a["y"] - a["true_y"]
            a["moved"] = (ddx * ddx + ddy * ddy) ** 0.5 > 3
        return labeled

    labeled = run_collision(36)
    ahead_ok = all(a["x"] > BRUNO_CUR_PX and a["y"] < BRUNO_CUR_PY for a in labeled)
    if not ahead_ok:
        print("[P2douyin] 语义约束未满足，缩小到60px重排")
        labeled = run_collision(30)
        ahead_ok = all(a["x"] > BRUNO_CUR_PX and a["y"] < BRUNO_CUR_PY for a in labeled)
    print(f"[P2douyin] 两项都超过B费的头像终态满足'右上方'约束: {ahead_ok}")

    def text_w(s, fs=24):
        return text_px_width(s, fs)

    all_circles = [dict(x=BRUNO_CUR_PX, y=BRUNO_CUR_PY, r=BRUNO_CUR_R),
                   dict(x=to_x(bruno_ref["x"]), y=to_y(bruno_ref["y"]), r=57)]
    all_circles += [dict(x=a["x"], y=a["y"], r=a["r"]) for a in labeled]

    def label_box(a, side):
        w = text_w(a["label"]); h = 28
        if side == "below":
            return (a["x"] - w / 2, a["y"] + a["r"] + 4, a["x"] + w / 2, a["y"] + a["r"] + 4 + h)
        if side == "above":
            return (a["x"] - w / 2, a["y"] - a["r"] - 4 - h, a["x"] + w / 2, a["y"] - a["r"] - 4)
        if side == "left":
            return (a["x"] - a["r"] - 8 - w, a["y"] - h / 2, a["x"] - a["r"] - 8, a["y"] + h / 2)
        return (a["x"] + a["r"] + 8, a["y"] - h / 2, a["x"] + a["r"] + 8 + w, a["y"] + h / 2)

    def box_circle_hit(box, c):
        bx0, by0, bx1, by1 = box
        nx = max(bx0, min(c["x"], bx1)); ny = max(by0, min(c["y"], by1))
        return (nx - c["x"]) ** 2 + (ny - c["y"]) ** 2 < c["r"] ** 2

    # 顶部64px留给坐标轴标题"↑场均预期助攻"(锚点y=54，字体28px上延伸约
    # 32px，占到local y≈22-62)——被挤到画布最顶的头像(y被clamp到a.r)若
    # 选"above"侧标签，标签框会伸到y<0，冲出SVG容器顶部，跟容器外面的
    # .ghost-note这个HTML行叠在一起(2026-09-24真实发现，见check_qa.cjs)。
    # 标签方位候选必须先满足"框完全落在画布内、且避开这条顶部保留带"，
    # 不能只检测跟其它圆是否相交。
    MIN_LABEL_Y = 64

    def label_in_bounds(box):
        bx0, by0, bx1, by1 = box
        return by0 >= MIN_LABEL_Y and by1 <= SCATTER_H - 4 and bx0 >= 4 and bx1 <= SCATTER_W - 4

    for a in labeled:
        others = [c for c in all_circles if not (c["x"] == a["x"] and c["y"] == a["y"] and c["r"] == a["r"])]
        for side in ("below", "above", "left", "right"):
            box = label_box(a, side)
            if label_in_bounds(box) and not any(box_circle_hit(box, c) for c in others):
                a["label_side"] = side
                break
        else:
            a["label_side"] = "below"

    # "B费"自己的标签同样跑一遍相交检测（跟小红书版同一套逻辑）——96px头像
    # 缩小后紧挨着"两项都比他强"名单，默认下方偏移常伸进邻居头像圆。
    bruno_cur_label = dict(x=BRUNO_CUR_PX, y=BRUNO_CUR_PY, r=BRUNO_CUR_R, label="B费")
    bruno_others = [c for c in all_circles if not (c["x"] == BRUNO_CUR_PX and c["y"] == BRUNO_CUR_PY)]
    for side in ("below", "above", "left", "right"):
        box = label_box(bruno_cur_label, side)
        if label_in_bounds(box) and not any(box_circle_hit(box, c) for c in bruno_others):
            bruno_cur_label_side = side
            break
    else:
        bruno_cur_label_side = "below"
    print(f"[P2douyin] B费本赛季标签方位: {bruno_cur_label_side}")

    bruno_pool = data["pool_scatter"][season_cur]
    cc_ahead_full = sorted([p for p in bruno_pool if p["x"] > bruno_cur["x"]], key=lambda p: -p["x"])
    xa_ahead_full = sorted([p for p in bruno_pool if p["y"] > bruno_cur["y"]], key=lambda p: -p["y"])

    def strip_row(items, limit=None):
        shown = items if limit is None else items[:limit]
        out = [dict(pid=p["pid"], avatar=avatar_abs(p["pid"]), badge=badge_abs(p["team_id"]),
                     label=CHINESE_SHORT.get(p["name"], p["name"]),
                     team_zh=TEAM_ZH.get(p["team_id"], str(p["team_id"]))) for p in shown]
        rest = len(items) - len(shown)
        return out, rest

    cc_strip, cc_rest = strip_row(cc_ahead_full)  # 4人，全展示
    xa_strip, xa_rest = strip_row(xa_ahead_full, limit=6)

    p2_ctx = dict(
        **common_ctx, page_no="2 / 4",
        page_title="去年英超独一档，今年掉进人堆",
        scatter_w=SCATTER_W, scatter_h=SCATTER_H, px0=PX0, py1=PY1,
        x_ticks=x_ticks, y_ticks=y_ticks, gray_dots=gray_dots, labeled=labeled,
        bruno_cur_x=BRUNO_CUR_PX, bruno_cur_y=BRUNO_CUR_PY, bruno_cur_r=BRUNO_CUR_R,
        bruno_cur_label_side=bruno_cur_label_side,
        bruno_ref_x=to_x(bruno_ref["x"]), bruno_ref_y=to_y(bruno_ref["y"]),
        bruno_avatar_big=avatar_abs("422685"), bruno_badge_big=bruno_badge,
        over_pct=over_pct, over_cheng=over_cheng, pct_cc=pct_cc,
        ahead_count=len(ahead3),
        cc_rank_cur=ranks["chances_created_avg"]["cur_rank"],
        xa_rank_cur=ranks["expected_assists_avg"]["cur_rank"],
        cc_strip=cc_strip, cc_rest=cc_rest, xa_strip=xa_strip, xa_rest=xa_rest,
    )

    # ======================= P3：生涯柱状图 =======================
    cc_series = [(s, v["chances_created_avg"]) for s, v in sorted(career.items())]
    best_season = max(cc_series, key=lambda t: t[1])[0]
    echo_seasons = {s for s, v in cc_series if s != season_cur and abs(v - cc_cur) < 0.15}

    def season_short(s):
        y1, y2 = s.split("/")
        return f"{y1[2:]}/{y2[2:]}"

    def build_bars(series, w, h, bar_gap, right_margin=44, vmax_mult=1.2):
        n = len(series)
        layout_w = w - right_margin
        bw = (layout_w - bar_gap * (n - 1)) / n
        vmax = max(v for _, v in series) * vmax_mult
        def to_yy(v):
            return h - (h * 0.8) * v / vmax
        dash_y = to_yy(cc_cur)
        bars = []
        for i, (s, v) in enumerate(series):
            x = i * (bw + bar_gap)
            y = to_yy(v)
            color = "#C8961E" if s == best_season else ("#D23B2C" if s == season_cur else "#D5D8DC")
            has_dot = s in echo_seasons
            val_y = min(y - 12, dash_y - 20) if has_dot else y - 12
            season_color = "#C8961E" if s == best_season else ("#D23B2C" if s == season_cur else None)
            bars.append(dict(x=x, y=y, w=bw, h=h - y, color=color, val=f"{v:.2f}", val_y=val_y,
                              season_short=season_short(s), dot=has_dot,
                              is_best=(s == best_season), is_cur=(s == season_cur), season_color=season_color))
        return bars, dash_y

    # vmax_mult=2.0(不是默认1.2)：120px 的"-33%"+下降括号要画在最高柱子
    # 上方，默认1.2倍留白(柱顶到viewBox顶只有约20%空间)完全不够放这么大的
    # 字，会顶出viewBox被裁——加大倍数把柱子整体压矮，换出顶部空间。
    main_bars, dashed_y = build_bars(cc_series, CONTENT_W, 650, 16, vmax_mult=2.0)
    best_bar = next(b for b in main_bars if b["is_best"])
    cur_bar = next(b for b in main_bars if b["is_cur"])

    p3_ctx = dict(
        **common_ctx, page_no="3 / 4",
        page_title="上赛季是回光返照？",
        main_bars=main_bars, chart_h=650, main_svg_h=730, dashed_y=dashed_y,
        best_bar=best_bar, cur_bar=cur_bar, best_val=f"{cc_ref:.2f}", pct_cc=pct_cc,
        conclusion="上赛季生涯最佳，这赛季打回原形",
    )

    # ======================= P4：逐场 + 投票 =======================
    team_ids_by_en = {"Hull City": 8667, "Manchester United": 10260, "Ipswich Town": 9902,
                       "Everton": 8668, "Manchester City": 8456, "Fulham": 9879}
    RESULT_COLOR = {"胜": "#2E9E5B", "平": "#9AA1A9", "负": "#D23B2C"}
    cards = []
    for m in data["matches"]:
        is_mu_home = m["home_name"] == "Manchester United"
        opp_en = m["away_name"] if is_mu_home else m["home_name"]
        result = MU_RESULTS[opp_en]
        is_hattrick = opp_en == "Ipswich Town"
        is_derby = opp_en == "Manchester City"
        promoted = BOTH_PROMOTED and opp_en in ("Hull City", "Ipswich Town")
        cards.append(dict(
            date=m["date"][5:], result=result, result_color=RESULT_COLOR[result],
            opp_zh=TEAM_ZH.get(team_ids_by_en[opp_en], opp_en),
            opp_badge=badge_abs(team_ids_by_en[opp_en]),
            score=f"{m['home_score']}-{m['away_score']}",
            goals=m["goals"], assists=m["assists"], chances_created=m["chances_created"],
            promoted=promoted, is_hattrick=is_hattrick, is_derby=is_derby,
        ))
    zero_n = sum(1 for c in cards if c["goals"] == 0 and c["assists"] == 0)

    p4_ctx = dict(
        **common_ctx, page_no="4 / 4",
        page_title="5场3球？帽子戏法打的是升班马",
        cards=cards,
        zero_line=f"他没进球没助攻的{zero_n}场，曼联{ZERO_MATCHES_RECORD}",
    )

    pages = [
        ("p1_bruno_douyin.html.j2", p1_ctx, "p1"),
        ("p2_bruno_douyin.html.j2", p2_ctx, "p2"),
        ("p3_bruno_douyin.html.j2", p3_ctx, "p3"),
        ("p4_bruno_douyin.html.j2", p4_ctx, "p4"),
    ]
    for tpl_name, ctx, stub in pages:
        html_path = render_page(tpl_name, ctx, f"{stub}{suffix}", plat_out_dir)
        out_2x = plat_out_dir / f"{stub}{suffix}_2x.png"
        out_final = plat_out_dir / f"{stub}{suffix}.png"
        shoot(html_path, out_2x, out_final, CANVAS_W, CANVAS_H)


if __name__ == "__main__":
    main()
