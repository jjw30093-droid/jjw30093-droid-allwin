"""render_bruno_series：B费"下滑有多严重"图文系列（喵弟球员体检 NO.02）。

沿用姆巴佩系列的设计系统（templates/base.css、_chrome.html.j2 品牌宏）和
render_series.py 的渲染基础设施（shoot.cjs 截图、text_px_width 自适应字号、
两平台安全区规则）。所有数值一律从 bruno_refresh.py 生成的
_cache/bruno_data.json 读取注入，本脚本不手写任何排名/倍数/坐标数字。

头像：Bruno Fernandes(422685)、Cole Palmer(1096353)、Martin Ødegaard(534670)、
Rayan Cherki(1104053)、Dominik Szoboszlai(846005)、Morgan Gibbs-White(789502)、
Granit Xhaka(207236)、Sávio(1174337) 已用同一条 FotMob CDN 规则
（frontend/components/players/playerAvatarUrl.ts）一次性下载到 _cache/avatars/。
队徽复用仓库已有的本地队徽缓存 runtime/media/team-crests/fotmob/{Team_ID}.png。
"""
from __future__ import annotations

import json
from pathlib import Path

from render_series import (
    CACHE_DIR, HERE, OUT_DIR, PLATFORMS, env,
    shoot, render_page, text_px_width,
)

DATA_PATH = CACHE_DIR / "bruno_data.json"

CHINESE_NAMES = {
    "Bruno Fernandes": "B费",
    "Morgan Gibbs-White": "吉布斯-怀特",
    "Granit Xhaka": "扎卡",
    "Sávio": "萨维尼奥",
    "Savinho": "萨维尼奥",
    "Cole Palmer": "帕尔默",
    "Martin Ødegaard": "厄德高",
    "Rayan Cherki": "谢尔基",
    "Dominik Szoboszlai": "索博斯洛伊",
    "Bukayo Saka": "萨卡",
    "Declan Rice": "赖斯",
    "Anton Stach": "斯塔赫",
}


def avatar_abs(pid: str) -> str:
    p = CACHE_DIR / "avatars" / f"{pid}.png"
    assert p.exists(), f"avatar missing for pid={pid}: {p}"
    return str(p)


def badge_abs(team_id) -> str:
    p = HERE.parent.parent.parent / "runtime" / "media" / "team-crests" / "fotmob" / f"{team_id}.png"
    assert p.exists(), f"badge missing for team_id={team_id}: {p}"
    return str(p)


def fit_font(text: str, max_size: int, max_width: int) -> int:
    w = text_px_width(text, max_size)
    if w <= max_width:
        return max_size
    return int(max_size * max_width / w)


