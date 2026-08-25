"""赛季制度表(dim_league_season_regime)的唯一 Python 出口(2026-08-25)。

背景(CLAUDE.md §6.3 赛季默认值真实事故):`dim_match.Season` 此前是"调用方
手填、六个互不协调的来源各写各的"的可写字段,生产实测 18,056 行里 878 行
标错——且**全部 878 行的标签都是同一个旧默认值 '2025/2026'**,除它以外的
每一个标签与"按 (League_ID, Date) 推导"100% 吻合(14,138/14,138)。据此把
Season 改为 FotMob 同款语义:比赛的赛季不是存出来的属性,是由联赛制度 +
比赛日期决定的派生值(FotMob 安卓端 Match 模型压根没有 Season 字段,APK
反编译核实)。

本模块提供:
- `derived_season_sql(date_expr, league_expr)`:生成推导 SQL 片段——
  migration 0011 的 dim_match 触发器、质量门 G12、season_audit CLI 三处
  必须用同一份推导逻辑,这里是唯一出处(触发器 SQL 在 migration 文件里是
  静态拷贝,tests/backend/test_season_regime.py 用行为等价测试钉住两者
  不漂移);
- `season_for_match(conn, league_id, date)`:单场推导的 Python 入口;
- `REGIME_SEED`:制度表种子数据的唯一定义,migration 0011 的 INSERT 与它
  必须一致(同样由测试钉住)。

制度表为什么必须按 effective_from 分版本而不是每联赛一个标量:日职(223)
2026 年真实地从自然年制改成了跨年制——2024/2025 是自然年各 380 场,2026 是
200 场的过渡半赛季(2~6 月),2026/2027 起跨年。单一标量表达不了这种切换。

cutover_month=7 的依据(生产 18,056 行实测):所有跨年联赛 7 月整月为空
(赛季间有完整的空月,不是勉强的阈值);欧战资格赛在 7 月开打属于**新**
赛季,同样被 month>=7 归入新赛季,方向正确。已知例外风险:类似 2019/20
疫情重启那种把联赛拖进 7 月的极端情况会被触发器如实拒绝——那是需要人来
决定的制度事件,fail-loud 优于静默写错(§2.2)。

未登记联赛一律 fail closed(推导返回 None、触发器 ABORT):逼新接入联赛
先在制度表登记,不猜。
"""

from __future__ import annotations

import sqlite3

# 制度表种子(migration 0011 的 INSERT 与此保持一致,测试交叉校验)。
# (league_id, effective_from, season_kind, cutover_month, note)
REGIME_SEED: tuple[tuple[int, str, str, int, str], ...] = (
    # 跨年联赛(欧洲主流 + 澳超;7 月整月无比赛,生产实测)
    (47, "1900-01-01", "cross_year", 7, "英超"),
    (48, "1900-01-01", "cross_year", 7, "英冠"),
    (53, "1900-01-01", "cross_year", 7, "法甲"),
    (54, "1900-01-01", "cross_year", 7, "德甲"),
    (55, "1900-01-01", "cross_year", 7, "意甲"),
    (57, "1900-01-01", "cross_year", 7, "荷甲"),
    (61, "1900-01-01", "cross_year", 7, "葡超"),
    (87, "1900-01-01", "cross_year", 7, "西甲"),
    (113, "1900-01-01", "cross_year", 7, "澳超(南半球 10 月-次年 5 月,同样跨年)"),
    (42, "1900-01-01", "cross_year", 7, "欧冠(7 月资格赛属新赛季,month>=7 正确归入)"),
    (73, "1900-01-01", "cross_year", 7, "欧联"),
    (10216, "1900-01-01", "cross_year", 7, "欧协联"),
    # 有历史数据但未进 LEAGUE_META 的四个联赛(生产各 90~110 行,全部
    # 2~5 月比赛、跨年标签,按跨年规则推导 100% 吻合,实测于 2026-08-25)
    (86, "1900-01-01", "cross_year", 7, "未登记联赛(历史数据,跨年制实测吻合)"),
    (110, "1900-01-01", "cross_year", 7, "未登记联赛(历史数据,跨年制实测吻合)"),
    (140, "1900-01-01", "cross_year", 7, "未登记联赛(历史数据,跨年制实测吻合)"),
    (146, "1900-01-01", "cross_year", 7, "未登记联赛(历史数据,跨年制实测吻合)"),
    # 自然年联赛
    (59, "1900-01-01", "calendar_year", 1, "挪威超"),
    (67, "1900-01-01", "calendar_year", 1, "瑞典超"),
    (268, "1900-01-01", "calendar_year", 1, "巴甲"),
    (9080, "1900-01-01", "calendar_year", 1, "韩K联"),
    # 日职:2026-07-01 起从自然年切换为跨年(2026 为 200 场过渡半赛季,
    # 2026/2027 起秋春制——J.League 官方换制,生产数据实测确认)
    (223, "1900-01-01", "calendar_year", 1, "日职联(自然年,至 2026-06)"),
    (223, "2026-07-01", "cross_year", 7, "日职联(2026-07 起秋春制)"),
)


def derived_season_sql(date_expr: str, league_expr: str) -> str:
    """生成"按 (联赛, 日期) 推导赛季"的 SQL 标量子查询片段。

    `date_expr` / `league_expr` 是嵌入点的 SQL 表达式(如 "m.Date" / "NEW.Date")。
    未登记联赛或日期为 NULL 时子查询返回 NULL——调用方自行决定 NULL 语义
    (触发器把未登记单独 ABORT;质量门把 NULL 记为不可判)。
    """
    return f"""(
      SELECT CASE r.season_kind
        WHEN 'calendar_year' THEN substr({date_expr}, 1, 4)
        ELSE CASE
          WHEN CAST(substr({date_expr}, 6, 2) AS INTEGER) >= r.cutover_month
            THEN substr({date_expr}, 1, 4) || '/'
                 || CAST(CAST(substr({date_expr}, 1, 4) AS INTEGER) + 1 AS TEXT)
          ELSE CAST(CAST(substr({date_expr}, 1, 4) AS INTEGER) - 1 AS TEXT)
                 || '/' || substr({date_expr}, 1, 4)
        END
      END
      FROM dim_league_season_regime r
      WHERE r.league_id = {league_expr} AND r.effective_from <= {date_expr}
      ORDER BY r.effective_from DESC LIMIT 1
    )"""


def season_for_match(
    conn: sqlite3.Connection, league_id: int, date: str
) -> str | None:
    """按制度表推导某联赛某日期所属赛季;未登记联赛/无效输入返回 None。

    与 `backend/season_resolver.py::resolve_current_season` 是两个不同的问题,
    互不替代:那边回答"现在(没有比赛日期)是哪个赛季",跨年联赛无 provider
    证据时如实 SEASON_UNVERIFIED、绝不按当前月份猜;这里回答"已知一场比赛
    的日期,它属于哪个赛季"——赛季间有整月空档,这个严格更容易的问题可以
    确定性回答(生产 14,138 行无一例外)。
    """
    if not isinstance(date, str) or len(date) < 10:
        return None
    # 命名参数:date 表达式在片段里出现多次,位置参数极易错位
    row = conn.execute(
        f"SELECT {derived_season_sql(':d', ':lid')}",
        {"d": date[:10], "lid": league_id},
    ).fetchone()
    return row[0] if row else None
