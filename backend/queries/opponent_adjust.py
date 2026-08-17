"""对手强度校正 —— 只对**已通过样本外验证**的两类指标提供,不是通用工具。

**状态:DEFERRED / 未接入产品**(验收返工六如实标注)。本模块的方法论已经
通过下方描述的样本外验证,函数本身也有永久测试覆盖,但截至
PREMATCH_MOBILE_DATA_VISUALIZATION_V2 四张核心图(`attack_chain.py` /
`possession_control.py` / `defensive_pressure.py` / `matchup.py`)首版
上线,**没有任何一张图调用这个模块**——四张图目前展示的都是未经对手强度
校正的窗口均值。这不是被否定,是留给下一轮迭代的真实增强项,不能在交付
报告里含糊带过写成"已完成"。

背景:`backend/scripts/validate_opponent_adjustment.py` 严格样本外验证
(CUTOFF=2025-01-01,PIT-safe,四大联赛分别验证)得出的结论——产物见
docs/audits/opponent-adjustment-validation-v1.json:

- **进攻类**(自己创造 xG,按对手"近两年场均让出 xG"校正):四联赛全部提升,
  英超 0.2982→0.3151、西甲 0.4133→0.4191、德甲 0.4047→0.4052、意甲 0.3221→0.3298。
- **防守类**(自己让出 xG,按对手"近两年场均创造 xG"校正,方向相反):
  四联赛全部提升,英超 0.257→0.2632、西甲 0.1913→0.1922、德甲 0.2019→0.2164、
  意甲 0.1545→0.1573。
- **比例类**(如控球率):校正**全部四个联赛都变差**(英超 0.4347→0.3939 等)——
  **明确不采用**,本模块不提供比例类校正函数,调用方对控球率/传球成功率/
  进攻半场传球占比这类比例指标只能用未校正的窗口均值。

**不是一个通用 `adjusted_mean()`**——进攻类与防守类用的是完全不同方向的
对手历史(对手让出 vs 对手创造),故意拆成两个函数,防止未来有人图省事
传错方向。
"""

from __future__ import annotations

import datetime
import sqlite3
import statistics as st
from typing import Any

LOOKBACK_DAYS = 730
SHRINKAGE_K = 8


def shrunk_ratio(raw_ratio: float, n: int, k: int = SHRINKAGE_K) -> float:
    """小样本对手(如升班马)的校正力度收缩到接近 1.0(不校正)。
    n=0 时精确等于 1.0;n 远大于 k 时接近 raw_ratio 本身。"""
    w = n / (n + k)
    return 1 + (raw_ratio - 1) * w


def _lookback_floor(before_boundary: str, lookback_days: int = LOOKBACK_DAYS) -> str:
    date_part = before_boundary[:10]
    try:
        d = datetime.date.fromisoformat(date_part)
    except ValueError:
        return "0000-01-01"
    return (d - datetime.timedelta(days=lookback_days)).isoformat()


def _team_field_history_avg(
    conn: sqlite3.Connection, team_id: int, league_id: int, before_boundary: str,
    field: str, *, conceded: bool,
) -> tuple[float | None, int]:
    """team_id 自己(conceded=False)或其对手(conceded=True)在 before_boundary
    之前(不含)、LOOKBACK_DAYS 内的该字段均值与场次——PIT-safe:只用严格早于
    目标边界的历史,不使用目标比赛本身或更晚的信息。"""
    floor_ = _lookback_floor(before_boundary)
    if conceded:
        sql = """
          SELECT json_extract(t_opp.extra_json, '$.' || ?) v
            FROM dim_match m
            JOIN fact_team_match_stats t_own ON t_own.Match_ID=m.Match_ID AND t_own.Team_ID=? AND t_own.Period='All'
            JOIN fact_team_match_stats t_opp ON t_opp.Match_ID=m.Match_ID AND t_opp.Period='All'
             AND t_opp.Team_ID=(CASE WHEN m.Home_Team_ID=? THEN m.Away_Team_ID ELSE m.Home_Team_ID END)
           WHERE m.League_ID=? AND m.status IN ('Finish','Finished')
             AND COALESCE(m.kickoff_at_utc, m.Date) < ? AND COALESCE(m.kickoff_at_utc, m.Date) >= ?
        """
        params = (field, team_id, team_id, league_id, before_boundary, floor_)
    else:
        sql = """
          SELECT json_extract(t.extra_json, '$.' || ?) v
            FROM dim_match m
            JOIN fact_team_match_stats t ON t.Match_ID=m.Match_ID AND t.Team_ID=? AND t.Period='All'
           WHERE m.League_ID=? AND m.status IN ('Finish','Finished')
             AND COALESCE(m.kickoff_at_utc, m.Date) < ? AND COALESCE(m.kickoff_at_utc, m.Date) >= ?
        """
        params = (field, team_id, league_id, before_boundary, floor_)
    rows = conn.execute(sql, params).fetchall()
    vals = [r["v"] for r in rows if r["v"] is not None]
    if not vals:
        return None, 0
    return st.mean(vals), len(vals)


