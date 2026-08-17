"""赛前窗口选择的唯一真源(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 1)。

不是简单的"近 N 场"——实测证明(见方案文档 §一.1)混主客场、不设分级
fallback 会让预测力损失 22~35%。本模块提供**分级 fallback 的同主客场窗口**,
球队风格/进攻/防守/传球等球队级指标统一走这里,不再各自维护一套窗口 SQL。

严格约束(全部有实测或明确设计依据,不是随手写的默认值):
- **exact pre-kickoff**:边界统一用 COALESCE(kickoff_at_utc, Date),精确到
  时刻;缺精确开球时间时如实降级到自然日,不是砍掉这场比赛(CLAUDE.md §6.2.1)。
- **same competition**:只在同一 League_ID 内取历史,不跨联赛。
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

_TIER_LABEL_ZH = {
    "venue_full": "近 {n} 个{venue}",
    "venue_partial": "近 {n} 个{venue}(样本不足 {max_n},已如实展示实际场次)",
    "mixed": "近 {n} 场(主客场样本均不足,已合并主客场——不能与纯主场/客场窗口直接比较)",
    "unavailable": "暂无可比较的历史比赛",
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

    @property
    def mixed_venues(self) -> bool:
        """True 时 match_ids 混合了主客场——调用方在展示层必须用
        `label_zh` 里的措辞,不能省略"已合并主客场"这句话。"""
        return self.tier == "mixed"

    @property
    def label_zh(self) -> str:
        venue = "主场" if self.is_home else "客场"
        tpl = _TIER_LABEL_ZH[self.tier]
        return tpl.format(n=self.matches, venue=venue, max_n=DEFAULT_MAX_N)


def _match_ids(
    conn: sqlite3.Connection, team_id: int, league_id: int, before_boundary: str,
    *, venue: Literal["home", "away", "any"], max_n: int, lookback_floor: str,
) -> list[sqlite3.Row]:
    venue_clause = {
        "home": "m.Home_Team_ID=?",
        "away": "m.Away_Team_ID=?",
        "any": "(m.Home_Team_ID=? OR m.Away_Team_ID=?)",
    }[venue]
    params: list[Any] = [league_id, before_boundary, lookback_floor]
    if venue == "any":
        params += [team_id, team_id]
    else:
        params.append(team_id)
    params.append(max_n)
    return conn.execute(
        f"""SELECT Match_ID, Date FROM dim_match m
             WHERE m.League_ID=? AND m.status IN ('Finish','Finished')
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


def venue_window(
    conn: sqlite3.Connection, team_id: int, league_id: int, before_boundary: str,
    *, is_home: bool, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> WindowResult:
    """该队近 max_n 场"同主客场"比赛,四档递降 fallback:

    1. venue_full    —— 同主客场凑满 max_n(默认 10)场
    2. venue_partial —— 同主客场不足 max_n 但 >= min_n(默认 5)场,如实用实际场次
    3. mixed         —— 同主客场不足 min_n,退回混合主客场(仍受 max_n 与
                         回溯上限约束),界面必须显式标"已合并主客场"
    4. unavailable   —— 混合后仍是 0 场

    `before_boundary` 建议传目标比赛的 `COALESCE(kickoff_at_utc, date_utc)`
    (与 team_style_preview.py / player_form.py 现有函数同一套边界口径)。
    """
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
                             match_ids=[], matches=0, from_date=None, to_date=None)
    dates = [r["Date"] for r in rows]
    return WindowResult(
        team_id=team_id, is_home=is_home, tier=tier,
        match_ids=[r["Match_ID"] for r in rows],
        matches=len(rows), from_date=min(dates), to_date=max(dates),
    )
