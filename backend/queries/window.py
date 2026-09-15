"""赛前窗口选择的唯一真源(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 1)。

不是简单的"近 N 场"——实测证明(见方案文档 §一.1)混主客场、不设分级
fallback 会让预测力损失 22~35%。本模块提供**分级 fallback 的同主客场窗口**,
球队风格/进攻/防守/传球等球队级指标统一走这里,不再各自维护一套窗口 SQL。

严格约束(全部有实测或明确设计依据,不是随手写的默认值):
- **exact pre-kickoff**:边界统一用 COALESCE(kickoff_at_utc, Date),精确到
  时刻;缺精确开球时间时如实降级到自然日,不是砍掉这场比赛(CLAUDE.md §6.2.1)。
- **same competition(默认)**:传入 League_ID 时只在该联赛内取历史。
  **例外**:`league_id=None` 显式放宽为"不限赛事"。这道口子只给参赛队来自
  不同国内联赛的赛事(欧战三项,见 queries/leagues.py::is_cross_league_competition)
  ——那里"同一 League_ID 内取历史"这个前提本身不成立:欧冠联赛阶段每队只
  踢 8 场(主客各 4 场),同主客场样本永远到不了 min_n,只圈本赛事等于恒空。
  代价是窗口里各场比赛的对手强度差异极大(挪超球队踢挪超 vs 踢拜仁),
  聚合出来的数值是"近期比赛"而非"同水平比赛",调用方在展示层必须靠
  `label_zh` 如实说明,不得暗示可比。
- **venue-aware**:主队只用主场历史、客队只用客场历史——实测主客场差异
  达 17~18%(创造 xG/射门/禁区触球),混算会失真。
- **deterministic order**:`ORDER BY COALESCE(kickoff_at_utc, Date) DESC,
  Match_ID DESC`,同一时刻的比赛不会随查询计划漂移。
- **maximum lookback**:`MAX_LOOKBACK_DAYS`(730 天≈两年)硬上限——不为了
  凑够场次数无条件跨越多个赛季。选这个值不是拍脑袋:同一批口径下,
  "近两年"对手强度表比"全部历史"耗时从 341ms 降到 20ms 且区分度从 120%
  升到 175%(全部历史会把球队多个赛季的强弱混在一起拉平),两年窗口本身
  也留了充足余量覆盖跨赛季的"近 10 场同主客场"(上赛季末到本赛季开局
  一般不超过 4 个月缺口)。
- **minimum sample & clear fallback**:见 `Tier` 类型与 `venue_window()`
  文档,四档递降,不同档位在返回值里显式标出,调用方不得把它们当同一
  可信度直接比较。

**本模块只负责"选窗口"(哪些 match_id、什么档位),不负责把某个具体指标
在这些比赛上聚合成数值**——那是各图表查询函数(Phase 2)的职责,不同指标
的字段/公式差异太大,不适合塞进一个通用聚合器里。全联赛百分位基准
(主队用全联赛"主场基准"、客队用全联赛"客场基准"两套独立参考系,不是
拿主队主场均值直接比客队客场均值)是 Phase 1.5 的独立产物,建立在
`venue_window()` 之上,不在这个文件里。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from backend.queries.leagues import LEAGUE_META

MAX_LOOKBACK_DAYS = 730
# DEFAULT_MAX_N=10 不是拍脑袋:backend/scripts/validate_window_length.py 做过
# 一次按时间切分的样本外回测(CUTOFF=2025-01-01,验证目标只取 CUTOFF 之后、
# 且两组窗口都要求凑满历史场次才可比,不是在同一批数据上直接比相关系数)。
# **口径限定**:这是"当前这一次时间切分回测支持 N=10"的结论,不是独立最终
# 验证,也不代表 N=10 在所有场景下都是最优窗口长度——四大联赛在这一次回测
# 里 N=10 都不弱于 N=5(用历史均值预测下一场真实 xG 的相关系数):
# 英超 0.2493→0.2982、西甲 0.3984→0.4133、德甲 0.3747→0.4047、意甲 0.2752→0.3221。
# 产物见 docs/audits/window-length-validation-v1.json,可重跑复现;换一批
# 切分时间点或更长的历史区间重跑,结论可能变化。
DEFAULT_MAX_N = 10
DEFAULT_MIN_N = 5

Tier = Literal["venue_full", "venue_partial", "mixed", "unavailable"]

# `{league}` 是联赛中文名(如"英超"),由 WindowResult.league_zh 填。窗口默认圈在
# 单个 League_ID 内,欧战和国内杯赛全部被排除——不写出来,界面就会把"近 10 场
# 英超主场"说成"近 10 个主场",与下面 label_zh 里"不限赛事·"前缀防的是同一类
# 错误陈述(2026-09-13 先在百分位模块修过一次,这里补齐同一条纪律)。
# `{league}` 留空时这四条模板产出的字符串与加这个占位符之前**逐字节相同**,
# 这是跨赛事路径(没有联赛可写)零回归的落点。
_TIER_LABEL_ZH = {
    "venue_full": "近 {n} 个{league}{venue}",
    "venue_partial": "近 {n} 个{league}{venue}(样本不足 {max_n},已如实展示实际场次)",
    "mixed": "近 {n} 场{league}(主客场样本均不足,已合并主客场——不能与纯主场/客场窗口直接比较)",
    "unavailable": "暂无可比较的{league}历史比赛",
}


@dataclass(frozen=True)
class WindowResult:
    team_id: int
    is_home: bool
    tier: Tier
    match_ids: list[int]
    matches: int
    from_date: str | None
    to_date: str | None
    cross_league: bool = False
    # 这个窗口圈在哪个联赛里(如"英超")。跨赛事窗口(league_id=None)恒为 None
    # ——那时没有联赛可写,"不限赛事·"前缀已经把口径说清楚了。
    league_zh: str | None = None

    @property
    def mixed_venues(self) -> bool:
        """True 时 match_ids 混合了主客场——调用方在展示层必须用
        `label_zh` 里的措辞,不能省略"已合并主客场"这句话。"""
        return self.tier == "mixed"

    @property
    def label_zh(self) -> str:
        venue = "主场" if self.is_home else "客场"
        tpl = _TIER_LABEL_ZH[self.tier]
        label = tpl.format(
            n=self.matches, venue=venue, max_n=DEFAULT_MAX_N, league=self.league_zh or "",
        )
        # 跨赛事窗口的 tier 仍然是 venue_full,光看档位读不出"这 10 场不在
        # 同一个联赛里"。少了这个前缀,界面就会写"近 10 个主场"却隐瞒了它
        # 混了欧冠和国内联赛——那是错误陈述,不是省略。
        if self.cross_league and self.tier != "unavailable":
            return f"不限赛事·{label}"
        return label


def _match_ids(
    conn: sqlite3.Connection, team_id: int, league_id: int | None, before_boundary: str,
    *, venue: Literal["home", "away", "any"], max_n: int, lookback_floor: str,
) -> list[sqlite3.Row]:
    venue_clause = {
        "home": "m.Home_Team_ID=?",
        "away": "m.Away_Team_ID=?",
        "any": "(m.Home_Team_ID=? OR m.Away_Team_ID=?)",
    }[venue]
    # league_id 非 None 时拼出来的 SQL 与放宽之前**逐字节相同**,这是"常规
    # 联赛路径零回归"的落点;None 才整条去掉联赛谓词。
    league_clause = "m.League_ID=? AND " if league_id is not None else ""
    params: list[Any] = [] if league_id is None else [league_id]
    params += [before_boundary, lookback_floor]
    if venue == "any":
        params += [team_id, team_id]
    else:
        params.append(team_id)
    params.append(max_n)
    return conn.execute(
        f"""SELECT Match_ID, Date FROM dim_match m
             WHERE {league_clause}m.status IN ('Finish','Finished')
               AND COALESCE(m.kickoff_at_utc, m.Date) < ?
               AND COALESCE(m.kickoff_at_utc, m.Date) >= ?
               AND {venue_clause}
             ORDER BY COALESCE(m.kickoff_at_utc, m.Date) DESC, m.Match_ID DESC
             LIMIT ?""",
        params,
    ).fetchall()


def _lookback_floor(before_boundary: str) -> str:
    """`before_boundary` 可能是纯日期("2026-08-15")或完整 ISO 时刻
    ("2026-08-15T20:00:00Z")——两种格式取前 10 位再减 730 天的粗略算法
    (按 365.25 天/年折算,不追求日历精确,只是给回溯设一道硬闸)足够,
    比较仍然是 COALESCE(kickoff_at_utc, Date) 与这个下界的字符串比较,
    真正卡边界的是主查询里的 julianday 差值,这里只需给出一个不晚于
    目标下界的安全值即可。"""
    import datetime

    date_part = before_boundary[:10]
    try:
        d = datetime.date.fromisoformat(date_part)
    except ValueError:
        return "0000-01-01"
    return (d - datetime.timedelta(days=MAX_LOOKBACK_DAYS)).isoformat()


def resolve_match_window(
    conn: sqlite3.Connection, league_id: int, season: str, *,
    venue: Literal["home", "away", "all"], recency: int | None,
) -> list[tuple[int, int]] | None:
    """球队数据页"最近 N 场 / 主客场"筛选(2026-09-14)专用——按队各自的
    视角返回 [(Team_ID, Match_ID), ...]:`venue="home"` 只保留该队是主队的
    比赛,不是"联赛里发生在主场的比赛"这个全局概念(每场比赛对主队和客队
    是两件不同的事)。`recency` 用 `ROW_NUMBER() OVER (PARTITION BY
    Team_ID ...)` 限定每队各自最近 N 场,不是联赛级的最近 N 场比赛。

    `venue="all"` 且 `recency=None`(两个维度都没筛选)时返回 `None`——
    调用方据此回退到"无筛选"的整赛季查询路径,SQL 与今天逐字节相同,
    零性能回归。

    与 `venue_window()` 的关键差异:**没有 fallback 降级**。`venue_window()`
    是给"某场比赛找历史参照"设计的,样本不够时的 `mixed`(悄悄混主客场)
    档位在那个场景下是合理的诚实兜底;但这里是用户在球队数据页显式选了
    "主场"这个筛选,样本不够就该如实返回更少的场次甚至 0 场——静默把客场
    比赛混进来充数,会让"主场"这个筛选标签本身失真,等于骗用户。
    """
    if venue == "all" and recency is None:
        return None
    venue_clause = {
        "home": "m.Home_Team_ID = fts.Team_ID",
        "away": "m.Away_Team_ID = fts.Team_ID",
        "all": "1=1",
    }[venue]
    recency_clause = "AND rn <= ?" if recency is not None else ""
    sql = f"""
        WITH ranked AS (
            SELECT m.Match_ID AS mid, fts.Team_ID AS tid,
                   ROW_NUMBER() OVER (
                       PARTITION BY fts.Team_ID
                       ORDER BY COALESCE(m.kickoff_at_utc, m.Date) DESC, m.Match_ID DESC
                   ) AS rn
            FROM dim_match m
            JOIN fact_team_match_stats fts ON fts.Match_ID = m.Match_ID AND fts.Period = 'All'
            WHERE m.League_ID = ? AND m.Season = ? AND m.status = 'Finish'
              AND {venue_clause}
        )
        SELECT tid, mid FROM ranked WHERE 1=1 {recency_clause}
    """
    params: list[Any] = [league_id, season]
    if recency is not None:
        params.append(recency)
    return [(r[0], r[1]) for r in conn.execute(sql, params).fetchall()]


def match_window_join_sql(
    pairs: list[tuple[int, int]] | None, *, team_col: str, match_col: str
) -> tuple[str, list[int]]:
    """把 `resolve_match_window()` 的结果转成可直接拼进任意聚合 SQL 的
    `AND (...)` 片段 + 对应参数列表——`team_col`/`match_col` 是调用方查询里
    代表"本队"/"本场比赛"的列名(通常是 `fts.Team_ID`/`fts.Match_ID`)。

    `pairs is None`(未筛选)返回 `("", [])`,拼接后 SQL 与今天完全相同。
    `pairs == []`(筛选后一场都不剩,比如某队本赛季主场还没开踢过)返回
    一个恒假条件,让聚合查询自然产出空结果——不是抛异常,也不能悄悄退回
    查询全部比赛(那样"筛选生效"这件事本身就是假的)。行值比较
    `(a, b) IN (VALUES (?,?), ...)` 是 SQLite 3.15+ 支持的标准写法。
    """
    if pairs is None:
        return "", []
    if not pairs:
        return "AND 1=0", []
    placeholders = ",".join("(?,?)" for _ in pairs)
    values = [v for pair in pairs for v in pair]
    return f"AND ({team_col}, {match_col}) IN (VALUES {placeholders})", values


def venue_window(
    conn: sqlite3.Connection, team_id: int, league_id: int | None, before_boundary: str,
    *, is_home: bool, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> WindowResult:
    """该队近 max_n 场"同主客场"比赛,四档递降 fallback:

    1. venue_full    —— 同主客场凑满 max_n(默认 10)场
    2. venue_partial —— 同主客场不足 max_n 但 >= min_n(默认 5)场,如实用实际场次
    3. mixed         —— 同主客场不足 min_n,退回混合主客场(仍受 max_n 与
                         回溯上限约束),界面必须显式标"已合并主客场"
    4. unavailable   —— 混合后仍是 0 场

    `league_id=None` 表示"不限赛事"(见模块 docstring 的 same competition 例外),
    此时结果的 `cross_league=True`,`label_zh` 会带上"不限赛事·"前缀。

    `before_boundary` 建议传目标比赛的 `COALESCE(kickoff_at_utc, date_utc)`
    (与 team_style_preview.py / player_form.py 现有函数同一套边界口径)。
    """
    cross_league = league_id is None
    # 未登记的 League_ID 拿不到译名 → None → label 回落到不带联赛名的旧措辞,
    # 不硬凑一个名字出来。
    league_zh = None if cross_league else LEAGUE_META.get(league_id, {}).get("name_zh")
    floor_ = _lookback_floor(before_boundary)
    venue = "home" if is_home else "away"
    rows = _match_ids(conn, team_id, league_id, before_boundary, venue=venue, max_n=max_n, lookback_floor=floor_)

    if len(rows) >= min_n:
        tier: Tier = "venue_full" if len(rows) >= max_n else "venue_partial"
    else:
        mixed_rows = _match_ids(conn, team_id, league_id, before_boundary, venue="any", max_n=max_n, lookback_floor=floor_)
        if mixed_rows:
            rows = mixed_rows
            tier = "mixed"
        else:
            tier = "unavailable"

    if not rows:
        return WindowResult(team_id=team_id, is_home=is_home, tier="unavailable",
                             match_ids=[], matches=0, from_date=None, to_date=None,
                             cross_league=cross_league, league_zh=league_zh)
    dates = [r["Date"] for r in rows]
    return WindowResult(
        team_id=team_id, is_home=is_home, tier=tier,
        match_ids=[r["Match_ID"] for r in rows],
        matches=len(rows), from_date=min(dates), to_date=max(dates),
        cross_league=cross_league, league_zh=league_zh,
    )
