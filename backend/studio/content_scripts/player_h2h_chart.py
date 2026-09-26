"""player_h2h_chart：球员 1v1 对比图，一次性内容脚本，产出发小红书/视频号的
静态 PNG。跟 backend/studio/bundle.py 那套结构化 analysis_bundle 不是一回事——
这个脚本直连生产库 mode=ro 现查现画，不走 bundle，不服务任何线上接口。

2026-09-23 第二轮重设计（依赖度模块 + 联赛刻度条 + 射门效率拆分 + 效率模块，
取代第一轮的纯 butterfly 对比图；这一版起脚本本身迁入仓库，但生产数据缓存
(backend/studio/content_scripts/_cache/) 不进 git，见 .gitignore）。

数据来自生产库 fact_player_match_stats + fact_team_match_stats + dim_match
（League_ID=87 西甲，Season='2026/2027', status='Finish'）。三条纪律：

1. 百分位基准池：西甲 + usual_position=3(FWD) + 出场≥450分钟。不做
   position_id（FotMob 11..105 内部码）解码——项目代码里已经明确写着
   "不外露"（backend/queries/match_report.py 的 POSITION_GROUPS 注释），
   现在也没有解码表，池子描述如实写成"西甲前场球员（前锋+边锋）"，不
   假装能细分中锋/边锋/前腰。

2. 依赖度分母：不是联赛平均，是"该球员出场的每一场比赛，球队在那场比赛
   的真实产出"——xG/射门/xA/关键传球/成功过人全部用 fact_player_match_stats
   按 Match_ID+Team_ID 汇总（跟分子同源，避免"分子来自球员表、分母来自
   团队表"这种统计口径不一致的假比例）；进球用 dim_match 的真实比分
   （球队进球数不能从球员表求和，乌龙球等情况对不上）。上线前跑过交叉
   校验：球员表汇总的球队 xG/射门/成功过人 与 fact_team_match_stats.extra_json
   的对应字段逐场比对，14 场里最大偏差 3.3%，全部 < 5% 的门槛。

3. 射门效率分母：总射门 = 射正 + 射偏 + 被封堵（fact_player_match_stats 没
   有现成的 total_shots 列，射正率/转化率的分母不能只用"射正+射偏"漏掉
   被封堵的部分，那会把射门效率算高）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "_cache"

FONT_PATH = "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/3419f2a427639ad8c8e139149a287865a90fa17e.asset/AssetData/PingFang.ttc"


def pil_font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    idx = {"regular": 3, "medium": 7, "semibold": 11}[weight]
    return ImageFont.truetype(FONT_PATH, size, index=idx)


# ---- 配色（沿用第一轮重设计的浅色 token，未改动）----
LIGHT = dict(
    bg="#F4F5F2", card="#FFFFFF", border="#E3E5E0",
    ink="#14202B", ink2="#6B7785", ink3="#9AA3AD",
    gray="#D8DBD6", gold_deep="#8a6a0b", loss="#b83b2d",
)

TIE_THRESHOLD = 0.10


def relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def blend_over(fg_hex: str, bg_hex: str, alpha: float) -> str:
    fg = tuple(int(fg_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    bg = tuple(int(bg_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    mixed = tuple(round(fg[i] * alpha + bg[i] * (1 - alpha)) for i in range(3))
    return "#%02x%02x%02x" % mixed


def per90(total, minutes):
    if total is None or not minutes:
        return None
    return total / minutes * 90


def load_json(name: str) -> dict | list:
    return json.loads((CACHE_DIR / name).read_text())


# ---- 主体对比：8 行指标，沿用第一轮的分组，取消百分位显示，改成
# "联赛刻度条"（下面 compose() 里画），需要池内均值/最大值 ----
def shots_qualifies(row):
    return (row.get("total_shots") or 0) >= 10


METRICS = [
    ("进球", "射门类", lambda r: per90(r["goals"], r["minutes"]), False),
    ("xG", "射门类", lambda r: per90(r["xg"], r["minutes"]), False),
    ("射门", "射门类", lambda r: per90(r["total_shots"], r["minutes"]), False),
    ("xG/射门", "射门类",
     lambda r: (r["xg"] / r["total_shots"]) if shots_qualifies(r) else None, True),
    ("助攻", "创造类", lambda r: per90(r["assists"], r["minutes"]), False),
    ("xA", "创造类", lambda r: per90(r["xa"], r["minutes"]), False),
    ("关键传球", "创造类", lambda r: per90(r["chances_created"], r["minutes"]), False),
    ("成功过人", "带球类", lambda r: per90(r["dribbles_succeeded"], r["minutes"]), False),
]


def build_rows(pool, a_row, b_row):
    rows = []
    for label, group, fn, is_ratio in METRICS:
        a_val, b_val = fn(a_row), fn(b_row)
        if a_val is None or b_val is None:
            continue
        pool_vals = [v for v in (fn(r) for r in pool) if v is not None]
        pool_avg = sum(pool_vals) / len(pool_vals)
        pool_max = max(pool_vals)
        hi = max(a_val, b_val)
        if hi == 0 or abs(a_val - b_val) / hi < TIE_THRESHOLD:
            winner = "tie"
        else:
            winner = "a" if a_val > b_val else "b"
        rows.append(dict(label=label, group=group, a_val=a_val, b_val=b_val,
                          pool_avg=pool_avg, pool_max=pool_max, winner=winner))
    return rows


def summarize(rows, a_name, b_name):
    a_wins = sum(1 for r in rows if r["winner"] == "a")
    b_wins = sum(1 for r in rows if r["winner"] == "b")
    leader, trailer = (a_name, b_name) if a_wins >= b_wins else (b_name, a_name)
    leader_key, trailer_key = ("a", "b") if a_wins >= b_wins else ("b", "a")
    lead_score, trail_score = max(a_wins, b_wins), min(a_wins, b_wins)

    groups = ["射门类", "创造类", "带球类"]
    group_margin, group_trailer_wins = {}, {}
    for g in groups:
        g_rows = [r for r in rows if r["group"] == g]
        lw = sum(1 for r in g_rows if r["winner"] == leader_key)
        tw = sum(1 for r in g_rows if r["winner"] == trailer_key)
        group_margin[g] = lw - tw
        group_trailer_wins[g] = tw

    best_group_for_leader = max(groups, key=lambda g: group_margin[g])
    best_group_for_trailer = max(groups, key=lambda g: group_trailer_wins[g])

    if lead_score - trail_score >= 4:
        sentence = f"{leader} 全面压制 {trailer}"
    elif group_trailer_wins[best_group_for_trailer] > 0:
        sentence = f"{leader}{best_group_for_leader}碾压，{trailer}{best_group_for_trailer}更强"
    else:
        sentence = f"{leader} 全面压制 {trailer}"

    return dict(a_wins=a_wins, b_wins=b_wins, sentence=sentence)


def top_gaps(rows, a_name, b_name, n=2):
    decided = [r for r in rows if r["winner"] != "tie"]
    ranked = sorted(decided, key=lambda r: max(r["a_val"], r["b_val"]) / min(r["a_val"], r["b_val"])
                     if min(r["a_val"], r["b_val"]) > 0 else 0, reverse=True)
    out = []
    for r in ranked[:n]:
        hi_name, lo_name = (a_name, b_name) if r["winner"] == "a" else (b_name, a_name)
        hi_val, lo_val = max(r["a_val"], r["b_val"]), min(r["a_val"], r["b_val"])
        if lo_val <= 0:
            continue
        ratio = hi_val / lo_val
        out.append(dict(text=f"{r['label']}：{hi_name}是{lo_name}的 {ratio:.1f} 倍", winner=r["winner"]))
    return out


# ---- 依赖度：分母同源于 fact_player_match_stats 按 Match_ID+Team_ID 汇总
# （进球分母例外，用 dim_match 真实比分）----
DEPENDENCY_METRICS = [
    ("xG", "xg", "team_xg"),
    ("射门", "total_shots", "team_shots"),
    ("xA", "xa", "team_xa"),
    ("关键传球", "chances_created", "team_chances_created"),
    ("成功过人", "dribbles_succeeded", "team_dribbles"),
]


def compute_dependency(match_rows: list[dict]) -> list[dict]:
    out = []
    goals_sum = sum(m["goals"] for m in match_rows)
    team_goals_sum = sum(m["team_goals"] for m in match_rows)
    out.append(dict(label="进球", pct=round(100 * goals_sum / team_goals_sum, 1) if team_goals_sum else 0.0,
                     num=goals_sum, den=team_goals_sum))
    for label, num_key, den_key in DEPENDENCY_METRICS:
        num_sum = sum(m[num_key] for m in match_rows)
        den_sum = sum(m[den_key] for m in match_rows)
        out.append(dict(label=label, pct=round(100 * num_sum / den_sum, 1) if den_sum else 0.0,
                         num=round(num_sum, 2), den=round(den_sum, 2)))
    return out


def shot_efficiency(match_rows: list[dict]) -> dict:
    total_shots = sum(m["total_shots"] for m in match_rows)
    on_target = sum(m["shots_on_target"] for m in match_rows)
    goals = sum(m["goals"] for m in match_rows)
    xg = sum(m["xg"] for m in match_rows)
    return dict(
        total_shots=total_shots, on_target=on_target, goals=goals, xg=round(xg, 2),
        on_target_pct=round(100 * on_target / total_shots, 1) if total_shots else 0.0,
        conversion_pct=round(100 * goals / total_shots, 1) if total_shots else 0.0,
    )


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


# ---- 球衣剪影：真正的球衣轮廓（圆领+短袖+下摆），不是通用"人形"图标 ----
def _jersey_path(size: float) -> list[tuple[float, float]]:
    w, h = size, size
    return [
        (w * 0.36, h * 0.06), (w * 0.5, h * 0.14), (w * 0.64, h * 0.06),   # 领口 V 字
        (w * 0.78, h * 0.10), (w * 0.98, h * 0.30), (w * 0.86, h * 0.42),  # 右袖
        (w * 0.74, h * 0.32),
        (w * 0.74, h * 0.96), (w * 0.26, h * 0.96),                       # 下摆
        (w * 0.26, h * 0.32),
        (w * 0.14, h * 0.42), (w * 0.02, h * 0.30), (w * 0.22, h * 0.10),  # 左袖
    ]


LIGHT_JERSEY_LUMINANCE_THRESHOLD = 0.85


def draw_silhouette(size: int, jersey_color: str, number: str, outline_color: str,
                     dark_text_color: str) -> tuple[Image.Image, int]:
    """球衣形状剪影（矢量路径直接画，等价于内嵌 SVG 轮廓——本机没有
    cairosvg，直接用 PIL 多边形画同一条路径，效果与解析 SVG 一致，不需要
    额外依赖）。整个造型必须收在 size x size 画布内（含号码）。

    浅色球衣（亮度 > 0.85，比如皇马纯白主场服）在浅色背景上会融进背景——
    加 2px 描边 + 柔和投影；描边色用该队 light_primary。号码颜色按剪影
    亮度自动选深/浅，避免白底白字（2026-08-26 赔率涨跌色事故同一类教训：
    合成对比度要对着实际底色算）。"""
    needs_outline = relative_luminance(jersey_color) > LIGHT_JERSEY_LUMINANCE_THRESHOLD
    num_color = dark_text_color if relative_luminance(jersey_color) > 0.6 else "#ffffff"

    margin = 16 if needs_outline else 0
    work_size = size + margin * 2

    shape = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shape).polygon(_jersey_path(size), fill=jersey_color)

    if needs_outline:
        outline_mask = shape.split()[3].filter(ImageFilter.MaxFilter(5))
        outline_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        outline_layer.paste(Image.new("RGBA", (size, size), outline_color), (0, 0), outline_mask)

        shadow_mask = outline_mask.filter(ImageFilter.GaussianBlur(6))
        shadow_layer = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
        shadow_solid = Image.new("RGBA", (size, size), (0, 0, 0, int(255 * 0.12)))
        shadow_layer.paste(shadow_solid, (margin, margin + 2), shadow_mask)

        canvas = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
        canvas = Image.alpha_composite(canvas, shadow_layer)
        canvas.alpha_composite(outline_layer, (margin, margin))
        canvas.alpha_composite(shape, (margin, margin))
    else:
        canvas = shape

    d = ImageDraw.Draw(canvas)
    f_num = pil_font(int(size * 0.30), "semibold")
    text = str(int(float(number))) if number else "?"
    tw = d.textlength(text, font=f_num)
    d.text((work_size / 2 - tw / 2, margin + size * 0.42), text, font=f_num, fill=num_color)
    return canvas, margin


def draw_ruler_row(draw, x0, x1, y, pool_avg, pool_max, a_val, a_color, b_val, b_color, C):
    """联赛刻度条：单条共享标尺，底轨 = 池内最大值 * 1.15，标联赛平均和
    联赛最高两个刻度，两名球员各一个圆点标记 + 数值。取代第一轮的左右
    对开 butterfly 条（那版每行独立按胜者=100%归一化，看不出两人的值在
    联赛里究竟算高算低；这版所有行共享同一把"联赛真实尺子"）。"""
    track_max = pool_max * 1.15
    track_y = y + 8
    draw.rounded_rectangle([x0, track_y, x1, track_y + 6], radius=3, fill=C["gray"])

    def to_x(v):
        return x0 + (x1 - x0) * min(v / track_max, 1.0)

    avg_x = to_x(pool_avg)
    max_x = to_x(pool_max)
    draw.line([(avg_x, track_y - 5), (avg_x, track_y + 11)], fill=C["ink3"], width=2)
    draw.line([(max_x, track_y - 5), (max_x, track_y + 11)], fill=C["ink3"], width=2)

    f_tick = pil_font(12, "regular")
    draw.text((avg_x, track_y + 15), "联赛平均", font=f_tick, fill=C["ink3"], anchor="ma")
    draw.text((max_x, track_y + 15), "联赛最高", font=f_tick, fill=C["ink3"], anchor="ma")

    r = 8
    ax, bx = to_x(a_val), to_x(b_val)
    draw.ellipse([ax - r, track_y + 3 - r, ax + r, track_y + 3 + r], fill=a_color, outline="#ffffff", width=2)
    draw.ellipse([bx - r, track_y + 3 - r, bx + r, track_y + 3 + r], fill=b_color, outline="#ffffff", width=2)


def compose(theme, subtitle, a, b, a_row, b_row, a_light, b_light, a_jersey_color, b_jersey_color,
            rows, summary, gaps, a_dep, b_dep, a_shot_eff, b_shot_eff,
            crest_a, crest_b, avatar_a, margin_a, avatar_b, margin_b, logo, pool_size,
            out_path: Path) -> None:
    # H 是安全上限，不是目标高度——实际内容多半矮于这个数，最后按真实画到
    # 的位置裁掉多余空白（见函数结尾的 canvas.crop）。开一个明显够用的
    # 上限，而不是精确预估，是为了避免上面那种"画到画布外被静默丢弃"的
    # 情况在未来加新模块时重演。
    W, H = 1080, 2600
    C = theme
    canvas = Image.new("RGB", (W, H), C["bg"])
    draw = ImageDraw.Draw(canvas)
    pad = 40

    f_subtitle = pil_font(19, "regular")
    f_playername = pil_font(28, "semibold")
    f_meta = pil_font(18, "regular")
    f_score = pil_font(78, "semibold")
    f_sentence = pil_font(24, "semibold")

    sub_w = draw.textlength(subtitle, font=f_subtitle)
    draw.text((W / 2 - sub_w / 2, pad), subtitle, font=f_subtitle, fill=C["ink2"])

    avatar_size = 150
    ay = pad + 36
    canvas.paste(avatar_a, (pad - margin_a, ay - margin_a), avatar_a)
    canvas.paste(avatar_b, (W - pad - avatar_size - margin_b, ay - margin_b), avatar_b)

    cw = crest_a.copy(); cw.thumbnail((36, 36))
    canvas.paste(cw, (pad, ay + avatar_size + 6), cw)
    draw.text((pad + 44, ay + avatar_size + 6), a["name_zh"], font=f_playername, fill=C["ink"])
    draw.text((pad, ay + avatar_size + 42), f"{a_row['matches']} 场 · {a_row['minutes']} 分钟",
               font=f_meta, fill=C["ink3"])

    cw2 = crest_b.copy(); cw2.thumbnail((36, 36))
    bname_w = draw.textlength(b["name_zh"], font=f_playername)
    bx_right = W - pad
    canvas.paste(cw2, (bx_right - 36, ay + avatar_size + 6), cw2)
    draw.text((bx_right - 44 - bname_w, ay + avatar_size + 6), b["name_zh"], font=f_playername, fill=C["ink"])
    meta_b = f"{b_row['matches']} 场 · {b_row['minutes']} 分钟"
    draw.text((bx_right - draw.textlength(meta_b, font=f_meta), ay + avatar_size + 42),
               meta_b, font=f_meta, fill=C["ink3"])

    score_text = f"{summary['a_wins']} : {summary['b_wins']}"
    sw = draw.textlength(score_text, font=f_score)
    score_y = ay + avatar_size // 2 - 40
    draw.text((W / 2 - sw / 2, score_y), score_text, font=f_score, fill=C["ink"])

    sentence_y = ay + avatar_size + 78
    sw2 = draw.textlength(summary["sentence"], font=f_sentence)
    draw.text((W / 2 - sw2 / 2, sentence_y), summary["sentence"], font=f_sentence, fill=C["gold_deep"])

    y = sentence_y + 42
    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 20

    # ---- 依赖度模块 ----
    f_mod_title = pil_font(19, "semibold")
    f_mod_note = pil_font(14, "regular")
    f_dep_label = pil_font(17, "semibold")
    f_dep_val = pil_font(16, "semibold")

    draw.text((pad, y), "依赖度", font=f_mod_title, fill=C["ink"])
    y += 24
    draw.text((pad, y), "按出场比赛全场统计", font=f_mod_note, fill=C["ink3"])
    y += 24

    dep_row_h = 34
    dep_track_x0, dep_track_x1 = pad + 90, W - pad - 90
    for dep_a, dep_b in zip(a_dep, b_dep):
        draw.text((pad, y + 3), dep_a["label"], font=f_dep_label, fill=C["ink"])
        mid = (dep_track_x0 + dep_track_x1) / 2
        # 两条从中线向两端延伸的条，各自长度 = min(占比,100%) * 半轨
        half = (dep_track_x1 - dep_track_x0) / 2 - 4
        a_w = half * min(dep_a["pct"], 100) / 100
        b_w = half * min(dep_b["pct"], 100) / 100
        draw.rounded_rectangle([mid - a_w, y + 4, mid, y + 4 + 14], radius=4, fill=a_light)
        draw.rounded_rectangle([mid, y + 4, mid + b_w, y + 4 + 14], radius=4, fill=b_light)
        a_txt = f"{dep_a['pct']:.0f}%"
        b_txt = f"{dep_b['pct']:.0f}%"
        draw.text((mid - a_w - 8, y + 1), a_txt, font=f_dep_val, fill=C["ink"], anchor="ra")
        draw.text((mid + b_w + 8, y + 1), b_txt, font=f_dep_val, fill=C["ink"])
        y += dep_row_h

    y += 10
    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 20

    # ---- 主体对比：联赛刻度条 ----
    mid_x = W / 2
    row_h = 56
    f_label = pil_font(19, "semibold")
    f_val = pil_font(20, "semibold")
    f_group = pil_font(17, "semibold")

    groups_order = ["射门类", "创造类", "带球类"]
    for g in groups_order:
        g_rows = [r for r in rows if r["group"] == g]
        if not g_rows:
            continue
        draw.text((pad, y), g, font=f_group, fill=C["ink3"])
        y += 26
        for r in g_rows:
            label_w = draw.textlength(r["label"], font=f_label)
            draw.text((pad, y), r["label"], font=f_label, fill=C["ink"])

            a_color = a_light if r["winner"] == "a" else blend_over(a_light, C["bg"], 0.45)
            b_color = b_light if r["winner"] == "b" else blend_over(b_light, C["bg"], 0.45)

            track_x0 = pad + 140
            track_x1 = W - pad - 70
            draw_ruler_row(draw, track_x0, track_x1, y, r["pool_avg"], r["pool_max"],
                            r["a_val"], a_color, r["b_val"], b_color, C)

            a_txt = f"{r['a_val']:.2f}"
            b_txt = f"{r['b_val']:.2f}"
            draw.text((W - pad, y), f"{a_txt} / {b_txt}", font=f_val, fill=C["ink"], anchor="ra")

            y += row_h
        y += 10

    y += 4
    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 20

    # ---- 效率模块：进球 - xG ----
    draw.text((pad, y), "效率", font=f_mod_title, fill=C["ink"])
    y += 24
    draw.text((pad, y), "样本 7 场，效率类指标波动大，仅供参考", font=f_mod_note, fill=C["ink3"])
    y += 26

    f_eff_delta = pil_font(30, "semibold")
    f_eff_raw = pil_font(15, "regular")
    for name, color, eff in [(a["name_zh"], a_light, a_shot_eff), (b["name_zh"], b_light, b_shot_eff)]:
        delta = eff["goals"] - eff["xg"]
        sign = "+" if delta >= 0 else ""
        col_x = pad if name == a["name_zh"] else W / 2 + 10
        draw.text((col_x, y), name, font=f_dep_label, fill=C["ink"])
        draw.text((col_x, y + 24), f"{sign}{delta:.1f}", font=f_eff_delta,
                   fill=(color if delta >= 0 else C["loss"]))
        draw.text((col_x, y + 62), f"{eff['goals']:.0f} 球 / {eff['xg']:.1f} xG", font=f_eff_raw, fill=C["ink3"])
    y += 96

    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 20

    # ---- 射门效率：射正率 / 转化率（分母=总射门，含被封堵）----
    draw.text((pad, y), "射门效率", font=f_mod_title, fill=C["ink"])
    y += 24
    draw.text((pad, y), "分母为总射门（射正 + 射偏 + 被封堵）", font=f_mod_note, fill=C["ink3"])
    y += 26

    f_shot_label = pil_font(16, "regular")
    f_shot_val = pil_font(22, "semibold")
    col_w = (W - pad * 2) / 2
    for i, (name, color, eff) in enumerate([(a["name_zh"], a_light, a_shot_eff), (b["name_zh"], b_light, b_shot_eff)]):
        cx = pad + i * col_w
        draw.text((cx, y), f"{name}：总射门 {eff['total_shots']:.0f} 次（射正 {eff['on_target']:.0f}）",
                   font=f_shot_label, fill=C["ink3"])
        draw.text((cx, y + 22), f"射正率 {eff['on_target_pct']:.1f}%", font=f_shot_val, fill=color)
        draw.text((cx, y + 50), f"转化率 {eff['conversion_pct']:.1f}%", font=f_shot_val, fill=color)
    y += 96

    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 20

    # ---- 最大差距 callout ----
    f_gap_title = pil_font(18, "semibold")
    f_gap_body = pil_font(25, "semibold")
    card_pad = 18
    line_h = 36
    card_h = card_pad * 2 + 26 + len(gaps) * line_h
    draw.rounded_rectangle([pad, y, W - pad, y + card_h], radius=14, fill=C["card"],
                            outline=C["border"], width=1)
    ty = y + card_pad
    draw.text((pad + card_pad, ty), "最大差距", font=f_gap_title, fill=C["ink3"])
    ty += 32
    for gp in gaps:
        gap_color = a_light if gp["winner"] == "a" else b_light if gp["winner"] == "b" else C["loss"]
        draw.text((pad + card_pad, ty), gp["text"], font=f_gap_body, fill=gap_color)
        ty += line_h
    y += card_h + 22

    draw.line([(pad, y), (W - pad, y)], fill=C["border"], width=1)
    y += 20

    # ---- 底部：样本说明 + 品牌署名 ----
    f_note = pil_font(16, "regular")
    note = (f"数据来源：西甲 2026/2027 赛季至今 · 基准池：西甲前场球员（前锋+边锋）、"
            f"出场≥450分钟，样本 {pool_size} 人")
    for line in wrap_text(draw, note, f_note, W - pad * 2 - 210):
        draw.text((pad, y), line, font=f_note, fill=C["ink3"])
        y += 23

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
    # 品牌角标紧跟在样本说明文字后面顺流而下，不是锚定在画布固定高度上——
    # 早期版本这里用 `H - pad - logo_badge_h`，H 是函数开头就定死的常量，
    # 而这版内容（依赖度/刻度条/效率/射门效率/差距卡片五个新模块）比当初
    # 估的高度多得多，固定锚点算出来的 badge_y 比实际内容流到的 y 还要
    # 靠上，角标和"样本说明"文字互相压在一起（更糟的是，样本说明那行
    # 文字画的时候 y 已经超出了当初创建画布时给的高度，直接被裁没了——
    # PIL 画到画布边界外的内容不会报错，只是安静地丢掉）。
    badge_y = y + 20
    text_x = badge_x - 12 - tw
    text_y = badge_y + (logo_badge_h - 28) // 2
    draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + logo_badge_h], radius=14,
                            fill=C["card"], outline=C["border"], width=1)
    canvas.paste(logo_fit, (badge_x + logo_pad, badge_y + logo_pad), logo_fit)
    draw.text((text_x, text_y), brand_text, font=f_brand, fill=C["gold_deep"])

    used_h = badge_y + logo_badge_h + pad
    canvas = canvas.crop((0, 0, W, used_h))
    canvas.save(out_path)
    print(f"canvas height used: {used_h}px（初始画布 H={H}px 生成时留了足够余量，用完再裁到实际高度）")


SEASON = "2026/2027"
ROUND = 7
DEFAULT_SUBTITLE = f"两位金球候选本赛季表现 · {SEASON} 第 {ROUND} 轮后"

LIGHT_PRIMARY_OVERRIDES = {
    "8633": "#C8961E",
    "8634": "#A50044",
}


def load_team_colors_with_light_primary(bg_hex: str) -> tuple[dict, list[str]]:
    colors = json.loads((CACHE_DIR / "team_colors.json").read_text())
    warnings = []
    for team_id, info in colors.items():
        light_primary = LIGHT_PRIMARY_OVERRIDES.get(team_id, info.get("light"))
        info["light_primary"] = light_primary
        if light_primary:
            ratio = contrast_ratio(light_primary, bg_hex)
            if ratio < 3.0:
                warnings.append(f"{info.get('name_zh', team_id)}（{light_primary}，对比度 {ratio:.2f}:1）")
    return colors, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subtitle", default=DEFAULT_SUBTITLE)
    args = ap.parse_args()

    pool = load_json("pool.json")
    match_level = load_json("matchlevel.json")
    colors, contrast_warnings = load_team_colors_with_light_primary(LIGHT["bg"])

    print(f"对比度检查（light_primary vs 背景 {LIGHT['bg']}，全部 {len(colors)} 支球队，阈值 3:1）：")
    if contrast_warnings:
        print(f"  {len(contrast_warnings)} 支不达标：")
        for w in contrast_warnings:
            print(f"    WARNING: {w}")
    else:
        print("  全部达标")

    a_row = next(r for r in pool if r["player_name"] == "Kylian Mbappé")
    b_row = next(r for r in pool if r["player_name"] == "Lamine Yamal")
    a = dict(name_zh="姆巴佩", team_id=a_row["team_id"])
    b = dict(name_zh="亚马尔", team_id=b_row["team_id"])

    a_matches = [m for m in match_level if m["player_name"] == "Kylian Mbappé"]
    b_matches = [m for m in match_level if m["player_name"] == "Lamine Yamal"]

    rows = build_rows(pool, a_row, b_row)
    print(f"{len(rows)} 行指标进入对比（8 项里跳过了 {8-len(rows)} 项）")
    for r in rows:
        print(f"  {r['group']:5s} {r['label']:8s} a={r['a_val']:.2f} b={r['b_val']:.2f} "
              f"池均值={r['pool_avg']:.2f} 池最高={r['pool_max']:.2f} winner={r['winner']}")

    summary = summarize(rows, a["name_zh"], b["name_zh"])
    print("summary:", summary)
    gaps = top_gaps(rows, a["name_zh"], b["name_zh"])
    print("gaps:", gaps)

    a_dep = compute_dependency(a_matches)
    b_dep = compute_dependency(b_matches)
    print("姆巴佩依赖度:", a_dep)
    print("亚马尔依赖度:", b_dep)

    a_shot_eff = shot_efficiency(a_matches)
    b_shot_eff = shot_efficiency(b_matches)
    print("姆巴佩射门效率:", a_shot_eff)
    print("亚马尔射门效率:", b_shot_eff)

    logo = Image.open(CACHE_DIR / "logo-mark.png").convert("RGBA")
    crest_a = Image.open(CACHE_DIR / "real_madrid.png").convert("RGBA")
    crest_b = Image.open(CACHE_DIR / "barcelona.png").convert("RGBA")

    a_info = colors[str(a_row["team_id"])]
    b_info = colors[str(b_row["team_id"])]
    a_light_primary, a_jersey = a_info["light_primary"], a_info["dark"]
    b_light_primary, b_jersey = b_info["light_primary"], b_info["dark"]
    print("team colors:", a["name_zh"], "light_primary=", a_light_primary, "jersey=", a_jersey,
          "|", b["name_zh"], "light_primary=", b_light_primary, "jersey=", b_jersey)

    avatar_a, margin_a = draw_silhouette(150, a_jersey, a_row["shirt_number"], a_light_primary, "#14202B")
    avatar_b, margin_b = draw_silhouette(150, b_jersey, b_row["shirt_number"], b_light_primary, "#14202B")

    out_path = HERE / "mbappe_vs_yamal_h2h_v2_light.png"
    compose(LIGHT, args.subtitle, a, b, a_row, b_row, a_light_primary, b_light_primary,
            a_jersey, b_jersey, rows, summary, gaps, a_dep, b_dep, a_shot_eff, b_shot_eff,
            crest_a, crest_b, avatar_a, margin_a, avatar_b, margin_b, logo, len(pool), out_path)
    print(f"WROTE {out_path}")


if __name__ == "__main__":
    main()
