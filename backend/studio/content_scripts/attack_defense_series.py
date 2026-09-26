"""attack_defense_series：姆巴佩"进攻浪费 + 防守参与度"系列内容图，一次性
内容脚本，产出发小红书/视频号的静态 PNG。跟 player_h2h_chart.py 同一类——
直连生产库 mode=ro 现查现画，不服务任何线上接口，数据缓存在 _cache/ 不进 git。

数据来自生产库 fact_player_match_stats / fact_match_events / fact_shotmap /
fact_team_match_stats / dim_match（西甲 League_ID=87，欧冠 League_ID=42，
Season='2026/2027'）。基准池口径（本轮改定，替换 player_h2h_chart.py 那版）：

    usual_position 按整季众数判定（该球员本赛季西甲全部出场里，出现次数
    最多的 usual_position 值），=3(FWD) 即入池；分钟数累加该球员整季西甲
    出场（不按单场 usual_position 过滤），门槛 ≥450 分钟。
    ——旧版按"逐场 usual_position=3 过滤后再求和分钟"，会把 Arnaut Danjuma
    这类赛季中段被偶尔标成 MID 的球员漏掉（19 人 vs 这版 20 人，差的就是他）。

defensive_actions 构成已用姆巴佩+维尼修斯 14 场逐场数据验证：
    defensive_actions == tackles + interceptions（14/14 场精确相等，
    shot_blocks/clearances 在这些场次均为 0，不能排除极端场次下也计入，
    但样本内无一次偏差）。

图1（进攻"浪费"）口径是西甲+欧冠 8 场；图2/图3/图4 里凡涉及"排名/参与度"
的部分口径是纯西甲 7 场（欧冠只有 1 场，混进排名会破坏"前场基准池"这个
西甲专属分母的同源性）——两种口径在各图上都有文字标注，不混用不标注。
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "_cache"

FONT_PATH = "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/3419f2a427639ad8c8e139149a287865a90fa17e.asset/AssetData/PingFang.ttc"


def pil_font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    idx = {"regular": 3, "medium": 7, "semibold": 11}[weight]
    return ImageFont.truetype(FONT_PATH, size, index=idx)


LIGHT = dict(
    bg="#F4F5F2", card="#FFFFFF", border="#E3E5E0",
    ink="#14202B", ink2="#6B7785", ink3="#9AA3AD",
    gray="#D8DBD6", gold_deep="#8a6a0b", loss="#b83b2d",
)

RM_COLOR = "#C8961E"
BARCA_COLOR = "#A50044"


def wrap_text(draw, text, font, max_width):
    lines, cur = [], ""
    for ch in text:
        trial = cur + ch
        if draw.textlength(trial, font=font) > max_width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def load_json(name: str):
    return json.loads((CACHE_DIR / name).read_text())


def draw_footer(canvas, draw, C, y, W, pad, note_lines, logo):
    f_note = pil_font(16, "regular")
    for line in note_lines:
        for wrapped in wrap_text(draw, line, f_note, W - pad * 2 - 210):
            draw.text((pad, y), wrapped, font=f_note, fill=C["ink3"])
            y += 23
        y += 2

    logo_badge_h = 66
    logo_pad = 10
    logo_disp_h = logo_badge_h - logo_pad * 2
    logo_disp_w = int(logo_disp_h * logo.width / logo.height)
    logo_fit = logo.resize((logo_disp_w, logo_disp_h), Image.LANCZOS)
    badge_w = logo_disp_w + logo_pad * 2
    f_brand = pil_font(20, "semibold")
    brand_text = "喵弟数据研究室"
    tw = draw.textlength(brand_text, font=f_brand)
    badge_x = W - pad - badge_w
    badge_y = y + 16
    text_x = badge_x - 12 - tw
    text_y = badge_y + (logo_badge_h - 28) // 2
    draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + logo_badge_h], radius=14,
                            fill=C["card"], outline=C["border"], width=1)
    canvas.paste(logo_fit, (badge_x + logo_pad, badge_y + logo_pad), logo_fit)
    draw.text((text_x, text_y), brand_text, font=f_brand, fill=C["gold_deep"])
    return badge_y + logo_badge_h + pad


# =====================================================================
# 图1：姆巴佩的进攻"浪费"（西甲 + 欧冠，8 场）
# =====================================================================
def build_chart1(matches, yamal_bcm_total, logo, out_path):
    W, H = 1080, 1700
    C = LIGHT
    canvas = Image.new("RGB", (W, H), C["bg"])
    draw = ImageDraw.Draw(canvas)
    pad = 40

    f_title = pil_font(34, "semibold")
    f_subtitle = pil_font(19, "regular")
    title = "姆巴佩的进攻：浪费"
    subtitle = "2026/2027 赛季 · 西甲 + 欧冠 · 8 场"
    tw = draw.textlength(title, font=f_title)
    draw.text((W / 2 - tw / 2, pad), title, font=f_title, fill=C["ink"])
    sw = draw.textlength(subtitle, font=f_subtitle)
    draw.text((W / 2 - sw / 2, pad + 48), subtitle, font=f_subtitle, fill=C["ink2"])

    y = pad + 90
    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 30

    # ---- 主数字：大机会错失 11 次 + 罚丢点球 1 次（不确定是否重叠，分列） ----
    total_bcm = sum(m["bcm"] for m in matches)
    total_pen_missed = sum(1 for m in matches if m["pen_missed"])
    f_big_label = pil_font(20, "regular")
    f_big_num = pil_font(64, "semibold")
    draw.text((pad, y), "大机会错失", font=f_big_label, fill=C["ink3"])
    y += 30
    draw.text((pad, y), f"{total_bcm} 次", font=f_big_num, fill=RM_COLOR)
    y += 74

    f_sub_num_label = pil_font(17, "regular")
    f_sub_num = pil_font(26, "semibold")
    draw.text((pad, y), "罚丢点球", font=f_sub_num_label, fill=C["ink3"])
    draw.text((pad + 100, y - 4), f"{total_pen_missed} 次", font=f_sub_num, fill=C["loss"])
    y += 40
    f_caveat = pil_font(13, "regular")
    caveat = ("说明：09-04 客场贝蒂斯那场同时出现 4 次大机会错失与 1 次罚丢点球，两者是否"
              "存在重叠（点球本身是否被计入大机会）在现有数据字段里无法确认，故分开列示。")
    for line in wrap_text(draw, caveat, f_caveat, W - pad * 2):
        draw.text((pad, y), line, font=f_caveat, fill=C["ink3"])
        y += 19
    y += 18

    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 26

    # ---- 8 场逐场小图 ----
    f_grid_title = pil_font(19, "semibold")
    draw.text((pad, y), "逐场表现", font=f_grid_title, fill=C["ink"])
    y += 32

    n = len(matches)
    col_gap = 6
    col_w = (W - pad * 2 - col_gap * (n - 1)) / n
    bar_track_h = 180
    bar_track_y0 = y
    bar_track_y1 = y + bar_track_h
    max_xg = max(m["xg"] for m in matches)

    f_opp = pil_font(13, "semibold")
    f_side = pil_font(11, "regular")
    f_bcm = pil_font(12, "regular")
    f_dot = pil_font(14, "semibold")
    f_tag = pil_font(10, "semibold")

    for i, m in enumerate(matches):
        cx0 = pad + i * (col_w + col_gap)
        cx1 = cx0 + col_w
        cxm = (cx0 + cx1) / 2

        highlight = m.get("bcm", 0) >= 4
        derby = m.get("derby", False)

        if highlight:
            draw.rounded_rectangle([cx0 - 3, bar_track_y0 - 46, cx1 + 3, bar_track_y1 + 84],
                                    radius=10, outline=C["loss"], width=2)

        # 竖条 = 当场 xG
        bar_h = bar_track_h * (m["xg"] / max_xg) if max_xg else 0
        bar_w = min(col_w * 0.42, 22)
        bx0 = cxm - bar_w / 2
        bar_top = bar_track_y1 - bar_h
        bar_color = RM_COLOR if not highlight else C["loss"]
        draw.rounded_rectangle([bx0, bar_top, bx0 + bar_w, bar_track_y1], radius=4, fill=bar_color)
        xg_txt = f"{m['xg']:.2f}"
        draw.text((cxm, bar_top - 6), xg_txt, font=f_side, fill=C["ink3"], anchor="mb")

        # 条上方圆点 = 实际进球数
        dot_y = bar_track_y0 - 22
        goals = m["goals"]
        if goals > 0:
            dot_r = 9
            total_dw = goals * (dot_r * 2 + 4) - 4
            dstart = cxm - total_dw / 2
            for g in range(goals):
                dcx = dstart + g * (dot_r * 2 + 4) + dot_r
                draw.ellipse([dcx - dot_r, dot_y - dot_r, dcx + dot_r, dot_y + dot_r], fill=C["ink"])
        else:
            draw.text((cxm, dot_y), "0球", font=f_side, fill=C["ink3"], anchor="mm")

        # 底部标签：对手 + 主客场 + 联赛标签
        label_y = bar_track_y1 + 10
        side_txt = "主" if m["side"] == "home" else "客"
        opp_line = f"{side_txt} {m['opp']}"
        for line in wrap_text(draw, opp_line, f_opp, col_w):
            ow = draw.textlength(line, font=f_opp)
            draw.text((cxm - ow / 2, label_y), line, font=f_opp, fill=C["ink"])
            label_y += 16

        if m["league"] == "欧冠":
            tag_w = draw.textlength("欧冠", font=f_tag) + 10
            draw.rounded_rectangle([cxm - tag_w / 2, label_y, cxm + tag_w / 2, label_y + 16],
                                    radius=4, fill=C["gold_deep"])
            draw.text((cxm, label_y + 8), "欧冠", font=f_tag, fill="#ffffff", anchor="mm")
            label_y += 20

        bcm_txt = f"大机会错失 {m['bcm']}"
        bw = draw.textlength(bcm_txt, font=f_bcm)
        draw.text((cxm - bw / 2, label_y), bcm_txt, font=f_bcm,
                   fill=(C["loss"] if highlight else C["ink3"]))
        label_y += 18

        if derby:
            derby_txt = f"德比全场 xG {m['xg']:.2f}"
            dw = draw.textlength(derby_txt, font=f_bcm)
            draw.text((cxm - dw / 2, label_y), derby_txt, font=f_bcm, fill=C["loss"])
            label_y += 18

        if highlight:
            note = "4次大机会+罚丢点球，全场0球"
            for line in wrap_text(draw, note, f_bcm, col_w + 20):
                nw = draw.textlength(line, font=f_bcm)
                draw.text((cxm - nw / 2, label_y), line, font=f_bcm, fill=C["loss"])
                label_y += 16

    y = bar_track_y1 + 150
    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 24

    # ---- 底部参照小框：亚马尔同口径 ----
    f_ref_title = pil_font(16, "semibold")
    f_ref_body = pil_font(15, "regular")
    box_h = 70
    draw.rounded_rectangle([pad, y, W - pad, y + box_h], radius=12, fill=C["card"], outline=C["border"], width=1)
    draw.text((pad + 18, y + 14), "参照：亚马尔同口径（西甲+欧冠 8 场）",
               font=f_ref_title, fill=C["ink3"])
    draw.text((pad + 18, y + 38), f"大机会错失 {yamal_bcm_total} 次", font=f_ref_body, fill=BARCA_COLOR)
    y += box_h + 24

    note_lines = ["数据来源：西甲 + 欧冠 2026/2027 赛季至今 · 大机会错失/罚丢点球为 FotMob 原始统计口径"]
    used_h = draw_footer(canvas, draw, C, y, W, pad, note_lines, logo)
    canvas = canvas.crop((0, 0, W, used_h))
    canvas.save(out_path)
    print(f"[图1] canvas height used: {used_h}px")
    return total_bcm, total_pen_missed


# =====================================================================
# 图2：西甲前场：抢断 + 拦截 排行（纯西甲 20 人）
# =====================================================================
def build_chart2(pool, mbappe_zero_count, logo, out_path):
    W = 1080
    C = LIGHT
    pad = 40

    ranked = sorted(pool, key=lambda r: -r["defensive_actions_p90"])
    mbappe_rank = next(i for i, r in enumerate(ranked, 1) if r["player_name"] == "Kylian Mbappé")
    n = len(ranked)

    bold_names = {"Vinícius Júnior", "Lamine Yamal", "Raphinha"}

    row_h = 48
    header_h = 260
    footer_reserve = 130
    H = header_h + row_h * n + footer_reserve + 40
    canvas = Image.new("RGB", (W, H), C["bg"])
    draw = ImageDraw.Draw(canvas)

    f_hook = pil_font(30, "semibold")
    hook = f"7 场西甲，{mbappe_zero_count} 场抢断拦截为 0"
    hw = draw.textlength(hook, font=f_hook)
    draw.text((W / 2 - hw / 2, pad), hook, font=f_hook, fill=C["loss"])

    f_title = pil_font(24, "semibold")
    f_subtitle = pil_font(17, "regular")
    title = "西甲前场：抢断 + 拦截 排行"
    tw = draw.textlength(title, font=f_title)
    draw.text((W / 2 - tw / 2, pad + 46), title, font=f_title, fill=C["ink"])
    subtitle = "defensive_actions_p90 = (抢断+拦截) / 90分钟，降序"
    sw = draw.textlength(subtitle, font=f_subtitle)
    draw.text((W / 2 - sw / 2, pad + 82), subtitle, font=f_subtitle, fill=C["ink3"])

    y = pad + 118
    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 20

    max_val = max(r["defensive_actions_p90"] for r in ranked) or 1
    track_x0 = pad + 250
    track_x1 = W - pad - 90
    f_name = pil_font(16, "semibold")
    f_team = pil_font(13, "regular")
    f_val = pil_font(16, "semibold")
    f_rank = pil_font(14, "regular")

    for i, r in enumerate(ranked, 1):
        is_mbappe = r["player_name"] == "Kylian Mbappé"
        row_y = y + (i - 1) * row_h
        bar_color = RM_COLOR if is_mbappe else C["gray"]
        name_color = C["ink"] if (is_mbappe or r["player_name"] in bold_names) else C["ink2"]
        name_font = pil_font(16, "semibold") if (is_mbappe or r["player_name"] in bold_names) else pil_font(16, "regular")

        draw.text((pad, row_y + 4), f"{i}", font=f_rank, fill=C["ink3"])
        label = f"{r['player_name']}"
        draw.text((pad + 26, row_y + 2), label, font=name_font, fill=name_color)
        team_w = draw.textlength(label, font=name_font)
        draw.text((pad + 26 + team_w + 8, row_y + 5), r["team"], font=f_team, fill=C["ink3"])

        bar_w = (track_x1 - track_x0) * (r["defensive_actions_p90"] / max_val)
        bar_h = 20
        draw.rounded_rectangle([track_x0, row_y + 2, track_x0 + max(bar_w, 4), row_y + 2 + bar_h],
                                radius=5, fill=bar_color)
        val_txt = f"{r['defensive_actions_p90']:.2f}"
        draw.text((W - pad, row_y + 3), val_txt, font=f_val, fill=(RM_COLOR if is_mbappe else C["ink"]),
                   anchor="ra")

        if is_mbappe:
            tag = f"第 {mbappe_rank} / {n}"
            tag_w = draw.textlength(tag, font=f_team) + 12
            draw.rounded_rectangle([track_x0 + max(bar_w, 4) + 10, row_y + 4, track_x0 + max(bar_w, 4) + 10 + tag_w, row_y + 20],
                                    radius=4, fill=RM_COLOR)
            draw.text((track_x0 + max(bar_w, 4) + 16, row_y + 6), tag, font=f_team, fill="#ffffff")

    y = y + row_h * n + 16
    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 20
    note_lines = ["口径：FotMob defensive_actions（=抢断+拦截），西甲 2026/2027 赛季至今，出场 ≥450 分钟，样本 20 人"]
    used_h = draw_footer(canvas, draw, C, y, W, pad, note_lines, logo)
    canvas = canvas.crop((0, 0, W, used_h))
    canvas.save(out_path)
    print(f"[图2] canvas height used: {used_h}px, 姆巴佩排名 {mbappe_rank}/{n}")
    return mbappe_rank, n


# =====================================================================
# 图3：防守举证"不是控球的问题"
# =====================================================================
def build_chart3(pool, regression, same_pos_group, teammate, weekly, rm_possession, barca_possession, logo, out_path):
    W = 1080
    C = LIGHT
    pad = 40

    H = 1980
    canvas = Image.new("RGB", (W, H), C["bg"])
    draw = ImageDraw.Draw(canvas)

    f_title = pil_font(28, "semibold")
    f_subtitle = pil_font(17, "regular")
    title = "防守举证：不是控球的问题"
    tw = draw.textlength(title, font=f_title)
    draw.text((W / 2 - tw / 2, pad), title, font=f_title, fill=C["ink"])
    subtitle = "横轴：球队西甲场均控球率 · 纵轴：球员 defensive_actions_p90"
    sw = draw.textlength(subtitle, font=f_subtitle)
    draw.text((W / 2 - sw / 2, pad + 38), subtitle, font=f_subtitle, fill=C["ink3"])

    y = pad + 74
    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 24

    # ---- 散点图 ----
    plot_h = 420
    plot_x0, plot_x1 = pad + 50, W - pad - 20
    plot_y0, plot_y1 = y, y + plot_h

    xs = [r["team_possession"] for r in pool]
    ys = [r["defensive_actions_p90"] for r in pool]
    x_min, x_max = min(xs) - 3, max(xs) + 3
    y_min, y_max = 0, max(ys) * 1.15

    def to_px(v):
        return plot_x0 + (plot_x1 - plot_x0) * (v - x_min) / (x_max - x_min)

    def to_py(v):
        return plot_y1 - (plot_y1 - plot_y0) * (v - y_min) / (y_max - y_min)

    draw.line([(plot_x0, plot_y0), (plot_x0, plot_y1)], fill=C["border"], width=1)
    draw.line([(plot_x0, plot_y1), (plot_x1, plot_y1)], fill=C["border"], width=1)

    f_axis = pil_font(12, "regular")
    for gx in range(40, int(x_max) + 1, 10):
        if gx < x_min:
            continue
        px = to_px(gx)
        draw.line([(px, plot_y1), (px, plot_y1 + 5)], fill=C["ink3"], width=1)
        draw.text((px, plot_y1 + 8), f"{gx}%", font=f_axis, fill=C["ink3"], anchor="ma")

    # 趋势线
    slope, intercept = regression["slope"], regression["intercept"]
    tx0, tx1 = x_min, x_max
    ty0, ty1 = slope * tx0 + intercept, slope * tx1 + intercept
    line_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(line_img)
    dash_len = 8
    px0, py0v = to_px(tx0), to_py(ty0)
    px1, py1v = to_px(tx1), to_py(ty1)
    steps = int(((px1 - px0) ** 2 + (py1v - py0v) ** 2) ** 0.5 / (dash_len * 2))
    for s in range(max(steps, 1)):
        t0 = s / steps
        t1 = (s + 0.5) / steps
        ld.line([(px0 + (px1 - px0) * t0, py0v + (py1v - py0v) * t0),
                  (px0 + (px1 - px0) * t1, py0v + (py1v - py0v) * t1)], fill=(154, 163, 173, 255), width=2)
    canvas.paste(line_img, (0, 0), line_img)

    for r in pool:
        px, py = to_px(r["team_possession"]), to_py(r["defensive_actions_p90"])
        name = r["player_name"]
        if name == "Kylian Mbappé":
            continue  # 最后画，确保压在最上层
        if name in ("Lamine Yamal", "Raphinha"):
            rad, color = 7, BARCA_COLOR
        else:
            rad, color = 5, C["gray"]
        draw.ellipse([px - rad, py - rad, px + rad, py + rad], fill=color)
        if name in ("Lamine Yamal", "Raphinha"):
            zh = "亚马尔" if name == "Lamine Yamal" else "拉菲尼亚"
            # 两人同队、控球率横坐标几乎重合，标签一个放点上方一个放点下方避免压字
            if name == "Lamine Yamal":
                draw.text((px, py - rad - 6), zh, font=f_axis, fill=BARCA_COLOR, anchor="mb")
            else:
                draw.text((px, py + rad + 6), zh, font=f_axis, fill=BARCA_COLOR, anchor="mt")

    mb = next(r for r in pool if r["player_name"] == "Kylian Mbappé")
    mpx, mpy = to_px(mb["team_possession"]), to_py(mb["defensive_actions_p90"])
    draw.ellipse([mpx - 10, mpy - 10, mpx + 10, mpy + 10], fill=RM_COLOR, outline="#ffffff", width=2)
    f_mb_label = pil_font(15, "semibold")
    draw.text((mpx, mpy - 18), "姆巴佩", font=f_mb_label, fill=RM_COLOR, anchor="mb")

    f_axis_note = pil_font(13, "regular")
    draw.text((to_px(rm_possession), plot_y1 + 24), f"皇马 {rm_possession:.1f}%", font=f_axis_note,
               fill=RM_COLOR, anchor="ma")
    draw.text((to_px(barca_possession), plot_y1 + 24), f"巴萨 {barca_possession:.1f}%", font=f_axis_note,
               fill=BARCA_COLOR, anchor="ma")

    dev_text = f"姆巴佩低于趋势线预测值 {abs(regression['mbappe_deviation']):.2f}"
    dw = draw.textlength(dev_text, font=f_axis_note)
    draw.text((W - pad - dw, plot_y0 - 6), dev_text, font=f_axis_note, fill=C["loss"])

    y = plot_y1 + 50
    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 24

    # ---- 3 张小卡 ----
    card_gap = 16
    card_w = W - pad * 2
    f_card_title = pil_font(17, "semibold")
    f_card_note = pil_font(13, "regular")

    # 卡1：同站位对比
    card1_h = 40 + len(same_pos_group) * 34 + 20
    draw.rounded_rectangle([pad, y, pad + card_w, y + card1_h], radius=12, fill=C["card"], outline=C["border"], width=1)
    draw.text((pad + 18, y + 14), "与姆巴佩站位相同的球员", font=f_card_title, fill=C["ink"])
    ry = y + 44
    max_v = max(g["defensive_actions_p90"] for g in same_pos_group) or 1
    bar_x0, bar_x1 = pad + 220, pad + card_w - 100
    for g in same_pos_group:
        is_mb = g["player_name"] == "Kylian Mbappé"
        name_font = pil_font(14, "semibold") if is_mb else pil_font(14, "regular")
        name_color = RM_COLOR if is_mb else C["ink2"]
        draw.text((pad + 18, ry), g["player_name"], font=name_font, fill=name_color)
        bw = (bar_x1 - bar_x0) * (g["defensive_actions_p90"] / max_v)
        draw.rounded_rectangle([bar_x0, ry + 2, bar_x0 + max(bw, 3), ry + 16], radius=4,
                                fill=(RM_COLOR if is_mb else C["gray"]))
        draw.text((pad + card_w - 18, ry), f"{g['defensive_actions_p90']:.2f}", font=f_card_note,
                   fill=(RM_COLOR if is_mb else C["ink3"]), anchor="ra")
        ry += 34
    y += card1_h + card_gap

    # 卡2：队友对比
    card2_h = 150
    draw.rounded_rectangle([pad, y, pad + card_w, y + card2_h], radius=12, fill=C["card"], outline=C["border"], width=1)
    draw.text((pad + 18, y + 14), "队友对比：夺回球权 per90", font=f_card_title, fill=C["ink"])
    bar_y = y + 50
    bar_h2 = 30
    max_v2 = max(teammate["vini_p90"], teammate["mbappe_p90"]) * 1.15
    bx0, bx1 = pad + 140, pad + card_w - 100
    for label, val, color, raw in [
        ("维尼修斯", teammate["vini_p90"], RM_COLOR, teammate["vini_raw"]),
        ("姆巴佩", teammate["mbappe_p90"], C["ink3"], teammate["mbappe_raw"]),
    ]:
        draw.text((pad + 18, bar_y + 6), label, font=f_card_note, fill=C["ink"])
        bw = (bx1 - bx0) * (val / max_v2)
        draw.rounded_rectangle([bx0, bar_y, bx0 + max(bw, 3), bar_y + bar_h2], radius=6, fill=color)
        draw.text((pad + card_w - 18, bar_y + 6), f"{val:.2f}", font=f_card_note, fill=C["ink"], anchor="ra")
        bar_y += bar_h2 + 12
    raw_txt = f"原始次数：{teammate['vini_raw']} vs {teammate['mbappe_raw']}"
    draw.text((pad + 18, y + card2_h - 22), raw_txt, font=pil_font(12, "regular"), fill=C["ink3"])
    y += card2_h + card_gap

    # 卡3：逐场小方块
    card3_h = 150
    draw.rounded_rectangle([pad, y, pad + card_w, y + card3_h], radius=12, fill=C["card"], outline=C["border"], width=1)
    draw.text((pad + 18, y + 14), "姆巴佩逐场：抢断+拦截", font=f_card_title, fill=C["ink"])
    sq_gap = 10
    sq_n = len(weekly)
    sq_w = (card_w - 36 - sq_gap * (sq_n - 1)) / sq_n
    sq_size = min(sq_w, 76)
    sq_y = y + 50
    f_sq_opp = pil_font(12, "regular")
    f_sq_val = pil_font(18, "semibold")
    for i, wk in enumerate(weekly):
        sx0 = pad + 18 + i * (sq_w + sq_gap)
        zero = wk["defact"] == 0
        fill_c = C["loss"] if zero else RM_COLOR
        draw.rounded_rectangle([sx0, sq_y, sx0 + sq_size, sq_y + sq_size], radius=8,
                                fill=(blend_over_local(fill_c, C["card"], 0.12)), outline=fill_c, width=2)
        draw.text((sx0 + sq_size / 2, sq_y + sq_size * 0.38), str(int(wk["defact"])), font=f_sq_val,
                   fill=fill_c, anchor="mm")
        for j, line in enumerate(wrap_text(draw, wk["opp"], f_sq_opp, sq_size)):
            draw.text((sx0 + sq_size / 2, sq_y + sq_size + 4 + j * 15), line, font=f_sq_opp,
                       fill=C["ink3"], anchor="ma")
    y += card3_h + 30

    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 20
    note_lines = [
        f"口径：西甲 2026/2027 赛季至今 · 散点图为西甲前场基准池 20 人 · 控球率数据来源 fact_team_match_stats",
        f"控球率：皇马 {rm_possession:.1f}% · 巴萨 {barca_possession:.1f}%（控球率高的球队前锋防守机会天然更少，"
        "但姆巴佩即便对照球队控球率仍低于趋势线预测值）",
    ]
    used_h = draw_footer(canvas, draw, C, y, W, pad, note_lines, logo)
    canvas = canvas.crop((0, 0, W, used_h))
    canvas.save(out_path)
    print(f"[图3] canvas height used: {used_h}px")


def blend_over_local(fg_hex: str, bg_hex: str, alpha: float) -> str:
    fg = tuple(int(fg_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    bg = tuple(int(bg_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    mixed = tuple(round(fg[i] * alpha + bg[i] * (1 - alpha)) for i in range(3))
    return "#%02x%02x%02x" % mixed


# =====================================================================
# 图4：总结海报
# =====================================================================
def build_chart4(total_bcm, total_pen_missed, mbappe_zero_count, mbappe_rank, pool_n, logo, out_path):
    W, H = 1080, 1000
    C = LIGHT
    canvas = Image.new("RGB", (W, H), C["bg"])
    draw = ImageDraw.Draw(canvas)
    pad = 40

    f_title = pil_font(30, "semibold")
    title = "姆巴佩的 7 个进球，皇马付出了什么？"
    for i, line in enumerate(wrap_text(draw, title, f_title, W - pad * 2)):
        tw = draw.textlength(line, font=f_title)
        draw.text((W / 2 - tw / 2, pad + i * 42), line, font=f_title, fill=C["ink"])

    y = pad + 100
    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 30

    cards = [
        ("大机会错失", f"{total_bcm} 次", "西甲 + 欧冠", RM_COLOR),
        ("罚丢点球", f"{total_pen_missed} 次", "西甲", C["loss"]),
        ("抢断 + 拦截 西甲前场", f"{mbappe_rank} / {pool_n}", "西甲", RM_COLOR),
    ]
    card_w = (W - pad * 2 - 2 * 16) / 3
    card_h = 220
    f_card_label = pil_font(16, "regular")
    f_card_num = pil_font(34, "semibold")
    f_card_scope = pil_font(13, "regular")
    for i, (label, num, scope, color) in enumerate(cards):
        cx0 = pad + i * (card_w + 16)
        draw.rounded_rectangle([cx0, y, cx0 + card_w, y + card_h], radius=14, fill=C["card"],
                                outline=C["border"], width=1)
        ty = y + 24
        for line in wrap_text(draw, label, f_card_label, card_w - 24):
            draw.text((cx0 + card_w / 2, ty), line, font=f_card_label, fill=C["ink3"], anchor="ma")
            ty += 20
        num_y = y + card_h / 2 - 10
        nw = draw.textlength(num, font=f_card_num)
        if nw > card_w - 20:
            f_num_fit = pil_font(24, "semibold")
            nw = draw.textlength(num, font=f_num_fit)
            draw.text((cx0 + card_w / 2 - nw / 2, num_y), num, font=f_num_fit, fill=color)
        else:
            draw.text((cx0 + card_w / 2 - nw / 2, num_y), num, font=f_card_num, fill=color)
        sw = draw.textlength(scope, font=f_card_scope)
        draw.text((cx0 + card_w / 2 - sw / 2, y + card_h - 30), scope, font=f_card_scope, fill=C["ink3"])
    y += card_h + 40

    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 30

    f_tag = pil_font(15, "semibold")
    tag_text = "喵弟观点"
    tag_w = draw.textlength(tag_text, font=f_tag) + 24
    draw.rounded_rectangle([W - pad - tag_w, y, W - pad, y + 32], radius=16, fill=C["gold_deep"])
    draw.text((W - pad - tag_w / 2, y + 16), tag_text, font=f_tag, fill="#ffffff", anchor="mm")
    y += 50

    note_lines = ["数据来源：西甲 + 欧冠 2026/2027 赛季至今"]
    used_h = draw_footer(canvas, draw, C, y, W, pad, note_lines, logo)
    canvas = canvas.crop((0, 0, W, used_h))
    canvas.save(out_path)
    print(f"[图4] canvas height used: {used_h}px")


def main():
    logo = Image.open(CACHE_DIR / "logo-mark.png").convert("RGBA")

    mbappe_matches = load_json("mbappe_matches.json")
    yamal_summary = load_json("yamal_summary.json")
    pool = load_json("pool20.json")
    regression = load_json("regression.json")

    print("=== 图1 ===")
    total_bcm, total_pen_missed = build_chart1(
        mbappe_matches, yamal_summary["bcm_total"], logo,
        HERE / "mbappe_attack_waste.png",
    )

    # 纯西甲 7 场逐场 defensive_actions（用于图2 hook + 图3 卡3）
    laliga_rows = [m for m in json.loads((CACHE_DIR / "_tmp_defense_fields.json").read_text())
                   if m["player_name"] == "Kylian Mbappé"]
    weekly = []
    opp_by_match = {
        5868018: "皇家社会", 5868025: "西班牙人", 5868038: "马拉加", 5868043: "皇家贝蒂斯",
        5868057: "巴列卡诺", 5868065: "埃尔切", 5868072: "马德里竞技",
    }
    for r in sorted(laliga_rows, key=lambda x: x["Match_ID"]):
        weekly.append(dict(opp=opp_by_match[r["Match_ID"]], defact=r["defensive_actions"]))
    mbappe_zero_count = sum(1 for w in weekly if w["defact"] == 0)

    print("=== 图2 ===")
    mbappe_rank, pool_n = build_chart2(pool, mbappe_zero_count, logo, HERE / "laliga_defensive_ranking.png")

    print("=== 图3 ===")
    rm_possession = next(r for r in pool if r["player_name"] == "Kylian Mbappé")["team_possession"]
    barca_possession = next(r for r in pool if r["player_name"] == "Lamine Yamal")["team_possession"]

    same_pos_names = ["Fer Niño", "Ante Budimir", "Chupe", "Roberto Fernández",
                       "Sergio Camello", "Raphinha", "Kylian Mbappé"]
    same_pos_group = sorted(
        [r for r in pool if r["player_name"] in same_pos_names],
        key=lambda r: -r["defensive_actions_p90"],
    )
    assert len(same_pos_group) == 7
    print("同站位(115)子集排序:", [(g["player_name"], g["defensive_actions_p90"]) for g in same_pos_group])

    vini = next(r for r in pool if r["player_name"] == "Vinícius Júnior")
    mbappe_pool = next(r for r in pool if r["player_name"] == "Kylian Mbappé")
    teammate = dict(
        vini_p90=vini["recoveries_p90"], mbappe_p90=mbappe_pool["recoveries_p90"],
        vini_raw=f"{round(vini['recoveries_p90'] * vini['mins'] / 90)}次 / {vini['mins']}分钟",
        mbappe_raw=f"{round(mbappe_pool['recoveries_p90'] * mbappe_pool['mins'] / 90)}次 / {mbappe_pool['mins']}分钟",
    )
    print("队友对比:", teammate)

    build_chart3(pool, regression, same_pos_group, teammate, weekly, rm_possession, barca_possession,
                 logo, HERE / "defensive_evidence.png")

    print("=== 图4 ===")
    build_chart4(total_bcm, total_pen_missed, mbappe_zero_count, mbappe_rank, pool_n, logo,
                 HERE / "summary_poster.png")


if __name__ == "__main__":
    main()
