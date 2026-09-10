"""球队风格定位(数据 tab 模块二:象限图)+ 进攻来源拆解(模块三)只读查询。

近 N 场(默认 5)全联赛球队风格聚合,每次一条 SQL 给整个联赛(用作象限图背景
分布),不逐队查询——已实测整联赛聚合 0.01s user / 0.5s wall(2026-08-14,
J1 26 队真实数据)。每队各自的"最近 N 场同联赛比赛"独立倒推(不是同一批
比赛日期),与 team_form.py::team_recent_profile 同一套"该队最近打了什么"
语义,但这里要一次性拿全联赛分布,不逐队调用它(避免 20+ 次重复的候选
比赛查询)。

诚实纪律(CLAUDE.md §6.2/§11.2):
- 缺失维度的球队直接从该视角的点集里剔除,不补 0(0 是"实测就是 0"的
  合法值,不能拿来表示"没有数据")——同 components/league/TeamQuadrantChart
  的既有纪律。
- 进攻来源(fact_shotmap.Situation)缺失 xG 时该行 xg=None,不补 0;
  不凑满全部来源类型,某队近 N 场只出现 3~4 种就只给 3~4 种。
- 视角有效点数(x、y 都非空的球队数)由调用方按 §4(<4 支不可用)决定是否
  展示,本模块不做这个判断,只如实返回点集,让前端(同 TeamQuadrantChart)
  统一处理"数据不足禁用该视角"。

2026-08-25 修复:所有查询必须同时按 `League_ID` 与 `Season` 过滤(真实事故——
富勒姆 vs 切尔西「分析」tab 的风格象限画出 31 支球队,而英超单赛季只有 20
支)。`dim_match.League_ID` 是跨赛季持久的联赛实体(英超 47 号从 2020/2021
一直用到 2026/2027),此前只按 `League_ID` 圈定"该队近 N 场同联赛比赛"的
候选池,任何历史上打过这个联赛的球队(含多年前已降级、此后再没打过顶级
联赛的球队)只要有一场历史记录早于 `before_date`,就会被 ROW_NUMBER 选中
它自己"最近的 N 场"(哪怕那 N 场是 2021 年打的)、进而出现在散点图里——
不是"数据分布图"该有的行为,是把已经不在这个联赛的球队错误地画进了当前
赛季的联赛画像。修复后每次查询都限定 `Season=?`(取自被查看比赛自己的
`dim_match.Season`),球队池与"近 N 场"窗口天然只在同一赛季内滚动;赛季
初样本不足时如实返回更少的点(不跨赛季借数据),与本文件既有的"部分历史
也是真历史"降级哲学一致。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from backend.media.team_crests import resolve_team_crest_url
from backend.queries.teams import display_name_for_team, team_display_for

WINDOW = 5

# fact_shotmap.Situation 真实枚举(2026-08-14 实测,10 个联赛近一年覆盖 99.4%–100%)
# → 中文标签。个人突破/点球在前端调色板里有专色,其余未特别指定色的标签
# 会落到统一的灰色"其他"兜底,不需要在这里预先合并成同一个桶。
_SITUATION_ZH: dict[str, str] = {
    "RegularPlay": "运动战",
    "FastBreak": "反击",
    "FromCorner": "角球",
    "SetPiece": "定位球",
    "FreeKick": "任意球",
    "ThrowInSetPiece": "界外球战术",
    "IndividualPlay": "个人突破",
    "Penalty": "点球",
}

# 三个视角的定义:(id, tab短标签, 标题, x字段, y字段, x轴名, y轴名, 小数位, 四象限中文名)
# 象限顺序:x高y高 / x高y低 / x低y高 / x低y低(与 TeamStyleQuadrant.tsx 的 quadOf 对应)。
#
# 文案纪律(2026-09-09 术语校准):象限名一律用中文足球术语(攻守兼备 / 防守反击 /
# 两翼齐飞……),不用口语("既控又快""少压向禁区")。xG 轴给中文全称"预期进球 /
# 预期失球",英文缩写跟在后面,新用户不认识 xG 也能读。
_TEAM_STAT_VIEWS = [
    {
        "id": "poss-fastbreak", "tab": "控球 × 快攻", "title": "控球率 × 快攻射门占比",
        "x_label": "控球率 %", "y_label": "快攻射门占比 %", "digits": 1,
        # 控球高+快攻高 / 控球高+快攻低 / 控球低+快攻高 / 控球低+快攻低
        "quadrants": ["控快兼备", "阵地控球", "防守反击", "控守被动"],
    },
    {
        "id": "cross-box", "tab": "传中 × 禁区触球", "title": "场均成功传中 × 禁区触球",
        "x_label": "场均成功传中", "y_label": "场均禁区触球", "digits": 1,
        # 传中多+禁区触球多 / 传中多+禁区触球少 / 传中少+禁区触球多 / 传中少+禁区触球少
        "quadrants": ["两翼齐飞", "边路起球", "中路渗透", "难入禁区"],
    },
    {
        # x(预期进球)越高越好,y(预期失球)越低越好——quadrants 数组仍按既有
        # [x高y高, x高y低, x低y高, x低y低](原始高低,不是好坏)下标约定,
        # 但文案必须换算成"两个方向都指向好"才算"攻守兼备":
        # x高y低(进球多+失球少)才是攻守兼备;x高y高(进球多+失球多)是同时开放的对攻型;
        # x低y低(进球少+失球少)是重守轻攻;x低y高(进球少+失球多)才是真正的攻守俱弱。
        # 见 tests/backend/test_team_style_preview.py::test_direction_semantics_propagated_and_quadrant_labels_correct
        "id": "xg-for-against", "tab": "攻防 xG", "title": "预期进球 × 预期失球",
        "x_label": "场均预期进球 xG", "y_label": "场均预期失球 xGA", "digits": 2,
        "quadrants": ["对攻型", "攻守兼备", "攻守俱弱", "重守轻攻"],
        "y_lower_is_better": True,
    },
]


def _single_team_stat(
    conn_core: sqlite3.Connection, league_id: int, season: str, before_date: str, key: str,
    window: int,
) -> dict[int, float | None]:
    """单个 TEAM_STAT_KEYS 来源键(自身 extra_json)的近 N 场均值。"""
    sql = """
      WITH ranked AS (
        SELECT m.Match_ID mid, t.Team_ID tid,
               ROW_NUMBER() OVER (
                 PARTITION BY t.Team_ID
                 ORDER BY COALESCE(m.kickoff_at_utc, m.Date) DESC, m.Match_ID DESC
               ) rn
          FROM dim_match m
          JOIN fact_team_match_stats t ON t.Match_ID=m.Match_ID AND t.Period='All'
         WHERE m.League_ID=? AND m.Season=? AND m.status IN ('Finish','Finished')
           AND COALESCE(m.kickoff_at_utc, m.Date) < ?
      ),
      last_n AS (SELECT mid, tid FROM ranked WHERE rn<=?)
      SELECT l.tid, AVG(json_extract(t.extra_json, ?)) v
        FROM last_n l
        JOIN fact_team_match_stats t ON t.Match_ID=l.mid AND t.Team_ID=l.tid AND t.Period='All'
       GROUP BY l.tid
    """
    rows = conn_core.execute(sql, (league_id, season, before_date, window, f"$.{key}")).fetchall()
    return {int(r["tid"]): r["v"] for r in rows}


def _team_stat_points(
    conn_core: sqlite3.Connection, league_id: int, season: str, before_date: str, x_key: str,
    y_key: str, window: int,
) -> dict[int, dict[str, float | None]]:
    """两个 TEAM_STAT_KEYS 来源键(自身 extra_json,不涉及对手)的近 N 场均值。"""
    sql = f"""
      WITH ranked AS (
        SELECT m.Match_ID mid, t.Team_ID tid,
               ROW_NUMBER() OVER (
                 PARTITION BY t.Team_ID
                 ORDER BY COALESCE(m.kickoff_at_utc, m.Date) DESC, m.Match_ID DESC
               ) rn
          FROM dim_match m
          JOIN fact_team_match_stats t ON t.Match_ID=m.Match_ID AND t.Period='All'
         WHERE m.League_ID=? AND m.Season=? AND m.status IN ('Finish','Finished')
           AND COALESCE(m.kickoff_at_utc, m.Date) < ?
      ),
      last_n AS (SELECT mid, tid FROM ranked WHERE rn<=?)
      SELECT l.tid,
             AVG(json_extract(t.extra_json, '$.{x_key}')) x,
             AVG(json_extract(t.extra_json, '$.{y_key}')) y
        FROM last_n l
        JOIN fact_team_match_stats t ON t.Match_ID=l.mid AND t.Team_ID=l.tid AND t.Period='All'
       GROUP BY l.tid
    """
    rows = conn_core.execute(sql, (league_id, season, before_date, window)).fetchall()
    return {int(r["tid"]): {"x": r["x"], "y": r["y"]} for r in rows}


def _xg_for_against_points(
    conn_core: sqlite3.Connection, league_id: int, season: str, before_date: str, window: int,
) -> dict[int, dict[str, float | None]]:
    """场均创造 xG(自身)× 场均让出 xG(同场对手)。需要按主客定位对手,单独实现。"""
    sql = """
      WITH ranked AS (
        SELECT m.Match_ID mid, t.Team_ID tid, m.Home_Team_ID home_id, m.Away_Team_ID away_id,
               ROW_NUMBER() OVER (
                 PARTITION BY t.Team_ID
                 ORDER BY COALESCE(m.kickoff_at_utc, m.Date) DESC, m.Match_ID DESC
               ) rn
          FROM dim_match m
          JOIN fact_team_match_stats t ON t.Match_ID=m.Match_ID AND t.Period='All'
         WHERE m.League_ID=? AND m.Season=? AND m.status IN ('Finish','Finished')
           AND COALESCE(m.kickoff_at_utc, m.Date) < ?
      ),
      last_n AS (SELECT mid, tid, home_id, away_id FROM ranked WHERE rn<=?)
      SELECT l.tid,
             AVG(json_extract(t_own.extra_json, '$.expected_goals')) x,
             AVG(json_extract(t_opp.extra_json, '$.expected_goals')) y
        FROM last_n l
        JOIN fact_team_match_stats t_own ON t_own.Match_ID=l.mid AND t_own.Team_ID=l.tid AND t_own.Period='All'
        JOIN fact_team_match_stats t_opp ON t_opp.Match_ID=l.mid
         AND t_opp.Team_ID = (CASE WHEN l.tid=l.home_id THEN l.away_id ELSE l.home_id END)
         AND t_opp.Period='All'
       GROUP BY l.tid
    """
    rows = conn_core.execute(sql, (league_id, season, before_date, window)).fetchall()
    return {int(r["tid"]): {"x": r["x"], "y": r["y"]} for r in rows}


def league_style_views(
    conn_core: sqlite3.Connection, league_id: int, season: str, before_date: str,
    window: int = WINDOW,
) -> list[dict[str, Any]]:
    """整个联赛近 window 场的三个风格视角,每个视角是全联赛球队的散点集合。

    `season` 圈定"全联赛"具体是哪个赛季(dim_match.League_ID 跨赛季持久,
    仅按 league_id 会把历史上打过这个联赛、此后已降级的球队也算进来——
    2026-08-25 真实事故,见文件头部说明)。

    有效点(x、y 都非空)不足 4 支球队时,该视角仍然返回(点集可能很小甚至为
    空)——是否禁用交给调用方按 §4 的门槛判断,本函数只如实聚合。
    """
    fastbreak = _fastbreak_share_by_team(conn_core, league_id, season, before_date, window)

    # 2026-08-19 性能修复:先把三个视角各自的 points_map 都算出来,再用它们
    # team_id 的并集去查译名——team_display_map() 每次都全扫 dim_match
    # (33,868 行)求全部 304 支球队的译名,而这里最多只用得上一个联赛的
    # 十几到二十支球队。改成两遍循环(先聚合、再拼名字)是为了让
    # team_display_for 的收窄范围与"最终真的会用到的 team_id"完全一致,
    # 不猜、不用 fact_league_table 这类可能覆盖不全的替代来源。
    view_points: list[tuple[dict[str, Any], dict[int, dict[str, Any]]]] = []
    all_team_ids: set[int] = set()
    for view in _TEAM_STAT_VIEWS:
        if view["id"] == "xg-for-against":
            points_map = _xg_for_against_points(conn_core, league_id, season, before_date, window)
        elif view["id"] == "poss-fastbreak":
            poss = _single_team_stat(
                conn_core, league_id, season, before_date, "BallPossesion", window
            )
            points_map = {
                tid: {"x": v, "y": fastbreak.get(tid)} for tid, v in poss.items()
            }
        else:  # cross-box
            points_map = _team_stat_points(
                conn_core, league_id, season, before_date, "accurate_crosses", "touches_opp_box",
                window,
            )
        view_points.append((view, points_map))
        all_team_ids.update(points_map.keys())

    display = team_display_for(conn_core, all_team_ids)
    # 队徽按球队并集一次性解析(resolve_team_crest_url 会读本地文件),
    # 不在每个视角的循环里逐队重复调用——三个视角的球队集合几乎完全重叠。
    crest_map = {tid: resolve_team_crest_url("fotmob", tid) for tid in all_team_ids}

    out: list[dict[str, Any]] = []
    for view, points_map in view_points:
        points = []
        for team_id, xy in points_map.items():
            points.append({
                "team_id": team_id,
                "name": display_name_for_team(team_id, display=display),
                "crest_url": crest_map.get(team_id),
                "x": round(xy["x"], view["digits"]) if isinstance(xy["x"], (int, float)) else None,
                "y": round(xy["y"], view["digits"]) if isinstance(xy["y"], (int, float)) else None,
            })
        out.append({
            "id": view["id"], "tab": view["tab"], "title": view["title"],
            "x_label": view["x_label"], "y_label": view["y_label"], "digits": view["digits"],
            "quadrants": view["quadrants"], "points": points,
            # 2026-09 真实缺陷修复:卡片头曾经写死"每队 5 场",与卡片上方
            # windowNote 的真实窗口(可能因为 team_window_bounds 样本不足而
            # 少于 WINDOW)不一致,同一张卡里两个矛盾的样本量。原样透出调用
            # 该函数时使用的 window 参数,前端不再自己猜。
            "window": window,
            # 方向语义必须端到端传播到 DTO/前端(不能只在这个模块内部知道)——
            # 前端 quadOf() 靠这个字段判断"y 高是不是好",不能假定越高越好。
            "y_lower_is_better": view.get("y_lower_is_better", False),
        })
    return out


def _fastbreak_share_by_team(
    conn_core: sqlite3.Connection, league_id: int, season: str, before_date: str, window: int
) -> dict[int, float | None]:
    """近 N 场反击射门占该队总射门的百分比(fact_shotmap.Situation='FastBreak')。
    该队近 N 场完全没有射门记录时不出现在返回字典里(不是 0%——没有分母)。
    """
    sql = """
      WITH ranked AS (
        SELECT m.Match_ID mid, t.Team_ID tid,
               ROW_NUMBER() OVER (
                 PARTITION BY t.Team_ID
                 ORDER BY COALESCE(m.kickoff_at_utc, m.Date) DESC, m.Match_ID DESC
               ) rn
          FROM dim_match m
          JOIN fact_team_match_stats t ON t.Match_ID=m.Match_ID AND t.Period='All'
         WHERE m.League_ID=? AND m.Season=? AND m.status IN ('Finish','Finished')
           AND COALESCE(m.kickoff_at_utc, m.Date) < ?
      ),
      last_n AS (SELECT mid, tid FROM ranked WHERE rn<=?)
      SELECT l.tid,
             COUNT(*) shots,
             SUM(CASE WHEN f.Situation='FastBreak' THEN 1 ELSE 0 END) fastbreak
        FROM last_n l
        JOIN fact_shotmap f ON f.Match_ID=l.mid AND f.Team_ID=l.tid
       GROUP BY l.tid
    """
    rows = conn_core.execute(sql, (league_id, season, before_date, window)).fetchall()
    return {
        int(r["tid"]): round(100.0 * r["fastbreak"] / r["shots"], 1)
        for r in rows if r["shots"]
    }


def team_window_bounds(
    conn_core: sqlite3.Connection, team_id: int, league_id: int, season: str, before_date: str,
    window: int = WINDOW,
) -> dict[str, Any] | None:
    """该队近 window 场同联赛同赛季比赛的最早/最晚比赛日期——"近 N 场"必须标
    真实日期区间(CLAUDE.md §11.2),不能只写"近 5 场"三个字了事。样本不足时
    仍返回实际找到的场次数(可能 < window),不是 None(部分历史也是真历史,
    但不跨赛季借数据——见文件头部 2026-08-25 修复说明)。
    """
    rows = conn_core.execute(
        """SELECT Date FROM dim_match
             WHERE League_ID=? AND Season=? AND status IN ('Finish','Finished')
             AND COALESCE(kickoff_at_utc, Date) < ? AND (Home_Team_ID=? OR Away_Team_ID=?)
           ORDER BY COALESCE(kickoff_at_utc, Date) DESC, Match_ID DESC LIMIT ?""",
        (league_id, season, before_date, team_id, team_id, window),
    ).fetchall()
    if not rows:
        return None
    dates = [r[0] for r in rows]
    return {"matches": len(dates), "from": min(dates), "to": max(dates)}


def team_attack_sources(
    conn_core: sqlite3.Connection, team_id: int, league_id: int | None, season: str | None,
    before_date: str, window: int = WINDOW,
) -> list[dict[str, Any]]:
    """近 window 场按 Situation 拆解的射门来源:次数 + 占比 + xG(缺失给 None,不补 0)。

    只返回该队真实出现过的来源(不凑满全部 8 种);按射门数降序排列。
    该队近 window 场完全没有射门记录时返回空列表。

    `league_id=None`(欧战等跨联赛赛事)时不限赛事取近 window 场。`season`
    必须一并置 None:跨联赛比赛两队的赛季串本来就对不上(欧冠 "2026/2027"
    vs 挪超 "2026"),留着按赛季过滤会把该队的比赛全筛没。这个模块只算单队
    自己的射门构成、不需要任何联赛分布,所以放宽后语义完全成立。
    """
    league_clause = "m.League_ID=? AND " if league_id is not None else ""
    season_clause = "m.Season=? AND " if season is not None else ""
    scope_params = [p for p in (league_id, season) if p is not None]
    rows = conn_core.execute(
        f"""
        WITH ranked AS (
          SELECT m.Match_ID mid,
                 ROW_NUMBER() OVER (
                   ORDER BY COALESCE(m.kickoff_at_utc, m.Date) DESC, m.Match_ID DESC
                 ) rn
            FROM dim_match m
           WHERE {league_clause}{season_clause}m.status IN ('Finish','Finished')
             AND COALESCE(m.kickoff_at_utc, m.Date) < ?
             AND (m.Home_Team_ID=? OR m.Away_Team_ID=?)
        ),
        last_n AS (SELECT mid FROM ranked WHERE rn<=?)
        SELECT f.Situation situation, COUNT(*) shots,
               SUM(CASE WHEN f.xG IS NOT NULL THEN f.xG ELSE 0 END) xg_sum,
               SUM(CASE WHEN f.xG IS NOT NULL THEN 1 ELSE 0 END) xg_n
          FROM last_n l JOIN fact_shotmap f ON f.Match_ID=l.mid AND f.Team_ID=?
         GROUP BY f.Situation
         ORDER BY shots DESC
        """,
        (*scope_params, before_date, team_id, team_id, window, team_id),
    ).fetchall()
    total_shots = sum(r["shots"] for r in rows) or 1
    out = []
    for r in rows:
        situation = r["situation"] or "其他"
        out.append({
            "key": situation,
            "label": _SITUATION_ZH.get(situation, situation),
            "shots": r["shots"],
            "shot_pct": round(100.0 * r["shots"] / total_shots, 1),
            # 该来源里有射门缺 xG 时(理论上少见),只对有 xG 的那些求和会低估——
            # 只有该来源全部射门都有 xG 时才给数值,否则诚实给 None。
            "xg": round(r["xg_sum"], 2) if r["xg_n"] == r["shots"] and r["shots"] > 0 else None,
        })
    return out