def _league_avg_conceded(
    conn: sqlite3.Connection, league_id: int, before_boundary: str, field: str,
) -> float | None:
    """整联赛在 before_boundary 之前、LOOKBACK_DAYS 内的场均让出该字段
    (用作校正公式的分子基准)。"""
    floor_ = _lookback_floor(before_boundary)
    rows = conn.execute(
        f"""SELECT json_extract(t.extra_json, '$.{field}') v
              FROM dim_match m JOIN fact_team_match_stats t ON t.Match_ID=m.Match_ID AND t.Period='All'
             WHERE m.League_ID=? AND m.status IN ('Finish','Finished')
               AND COALESCE(m.kickoff_at_utc, m.Date) < ? AND COALESCE(m.kickoff_at_utc, m.Date) >= ?""",
        (league_id, before_boundary, floor_),
    ).fetchall()
    vals = [r["v"] for r in rows if r["v"] is not None]
    return st.mean(vals) if vals else None


def adjust_attack_window(
    conn: sqlite3.Connection, league_id: int, before_boundary: str,
    window_rows: list[dict[str, Any]], field: str = "expected_goals",
) -> list[float]:
    """进攻类指标(自己创造 field)的对手强度校正——已通过样本外验证,采用。

    `window_rows`:该队某窗口内每场历史比赛的 [{"opp": 对手team_id,
    "boundary": 该历史比赛的边界字符串, "value": 该场自己的原始值}, ...]
    (调用方已经拿到窗口内容,这里只做校正,不重新选窗口——选窗口是
    backend/queries/window.py 的职责)。

    校正公式:每场原始值 × 收缩后的 (联赛场均让出 field ÷ 该场对手近两年
    场均让出 field),对手历史场次越少收缩力度越接近 1.0。
    """
    league_avg = _league_avg_conceded(conn, league_id, before_boundary, field)
    if league_avg is None or league_avg <= 0:
        return [r["value"] for r in window_rows]
    out = []
    for r in window_rows:
        opp_strength, n_opp = _team_field_history_avg(
            conn, r["opp"], league_id, r["boundary"], field, conceded=True,
        )
        if opp_strength is None or opp_strength <= 0:
            out.append(r["value"])
            continue
        ratio = shrunk_ratio(league_avg / opp_strength, n_opp)
        out.append(r["value"] * ratio)
    return out


def adjust_defense_window(
    conn: sqlite3.Connection, league_id: int, before_boundary: str,
    window_rows: list[dict[str, Any]], field: str = "expected_goals",
) -> list[float]:
    """防守类指标(自己让出 field,即 xGA 这类)的对手强度校正——已通过
    样本外验证,采用。方向与 `adjust_attack_window` 相反:用对手近两年
    场均**创造** field(不是让出),因为这里要问的是"这场对手本来就很能
    创造机会吗",不是"这场对手擅长让球"。
    """
    league_avg = _league_avg_conceded(conn, league_id, before_boundary, field)  # 联赛场均创造=场均让出,零和口径下数值相同
    if league_avg is None or league_avg <= 0:
        return [r["value"] for r in window_rows]
    out = []
    for r in window_rows:
        opp_strength, n_opp = _team_field_history_avg(
            conn, r["opp"], league_id, r["boundary"], field, conceded=False,
        )
        if opp_strength is None or opp_strength <= 0:
            out.append(r["value"])
            continue
        ratio = shrunk_ratio(league_avg / opp_strength, n_opp)
        out.append(r["value"] * ratio)
    return out