def build_panel(season_label: str, data: dict, w: int, h: int, panel_id: str,
                 x_min: float, x_max: float, y_min: float, y_max: float,
                 pool_scatter: list, ahead: list, stars: dict, bruno_xy: tuple,
                 labeled_star_zh: dict, gray_star_zh: dict, note_top: str, avatar_r: int):
    margin_l, margin_r, margin_t, margin_b = 8, 8, 8, 26
    px0, px1 = margin_l, w - margin_r
    py0, py1 = margin_t, h - margin_b

    def to_x(v):
        return px0 + (px1 - px0) * (v - x_min) / (x_max - x_min)

    def to_y(v):
        return py1 - (py1 - py0) * (v - y_min) / (y_max - y_min)

    gray_dots = [{"x": to_x(pt["x"]), "y": to_y(pt["y"])} for pt in pool_scatter]

    avatars = []
    bx, by = bruno_xy
    avatars.append(dict(pid="bruno", x=to_x(bx), y=to_y(by), r=avatar_r + 6,
                         avatar=avatar_abs("422685"), label="B费", is_bruno=True))
    for a in ahead:
        avatars.append(dict(pid=a["pid"], x=to_x(a["x"]), y=to_y(a["y"]), r=avatar_r,
                             avatar=avatar_abs(a["pid"]), label=CHINESE_NAMES.get(a["name"], a["name"]), is_bruno=False))
    plain_dots = []
    for zh, en in labeled_star_zh.items():
        info = stars.get(zh, {}).get(season_label)
        if info and info.get("present"):
            avatars.append(dict(pid=info["pid"], x=to_x(info["x"]), y=to_y(info["y"]), r=avatar_r,
                                 avatar=avatar_abs(info["pid"]), label=zh.split()[0], is_bruno=False))
    for zh, en in gray_star_zh.items():
        info = stars.get(zh, {}).get(season_label)
        if info and info.get("present"):
            plain_dots.append({"x": to_x(info["x"]), "y": to_y(info["y"])})

    # ---- 头像防碰撞：早季样本量小（apps 少）时多名球员的 (关键传球,xA)
    # 坐标非常接近，按真实坐标摆头像会大面积重叠。用简单的成对排斥力迭代
    # 把重叠的头像沿连线方向推开，真实坐标偏移超过阈值时画一条细引导线
    # 连回原位——跟 render_series.py P4 散点图同一套处理方式。 ----
    for a in avatars:
        a["true_x"], a["true_y"] = a["x"], a["y"]

    # 头像正下方还有一行姓名标签(~26px)，纯圆形碰撞只保证头像本身不重叠，
    # 标签仍会被上方相邻头像的圆形切掉一截（实测"吉布斯-怀特"被切成视觉上
    # 像"卡布斯-怀特"）——改成椭圆碰撞域，纵向所需间距明显大于横向，且
    # 只在下方留出标签空间（上方不需要）。
    LABEL_H = 30

    def gap_xy(p, q):
        return p["r"] + q["r"] + 8, p["r"] + q["r"] + LABEL_H

    def overlaps(p, q):
        gx, gy = gap_xy(p, q)
        dx, dy = p["x"] - q["x"], p["y"] - q["y"]
        return (dx / gx) ** 2 + (dy / gy) ** 2 < 1

    import random
    rnd = random.Random(42)
    for _ in range(200):
        moved = False
        for i in range(len(avatars)):
            for j in range(i + 1, len(avatars)):
                p, q = avatars[i], avatars[j]
                if overlaps(p, q):
                    gx, gy = gap_xy(p, q)
                    dx, dy = p["x"] - q["x"], p["y"] - q["y"]
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist < 0.5:
                        dx, dy = rnd.uniform(-1, 1), rnd.uniform(-1, 1)
                        dist = (dx * dx + dy * dy) ** 0.5
                    ux, uy = dx / dist, dy / dist
                    # 按椭圆在该方向上的实际所需半径推开，而不是圆形半径
                    need = 1 / (((ux / gx) ** 2 + (uy / gy) ** 2) ** 0.5)
                    push = (need - dist) / 2 + 0.5
                    p["x"] += ux * push; p["y"] += uy * push
                    q["x"] -= ux * push; q["y"] -= uy * push
                    moved = True
        if not moved:
            break
    for a in avatars:
        a["x"] = min(max(a["x"], a["r"]), w - a["r"])
        a["y"] = min(max(a["y"], a["r"]), h - a["r"])
        ddx, ddy = a["x"] - a["true_x"], a["y"] - a["true_y"]
        a["moved"] = (ddx * ddx + ddy * ddy) ** 0.5 > 3

    return dict(id=panel_id, w=w, h=h, x0=px0, y0=py1, season_label=season_label,
                note_top=note_top, ahead_count=len(ahead),
                gray_dots=gray_dots, plain_dots=plain_dots, avatars=avatars)


