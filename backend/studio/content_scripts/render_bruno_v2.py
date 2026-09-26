"""render_bruno_v2：B费"下滑有多严重"图文系列 NO.02 小红书版重做。

只做小红书 1080x1440（--platform xhs 语境下的唯一产物），抖音版按站长要求
本轮不动——复用 render_bruno_series.py 已经生成好的 _out/bruno/douyin/*.png，
本脚本完全不碰那 4 个文件，也不 import/touch render_bruno_series.py 的
main()，只借用它里面的头像/队徽/字号小工具，避免两份重复实现。

数值来源同上一版：_cache/bruno_data.json（bruno_refresh.py 生成，一键刷新）。
本脚本新增的"曼联战绩/升班马判定"等第零步核实结果直接嵌在下面（对应这一轮
对话里贴过的原始 SQL/Python 输出，非查库不可得的临时事实型结论，不是可以
从 bruno_data.json 派生的数值，所以没有反向塞回那份 JSON）。
"""
from __future__ import annotations

import json
from pathlib import Path

from render_series import CACHE_DIR, OUT_DIR, PLATFORMS, render_page, shoot, text_px_width
from render_bruno_series import avatar_abs, badge_abs, fit_font, CHINESE_NAMES
from bruno_names import CHINESE_SHORT, CHINESE_FULL, TEAM_ZH

DATA_PATH = CACHE_DIR / "bruno_data.json"

# ---- 第零步核实结果（真实 SQL 查询，本轮对话已贴过原始输出）----
HULL_PROMOTED = True   # 赫尔城 2025/26 英超 0 场记录
IPSWICH_PROMOTED = True  # 伊普斯维奇 2025/26 英超 0 场记录
BOTH_PROMOTED = HULL_PROMOTED and IPSWICH_PROMOTED

MU_RESULTS = {  # 曼联五场 胜/平/负（对手英文名 -> 结果）
    "Hull City": "负", "Ipswich Town": "胜", "Everton": "平",
    "Manchester City": "负", "Fulham": "平",
}
# B费0球0助的4场：曼联 2平2负，一胜未取
ZERO_MATCHES_RECORD = "两平两负，一胜未取"


