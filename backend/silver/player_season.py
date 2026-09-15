"""silver_player_season + silver_player_season_ratios 的赛季聚合(2026-09-15,
联赛球员象限图)。

与 backend/silver/ratio_metrics.py(球队侧)故意分开,不塞进同一个文件:
那套 spec 的 self/opponent 自连接是为 fact_team_match_stats.extra_json(半
结构化 JSON)写的;fact_player_match_stats 是真实列的宽表,SQL 生成器的形状
完全不同,混在一起会把两套逻辑搅在一起。

零值语义(2026-09-15 生产库实测,见方案文档"第 0 步"):FotMob 对 xA/xG/对抗/
三区传球这批"事件条件性"字段**省略零值键而不是写 0**,且缺失是逐球员的
(不存在"整场比赛所有球员都缺"的情况)。本文件按列独立 SUM()(SQL 的 SUM
天然跳过 NULL,不传播 NULL),再在外层把多个独立 SUM 相加——这与
"COALESCE(x,0) 后再相加"数值等价,但不需要在每一行上做 CASE 判断,也不会
出现"一个字段缺失导致整行被当整场排除"的连坐问题(球队侧的逐场配对模型
需要这种连坐,是因为要求分子分母同场;球员侧的 per-90/season 比率不需要
这种严格配对,只要季度总量准)。

⚠ 带点号的列名 `matchstats.headers.tackles` 是**真实 SQL 列名**(不是 JSON
key),必须用 schema.py::_quote() 双引号包裹,否则 SQLite 会把点号解析成
"表名.列名"语法,报 "no such column" 或（更危险的）静默匹配到错误的列。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Literal

from backend.schema import _quote
from backend.silver.ratio_metrics import season_start_year

Denominator = Literal["minutes"] | tuple[str, ...]

# accurate_passes_total(传球尝试总数,算传球成功率/长传占比必须要有的分母)
# 2026-09-16 生产实测:2020/2021~2025/2026 五大联赛门将行几乎 0% 覆盖
# (历史上这个字段等同不存在),2026/2027 赛季陡然跳到 75%~100%——不是逐步
# 变好,是这个新赛季 FotMob 才开始可靠下发这个字段,全部位置(不只门将)
# 同一时间点生效。与 touches_opp_box 同一种"数据源在某个时间点后才可信"
# 的模式,但分界点不同(这次是"这个新赛季"而不是某个历史年份),复用同一套
# season_start_year 判定式,门槛先设 2026——等未来真的出现 2027/2028 等
# 赛季且实测同样可信时,这个判定式不需要改代码就能自动覆盖。
PASS_COMPLETION_ELIGIBLE_FROM_YEAR = 2026


def pass_completion_eligible(season: str) -> bool:
    year = season_start_year(season)
    return year is not None and year >= PASS_COMPLETION_ELIGIBLE_FROM_YEAR


@dataclass(frozen=True)
class PlayerMetricSpec:
    metric_key: str
    numerator: tuple[str, ...]  # fact_player_match_stats 的真实列名,同一行内相加
    denominator: Denominator  # "minutes" = per-90(分母存 SUM(minutes_played));
    # 元组 = 比率(分母存这些列各自 SUM 后相加)
    numerator_minus: tuple[str, ...] = field(default_factory=tuple)
    methodology_version: str = "v1"
    min_minutes_played_only: bool = False  # 目前未使用,占位对齐团队侧命名习惯
    # None = 所有赛季都算;给定判定函数则只在返回 True 的赛季计算,其它赛季
    # 这个 spec 完全不产出行——与 backend/silver/ratio_metrics.py::RatioSpec
    # 同一套设计取舍(可调用对象而不是固定 frozenset,避免清单式配置随新赛季
    # 持续过期)。
    eligible_seasons: Callable[[str], bool] | None = None


PLAYER_METRIC_SPECS: list[PlayerMetricSpec] = [
    # 射门与终结:x 轴。npxG 直接是 fact_player_match_stats 的真实列
    # (expected_goals_non_penalty),不需要 shotmap。
    PlayerMetricSpec(
        metric_key="npxg_per90",
        numerator=("expected_goals_non_penalty",),
        denominator="minutes",
    ),
    # 进攻创造力:x 轴——每 90 分钟创造机会数。
    PlayerMetricSpec(
        metric_key="chances_created_per90",
        numerator=("chances_created",),
        denominator="minutes",
    ),
    # 进攻创造力:y 轴——每 90 分钟预期助攻。
    PlayerMetricSpec(
        metric_key="xa_per90",
        numerator=("expected_assists",),
        denominator="minutes",
    ),
    # 防守贡献:x 轴——行业标准 CBIRT(Clearances+Blocks+Interceptions+
    # Recoveries+Tackles,FPL/Opta 的 Defensive Contribution 口径)。界面上
    # 不得自称 PPDA——那是球队侧 def_action_density 已有的命名红线,这里
    # 同样不适用(CBIRT 本身就是公开的行业术语,不是本站发明的代理指标)。
    PlayerMetricSpec(
        metric_key="defensive_actions_per90",
        numerator=(_quote("matchstats.headers.tackles"), "interceptions", "clearances", "shot_blocks", "recoveries"),
        denominator="minutes",
    ),
    # 防守贡献:y 轴——对抗成功率。
    PlayerMetricSpec(
        metric_key="duel_win_rate",
        numerator=("duel_won",),
        denominator=("duel_won", "duel_lost"),
    ),
    # 持球推进:x 轴——每 90 分钟触球数。
    PlayerMetricSpec(
        metric_key="touches_per90",
        numerator=("touches",),
        denominator="minutes",
    ),
    # 持球推进:y 轴——每百次触球送进前场的传球数。
    PlayerMetricSpec(
        metric_key="progression_rate",
        numerator=("passes_into_final_third",),
        denominator=("touches",),
    ),
    # 门将:x 轴——每 90 分钟面对射正预期进球(承压程度)。
    PlayerMetricSpec(
        metric_key="xgot_faced_per90",
        numerator=("expected_goals_on_target_faced",),
        denominator="minutes",
    ),
    # 门将:y 轴——FotMob 自己算好的 goals_prevented(不要用 xGOT−失球 重推,
    # 实测两者对部分门将差 2~4 球,是点球口径差异;goals_prevented 对真正的
    # 门将实测 ~100% 填充,见方案文档)。
    PlayerMetricSpec(
        metric_key="goals_prevented_per90",
        numerator=("goals_prevented",),
        denominator="minutes",
    ),
    # 门将出球:x 轴——传球成功率。仅 2026/2027 起可信(accurate_passes_total
    # 历史上五大联赛门将行几乎 0% 覆盖,这个新赛季陡然跳到 75%~100%)。
    PlayerMetricSpec(
        metric_key="pass_completion_rate",
        numerator=("accurate_passes",),
        denominator=("accurate_passes_total",),
        eligible_seasons=pass_completion_eligible,
    ),
    # 门将出球:y 轴——长传占全部传球的比例。与 x 轴共用同一个分母
    # (accurate_passes_total),不会出现"两种口径打架"的问题;已实测
    # long_balls_accurate 从不超过 accurate_passes_total。
    PlayerMetricSpec(
        metric_key="long_ball_share",
        numerator=("long_balls_accurate",),
        denominator=("accurate_passes_total",),
        eligible_seasons=pass_completion_eligible,
    ),
]


def _sum_expr(columns: tuple[str, ...]) -> str:
    """多个列同一行内相加(用于 numerator/denominator 是元组的情况)。
    统一加 `p.` 前缀:虽然这些列名与 dim_match 的列名没有实际冲突(靠
    SQLite 的隐式消歧也能跑通),但显式前缀更稳妥,不依赖"恰好不冲突"
    这个脆弱的隐含前提。"""
    return " + ".join(f"COALESCE(p.{c}, 0)" for c in columns)


def _rows_for_spec(
    conn: sqlite3.Connection, league_id: int, season: str, spec: PlayerMetricSpec
) -> list[tuple]:
    """返回 (Player_ID, numerator_sum, denominator_sum, paired_matches) 元组列表。

    与球队侧的"逐场配对"模型不同(见本文件头注释):这里不要求分子分母
    在同一场都非空,只要求球员本场确实有出场记录(minutes_played 100% 非空,
    见方案文档实测)。COALESCE(x,0) 用在**多列相加**的场景(CBIRT 五项、
    duel_won+duel_lost 这类分母),单列的 numerator/denominator 直接
    SUM(column)——SQL 的 SUM 天然跳过 NULL,与 SUM(COALESCE(x,0)) 数值
    等价,不需要额外包一层 COALESCE。
    """
    if len(spec.numerator) == 1 and not spec.numerator_minus:
        num_expr = f"SUM(p.{spec.numerator[0]})"
    else:
        plus = " + ".join(f"COALESCE(p.{c}, 0)" for c in spec.numerator)
        if spec.numerator_minus:
            minus = " + ".join(f"COALESCE(p.{c}, 0)" for c in spec.numerator_minus)
            num_expr = f"SUM({plus} - ({minus}))"
        else:
            num_expr = f"SUM({plus})"

    if spec.denominator == "minutes":
        den_expr = "SUM(p.minutes_played)"
    elif len(spec.denominator) == 1:
        den_expr = f"SUM(p.{spec.denominator[0]})"
    else:
        den_expr = f"SUM({_sum_expr(spec.denominator)})"

    sql = f"""
        SELECT p.Player_ID AS Player_ID,
               {num_expr} AS numerator_sum,
               {den_expr} AS denominator_sum,
               COUNT(*) AS paired_matches
        FROM fact_player_match_stats p
        JOIN dim_match dm ON dm.Match_ID = p.Match_ID
        WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
        GROUP BY p.Player_ID
        HAVING denominator_sum IS NOT NULL AND denominator_sum > 0
    """
    return conn.execute(sql, (league_id, season)).fetchall()


def _rows_for_finishing_delta(
    conn: sqlite3.Connection, league_id: int, season: str
) -> list[tuple]:
    """场均(非点球进球 − 非点球xG)per-90:与球队侧 finishing_delta 同一个
    "受益方计乌龙" + "扣点球" 的思路,但这里点球扣减直接走
    fact_player_match_stats.goals 逐场求和后,再统一减去该球员整季的点球
    进球数(从 fact_shotmap 按 Player_ID+Situation='Penalty'+Outcome='Goal'
    精确聚合,生产实测球员级点球可精确统计,如某球员 9 罚 8 中)。

    不走"乌龙球受益方推断"那一套(fact_team_match_stats.Goals 核心列已经是
    球队维度处理过乌龙球的口径;但 fact_player_match_stats.goals 是**球员
    自己的进球数**,乌龙球的"受益方"概念不适用于球员个人统计——球员乌龙球
    不计入自己的 goals,FotMob 的 goals 字段本来就已经是"该球员本人打进的
    球",这条不需要额外处理。唯一要扣的是点球(点球不是"终结能力"的一部分,
    与球队侧同一理由)。
    """
    sql = """
        SELECT p.Player_ID AS Player_ID,
               SUM(p.goals) - COALESCE((
                   SELECT COUNT(*) FROM fact_shotmap s
                   JOIN dim_match dm2 ON dm2.Match_ID = s.Match_ID
                   WHERE s.Player_ID = p.Player_ID
                     AND dm2.status = 'Finish' AND dm2.League_ID = ? AND dm2.Season = ?
                     AND s.Situation = 'Penalty' AND s.Outcome = 'Goal'
               ), 0) - SUM(p.expected_goals_non_penalty) AS numerator_sum,
               SUM(p.minutes_played) AS denominator_sum,
               COUNT(*) AS paired_matches
        FROM fact_player_match_stats p
        JOIN dim_match dm ON dm.Match_ID = p.Match_ID
        WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
        GROUP BY p.Player_ID
        HAVING denominator_sum IS NOT NULL AND denominator_sum > 0
    """
    return conn.execute(sql, (league_id, season, league_id, season)).fetchall()


FINISHING_DELTA_METRIC_KEY = "finishing_delta_per90"


def build_player_season_ratios(conn: sqlite3.Connection, league_id: int, season: str) -> list[dict]:
    """`silver_player_season_ratios` 的行,按 `PLAYER_METRIC_SPECS` + 终结超额
    (需要跨表扣点球,单独处理)逐个指标聚合。"""
    out: list[dict] = []
    for spec in PLAYER_METRIC_SPECS:
        if spec.eligible_seasons is not None and not spec.eligible_seasons(season):
            continue  # 该赛季数据源本身不可信(如 accurate_passes_total 历史几乎全无),完全不产出行
        for player_id, numerator_sum, denominator_sum, paired_matches in _rows_for_spec(
            conn, league_id, season, spec
        ):
            out.append(
                {
                    "League_ID": league_id,
                    "Season": season,
                    "Player_ID": player_id,
                    "metric_key": spec.metric_key,
                    "numerator_sum": numerator_sum,
                    "denominator_sum": denominator_sum,
                    "paired_matches": paired_matches,
                    "methodology_version": spec.methodology_version,
                }
            )
    for player_id, numerator_sum, denominator_sum, paired_matches in _rows_for_finishing_delta(
        conn, league_id, season
    ):
        out.append(
            {
                "League_ID": league_id,
                "Season": season,
                "Player_ID": player_id,
                "metric_key": FINISHING_DELTA_METRIC_KEY,
                "numerator_sum": numerator_sum,
                "denominator_sum": denominator_sum,
                "paired_matches": paired_matches,
                "methodology_version": "v1",
            }
        )
    return out


def build_player_season_stats(conn: sqlite3.Connection, league_id: int, season: str) -> list[dict]:
    """`silver_player_season` 的行:身份 + 出场门槛维度。

    team_minutes(分母)= MAX over 该球员出场过的每支球队(该队该赛季已完赛
    场次 × 90)。Team_ID(显示用)= 出场分钟最多的那支队。两者刻意不同源
    (见方案文档"转会球员的分母口径"),转会球员的分母比"主队"更严格。
    """
    sql = """
        WITH fin AS (
            SELECT Match_ID, Home_Team_ID, Away_Team_ID FROM dim_match
            WHERE League_ID = ? AND Season = ? AND status = 'Finish'
        ),
        team_rounds AS (
            SELECT tid, COUNT(*) AS rounds FROM (
                SELECT Home_Team_ID AS tid FROM fin
                UNION ALL SELECT Away_Team_ID AS tid FROM fin
            ) GROUP BY tid
        ),
        pt AS (
            SELECT p.Player_ID AS Player_ID, p.Team_ID AS Team_ID,
                   SUM(p.minutes_played) AS team_minutes_played,
                   ROW_NUMBER() OVER (
                       PARTITION BY p.Player_ID ORDER BY SUM(p.minutes_played) DESC
                   ) AS rn
            FROM fact_player_match_stats p
            JOIN fin f ON f.Match_ID = p.Match_ID
            GROUP BY p.Player_ID, p.Team_ID
        ),
        primary_team AS (
            SELECT Player_ID, Team_ID FROM pt WHERE rn = 1
        ),
        team_avail AS (
            SELECT pt.Player_ID AS Player_ID,
                   MAX(tr.rounds * 90) AS team_minutes,
                   COUNT(DISTINCT pt.Team_ID) AS teams_count
            FROM pt JOIN team_rounds tr ON tr.tid = pt.Team_ID
            GROUP BY pt.Player_ID
        ),
        totals AS (
            SELECT p.Player_ID AS Player_ID,
                   MAX(p.player_name) AS player_name,
                   CAST(MAX(p.usual_position) AS INTEGER) AS usual_position,
                   COUNT(*) AS appearances,
                   SUM(p.minutes_played) AS minutes_played
            FROM fact_player_match_stats p
            JOIN fin f ON f.Match_ID = p.Match_ID
            GROUP BY p.Player_ID
        )
        SELECT t.Player_ID, pr.Team_ID, t.player_name, t.usual_position,
               t.appearances, t.minutes_played, ta.team_minutes, ta.teams_count
        FROM totals t
        JOIN primary_team pr ON pr.Player_ID = t.Player_ID
        JOIN team_avail ta ON ta.Player_ID = t.Player_ID
    """
    rows = conn.execute(sql, (league_id, season)).fetchall()
    out = []
    for r in rows:
        player_id, team_id, player_name, usual_position, appearances, minutes_played, team_minutes, teams_count = r
        minutes_share = (
            round(1.0 * minutes_played / team_minutes, 4) if team_minutes else None
        )
        out.append(
            {
                "League_ID": league_id,
                "Season": season,
                "Player_ID": player_id,
                "Team_ID": team_id,
                "player_name": player_name,
                "usual_position": usual_position,
                "appearances": appearances,
                "minutes_played": minutes_played,
                "team_minutes": team_minutes,
                "minutes_share": minutes_share,
                "teams_count": teams_count,
            }
        )
    return out