def main():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    season_cur = data["season_current"]
    season_ref = data["season_reference"]

    COLUMN_NAME = "喵弟球员体检"
    ISSUE_NO = "NO.02"
    bruno_team_id = data["bruno_team_id"]
    bruno_avatar = avatar_abs("422685")
    bruno_badge = badge_abs(bruno_team_id)

    caveat = f"数据：英超，截至 {season_cur.replace('/', '-')} 第{len(data['matches'])}轮（{data['matches'][-1]['date']}）"

    ranks = data["ranks"]
    rank_cc_ref = ranks["chances_created_avg"]["ref_rank"]
    rank_cc_cur = ranks["chances_created_avg"]["cur_rank"]
    rank_xa_ref = ranks["expected_assists_avg"]["ref_rank"]
    rank_xa_cur = ranks["expected_assists_avg"]["cur_rank"]
    rank_ast_ref = ranks["assists_avg"]["ref_rank"]
    rank_ast_cur = ranks["assists_avg"]["cur_rank"]

    for platform in ("xhs", "douyin"):
        plat_cfg = PLATFORMS[platform]
        CANVAS_W, CANVAS_H = plat_cfg["w"], plat_cfg["h"]
        SAFEZONE = plat_cfg["safezone"]
        DEBUG_SAFEZONE = False
        plat_out_dir = OUT_DIR / "bruno" if platform == "xhs" else OUT_DIR / "bruno" / "douyin"
        print(f"=== 平台: {platform}  画布: {CANVAS_W}x{CANVAS_H}  输出目录: {plat_out_dir} ===")

        common_ctx = dict(
            logo_path=str(CACHE_DIR / "logo-mark.png"), column_name=COLUMN_NAME, issue_no=ISSUE_NO,
            platform=platform, canvas_w=CANVAS_W, canvas_h=CANVAS_H,
            safezone=SAFEZONE, debug_safezone=DEBUG_SAFEZONE,
        )

        # ======================= P1 =======================
        CONTENT_W, DY_CONTENT_W = 952, 816
        title1, title2 = "3 项英超第一", "一个没保住"
        title1_fs = fit_font(title1, 120, CONTENT_W)
        title2_fs = fit_font(title2, 130, CONTENT_W)
        title1_fs_dy = fit_font(title1, 100, DY_CONTENT_W)
        title2_fs_dy = fit_font(title2, 110, DY_CONTENT_W)
        print(f"[P1] 标题自适应字号 xhs: {title1_fs}/{title2_fs}px  douyin: {title1_fs_dy}/{title2_fs_dy}px")

        p1_ctx = dict(
            **common_ctx, page_no="1 / 4",
            title1=title1, title1_fs=title1_fs, title1_fs_dy=title1_fs_dy,
            title2=title2, title2_fs=title2_fs, title2_fs_dy=title2_fs_dy,
            avatar_path=bruno_avatar, badge_path=bruno_badge,
            rank_cc_ref=rank_cc_ref, rank_cc_cur=rank_cc_cur,
            rank_xa_ref=rank_xa_ref, rank_xa_cur=rank_xa_cur,
            rank_ast_ref=rank_ast_ref, rank_ast_cur=rank_ast_cur,
            subtitle=f"{data['bruno_age']}岁的B费，还是曼联核心吗？",
            footer_note=caveat,
        )

        # ======================= P2 =======================
        labeled_star_zh = {k: v for k, v in {
            "帕尔默 Cole Palmer": "Cole Palmer", "厄德高 Martin Ødegaard": "Martin Ødegaard",
            "切尔基 Rayan Cherki": "Rayan Cherki", "索博斯洛伊 Dominik Szoboszlai": "Dominik Szoboszlai",
        }.items()}
        gray_star_zh = {"萨卡 Bukayo Saka": "Bukayo Saka", "赖斯 Declan Rice": "Declan Rice"}

        all_x, all_y = [], []
        for season in (season_ref, season_cur):
            for pt in data["pool_scatter"][season]:
                all_x.append(pt["x"]); all_y.append(pt["y"])
            all_x.append(data["combo"][season]["bruno"]["x"]); all_y.append(data["combo"][season]["bruno"]["y"])
        x_min, x_max = 0, max(all_x) * 1.08
        y_min, y_max = 0, max(all_y) * 1.08
        print(f"[P2] 坐标范围（两季统一）: x∈[{x_min:.2f},{x_max:.2f}]  y∈[{y_min:.2f},{y_max:.2f}]")

        if platform == "xhs":
            panel_w, panel_h, avatar_r = 460, 950, 26
        else:
            # dy-panels 顶部 y=400，dy-rank-bar 顶部 y=1330，可用 930px；
            # panel-head(36,flex-shrink:0)+panel-scatter(h)+panel-foot(32,
            # flex-shrink:0) 两份 + gap(16) 必须严格小于 930，否则 .dy-panels
            # 没设固定高度时看似不会被压缩，但会真实溢出撑穿 dy-rank-bar
            # 的绝对定位框——36+376+32=444，444*2+16=904 < 930，留 26px 余量。
            panel_w, panel_h, avatar_r = 816, 376, 22

        second_cc = data["seconds"]["chances_created_avg"]
        note_ref = f"关键传球是{CHINESE_NAMES.get(second_cc['name'], second_cc['name'])}的{second_cc['multiple']:.2f}倍"

        panels = [
            build_panel(season_ref, data, panel_w, panel_h, "ref", x_min, x_max, y_min, y_max,
                        data["pool_scatter"][season_ref], data["combo"][season_ref]["ahead"], data["stars"],
                        (data["combo"][season_ref]["bruno"]["x"], data["combo"][season_ref]["bruno"]["y"]),
                        labeled_star_zh, gray_star_zh, note_ref, avatar_r),
            build_panel(season_cur, data, panel_w, panel_h, "cur", x_min, x_max, y_min, y_max,
                        data["pool_scatter"][season_cur], data["combo"][season_cur]["ahead"], data["stars"],
                        (data["combo"][season_cur]["bruno"]["x"], data["combo"][season_cur]["bruno"]["y"]),
                        labeled_star_zh, gray_star_zh, "", avatar_r),
        ]
        print(f"[P2] {season_ref} 两项都比B费强: {panels[0]['ahead_count']}人  "
              f"{season_cur} 两项都比B费强: {panels[1]['ahead_count']}人 "
              f"({', '.join(CHINESE_NAMES.get(a['name'], a['name']) for a in data['combo'][season_cur]['ahead'])})")

        p2_ctx = dict(
            **common_ctx, page_no="2 / 4",
            page_title="去年一骑绝尘，今年泯然众人",
            panels=panels,
            rank_cc_ref=rank_cc_ref, rank_cc_cur=rank_cc_cur,
            rank_xa_ref=rank_xa_ref, rank_xa_cur=rank_xa_cur,
            rank_ast_ref=rank_ast_ref, rank_ast_cur=rank_ast_cur,
            footer_note=caveat,
        )

        # ======================= P3 =======================
        bar_data = data["bar_chart"]
        cc_series = bar_data["cc_series"]
        xa_series = bar_data["xa_series"]
        best_season = bar_data["best_season"]
        echo_seasons = set(bar_data["echo_seasons"])

        def season_short(s):
            y1, y2 = s.split("/")
            return f"{y1[2:]}/{y2[2:]}"

        def build_bars(series, w, h, bar_gap, val_fmt, dashed_val):
            n = len(series)
            # 最后一根柱子常常正好是"生涯最佳"或"当前"，柱顶标注文字比柱子本身
            # 宽，贴着 viewBox 右边界摆会被 SVG 默认 overflow:hidden 切掉（实测
            # "当前 2.60" 被切成"当前 2.6"）——右侧留 44px 空白只用来放溢出的
            # 标注文字，不参与柱子布局本身。
            layout_w = w - 44
            bw = (layout_w - bar_gap * (n - 1)) / n
            vmax = max(v for _, v in series) * 1.18
            def to_y(v):
                return h - (h * 0.82) * v / vmax
            bars = []
            for i, (s, v) in enumerate(series):
                x = i * (bw + bar_gap)
                y = to_y(v)
                color = "#C8961E" if s == best_season else ("#D23B2C" if s == season_cur else "#D5D8DC")
                tag = None
                tag_gold = False
                if s == best_season:
                    tag = f"生涯最佳 {v:.2f}"
                    tag_gold = True
                elif s == season_cur:
                    tag = f"当前 {v:.2f}"
                bars.append(dict(x=x, y=y, w=bw, h=h - y, color=color,
                                  val_label=val_fmt(v), season_short=season_short(s),
                                  tag=tag, tag_gold=tag_gold, dot=s in echo_seasons))
            dashed_y = to_y(dashed_val)
            return bars, dashed_y

        for w, h, gap, suffix in [(680, 480, 14, ""), (816, 480, 12, "_dy")]:
            bars, dashed_y = build_bars(cc_series, w, h, gap, lambda v: f"{v:.2f}", bar_data["cur_val"])
            if suffix:
                bars_dy, dashed_y_dy, chart_h_dy = bars, dashed_y, h
            else:
                bars_main, dashed_y_main, chart_h = bars, dashed_y, h

        for w, h, gap, suffix in [(190, 150, 6, ""), (776, 150, 8, "_dy")]:
            mbars, _ = build_bars(xa_series, w, h, gap, lambda v: f"{v:.2f}", -1)
            if suffix:
                mini_bars_dy, mini_h_dy = mbars, h
            else:
                mini_bars, mini_h = mbars, h

        p3_ctx = dict(
            **common_ctx, page_no="3 / 4",
            page_title="上赛季是回光返照？",
            bars=bars_main, dashed_y=dashed_y_main, chart_h=chart_h,
            bars_dy=bars_dy, dashed_y_dy=dashed_y_dy, chart_h_dy=chart_h_dy,
            mini_bars=mini_bars, mini_h=mini_h,
            mini_bars_dy=mini_bars_dy, mini_h_dy=mini_h_dy,
            footer_note=caveat,
        )

        # ======================= P4 =======================
        team_zh = {
            "Hull City": "赫尔城", "Manchester United": "曼联", "Ipswich Town": "伊普斯维奇",
            "Everton": "埃弗顿", "Manchester City": "曼城", "Fulham": "富勒姆",
        }
        matches_ctx = []
        for m in data["matches"]:
            highlight = m["home_name"] == "Manchester United" and m["away_name"] == "Ipswich Town" or \
                        m["away_name"] == "Manchester United" and m["home_name"] == "Ipswich Town"
            is_mu_home = m["home_name"] == "Manchester United"
            opp_en = m["away_name"] if is_mu_home else m["home_name"]
            score = f"{m['home_score']}-{m['away_score']}"
            extra = None
            if opp_en == "Manchester City":
                extra = f"全场{m['chances_created']}次关键传球"
            matches_ctx.append(dict(
                date=m["date"].replace("2026-", "").replace("2027-", ""),
                opp_zh=team_zh.get(opp_en, opp_en),
                score=f"{team_zh.get(m['home_name'], m['home_name'])} {score} {team_zh.get(m['away_name'], m['away_name'])}",
                goals=m["goals"], assists=m["assists"], chances_created=m["chances_created"],
                highlight=highlight, extra=extra,
            ))

        p4_ctx = dict(
            **common_ctx, page_no="4 / 4",
            page_title="5 场 3 球？3 个都在同一场",
            matches=matches_ctx,
            footer_note=caveat,
        )

        # ======================= 渲染 + 截图 =======================
        pages = [
            ("p1_bruno.html.j2", p1_ctx, "p1"),
            ("p2_bruno.html.j2", p2_ctx, "p2"),
            ("p3_bruno.html.j2", p3_ctx, "p3"),
            ("p4_bruno.html.j2", p4_ctx, "p4"),
        ]
        for tpl_name, ctx, stub in pages:
            html_path = render_page(tpl_name, ctx, stub, plat_out_dir)
            out_2x = plat_out_dir / f"{stub}_2x.png"
            out_final = plat_out_dir / f"{stub}.png"
            shoot(html_path, out_2x, out_final, CANVAS_W, CANVAS_H)
        print()


if __name__ == "__main__":
    main()