def main():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    season_cur = data["season_current"]
    season_ref = data["season_reference"]
    age = data["bruno_age"]

    platform = "xhs"
    plat_cfg = PLATFORMS[platform]
    CANVAS_W, CANVAS_H = plat_cfg["w"], plat_cfg["h"]
    plat_out_dir = OUT_DIR / "bruno"
    print(f"=== 平台: {platform}（本轮不生成抖音版）  画布: {CANVAS_W}x{CANVAS_H}  输出目录: {plat_out_dir} ===")

    COLUMN_NAME = "喵弟球员体检"
    ISSUE_NO = "NO.02"
    bruno_team_id = data["bruno_team_id"]
    bruno_avatar = avatar_abs("422685")
    bruno_badge = badge_abs(bruno_team_id)

    last_date = data["matches"][-1]["date"]
    caveat = f"数据：英超，截至 {last_date}"

    common_ctx = dict(
        logo_path=str(CACHE_DIR / "logo-mark.png"), column_name=COLUMN_NAME, issue_no=ISSUE_NO,
        platform=platform, canvas_w=CANVAS_W, canvas_h=CANVAS_H,
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
    print(f"[核算] 跌幅 关键传球={pct_cc}% xA={pct_xa}% 助攻={pct_ast}%  本赛季场均进球={goals_cur:.2f}")

    # ======================= P1：体检报告单 =======================
    CONTENT_W = 952
    title1, title2 = "3 项英超第一", "一个没保住"
    title1_fs = fit_font(title1, 120, CONTENT_W)
    title2_fs = fit_font(title2, 130, CONTENT_W)

    rank_line = lambda key: ranks[key]["cur_rank"]
    report_rows = [
        dict(item="进球", sub="3 个全在同一场", trans=f"场均 {goals_cur:.2f}", rank=None, change="—",
             change_red=False, verdict="正常", arrow="", normal=True),
        dict(item="关键传球", sub=None, trans=f"{cc_ref:.2f} → {cc_cur:.2f}", rank=rank_line("chances_created_avg"),
             change=f"{pct_cc}%", change_red=True, verdict="异常", arrow="↓", normal=False),
        dict(item="预期助攻 xA", sub=None, trans=f"{xa_ref:.2f} → {xa_cur:.2f}", rank=rank_line("expected_assists_avg"),
             change=f"{pct_xa}%", change_red=True, verdict="异常", arrow="↓", normal=False),
        dict(item="助攻", sub=None, trans=f"{ast_ref:.2f} → {ast_cur:.2f}", rank=rank_line("assists_avg"),
             change=f"{pct_ast}%", change_red=True, verdict="异常", arrow="↓", normal=False),
    ]

    p1_ctx = dict(
        **common_ctx, page_no="1 / 4",
        title1=title1, title1_fs=title1_fs, title2=title2, title2_fs=title2_fs,
        subtitle="上赛季的英超第一组织核心，这赛季最后一传全面缩水",
        scope_text="英超 · 上季 vs 本季前5轮",
        report_rows=report_rows,
    )

    # ======================= P2：残影散点图（2026-09-24 同步抖音版改版）=======================
    # 改版内容：标题换成"去年英超独一档，今年掉进人堆"；去掉扎卡专属红字
    # 标签，"两项都超过B费"3人一视同仁；帕尔默/厄德高/谢尔基/索博斯洛伊/
    # 萨卡不再单独标头像+名字(他们本季都没有两项都超过B费，只是碰巧在单项
    # 排名里出现)，一律降级成灰点；图表下方新增"排在他前面的人"两行头像
    # 横排(关键传球全部/xA前6+"等N人")；残影旁注单位从"倍"改成"成"。
    bruno_cur = data["combo"][season_cur]["bruno"]
    bruno_ref = data["combo"][season_ref]["bruno"]
    ahead = data["combo"][season_cur]["ahead"]

    second_cc = data["seconds"]["chances_created_avg"]
    over_pct = round((second_cc["bruno_value"] - second_cc["value"]) / second_cc["value"] * 100)
    over_cheng = round(over_pct / 10)

    pool_cur_scatter = data["pool_scatter"][season_cur]
    all_x = [bruno_cur["x"], bruno_ref["x"]] + [a["x"] for a in ahead] + [p["x"] for p in pool_cur_scatter]
    all_y = [bruno_cur["y"], bruno_ref["y"]] + [a["y"] for a in ahead] + [p["y"] for p in pool_cur_scatter]

    x_min, x_max = 0, max(4.0, max(all_x) * 1.05)
    # pct-pill 已经挪出SVG变成独立HTML行，不再需要专门给它留顶部空间，
    # 一个小的安全边距即可（避免残影圆环贴viewBox顶边）。
    y_min, y_max = 0, max(0.4, max(all_y) * 1.05) + 0.02

    SCATTER_W, SCATTER_H = 952, 676
    MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 60, 20, 44, 40
    PX0, PX1 = MARGIN_L, SCATTER_W - MARGIN_R
    PY0, PY1 = MARGIN_T, SCATTER_H - MARGIN_B

    def to_x(v):
        return PX0 + (PX1 - PX0) * (v - x_min) / (x_max - x_min)

    def to_y(v):
        return PY1 - (PY1 - PY0) * (v - y_min) / (y_max - y_min)

    x_ticks = [dict(x=to_x(v), label=str(v)) for v in (0, 1, 2, 3, 4)]
    y_ticks = [dict(y=to_y(v), label=f"{v:.1f}") for v in (0, 0.1, 0.2, 0.3, 0.4)]
    gray_dots = [{"x": to_x(pt["x"]), "y": to_y(pt["y"])} for pt in pool_cur_scatter]

    BRUNO_CUR_PX, BRUNO_CUR_PY = to_x(bruno_cur["x"]), to_y(bruno_cur["y"])
    BRUNO_CUR_R = 48

    def build_labeled(r):
        out = []
        for a in ahead:
            out.append(dict(pid=a["pid"], x=to_x(a["x"]), y=to_y(a["y"]), r=r,
                             avatar=avatar_abs(a["pid"]), badge=badge_abs(a["team_id"]),
                             label=CHINESE_SHORT.get(a["name"], a["name"]),
                             team_zh=TEAM_ZH.get(a["team_id"], str(a["team_id"])), is_ahead=True))
        return out

    LABEL_H = 46  # 22px姓名 + 20px球队名两行

    def gap_xy(p, q):
        return p["r"] + q["r"] + 8, p["r"] + q["r"] + LABEL_H

    import random

    def run_collision(r):
        rnd = random.Random(7)
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
        print("[P2] 两项都超过B费名单未能满足'右上方'约束，缩小到60px重排")
        labeled = run_collision(30)
        ahead_ok = all(a["x"] > BRUNO_CUR_PX and a["y"] < BRUNO_CUR_PY for a in labeled)
    print(f"[P2] 两项都超过B费名单终态满足'x>B费 且 y在B费之上'约束: {ahead_ok}")

    def text_w(s, fs=22):
        return text_px_width(s, fs)

    all_circles = [dict(x=BRUNO_CUR_PX, y=BRUNO_CUR_PY, r=BRUNO_CUR_R),
                   dict(x=to_x(bruno_ref["x"]), y=to_y(bruno_ref["y"]), r=57)]
    all_circles += [dict(x=a["x"], y=a["y"], r=a["r"]) for a in labeled]

    def label_box(a, side):
        w = text_w(a.get("label", "B费")); h = 46 if "team_zh" in a else 26
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

    for a in labeled:
        others = [c for c in all_circles if not (c["x"] == a["x"] and c["y"] == a["y"] and c["r"] == a["r"])]
        for side in ("below", "above", "left", "right"):
            if not any(box_circle_hit(label_box(a, side), c) for c in others):
                a["label_side"] = side
                break
        else:
            a["label_side"] = "below"

    bruno_cur_label = dict(x=BRUNO_CUR_PX, y=BRUNO_CUR_PY, r=BRUNO_CUR_R, label="B费")
    bruno_others = [c for c in all_circles if not (c["x"] == BRUNO_CUR_PX and c["y"] == BRUNO_CUR_PY)]
    for side in ("below", "above", "left", "right"):
        if not any(box_circle_hit(label_box(bruno_cur_label, side), c) for c in bruno_others):
            bruno_cur_label_side = side
            break
    else:
        bruno_cur_label_side = "below"
    print(f"[P2] B费本赛季标签方位: {bruno_cur_label_side}")

    bruno_pool = data["pool_scatter"][season_cur]
    cc_ahead_full = sorted([p for p in bruno_pool if p["x"] > bruno_cur["x"]], key=lambda p: -p["x"])
    xa_ahead_full = sorted([p for p in bruno_pool if p["y"] > bruno_cur["y"]], key=lambda p: -p["y"])

    def strip_row(items, limit=None):
        shown = items if limit is None else items[:limit]
        out = [dict(pid=p["pid"], avatar=avatar_abs(p["pid"]), badge=badge_abs(p["team_id"]),
                     label=CHINESE_SHORT.get(p["name"], p["name"]),
                     team_zh=TEAM_ZH.get(p["team_id"], str(p["team_id"]))) for p in shown]
        return out, len(items) - len(shown)

    cc_strip, cc_rest = strip_row(cc_ahead_full)
    xa_strip, xa_rest = strip_row(xa_ahead_full, limit=6)

    p2_ctx = dict(
        **common_ctx, page_no="2 / 4",
        page_title="去年英超独一档，今年掉进人堆",
        scatter_w=SCATTER_W, scatter_h=SCATTER_H, px0=PX0, py1=PY1,
        x_ticks=x_ticks, y_ticks=y_ticks, gray_dots=gray_dots, labeled=labeled,
        bruno_cur_x=BRUNO_CUR_PX, bruno_cur_y=BRUNO_CUR_PY,
        bruno_ref_x=to_x(bruno_ref["x"]), bruno_ref_y=to_y(bruno_ref["y"]),
        bruno_avatar_big=avatar_abs("422685"), bruno_badge_big=bruno_badge, bruno_cur_r=BRUNO_CUR_R,
        bruno_cur_label_side=bruno_cur_label_side,
        over_cheng=over_cheng,
        ahead_count=len(ahead), ahead_count_ref=len(data["combo"][season_ref]["ahead"]),
        cc_rank_cur=ranks["chances_created_avg"]["cur_rank"],
        xa_rank_cur=ranks["expected_assists_avg"]["cur_rank"],
        cc_strip=cc_strip, cc_rest=cc_rest, xa_strip=xa_strip, xa_rest=xa_rest,
        pct_cc=pct_cc, pct_xa=pct_xa,
    )

    # ======================= P3：生涯柱状图 =======================
    cc_series = career and [(s, v["chances_created_avg"]) for s, v in sorted(career.items())]
    xa_series = [(s, v["expected_assists_avg"]) for s, v in sorted(career.items())]
    ast_series = [(s, v["assists_avg"]) for s, v in sorted(career.items())]
    best_season = max(cc_series, key=lambda t: t[1])[0]
    echo_seasons = {s for s, v in cc_series if s != season_cur and abs(v - cc_cur) < 0.15}

    def season_short(s):
        y1, y2 = s.split("/")
        return f"{y1[2:]}/{y2[2:]}"

    def build_bars(series, w, h, bar_gap, right_margin=44):
        n = len(series)
        layout_w = w - right_margin
        bw = (layout_w - bar_gap * (n - 1)) / n
        vmax = max(v for _, v in series) * 1.2
        def to_yy(v):
            return h - (h * 0.8) * v / vmax
        dash_y = to_yy(cc_cur) if series is cc_series else None
        bars = []
        for i, (s, v) in enumerate(series):
            x = i * (bw + bar_gap)
            y = to_yy(v)
            color = "#C8961E" if s == best_season else ("#D23B2C" if s == season_cur else "#D5D8DC")
            has_dot = s in echo_seasons and series is cc_series
            # 数值标签默认贴在柱顶正上方(y-10)；"打回原形"的圆点标记也画在
            # dashed_y 高度——差值<0.15 定义的回声柱，柱顶本来就离 dashed_y
            # 很近，数值标签会被圆点压住(实测 2.47 被小圆圈啃掉一角)，这类
            # 柱子把数值再往上多推一截，让开圆点。
            val_y = min(y - 10, dash_y - 18) if (has_dot and dash_y is not None) else y - 10
            season_color = "#C8961E" if s == best_season else ("#D23B2C" if s == season_cur else None)
            bars.append(dict(x=x, y=y, w=bw, h=h - y, color=color, val=f"{v:.2f}", val_y=val_y,
                              season_short=season_short(s), dot=has_dot,
                              is_best=(s == best_season), is_cur=(s == season_cur), season_color=season_color))
        return bars, dash_y

    main_bars, dashed_y = build_bars(cc_series, 680, 500, 16)
    xa_bars, _ = build_bars(xa_series, 300, 220, 8, right_margin=8)
    ast_bars, _ = build_bars(ast_series, 300, 220, 8, right_margin=8)

    best_bar = next(b for b, (s, v) in zip(main_bars, cc_series) if s == best_season)
    cur_bar = next(b for b, (s, v) in zip(main_bars, cc_series) if s == season_cur)

    p3_ctx = dict(
        **common_ctx, page_no="3 / 4",
        page_title="创造力一个夏天蒸发三成",
        main_bars=main_bars, chart_h=500, main_svg_h=560, dashed_y=dashed_y,
        best_bar=best_bar, cur_bar=cur_bar, best_val=f"{cc_ref:.2f}", cur_val=f"{cc_cur:.2f}",
        pct_cc=pct_cc,
        xa_bars=xa_bars, xa_h=220, xa_svg_h=254, pct_xa=pct_xa,
        ast_bars=ast_bars, ast_h=220, ast_svg_h=254, pct_ast=pct_ast,
        conclusion=f"上赛季生涯最佳，这赛季回到 {season_short('2021/2022')} 的低谷水平",
    )

    # ======================= P4：逐场 =======================
    team_zh = {
        "Hull City": "赫尔城", "Manchester United": "曼联", "Ipswich Town": "伊普斯维奇",
        "Everton": "埃弗顿", "Manchester City": "曼城", "Fulham": "富勒姆",
    }
    team_ids = {
        "Hull City": 8667, "Manchester United": 10260, "Ipswich Town": 9902,
        "Everton": 8668, "Manchester City": 8456, "Fulham": 9879,
    }
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
            date=m["date"][5:].replace("-", "-"), result=result, result_color=RESULT_COLOR[result],
            opp_zh=team_zh[opp_en], opp_badge=badge_abs(team_ids[opp_en]),
            score=f"{m['home_score']}-{m['away_score']}",
            home_zh=team_zh[m["home_name"]], away_zh=team_zh[m["away_name"]],
            goals=m["goals"], assists=m["assists"], chances_created=m["chances_created"],
            promoted=promoted, is_hattrick=is_hattrick, is_derby=is_derby,
        ))

    title5 = "5 场 3 球？帽子戏法打的是升班马" if BOTH_PROMOTED else "5 场 3 球？扣掉一场帽子戏法，4 场 0 球 0 助"

    p4_ctx = dict(
        **common_ctx, page_no="4 / 4",
        page_title=title5,
        cards=cards,
        zero_matches_line=f"他没进球没助攻的 4 场，曼联{ZERO_MATCHES_RECORD}",
    )

    # ======================= 渲染 + 截图 =======================
    pages = [
        ("p1_bruno_v2.html.j2", p1_ctx, "p1"),
        ("p2_bruno_v2.html.j2", p2_ctx, "p2"),
        ("p3_bruno_v2.html.j2", p3_ctx, "p3"),
        ("p4_bruno_v2.html.j2", p4_ctx, "p4"),
    ]
    for tpl_name, ctx, stub in pages:
        html_path = render_page(tpl_name, ctx, stub, plat_out_dir)
        out_2x = plat_out_dir / f"{stub}_2x.png"
        out_final = plat_out_dir / f"{stub}.png"
        shoot(html_path, out_2x, out_final, CANVAS_W, CANVAS_H)


if __name__ == "__main__":
    main()
