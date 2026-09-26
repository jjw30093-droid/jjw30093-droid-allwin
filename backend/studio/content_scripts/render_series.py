"""render_series：把 attack_defense_series.py 已经核对过的数据（不重新查询）
渲染成 4 张 HTML/CSS 模板，再用 shoot.cjs（Playwright，复用 frontend 现有的
node_modules，不额外装 python playwright）截图成 PNG。

字体用本地打包的 Noto Sans SC 可变字体文件（fonts/NotoSansSC-Variable.ttf，
从 Google Fonts 官方仓库 google/fonts 下载，OFL 协议），通过 @font-face 在
HTML 里显式引用，不依赖本机系统字体安装状态——这样换机器跑效果也一致，
不会静默回退到系统默认字体。fonts/ 不进 git（见 .gitignore），换机器跑前
先手动下载一次：

    mkdir -p backend/studio/content_scripts/fonts
    curl -sL "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf" \
      -o backend/studio/content_scripts/fonts/NotoSansSC-Variable.ttf

截图用 frontend/node_modules/@playwright/test 里已经装好的 chromium（见
shoot.cjs），不额外 pip install playwright 装第二份浏览器二进制。

头像：基准池 20 人的头像用生产前端同一套来源（FotMob 图片 CDN，
frontend/components/players/playerAvatarUrl.ts 的同一条 URL 规则），这次
按站长要求一次性下载缓存到 _cache/avatars/{Player_ID}.png（不进 git，见
.gitignore），不是每次渲染都发起热链请求。

队徽：直接复用仓库里已有的本地队徽缓存 runtime/media/team-crests/fotmob/
{Team_ID}.png（后端 /api/v1/media/team-crests 接口本来就服务这批文件，
backend/api/routes_media.py），拷贝进 _cache/badges/，没有重新下载。
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from PIL import Image

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "_cache"
TEMPLATES_DIR = HERE / "templates"
OUT_DIR = HERE / "_out"
OUT_DIR.mkdir(exist_ok=True)

SCALE = 2

# ---- 平台画布 + 抖音安全区（像素，均为最终 1x 尺寸，不是 2x 截图尺寸）----
# 抖音遮挡区三块：顶部导航/关注栏、底部文案+@账号名、右侧点赞/评论/分享按钮列。
# 三块只允许背景色/装饰，不放任何信息；安全区是排除这三块之后剩下的
# x 64–880、y 240–1440（这里 x0/x1 特意跟内容区左右内边距 64px 对齐，不是
# 巧合——安全区宽度 816px 就是标题自适应字号那批计算要用的基准宽度）。
PLATFORMS = {
    "xhs": dict(w=1080, h=1440, safezone=None),
    "douyin": dict(
        w=1080, h=1920,
        safezone=dict(
            top=(0, 0, 1080, 240),          # 顶部遮挡：x0,y0,x1,y1
            bottom=(0, 1440, 1080, 1920),   # 底部遮挡
            right=(900, 880, 1080, 1920),   # 右侧按钮列
            content=(64, 240, 880, 1440),   # 安全内容区
        ),
    ),
}

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

PLAYER_IDS = {
    "Álvaro Garcia": "474658", "Lucas Boyé": "561164", "Iñigo Vicente": "889229",
    "Fer Niño": "1127224", "Ante Budimir": "251269", "Miguel Sierra": "1797904",
    "Antony": "967622", "Martín Satriano": "1197039", "Pierre-Emerick Aubameyang": "150565",
    "Chupe": "1669622", "Enes Ünal": "464486", "Roberto Fernández": "1252373",
    "Javi Hernández": "1427828", "Sergio Camello": "980422", "Mikel Oyarzabal": "678234",
    "Lamine Yamal": "1467236", "Raphinha": "696679", "Vinícius Júnior": "846033",
    "Arnaut Danjuma": "704151", "Kylian Mbappé": "701154",
}


def load_json(name: str):
    return json.loads((CACHE_DIR / name).read_text())


def load_names():
    data = json.loads((HERE / "player_names_zh.json").read_text())
    return data["names"], data["short_names"], data["unverified_sources"], data.get("verification_notes", {})


def avatar_rel(player_name: str) -> str | None:
    # 绝对路径而不是 "../_cache/..." 相对路径：抖音版输出到 _out/douyin/
    # 二级子目录，跟小红书版 _out/ 的相对深度不一样，相对路径在两边会解析到
    # 不同的地方；用绝对文件路径两边都对，不用为每个输出目录分别算"../"层数。
    pid = PLAYER_IDS[player_name]
    p = CACHE_DIR / "avatars" / f"{pid}.png"
    return str(p) if p.exists() else None


def badge_rel(team_id) -> str | None:
    p = CACHE_DIR / "badges" / f"{team_id}.png"
    return str(p) if p.exists() else None


def text_px_width(s: str, font_size: int = 22) -> int:
    # 粗略估算 Noto Sans SC 文字像素宽度（CJK/全角标点 ≈ 1em，ASCII/数字 ≈
    # 0.58em）。用途：①给标注文字背后垫一块不透明背景，不需要精确到像素；
    # ②P1 标题自适应字号，判断整行是否超出内容区宽度——同一个估算函数两处
    # 复用，不重复定义两份可能悄悄跑偏的估算逻辑。
    w = 0.0
    for ch in s:
        w += font_size if ord(ch) > 0x2E80 else font_size * 0.58
    return int(w)


def shoot(html_path: Path, out_png_2x: Path, out_png_final: Path, w: int, h: int):
    subprocess.run(["node", str(HERE / "shoot.cjs"), str(html_path), str(out_png_2x), str(w), str(h)], check=True)
    im = Image.open(out_png_2x)
    assert im.size == (w * SCALE, h * SCALE), f"unexpected screenshot size: {im.size}"
    im_final = im.resize((w, h), Image.LANCZOS)
    im_final.save(out_png_final)
    assert im_final.size == (w, h)
    print(f"  {out_png_final.name}: {im.size} -> {im_final.size}")


def render_page(template_name: str, ctx: dict, page_stub: str, out_dir: Path) -> Path:
    tpl = env.get_template(template_name)
    html = tpl.render(**ctx)
    out_dir.mkdir(parents=True, exist_ok=True)
    font_abs = str(HERE / "fonts" / "NotoSansSC-Variable.ttf")
    css = (TEMPLATES_DIR / "base.css").read_text().replace("__FONT_ABS_PATH__", font_abs)
    (out_dir / "base.css").write_text(css, encoding="utf-8")
    html_path = out_dir / f"{page_stub}.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["xhs", "douyin"], default="xhs")
    ap.add_argument("--debug-safezone", action="store_true", dest="debug_safezone")
    args = ap.parse_args()

    platform = args.platform
    plat_cfg = PLATFORMS[platform]
    CANVAS_W, CANVAS_H = plat_cfg["w"], plat_cfg["h"]
    SAFEZONE = plat_cfg["safezone"]
    DEBUG_SAFEZONE = args.debug_safezone and platform == "douyin"
    plat_out_dir = OUT_DIR if platform == "xhs" else OUT_DIR / "douyin"
    print(f"=== 平台: {platform}  画布: {CANVAS_W}x{CANVAS_H}  "
          f"debug_safezone: {DEBUG_SAFEZONE}  输出目录: {plat_out_dir} ===\n")

    names, short_names, unverified, verify_notes = load_names()
    print("=== player_names_zh.json（20 人 full/short 中文名，供核对）===")
    for i, en in enumerate(names, 1):
        flag = "  ← 未经项目 i18n 校验，本次音译" if en in unverified else ""
        print(f"  {i:>2}. {en:<28} full={names[en]:<14} short={short_names[en]:<8}{flag}")
    print()
    if verify_notes:
        print("=== 专项核对 ===")
        for k, v in verify_notes.items():
            print(f"  {k}: {v}")
        print()

    pool = load_json("pool20.json")
    for r in pool:
        r["avatar"] = avatar_rel(r["player_name"])
        r["badge"] = badge_rel(r["Team_ID"])

    missing_avatars = [r["player_name"] for r in pool if not r["avatar"]]
    print("=== 头像缺失名单 ===")
    print("  无缺失（20/20 全部下载成功）" if not missing_avatars else f"  {missing_avatars}")
    print()

    # ---- 头像源文件像素尺寸清单（显示尺寸不得超过源图 1.5 倍的判据依据） ----
    print("=== 头像源文件像素尺寸清单 ===")
    avatar_sizes = {}
    for r in pool:
        pid = PLAYER_IDS[r["player_name"]]
        with Image.open(CACHE_DIR / "avatars" / f"{pid}.png") as im:
            avatar_sizes[r["player_name"]] = im.size
    for name, size in avatar_sizes.items():
        print(f"  {name:<28} {size}  (1.5x 上限 = {int(size[0]*1.5)}px)")
    AVATAR_SRC = 192
    AVATAR_MAX_DISPLAY = int(AVATAR_SRC * 1.5)
    print(f"  全部头像源均为 {AVATAR_SRC}x{AVATAR_SRC}px，本次所有显示尺寸上限统一按 {AVATAR_MAX_DISPLAY}px 控制")
    print()

    mbappe_matches = load_json("mbappe_matches.json")
    yamal_summary = load_json("yamal_summary.json")
    regression = load_json("regression.json")
    logo_path_rel = str(CACHE_DIR / "logo-mark.png")

    # ---- 口径行用的"截至日期"：西甲+欧冠(8场)和纯西甲(7场)恰好是同一场
    # (09-20 马竞)封顶，两个范围的最后一场日期相同，不是巧合合并写错 ----
    all_comp_last_date = max(m["date"] for m in mbappe_matches)
    laliga_last_date = max(m["date"] for m in mbappe_matches if m["league"] == "西甲")
    def fmt_date(iso: str) -> str:
        y, mo, d = iso.split("-")
        return f"{int(mo)}月{int(d)}日"
    caveat_all_comp = f"数据：西甲 + 欧冠，截至 {fmt_date(all_comp_last_date)}"
    caveat_laliga = f"数据：西甲，截至 {fmt_date(laliga_last_date)}"

    # ---- 皇马西甲控球率在全部20队中的排名：只打印给站长看，不写进图里 ----
    POSSESSION_ALL20 = {
        "Barcelona": 68.6, "Villarreal": 61.7, "Atletico Madrid": 57.3, "Real Betis": 54.0,
        "Celta Vigo": 52.6, "Athletic Club": 52.5, "Real Madrid": 52.4, "Malaga": 50.9,
        "Elche": 49.7, "Espanyol": 49.6, "Real Sociedad": 49.1, "Rayo Vallecano": 48.7,
        "Racing Santander": 48.0, "Valencia": 47.6, "Deportivo A Coruña": 46.6, "Osasuna": 45.6,
        "Deportivo Alaves": 42.1, "Sevilla": 41.9, "Getafe": 40.1, "Levante": 40.0,
    }
    possession_ranked = sorted(POSSESSION_ALL20.items(), key=lambda kv: -kv[1])
    rm_possession_rank = next(i for i, (name, _) in enumerate(possession_ranked, 1) if name == "Real Madrid")
    print("=== 皇马西甲控球率在全联赛20队中的排名（只打印，不进图）===")
    for i, (name, v) in enumerate(possession_ranked, 1):
        marker = "  <== 皇马" if name == "Real Madrid" else ""
        print(f"  {i:>2}. {name:<20} {v}%{marker}")
    print(f"  皇马排名：第 {rm_possession_rank} / 20")
    print()

    COLUMN_NAME = "喵弟球员体检"
    ISSUE_NO = "NO.01"
    common_ctx = dict(
        logo_path=logo_path_rel, column_name=COLUMN_NAME, issue_no=ISSUE_NO,
        platform=platform, canvas_w=CANVAS_W, canvas_h=CANVAS_H,
        safezone=SAFEZONE, debug_safezone=DEBUG_SAFEZONE,
    )

    mbappe_pool = next(r for r in pool if r["player_name"] == "Kylian Mbappé")
    vini_pool = next(r for r in pool if r["player_name"] == "Vinícius Júnior")

    total_goals = sum(m["goals"] for m in mbappe_matches)
    total_bcm = sum(m["bcm"] for m in mbappe_matches)
    total_pen_missed = sum(1 for m in mbappe_matches if m["pen_missed"])

    laliga_rows = [m for m in json.loads((CACHE_DIR / "_tmp_defense_fields.json").read_text())
                   if m["player_name"] == "Kylian Mbappé"]
    opp_by_match = {
        5868018: "皇家社会", 5868025: "西班牙人", 5868038: "马拉加", 5868043: "皇家贝蒂斯",
        5868057: "巴列卡诺", 5868065: "埃尔切", 5868072: "马德里竞技",
    }
    weekly = [dict(opp=opp_by_match[r["Match_ID"]], defact=int(r["defensive_actions"]))
              for r in sorted(laliga_rows, key=lambda x: x["Match_ID"])]
    zero_count = sum(1 for w in weekly if w["defact"] == 0)

    ranked = sorted(pool, key=lambda r: -r["defensive_actions_p90"])
    mbappe_rank = next(i for i, r in enumerate(ranked, 1) if r["player_name"] == "Kylian Mbappé")

    same_pos_names = ["Fer Niño", "Ante Budimir", "Chupe", "Roberto Fernández",
                       "Sergio Camello", "Raphinha", "Kylian Mbappé"]
    same_pos_group_raw = sorted([r for r in pool if r["player_name"] in same_pos_names],
                                 key=lambda r: -r["defensive_actions_p90"])
    same_pos_rank = next(i for i, r in enumerate(same_pos_group_raw, 1)
                          if r["player_name"] == "Kylian Mbappé")
    max_same_pos = max(r["defensive_actions_p90"] for r in same_pos_group_raw) or 1

    # ---- P2 可选数据：西甲 7 场口径，"每90分钟大机会错失" 在 20 人池的排名 ----
    # 数据来自production的一次专项查询（big_chance_missed_title 按 Match_ID+
    # Season+League_ID=87 汇总，20 人全覆盖），结果内嵌在这里，不在渲染时
    # 重新查库——跟 pool20.json 等既有缓存同一个"数据只查一次"的原则。
    BCM_P90_LALIGA = {
        "Kylian Mbappé": (9.0, 630), "Raphinha": (6.0, 555), "Vinícius Júnior": (5.0, 565),
        "Lamine Yamal": (4.0, 581), "Mikel Oyarzabal": (3.0, 478), "Ante Budimir": (3.0, 502),
        "Antony": (3.0, 522), "Pierre-Emerick Aubameyang": (3.0, 560), "Roberto Fernández": (3.0, 613),
        "Fer Niño": (2.0, 470), "Sergio Camello": (2.0, 470), "Arnaut Danjuma": (2.0, 526),
        "Chupe": (2.0, 563), "Martín Satriano": (1.0, 544), "Enes Ünal": (0, 540),
        "Iñigo Vicente": (0, 543), "Javi Hernández": (0, 561), "Lucas Boyé": (0, 484),
        "Miguel Sierra": (0, 496), "Álvaro Garcia": (0, 530),
    }
    bcm_p90_ranked = sorted(BCM_P90_LALIGA.items(), key=lambda kv: -(kv[1][0] * 90 / kv[1][1]))
    mbappe_bcm_p90_rank = next(i for i, (name, _) in enumerate(bcm_p90_ranked, 1) if name == "Kylian Mbappé")
    mbappe_bcm_p90 = BCM_P90_LALIGA["Kylian Mbappé"][0] * 90 / BCM_P90_LALIGA["Kylian Mbappé"][1]
    print("=== P2 可选数据：每90分钟大机会错失，西甲前场20人池排名 ===")
    for i, (name, (bcm, mins)) in enumerate(bcm_p90_ranked, 1):
        marker = "  <== 姆巴佩" if name == "Kylian Mbappé" else ""
        print(f"  {i:>2}. {name:<28} {bcm*90/mins:.3f}{marker}")
    print(f"  姆巴佩排名：第 {mbappe_bcm_p90_rank} / {len(bcm_p90_ranked)}（{'西甲前场最多' if mbappe_bcm_p90_rank == 1 else '不是第一'}）")
    print()

    # =========================== P1（体检报告单）===========================
    # 点球"罚2丢1"：09-12 主场巴列卡诺那场罚进(shotmapEvent.situation="Penalty"，
    # goalDescriptionKey="penalty")，09-04 客场贝蒂斯那场罚丢(missed_penalty=1)，
    # 这两个真实事件在"第一步数据确认"阶段已经逐场核对过，这里直接引用，不
    # 重新查库。
    total_pen_scored = 1
    total_pen_attempts = total_pen_scored + total_pen_missed

    CONTENT_W = 952  # 1080 - 64*2，标题自适应字号要用的内容区实际宽度

    def fit_font(text: str, max_size: int, max_width: int = CONTENT_W) -> int:
        w = text_px_width(text, max_size)
        if w <= max_width:
            return max_size
        return int(max_size * max_width / w)

    title1, title1_max = "金球奖？", 150
    title2, title2_max = "姆巴配吗？", 170
    title1_fs = fit_font(title1, title1_max)
    title2_fs = fit_font(title2, title2_max)
    # 抖音安全区宽度 816px(64-880)，比小红书内容区(952px)窄，标题自适应字号
    # 要单独按这个更窄的宽度算一遍，不能直接复用小红书那版字号。
    DY_CONTENT_W = 816
    title1_fs_dy = fit_font(title1, title1_max, DY_CONTENT_W)
    title2_fs_dy = fit_font(title2, title2_max, DY_CONTENT_W)
    print("=== P1 标题自适应字号 ===")
    print(f"  [小红书 内容区{CONTENT_W}px] {title1!r}: 估算宽度@{title1_max}px = "
          f"{text_px_width(title1, title1_max)}px → 采用 {title1_fs}px")
    print(f"  [小红书 内容区{CONTENT_W}px] {title2!r}: 估算宽度@{title2_max}px = "
          f"{text_px_width(title2, title2_max)}px → 采用 {title2_fs}px")
    print(f"  [抖音 安全区{DY_CONTENT_W}px] {title1!r}: 估算宽度@{title1_max}px = "
          f"{text_px_width(title1, title1_max)}px → 采用 {title1_fs_dy}px")
    print(f"  [抖音 安全区{DY_CONTENT_W}px] {title2!r}: 估算宽度@{title2_max}px = "
          f"{text_px_width(title2, title2_max)}px → 采用 {title2_fs_dy}px")
    print()

    report_rows = [
        dict(item="进球", result=f"{total_goals} 个", verdict="正常", arrow="", normal=True, sub=None),
        dict(item="大机会没进", result=f"{total_bcm} 次", verdict="异常", arrow="↑", normal=False, sub=None),
        dict(item="点球", result=f"罚 {total_pen_attempts} 丢 {total_pen_missed}", verdict="异常", arrow="", normal=False, sub=None),
        dict(item="前场防守", result="倒数第 1", verdict="异常", arrow="↓", normal=False, sub="西甲前场 20 人"),
    ]

    p1_ctx = dict(
        **common_ctx,
        page_no="1 / 5",
        title1=title1, title1_fs=title1_fs, title1_fs_dy=title1_fs_dy,
        title2=title2, title2_fs=title2_fs, title2_fs_dy=title2_fs_dy,
        subtitle_lines=["8 场 8 球，面板很好看。", "拆开看，就不是那么回事了。"],
        avatar_path=mbappe_pool["avatar"],
        badge_path=mbappe_pool["badge"],
        scope_text="体检范围：西甲 + 欧冠 8 场",
        report_rows=report_rows,
        footer_note=caveat_all_comp,
    )

    # =========================== P2 ===========================
    matches_ctx = []
    for m in mbappe_matches:
        highlight = m["bcm"] >= 4
        matches_ctx.append(dict(
            bcm=m["bcm"], opp=m["opp"], league=m["league"],
            side_zh="主" if m["side"] == "home" else "客",
            highlight=highlight,
        ))
    avg_bcm = round(total_bcm / len(mbappe_matches), 1)
    yamal_avg_bcm = round(yamal_summary["bcm_total"] / yamal_summary["matches"], 1)

    top5_names = [name for name, _ in bcm_p90_ranked[:5]]
    print("=== P2 TOP5 模块数据（每90分钟大机会错失，西甲口径）===")
    top5_ctx = []
    for name in top5_names:
        bcm, mins = BCM_P90_LALIGA[name]
        p90 = round(bcm * 90 / mins, 2)
        row = next(r for r in pool if r["player_name"] == name)
        print(f"  {name:<28} {p90}")
        top5_ctx.append(dict(name_zh=names[name], val=p90, avatar=row["avatar"],
                              is_mbappe=name == "Kylian Mbappé"))
    print()

    p2_ctx = dict(
        **common_ctx,
        page_no="2 / 5",
        avg_bcm=avg_bcm,
        bcm_total=total_bcm,
        n_matches=len(mbappe_matches),
        yamal_avg_bcm=yamal_avg_bcm,
        is_top1=mbappe_bcm_p90_rank == 1,
        matches=matches_ctx,
        top5=top5_ctx,
        footer_note=caveat_all_comp,
    )

    # =========================== P3 ===========================
    bold_en = {"Vinícius Júnior", "Lamine Yamal", "Raphinha"}
    ranked_top19 = [r for r in ranked if r["player_name"] != "Kylian Mbappé"]
    max_val_p3 = max(r["defensive_actions_p90"] for r in ranked) or 1
    ranked_ctx = []
    for i, r in enumerate(ranked_top19, 1):
        ranked_ctx.append(dict(
            rank=i,
            name_zh=short_names[r["player_name"]],
            bold=r["player_name"] in bold_en,
            val=r["defensive_actions_p90"],
            pct=round(100 * r["defensive_actions_p90"] / max_val_p3, 1),
        ))
    # 抖音版压缩：第1-3名 + "..."(省略中间12人) + 第16-19名(含亚马尔/拉菲尼亚/
    # 维尼修斯)，3+4=7 行显示 + 12 行省略 = 19，跟 ranked_top19 总数对得上。
    ranked_dy_top = ranked_ctx[0:3]
    ranked_dy_bottom = ranked_ctx[15:19]
    assert len(ranked_dy_top) == 3 and len(ranked_dy_bottom) == 4
    dy_omitted_count = len(ranked_ctx) - len(ranked_dy_top) - len(ranked_dy_bottom)

    p3_ctx = dict(
        **common_ctx,
        page_no="3 / 5",
        ranked=ranked_ctx,
        ranked_dy_top=ranked_dy_top, ranked_dy_bottom=ranked_dy_bottom, dy_omitted_count=dy_omitted_count,
        mbappe=dict(
            rank=mbappe_rank, avatar=mbappe_pool["avatar"], badge=mbappe_pool["badge"],
            val=mbappe_pool["defensive_actions_p90"],
            pct=round(100 * mbappe_pool["defensive_actions_p90"] / max_val_p3, 1),
        ),
        n_matches=len(weekly), zero_count=zero_count,
        weekly=weekly,
        footer_note=caveat_laliga,
    )

    # =========================== P4 ===========================
    rm_possession = mbappe_pool["team_possession"]
    barca_possession = next(r for r in pool if r["player_name"] == "Lamine Yamal")["team_possession"]

    # scatter-zone 的 CSS 高度是 860px，宽度是 page 内容区宽度(1080 - 64*2
    # 左右内边距 = 952px)；SVG viewBox 必须跟这个容器像素尺寸完全一致
    # (viewBox="0 0 952 860")，否则 SVG 画的坐标轴/趋势线会被浏览器按
    # viewBox 缩放，而绝对定位的头像 <img> 不缩放，两者用同一组 (x,y) 数值
    # 却渲染在不同位置——这里的坐标范围直接按这个真实容器尺寸来定，不留
    # 缩放不一致的空子。
    # 660px 高的 scatter-zone 是这版加长副标题(最多两行)之前量出来的；副标题
    # 变长之后 .content 里 title-row+subtitle-row(2行)+scatter-zone+
    # modules-row 四块的高度加总会超过 .content 实际可用高度(1242px)，
    # flexbox 默认 flex-shrink:1 会把 scatter-zone 和 modules-row 一起压缩，
    # SVG viewBox 却还按压缩前的高度画坐标——用 Playwright 量过
    # getBoundingClientRect 实测复现(scatter-zone 被压到 658.8px)。
    # 加 flex-shrink:0(见模板)钉住 800px 不让它被压缩后，modules-row 改为
    # 溢出到 content 区域以下——所以这里把 SCATTER_H 直接降到 670，把让出来
    # 的高度还给 modules-row，而不是让浏览器隐式压缩谁。
    # 抖音版散点图绘图区限定在页面绝对坐标 x64–880、y380–1200(816x820)，
    # 横轴数值范围不变(仍是数据驱动的 min-3/max+3，不是重新固定 35%-72%，
    # spec 里"横轴范围不变"是指这个数值范围本身不因为画布变了而改变)——
    # 只是把同一批坐标映射到更小的像素框里，SVG viewBox 和绝对定位头像
    # 用的是同一个 816x820 的局部坐标系（0,0 在这个框的左上角，不是页面
    # 左上角），外层用 position:absolute; left:64px; top:380px 整体挪过去，
    # 跟小红书版"以 .scatter-zone 自身为坐标原点"是同一个模式，只是这版
    # 的 .scatter-zone 不再是文档流里的 flex 子元素，是页面绝对定位的独立块。
    if platform == "douyin":
        # scatter 起点从 spec 给的 380 顺延到 470(标题/副标题要让开安全区
        # 顶部的品牌条，见模板注释)，高度从 820 压到 730，底部仍钉在 1200。
        SCATTER_W, SCATTER_H = 816, 730
        PX0, PX1 = 90, 726
        PY0, PY1 = 90, 620
    else:
        SCATTER_W, SCATTER_H = 952, 640
        PX0, PX1 = 90, 900
        PY0, PY1 = 90, 530
    xs = [r["team_possession"] for r in pool]
    ys = [r["defensive_actions_p90"] for r in pool]
    x_min, x_max = min(xs) - 3, max(xs) + 3
    y_min, y_max = 0, max(ys) * 1.15

    def to_x(v):
        return PX0 + (PX1 - PX0) * (v - x_min) / (x_max - x_min)

    def to_y(v):
        return PY1 - (PY1 - PY0) * (v - y_min) / (y_max - y_min)

    ticks = []
    for gx in (40, 50, 60, 70):
        if x_min <= gx <= x_max:
            ticks.append(dict(x=to_x(gx), label=f"{gx}%"))

    y_ticks = []
    for gy in (0, 1, 2, 3, 4):
        if gy <= y_max:
            y_ticks.append(dict(y=to_y(gy), label=str(gy)))

    slope, intercept = regression["slope"], regression["intercept"]
    trend = dict(
        x0=to_x(x_min), y0=to_y(slope * x_min + intercept),
        x1=to_x(x_max), y1=to_y(slope * x_max + intercept),
    )

    # 头像散点 + 简单防碰撞：按半径把点分组，组内重叠的沿垂直方向错开，
    # 用一条 1px 灰线连回真实坐标；姆巴佩不参与这个过程，固定在真实坐标。
    raw_points = []
    for r in pool:
        if r["player_name"] == "Kylian Mbappé":
            continue
        px, py = to_x(r["team_possession"]), to_y(r["defensive_actions_p90"])
        if platform == "douyin":
            size = 56 if r["player_name"] in ("Lamine Yamal", "Raphinha") else 44
        else:
            size = 68 if r["player_name"] in ("Lamine Yamal", "Raphinha") else 52
        raw_points.append(dict(
            name=r["player_name"], name_zh=names[r["player_name"]],
            show_label=r["player_name"] in ("Lamine Yamal", "Raphinha"),
            true_x=px, true_y=py, x=px, y=py, size=size,
            avatar=r["avatar"], val=r["defensive_actions_p90"],
        ))

    def overlaps(a, b):
        min_gap = (a["size"] + b["size"]) / 2 + 4
        dx, dy = a["x"] - b["x"], a["y"] - b["y"]
        return (dx * dx + dy * dy) ** 0.5 < min_gap

    for _ in range(60):
        any_overlap = False
        for i in range(len(raw_points)):
            for j in range(i + 1, len(raw_points)):
                a, b = raw_points[i], raw_points[j]
                if overlaps(a, b):
                    a["y"] -= 5
                    b["y"] += 5
                    any_overlap = True
        if not any_overlap:
            break

    for p in raw_points:
        dx, dy = p["x"] - p["true_x"], p["y"] - p["true_y"]
        p["moved"] = (dx * dx + dy * dy) ** 0.5 > 1

    others = raw_points

    mb_x, mb_y = to_x(mbappe_pool["team_possession"]), to_y(mbappe_pool["defensive_actions_p90"])
    mb_trend_y = to_y(slope * mbappe_pool["team_possession"] + intercept)

    trend_text = f"这个控球率，一般能有 {regression['mbappe_predicted']:.2f}"
    actual_text = f"他：{mbappe_pool['defensive_actions_p90']:.2f}"
    mb_size = 110 if platform == "douyin" else 130
    mb = dict(
        x=mb_x, y=mb_y, size=mb_size, trend_y=mb_trend_y,
        avatar=mbappe_pool["avatar"], badge=mbappe_pool["badge"],
        predicted=regression["mbappe_predicted"], actual=mbappe_pool["defensive_actions_p90"],
        trend_text=trend_text, trend_text_w=text_px_width(trend_text) + 16,
        actual_text=actual_text, actual_text_w=text_px_width(actual_text) + 16,
    )

    same_pos_ctx = [dict(name_zh=names[g["player_name"]], is_mbappe=g["player_name"] == "Kylian Mbappé",
                          val=g["defensive_actions_p90"], avatar=g["avatar"])
                    for g in same_pos_group_raw]

    vini_raw_n = round(vini_pool["recoveries_p90"] * vini_pool["mins"] / 90)
    mbappe_raw_n = round(mbappe_pool["recoveries_p90"] * mbappe_pool["mins"] / 90)

    # ---- 队友模块"2倍"这句文案的校验：比值落在 [1.8, 2.0) 才用，否则打印
    # 真实比值交给站长决定怎么写 ----
    vini_mbappe_ratio = vini_pool["recoveries_p90"] / mbappe_pool["recoveries_p90"]
    ratio_ok = 1.8 <= vini_mbappe_ratio < 2.0
    print("=== 队友模块「2倍」文案校验 ===")
    print(f"  维尼修斯 {vini_pool['recoveries_p90']} / 姆巴佩 {mbappe_pool['recoveries_p90']} = {vini_mbappe_ratio:.4f}")
    verdict = "采用「快2倍」文案" if ratio_ok else "不采用，需要改写"
    print(f"  落在 [1.8, 2.0) 区间：{ratio_ok} → {verdict}")
    print()

    teammate_ctx = dict(
        vini_val=vini_pool["recoveries_p90"], mbappe_val=mbappe_pool["recoveries_p90"],
        vini_avatar=vini_pool["avatar"], mbappe_avatar=mbappe_pool["avatar"],
        vini_raw=f"{vini_raw_n}次 / {vini_pool['mins']}分钟",
        mbappe_raw=f"{mbappe_raw_n}次 / {mbappe_pool['mins']}分钟",
    )

    rm_label = dict(x=to_x(rm_possession), y=PY1 + 54, text=f"皇马 {rm_possession:.1f}%")
    barca_label = dict(x=to_x(barca_possession), y=PY1 + 54, text=f"巴萨 {barca_possession:.1f}%")
    if abs(rm_label["x"] - barca_label["x"]) < 90:
        rm_label["x"] -= 45
        barca_label["x"] += 45
    x_axis_title_y = PY1 + 88

    p4_ctx = dict(
        **common_ctx,
        page_no="4 / 5",
        scatter_w=SCATTER_W, scatter_h=SCATTER_H,
        ax=dict(x0=PX0, y0=PY0, x1=PX1, y1=PY1, ticks=ticks, y_ticks=y_ticks),
        rm_label=rm_label, barca_label=barca_label,
        trend=trend, mb=mb, others=others,
        rm_possession_text=f"{rm_possession:.1f}%",
        x_axis_title_y=x_axis_title_y,
        same_pos_n=len(same_pos_group_raw), same_pos_rank=same_pos_rank, same_pos_group=same_pos_ctx,
        teammate=teammate_ctx, ratio_ok=ratio_ok, vini_mbappe_ratio=round(vini_mbappe_ratio, 2),
        footer_note=caveat_laliga,
    )

    # =========================== P5 ===========================
    p5_ctx = dict(
        **common_ctx,
        page_no="5 / 5",
        avatar_path=mbappe_pool["avatar"],
        badge_path=mbappe_pool["badge"],
    )

    pages = [
        ("p1_cover.html.j2", p1_ctx, "p1_cover"),
        ("p2_attack.html.j2", p2_ctx, "p2_attack"),
        ("p3_defense_rank.html.j2", p3_ctx, "p3_defense_rank"),
        ("p4_evidence.html.j2", p4_ctx, "p4_evidence"),
        ("p5_outro.html.j2", p5_ctx, "p5_outro"),
    ]

    print("=== 渲染 + 截图 ===")
    stub_suffix = "_debug" if DEBUG_SAFEZONE else ""
    out_paths = []
    for tpl_name, ctx, stub in pages:
        out_stub = f"{stub}{stub_suffix}"
        html_path = render_page(tpl_name, ctx, out_stub, plat_out_dir)
        png_2x = plat_out_dir / f"{out_stub}@2x.png"
        png_final = plat_out_dir / f"{out_stub}.png"
        print(f"[{out_stub}]")
        shoot(html_path, png_2x, png_final, CANVAS_W, CANVAS_H)
        out_paths.append(png_final)

    print("\n=== 尺寸自检（实际像素） ===")
    for p in out_paths:
        im = Image.open(p)
        ok = "OK" if im.size == (CANVAS_W, CANVAS_H) else "FAIL"
        print(f"  {p}: {im.size} [{ok}]")

    thumb_path = None
    if platform == "xhs":
        # ---- P1 的 360x480 缩略预览图：跟正片同一张 1080x1440 直接等比缩小
        # （1080/1440 = 360/480 = 0.75，缩放不改变构图），用来检查手机信息流
        # 缩略图尺寸下标题是否还看得清，不是另外单独截的图。只有小红书版有
        # 这个交付项，抖音是竖屏 1080x1920，不是同一个"信息流缩略图"场景。
        p1_final = plat_out_dir / "p1_cover.png"
        thumb_path = plat_out_dir / "p1_cover_thumb_360x480.png"
        with Image.open(p1_final) as im:
            thumb = im.resize((360, 480), Image.LANCZOS)
            thumb.save(thumb_path)
        assert thumb.size == (360, 480)
        print(f"\n=== P1 缩略预览图 ===\n  {thumb_path}: {thumb.size}")

    print("\n=== 图片路径 ===")
    for p in out_paths:
        print(f"  {p}")
    if thumb_path:
        print(f"  {thumb_path}")


if __name__ == "__main__":
    main()
