"""关键球员占比(数据 tab 模块四)+ 门将对位(模块五)只读查询。

背景:与 team_form.py 同一套"该队最近打了什么"语义(League_ID + Date<before_date,
不按 Season,天然跨赛季),但这里要球员级聚合,新开一个文件而不是塞进
team_form.py(后者是纯球队级)。

诚实纪律(CLAUDE.md §6.2/§11.2):
- 球员级 COALESCE(x,0) 是有依据的(见 team_style_preview.py 顶部同一处证据,
  J1 全量交叉校验 accurate_passes/dribbles/tackles/interceptions/crosses
  球员求和 vs 球队 extra_json 总计 99.9%+ 一致)——FotMob 对零值键是省略而不是
  写 0,不是"没采到"。这与 team_form.py 的"不补 0"规则不冲突:那条针对
  *球队级缺行*(整场没采到),这里针对*球队级已有该场统计行时*, 球员层面
  某个 key 缺席可证明是 0。
- 占比的分母是"该队近 window 场同联赛比赛全体球员真实总和",不是只统计
  达到出场门槛的球员——门槛只影响*谁能上榜*,不影响分母,否则榜单会因为
  门槛而系统性抬高百分比。
- 不做 per-90 外推(5 场窗口下低分钟替补会被放大,见 docs/design-brief-
  match-detail-viz.md 的实测陷阱);每行必须同时给次数、出场、分钟。
- `goals_prevented` 覆盖率(2026-08-15 全量重算,口径:is_goalkeeper=1 且
  minutes_played>0 的球员场,10 个联赛,2020-08-21~2026-08-10,共 26,402 行)
  实测 **39.0%**(10,299/26,402)——此前文档写的 68.3% 来源不明且与任何合理
  口径都对不上,已废弃。缺失时如实给 None,不在这一层用 xGOT-失球现算兜底
  ——前端按设计稿在展示层现算并标"估算",且只有 `xgot_faced_complete=True`
  (该门将窗口内每场都有 `expected_goals_on_target_faced`,同口径实测 97.2%,
  25,650/26,402)时才允许现算,否则连估算都不给,避免用不完整分母算出一个
  看似精确的数。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from backend.queries.league_stats import _player_i18n_map

MIN_APPEARANCES = 3
TOP_N = 5


def _display_name(pid: str, source_name: str | None, i18n: dict) -> str | None:
    """短中文名 > 全中文名 > 来源英文名——与 match_report.py/league_stats.py
    同一条回退链,不维护第二套忘记查译名的展示逻辑。"""
    name_zh, name_zh_short = i18n.get(str(pid), (None, None))
    return name_zh_short or name_zh or source_name

# (block id, 标题, 占比说明文案, 求和 SQL 表达式)
_KEY_PLAYER_BLOCKS = [
    ("dribbles", "破局点", "过人成功占全队", 'COALESCE(dribbles_succeeded,0)'),
    ("creating", "组织核心", "创造机会占全队", 'COALESCE(chances_created,0)'),
    ("defending", "防守支柱", "抢断+拦截占全队",
     'COALESCE("matchstats.headers.tackles",0) + COALESCE(interceptions,0)'),
]


def _last_n_match_ids(
    conn_core: sqlite3.Connection, team_id: int, league_id: int, before_date: str, window: int,
) -> list[int]:
    # 边界与排序统一用 COALESCE(kickoff_at_utc, Date) + Match_ID 兜底——
    # 只用日期字符串比较会在"日期相同"时把顺序交给 SQLite 未保证的扫描顺序,
    # 见 tests/backend/test_team_style_preview.py::
    # test_same_calendar_date_ties_broken_by_kickoff_then_match_id。
    rows = conn_core.execute(
        """SELECT Match_ID FROM dim_match WHERE League_ID=? AND status IN ('Finish','Finished')
             AND COALESCE(kickoff_at_utc, Date) < ? AND (Home_Team_ID=? OR Away_Team_ID=?)
           ORDER BY COALESCE(kickoff_at_utc, Date) DESC, Match_ID DESC LIMIT ?""",
        (league_id, before_date, team_id, team_id, window),
    ).fetchall()
    return [r[0] for r in rows]


def team_key_players(
    conn_core: sqlite3.Connection, team_id: int, league_id: int, before_date: str,
    window: int = 5,
) -> list[dict[str, Any]]:
    """近 window 场三个维度的球员占比榜(破局/组织/防守),每维度最多 TOP_N 人。

    某维度全队近 window 场没有任何统计(team_total=0)或没人达到出场门槛时,
    该维度 rows=[] ——前端据此展示"样本不够"而不是"这队没有这类球员"。
    """
    match_ids = _last_n_match_ids(conn_core, team_id, league_id, before_date, window)
    if not match_ids:
        return [
            {"id": bid, "title": title, "metric": metric, "rows": []}
            for bid, title, metric, _ in _KEY_PLAYER_BLOCKS
        ]
    placeholders = ",".join("?" * len(match_ids))
    player_zh = _player_i18n_map(conn_core)
    out = []
    for block_id, title, metric, expr in _KEY_PLAYER_BLOCKS:
        rows = conn_core.execute(
            f"""SELECT Player_ID, MAX(player_name) name,
                       SUM({expr}) count,
                       COUNT(*) appearances,
                       SUM(COALESCE(minutes_played,0)) minutes
                  FROM fact_player_match_stats
                 WHERE Team_ID=? AND Match_ID IN ({placeholders})
                   AND minutes_played IS NOT NULL AND minutes_played > 0
                 GROUP BY Player_ID""",
            (team_id, *match_ids),
        ).fetchall()
        team_total = sum(r["count"] for r in rows)
        eligible = [r for r in rows if r["appearances"] >= MIN_APPEARANCES]
        player_rows = []
        if team_total > 0:
            for r in sorted(eligible, key=lambda r: r["count"], reverse=True)[:TOP_N]:
                if r["count"] <= 0:
                    continue
                player_rows.append({
                    "player_id": r["Player_ID"],
                    "name": _display_name(r["Player_ID"], r["name"], player_zh),
                    "pct": round(100.0 * r["count"] / team_total, 1),
                    "appearances": r["appearances"],
                    "minutes": r["minutes"],
                    "count": round(r["count"]) if float(r["count"]).is_integer() else r["count"],
                })
        out.append({"id": block_id, "title": title, "metric": metric, "rows": player_rows})
    return out


def team_goalkeepers(
    conn_core: sqlite3.Connection, team_id: int, league_id: int, before_date: str,
    window: int = 5,
) -> list[dict[str, Any]]:
    """近 window 场出场过的门将,按出场次数降序(轮换主体排前面)。

    `goals_prevented` 数据源缺失时给 None,不现算兜底——展示层现算并标"估算"
    (README/CLAUDE.md §11.2 口径分离要求)。

    `expected_goals_on_target_faced`(实测口径:is_goalkeeper=1 且 minutes_played>0
    的球员场,2026-08-15 全量重算,10 个联赛,2020-08-21~2026-08-10 共 26,402 行,
    该字段非空 97.2%)——SQL 的 SUM() 本身就会跳过 NULL,不需要 COALESCE(...,0)
    也能拿到"已知场次"的正确合计;真正需要额外做的是**如实告知这个合计是不是
    覆盖了全部出场场次**:如果只是部分场次有数据,前端不能拿这个合计当满窗口
    数字去现算阻止进球估算,否则等于用不完整分母算出一个看似精确的数(§6.2 不
    伪装精确度)。`xgot_faced_complete` 就是这个完整性标记;`xgot_faced` 在
    一场都没有该字段时给 None(不是 0——0 是"确实面对了 0 次射正"的合法值)。
    """
    match_ids = _last_n_match_ids(conn_core, team_id, league_id, before_date, window)
    if not match_ids:
        return []
    placeholders = ",".join("?" * len(match_ids))
    rows = conn_core.execute(
        f"""SELECT Player_ID, MAX(player_name) name,
                   COUNT(*) appearances,
                   SUM(COALESCE(saves,0)) saves,
                   SUM(expected_goals_on_target_faced) xgot_faced,
                   SUM(CASE WHEN expected_goals_on_target_faced IS NOT NULL THEN 1 ELSE 0 END) xgot_n,
                   SUM(COALESCE(goals_conceded,0)) goals_conceded,
                   SUM(goals_prevented) goals_prevented,
                   SUM(CASE WHEN goals_prevented IS NOT NULL THEN 1 ELSE 0 END) gp_n
              FROM fact_player_match_stats
             WHERE Team_ID=? AND is_goalkeeper=1 AND Match_ID IN ({placeholders})
             GROUP BY Player_ID
             ORDER BY appearances DESC, Player_ID""",
        (team_id, *match_ids),
    ).fetchall()
    player_zh = _player_i18n_map(conn_core)
    out = []
    for r in rows:
        out.append({
            "player_id": r["Player_ID"],
            "name": _display_name(r["Player_ID"], r["name"], player_zh),
            "appearances": r["appearances"],
            "saves": round(r["saves"]),
            "xgot_faced": round(r["xgot_faced"], 2) if r["xgot_n"] > 0 else None,
            "xgot_faced_complete": r["xgot_n"] == r["appearances"],
            "goals_conceded": round(r["goals_conceded"]),
            # 只有该门将近 window 场每一场都有该字段时才给数值,否则求和会
            # 悄悄把缺失场次当 0 拉低阻止进球——不完整就诚实给 None。
            "goals_prevented": round(r["goals_prevented"], 2) if r["gp_n"] == r["appearances"] else None,
        })
    return out
