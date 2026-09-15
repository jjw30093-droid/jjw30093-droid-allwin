"""silver_team_season_ratios 的指标 spec 声明 + SQL 聚合(2026-09-14,球队象限图
复合指标扩展)。

设计取舍(详见 CLAUDE.md 之外、本次方案 docs 的记录):窄长表 + 具名 DTO 字段,
不是"每加一个比率就多一列/多一次 migration"。新增比率只需要:
1. 在 `backend/metrics/registry.py` 登记该 canonical_key(§一 的既有纪律:
   不把数据库字段直接推到页面);
2. 这里加一行 `RatioSpec`;
3. `backend/api/schemas.py` 的 `TeamSeasonRatios` 加一个具名字段。
不需要新的 migration。

配对纪律与 `backend/queries/attack_chain.py::_ratio_field()` 同一节奏,只是把
窗口尺度换成赛季尺度:分子分母必须取自**同一场**,逐场配对后再对整个赛季求和;
任一侧缺失或分母 <= 0 的场次整场不计入配对,不产生虚假的"0 分之几"。

⚠ dotted key 陷阱:`matchstats.headers.tackles` 含字面点号。SQLite 的
`json_extract(extra_json, '$.' || ?)` 对这种 key 会静默返回 NULL 而不报错
(`.` 在 JSON path 语法里是分隔符,不是字面字符)。本模块的 SQL 一律用
`'$."' || key || '"'`(引号包裹整个 key)取值,`_ratio_field` 本身这次不改
(它目前收不到带点号的 key),但这里必须处理对。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Literal

from backend.queries.window import match_window_join_sql

Source = Literal["self", "opponent", "self_plus_opponent", "self_over_opponent"]


def season_start_year(season: str) -> int | None:
    """从 "2024"/"2024/2025" 两种真实赛季字符串格式里取起始年份;解析不出来
    返回 None(调用方按"不合法"处理,不猜)。"""
    prefix = season[:4]
    return int(prefix) if prefix.isdigit() else None


# touches_opp_box 实测 2024 年起(含跨年赛制的"2024"/"2024/2025"及以后)
# 覆盖率稳定在 98%~99.8%,2024 之前的赛季是随机缺失(13%~87% 不等),球队
# 之间不可比——依赖它的指标必须显式按赛季关停,不能让"缺失"悄悄被
# _rows_for_spec 的 NULL 排除逻辑当成个别场次的正常缺配对(那样球队间的
# 可比性问题不会被发现)。
#
# 2026-09-16 真实反馈修正:此前这里是硬编码的两元素 frozenset
# `{"2024/2025", "2025/2026"}`,build_silver.py 里还独立重复声明了一份同名
# 常量——站长追问"技术只会变多不会变少,26-27赛季这个字段怎么会没有",生产
# 实测证实 2024/2024-2025/2025/2025-2026/2026/2026-2027 全部 98%+ 覆盖,
# 两份 frozenset 都漏掉了除最初两个赛季外的一切,不是数据源真的退化了,是
# 一份会随每个新赛季持续过期的清单。改成"起始年份 >= 这个阈值"的判定式后,
# build_silver.py 直接从本模块 import 这个函数(它已经 import
# build_team_season_ratios,不存在反向依赖,当初"避免循环 import 故独立声明
# 一份"的顾虑是不成立的),两处重复声明合并成一份,新赛季到来也不需要再改
# 代码。
TOUCHES_OPP_BOX_ELIGIBLE_FROM_YEAR = 2024


def touches_opp_box_eligible(season: str) -> bool:
    year = season_start_year(season)
    return year is not None and year >= TOUCHES_OPP_BOX_ELIGIBLE_FROM_YEAR


@dataclass(frozen=True)
class RatioSpec:
    metric_key: str  # 必须已在 backend/metrics/registry.py 登记(canonical_key)
    # self = 本队 extra_json;opponent = 同场对手 extra_json(自连接);
    # self_plus_opponent = 分子只取本队,分母取"本队+对手"之和(争顶优势这类
    # "我方成功 / 全场总量"的口径——全场总量没有单独字段,只能是两队各自
    # 同名字段相加);self_over_opponent = 分子取本队,分母独立取对手
    # (防守动作密度这类"我方动作 / 对手传球量"的口径,分子分母来自不同队、
    # 不同字段,不能相加)。
    source: Source
    numerator: tuple[str, ...]  # fact_team_match_stats.extra_json 的 key;多个则同一行内相加
    denominator: tuple[str, ...]
    methodology_version: str
    # 减法支持(如"绝佳机会把握率" = (大机会−错失大机会)/大机会):
    # 分子/分母的最终表达式是 sum(numerator/denominator keys) - sum(*_minus keys)。
    # 默认空元组,不影响现有 spec 的行为。source="self_plus_opponent" 时不支持
    # *_minus(目前没有指标同时需要两种复杂度,用到时再扩)。
    numerator_minus: tuple[str, ...] = field(default_factory=tuple)
    denominator_minus: tuple[str, ...] = field(default_factory=tuple)
    # None = 所有赛季都算;给定判定函数则只在返回 True 的赛季计算,其它赛季
    # 这个 spec 完全不产出行(不是产出后前端再隐藏——数据源本身在那些赛季就
    # 不可比)。用可调用对象而不是固定 frozenset,是因为"哪些赛季数据可比"
    # 天然是"从某个时间点起"这种开区间判断,固定集合会随每个新赛季持续过期
    # (见 touches_opp_box_eligible 的真实教训)。
    eligible_seasons: Callable[[str], bool] | None = None


# 包一(机制打通)接的第一个指标:opp_half_pass_share,顺带用改正后的分母
# (accurate_passes,不是 passes——见 backend/queries/attack_chain.py 同一天的改动)。
# 后续每新增一个比率型视角,在此列表追加一行即可,不需要新的 migration。
RATIO_SPECS: list[RatioSpec] = [
    RatioSpec(
        metric_key="opp_half_pass_share",
        source="self",
        numerator=("opposition_half_passes",),
        denominator=("accurate_passes",),
        methodology_version="v2",
    ),
    # 包二:站长点名的方向——定位球 xG 占比。分母是"运动战 xG + 定位球 xG"
    # 两项相加,不是 expected_goals_non_penalty 那个字段:实测两者在 99.7%
    # 场次上一致(±0.015,三值各存两位小数的舍入上限),但走"两项相加"能让
    # "定位球占比 + 运动战占比"在数值上恒为 100%,不需要额外解释"为什么加起来
    # 不到一百"(这条解释本身依赖 expected_goals_non_penalty 那 0.3% 的差异,
    # 没有实用价值)。denominator 是二元组,_sum_expr 天然支持同一行内相加。
    RatioSpec(
        metric_key="set_piece_xg_share",
        source="self",
        numerator=("expected_goals_set_play",),
        denominator=("expected_goals_open_play", "expected_goals_set_play"),
        methodology_version="v1",
    ),
    # 失球端定位球占比:同一个比率,但取自对手行(opponent 自连接)——
    # "对手用定位球打进我们多少比例的威胁"，与站长选定的口径同构。
    RatioSpec(
        metric_key="set_piece_xga_share",
        source="opponent",
        numerator=("expected_goals_set_play",),
        denominator=("expected_goals_open_play", "expected_goals_set_play"),
        methodology_version="v1",
    ),
    # 包三:对手每脚射门 xG——"对手射门质量",用于「攻防质量」视角的 y 轴
    # (反转,越低越好)。与既有客户端指标 METRICS.xgPerShot(我方 xG/射门数)
    # 同一口径,只是换成对手行,方便直接对照。
    RatioSpec(
        metric_key="opp_xg_per_shot",
        source="opponent",
        numerator=("expected_goals",),
        denominator=("total_shots",),
        methodology_version="v1",
    ),
    # 射正率:分母是"总射门 − 被封堵射门",实测 ShotsOnTarget+ShotsOffTarget+
    # blocked_shots = total_shots 精确成立(26096/26096),所以用
    # ShotsOnTarget+ShotsOffTarget 两项相加等价于"总射门−被封堵",不需要在
    # SQL 层实现减法。
    RatioSpec(
        metric_key="shot_accuracy",
        source="self",
        numerator=("ShotsOnTarget",),
        denominator=("ShotsOnTarget", "ShotsOffTarget"),
        methodology_version="v1",
    ),
    # 禁区内射门占比。
    RatioSpec(
        metric_key="box_shot_share",
        source="self",
        numerator=("shots_inside_box",),
        denominator=("shots_inside_box", "shots_outside_box"),
        methodology_version="v1",
    ),
    # 绝佳机会把握率:分子是"大机会 − 错失大机会"(实测 missed 从不超过
    # created,0/26096 反常,减法结果不会是负数)。denominator_minus 留空,
    # 分母就是 big_chance 本身。
    RatioSpec(
        metric_key="big_chance_conversion",
        source="self",
        numerator=("big_chance",),
        numerator_minus=("big_chance_missed_title",),
        denominator=("big_chance",),
        methodology_version="v1",
    ),
    # 争顶优势占比:分子只数本队的 aerials_won,分母是"本队+对手"的
    # aerials_won 之和——两队没有单独的"争顶总数"字段,只能用这种口径
    # 近似"全场争顶总量"。source="self_plus_opponent"。
    RatioSpec(
        metric_key="aerial_win_share",
        source="self_plus_opponent",
        numerator=("aerials_won",),
        denominator=("aerials_won",),
        methodology_version="v1",
    ),
    # 包四:禁区触球份额。touches_opp_box 只有这两个赛季覆盖率 100%,
    # 其余赛季随机缺失(13%~30%),球队之间不可比——eligible_seasons 显式
    # 关停其它赛季,不依赖"缺失自然被排除"这种隐式行为。
    RatioSpec(
        metric_key="box_touch_share",
        source="self_plus_opponent",
        numerator=("touches_opp_box",),
        denominator=("touches_opp_box",),
        methodology_version="v1",
        eligible_seasons=touches_opp_box_eligible,
    ),
    # 每次禁区触球 npxG:同样受 touches_opp_box 覆盖率限制。
    RatioSpec(
        metric_key="npxg_per_box_touch",
        source="self",
        numerator=("expected_goals_non_penalty",),
        denominator=("touches_opp_box",),
        methodology_version="v1",
        eligible_seasons=touches_opp_box_eligible,
    ),
    # 防守动作密度(每百次对手传球)——不是 PPDA,没有动作坐标、无法限定
    # 逼抢区域,刻意不含 clearances(解围是低位防守的产物,计入会把摆大巴
    # 伪装成压迫型)。分子本队抢断+拦截+犯规,分母对手传球总数(尝试,不是
    # accurate_passes——这里要的是"对手有多少次传球机会被我们的动作打断/
    # 伴随",不是"对手成功了多少次",用总量做分母)。source="self_over_opponent"。
    RatioSpec(
        metric_key="def_action_density",
        source="self_over_opponent",
        numerator=("matchstats.headers.tackles", "interceptions", "fouls"),
        denominator=("passes",),
        methodology_version="v1",
    ),
    # 对手前场成功传球占比("阵地"的另一半):与 opp_half_pass_share 同一个
    # 比率公式,但取自同场对手——"对手把球玩到我方半场的比例",配合
    # def_action_density 组成「阵地与拼抢」视角。
    RatioSpec(
        metric_key="opp_territory_share",
        source="opponent",
        numerator=("opposition_half_passes",),
        denominator=("accurate_passes",),
        methodology_version="v1",
    ),
]


@dataclass(frozen=True)
class ShotmapRatioSpec:
    """从 fact_shotmap(射门级)按 Situation 聚合的比率指标——与上面的
    RatioSpec(读 fact_team_match_stats.extra_json)是完全不同的表和聚合形状,
    分开声明、分开处理,不强行塞进同一个 dataclass。"""

    metric_key: str
    numerator_situations: tuple[str, ...]  # fact_shotmap.Situation 取值
    # None = 分母是全部非点球情境(与 set_piece_xg_share 的"不含点球"同惯例);
    # 显式给元组则只统计这些情境。
    denominator_situations: tuple[str, ...] | None
    methodology_version: str


SHOTMAP_RATIO_SPECS: list[ShotmapRatioSpec] = [
    # 快速反击 xG 占比:分母是全部非点球 xG(与 set_piece_xg_share 系列同一
    # "不含点球"惯例),不是"反击+运动战"这种子集——反击本来就是运动战的一种
    # 展开方式,不是与运动战并列的第三个互斥分类,子集分母会造成解释歧义。
    ShotmapRatioSpec(
        metric_key="fast_break_xg_share",
        numerator_situations=("FastBreak",),
        denominator_situations=None,
        methodology_version="v1",
    ),
]


@dataclass(frozen=True)
class MixedShotmapRatioSpec:
    """分子来自 fact_shotmap 按 Situation 的**射门次数**(不是 xG 求和),
    分母来自 fact_team_match_stats.extra_json 的某个 key——与 ShotmapRatioSpec
    (分子分母都在 shotmap 内部按 Situation 分)、RatioSpec(分子分母都在
    extra_json)都不同,是第三种聚合形状。目前只有角球成射率一个指标用它:
    分子是"由角球产生的射门次数",分母是"该场角球数"——两者一个在
    fact_shotmap、一个在 fact_team_match_stats,必须跨表按 (Match_ID,
    Team_ID) 配对,不是同一张表内的筛选。"""

    metric_key: str
    numerator_situations: tuple[str, ...]  # fact_shotmap.Situation 取值,按次数计
    denominator_extra_json_key: str  # fact_team_match_stats.extra_json 的 key
    methodology_version: str


MIXED_SHOTMAP_RATIO_SPECS: list[MixedShotmapRatioSpec] = [
    # 角球成射率:多少比例的角球真正形成了射门。分子是次数(不是 xG),分母
    # 是该场角球数——两者都是"件数",除出来的"XX/YY"本身就有直接的口径含义,
    # 前端 tooltip 按方案要求展示原始计数(如 11/48),不是只给百分比。
    MixedShotmapRatioSpec(
        metric_key="corner_shot_rate",
        numerator_situations=("FromCorner",),
        denominator_extra_json_key="corners",
        methodology_version="v1",
    ),
]


@dataclass(frozen=True)
class GoalDeltaSpec:
    """"进球数(非点球、按受益方计乌龙)− 非点球 xG"的场均差值——不是比率
    (分子分母不是同一维度的两个量相除),是"实际产出相对预期产出的场均超额"。
    目前只有终结记录(finishing_delta)一个指标用它。

    非点球进球的口径必须按受益方计乌龙球(CLAUDE.md §11.3 已有事故记录:
    FotMob 把乌龙球记在"打进自家球门那一队"名下,直接按 Team_ID 数进球会
    把乌龙球算给踢乌龙的那队,而不是真正受益的那队)。SQL 侧复刻
    backend/queries/match_report.py::_own_goal_fields 的判定优先级:采集值
    Is_Own_Goal 非 NULL 时用采集值,否则按"xG IS NULL 且 Outcome='Goal'"推断
    (全库 1022/1022 命中,已交叉验证)。点球不可能是乌龙球(Situation='Penalty'
    的行 xG 恒为常数 0.7884,从未出现过 NULL),所以"非点球"过滤只需要排除
    Situation='Penalty' 的那一支——乌龙球分支不需要再叠加这个条件。
    """

    metric_key: str
    npxg_extra_json_key: str  # fact_team_match_stats.extra_json 的 key
    methodology_version: str


GOAL_DELTA_SPECS: list[GoalDeltaSpec] = [
    # 场均终结超额:(非点球进球 − 非点球 xG) / 场次。赛季间相关性调研认为
    # 近于零(命中概率的运气成分大),象限名必须中性,不得出现"终结能力"。
    GoalDeltaSpec(
        metric_key="finishing_delta",
        npxg_extra_json_key="expected_goals_non_penalty",
        methodology_version="v1",
    ),
]


@dataclass(frozen=True)
class GkSavesAboveExpectedSpec:
    """"对手射正(非点球、非乌龙)xGOT 求和 − 非点球失球"的场均差值——正值
    表示门将扑救的比"射正质量"预期的要多。目前只有防线与门将
    (gk_saves_above_expected)一个指标用它。

    两处必须"排除缺失而非填 0"(CLAUDE.md §6.2/§11.3 的缺失语义纪律):
    ① xGOT 只在真实非 NULL 时才计入 Σ,被扑救射门实测缺失率 33.15%,缺失的
    射正整次跳过,不按 0 计入求和(那样会系统性低估门将的"预期失球");
    ② 同一份 SQL 顺带统计"纳入求和的有效射正次数"(sample_count),供前端
    公示"基于 N 次有 xGOT 的射正",不让用户误把覆盖率不足的小样本当结论。
    "非点球失球"与 GoalDeltaSpec 同一套乌龙球受益方判定,只是这里要的是
    "对手" 视角(该队被打进多少非点球球,不是该队自己进了多少)。
    """

    metric_key: str
    methodology_version: str


GK_SAVES_ABOVE_EXPECTED_SPECS: list[GkSavesAboveExpectedSpec] = [
    GkSavesAboveExpectedSpec(
        metric_key="gk_saves_above_expected",
        methodology_version="v1",
    ),
]


def _json_path(key: str) -> str:
    """带引号的 JSON path 字面量,规避 `'$.' || key` 对带点号 key 的静默失效。"""
    escaped = key.replace('"', '\\"')
    return f'$."{escaped}"'


def _sum_expr(keys: tuple[str, ...], alias: str) -> str:
    """多个 key 同一行内相加;单个 key 时就是普通取值。任一 key 缺失 → 整个表达式为 NULL
    (SQLite 里 NULL + 任何值 = NULL),配对判定据此自然生效,不需要额外 CASE。"""
    if len(keys) == 1:
        return f"json_extract({alias}.extra_json, '{_json_path(keys[0])}')"
    parts = [f"json_extract({alias}.extra_json, '{_json_path(k)}')" for k in keys]
    return "(" + " + ".join(parts) + ")"


def _diff_expr(plus: tuple[str, ...], minus: tuple[str, ...], alias: str) -> str:
    """sum(plus keys) - sum(minus keys);minus 为空时就是普通的 _sum_expr。
    减数一侧任一 key 缺失,同样让整个表达式变 NULL(NULL 参与减法结果仍是
    NULL),不会把"缺失"悄悄算成 0 从而虚增结果。"""
    if not minus:
        return _sum_expr(plus, alias)
    return f"({_sum_expr(plus, alias)} - {_sum_expr(minus, alias)})"


def _rows_for_spec(
    conn: sqlite3.Connection, league_id: int, season: str, spec: RatioSpec,
    *, window_pairs: list[tuple[int, int]] | None = None,
) -> list[tuple]:
    """返回 (Team_ID, numerator_sum, denominator_sum, paired_matches) 元组列表。

    ⚠ 用位置索引而不是 row["col"]:`backend.silver.build_silver` 用的
    `get_connection()` 不设 `conn.row_factory = sqlite3.Row`(与本文件同目录的
    `build_team_season_stats` 同样靠 `zip(col_names, r)` 手动取列名),这里
    保持同一约定,不能假设 Row 工厂已开。

    `window_pairs`(2026-09-14,球队数据页"最近 N 场/主客场"筛选新增,可选)
    —— `backend.queries.window.resolve_match_window()` 的产物,按队限定这次
    聚合只统计哪些比赛;`None`(默认)时行为与筛选功能上线前逐字节相同。
    只约束 `fts` 这一侧(本队视角),`opp` 自连接跟着 `fts.Match_ID` 自动
    继承限定,不需要对 `opp` 再加一次。"""
    window_join_sql, window_params = match_window_join_sql(
        window_pairs, team_col="fts.Team_ID", match_col="fts.Match_ID"
    )
    if spec.source == "self":
        num_expr = _diff_expr(spec.numerator, spec.numerator_minus, "fts")
        den_expr = _diff_expr(spec.denominator, spec.denominator_minus, "fts")
        sql = f"""
            SELECT t.Team_ID AS Team_ID,
                   SUM(t.num) AS numerator_sum,
                   SUM(t.den) AS denominator_sum,
                   COUNT(*) AS paired_matches
            FROM (
                SELECT fts.Team_ID AS Team_ID,
                       {num_expr} AS num,
                       {den_expr} AS den
                FROM fact_team_match_stats fts
                JOIN dim_match dm ON dm.Match_ID = fts.Match_ID
                WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
                  AND fts.Period = 'All'
                  {window_join_sql}
            ) t
            WHERE t.num IS NOT NULL AND t.den IS NOT NULL AND t.den > 0
            GROUP BY t.Team_ID
        """
    elif spec.source == "opponent":  # 自连接取同场另一队的 extra_json
        num_expr = _diff_expr(spec.numerator, spec.numerator_minus, "opp")
        den_expr = _diff_expr(spec.denominator, spec.denominator_minus, "opp")
        sql = f"""
            SELECT t.Team_ID AS Team_ID,
                   SUM(t.num) AS numerator_sum,
                   SUM(t.den) AS denominator_sum,
                   COUNT(*) AS paired_matches
            FROM (
                SELECT fts.Team_ID AS Team_ID,
                       {num_expr} AS num,
                       {den_expr} AS den
                FROM fact_team_match_stats fts
                JOIN dim_match dm ON dm.Match_ID = fts.Match_ID
                JOIN fact_team_match_stats opp
                  ON opp.Match_ID = fts.Match_ID AND opp.Team_ID != fts.Team_ID
                 AND opp.Period = 'All'
                WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
                  AND fts.Period = 'All'
                  {window_join_sql}
            ) t
            WHERE t.num IS NOT NULL AND t.den IS NOT NULL AND t.den > 0
            GROUP BY t.Team_ID
        """
    elif spec.source == "self_plus_opponent":  # 分子只取本队,分母是"本队+对手"同名字段之和
        num_expr = _diff_expr(spec.numerator, spec.numerator_minus, "fts")
        den_expr = (
            f"({_sum_expr(spec.denominator, 'fts')} + {_sum_expr(spec.denominator, 'opp')})"
        )
        sql = f"""
            SELECT t.Team_ID AS Team_ID,
                   SUM(t.num) AS numerator_sum,
                   SUM(t.den) AS denominator_sum,
                   COUNT(*) AS paired_matches
            FROM (
                SELECT fts.Team_ID AS Team_ID,
                       {num_expr} AS num,
                       {den_expr} AS den
                FROM fact_team_match_stats fts
                JOIN dim_match dm ON dm.Match_ID = fts.Match_ID
                JOIN fact_team_match_stats opp
                  ON opp.Match_ID = fts.Match_ID AND opp.Team_ID != fts.Team_ID
                 AND opp.Period = 'All'
                WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
                  AND fts.Period = 'All'
                  {window_join_sql}
            ) t
            WHERE t.num IS NOT NULL AND t.den IS NOT NULL AND t.den > 0
            GROUP BY t.Team_ID
        """
    else:  # self_over_opponent —— 分子取本队,分母独立取对手(不同队、不同字段,不相加)
        num_expr = _diff_expr(spec.numerator, spec.numerator_minus, "fts")
        den_expr = _diff_expr(spec.denominator, spec.denominator_minus, "opp")
        sql = f"""
            SELECT t.Team_ID AS Team_ID,
                   SUM(t.num) AS numerator_sum,
                   SUM(t.den) AS denominator_sum,
                   COUNT(*) AS paired_matches
            FROM (
                SELECT fts.Team_ID AS Team_ID,
                       {num_expr} AS num,
                       {den_expr} AS den
                FROM fact_team_match_stats fts
                JOIN dim_match dm ON dm.Match_ID = fts.Match_ID
                JOIN fact_team_match_stats opp
                  ON opp.Match_ID = fts.Match_ID AND opp.Team_ID != fts.Team_ID
                 AND opp.Period = 'All'
                WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
                  AND fts.Period = 'All'
                  {window_join_sql}
            ) t
            WHERE t.num IS NOT NULL AND t.den IS NOT NULL AND t.den > 0
            GROUP BY t.Team_ID
        """
    return conn.execute(sql, (league_id, season, *window_params)).fetchall()


def _rows_for_shotmap_spec(
    conn: sqlite3.Connection, league_id: int, season: str, spec: ShotmapRatioSpec,
    *, window_pairs: list[tuple[int, int]] | None = None,
) -> list[tuple]:
    """从 fact_shotmap 按 Situation 聚合,逐场求和后按队分组——同样是"先按
    (Match_ID, Team_ID) 聚合出该场的分子分母,再对整季求和"的节奏,只是这里
    分子分母来自同一张射门表按条件筛选求和,不是两个字段直接相加。

    与 extra_json 路径的一处不同:这里不需要"分子或分母缺失就整场排除"的
    NULL 判定——Situation 100% non-null(全库实测),一场比赛只要真的发生过
    非点球射门,分母就有值;真正的例外只是"这场比赛该队一脚都没射"(den=0),
    用 WHERE den>0 排除,语义上与其它 spec 的"分母<=0 不计入配对"一致。

    乌龙球处理:fact_shotmap 里 xG 为 NULL 的行 100% 是乌龙球(已验证),
    SUM() 天然跳过 NULL,不会被静默当 0 计入,也不会污染分子分母。

    `window_pairs`(2026-09-14,球队数据页筛选新增,可选)同 `_rows_for_spec`
    的同名参数——这里 join 列是 `s.Team_ID`/`s.Match_ID`(本函数主表是
    fact_shotmap,不是 fact_team_match_stats)。"""
    window_join_sql, window_params = match_window_join_sql(
        window_pairs, team_col="s.Team_ID", match_col="s.Match_ID"
    )
    num_situations = ",".join(f"'{s}'" for s in spec.numerator_situations)
    if spec.denominator_situations is None:
        den_filter = "s.Situation != 'Penalty'"
    else:
        den_values = ",".join(f"'{s}'" for s in spec.denominator_situations)
        den_filter = f"s.Situation IN ({den_values})"
    sql = f"""
        SELECT t.Team_ID AS Team_ID,
               SUM(t.num) AS numerator_sum,
               SUM(t.den) AS denominator_sum,
               COUNT(*) AS paired_matches
        FROM (
            SELECT s.Match_ID AS Match_ID, s.Team_ID AS Team_ID,
                   SUM(CASE WHEN s.Situation IN ({num_situations}) THEN s.xG ELSE 0 END) AS num,
                   SUM(CASE WHEN {den_filter} THEN s.xG ELSE 0 END) AS den
            FROM fact_shotmap s
            JOIN dim_match dm ON dm.Match_ID = s.Match_ID
            WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
              AND s.Period != 'PenaltyShootout'
              {window_join_sql}
            GROUP BY s.Match_ID, s.Team_ID
        ) t
        WHERE t.den > 0
        GROUP BY t.Team_ID
    """
    return conn.execute(sql, (league_id, season, *window_params)).fetchall()


def _rows_for_mixed_spec(
    conn: sqlite3.Connection, league_id: int, season: str, spec: MixedShotmapRatioSpec,
    *, window_pairs: list[tuple[int, int]] | None = None,
) -> list[tuple]:
    """分子(fact_shotmap 按 Situation 计次)与分母(fact_team_match_stats.
    extra_json 的某个 key)来自两张不同的表,按 (Match_ID, Team_ID) 用相关
    子查询取分子——分子是次数、天然非 NULL(没有该 Situation 的射门就是 0,
    不是缺失),真正的配对判定只需要看分母(角球数)是否存在且 >0。

    `window_pairs` 同 `_rows_for_spec` 的同名参数,约束外层的 `fts`(内层
    子查询取分子时用的是 `fts.Match_ID`/`fts.Team_ID` 相关列,天然跟着
    外层限定,不需要再约束一次)。"""
    window_join_sql, window_params = match_window_join_sql(
        window_pairs, team_col="fts.Team_ID", match_col="fts.Match_ID"
    )
    num_situations = ",".join(f"'{s}'" for s in spec.numerator_situations)
    den_expr = f"json_extract(fts.extra_json, '{_json_path(spec.denominator_extra_json_key)}')"
    sql = f"""
        SELECT t.Team_ID AS Team_ID,
               SUM(t.num) AS numerator_sum,
               SUM(t.den) AS denominator_sum,
               COUNT(*) AS paired_matches
        FROM (
            SELECT fts.Team_ID AS Team_ID,
                   (SELECT COUNT(*) FROM fact_shotmap s
                    WHERE s.Match_ID = fts.Match_ID AND s.Team_ID = fts.Team_ID
                      AND s.Situation IN ({num_situations})) AS num,
                   {den_expr} AS den
            FROM fact_team_match_stats fts
            JOIN dim_match dm ON dm.Match_ID = fts.Match_ID
            WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
              AND fts.Period = 'All'
              {window_join_sql}
        ) t
        WHERE t.den IS NOT NULL AND t.den > 0
        GROUP BY t.Team_ID
    """
    return conn.execute(sql, (league_id, season, *window_params)).fetchall()


def _non_penalty_goals_expr(team_alias: str, shot_alias: str = "s") -> str:
    """`team_alias.Goals(核心列)− 该场该队点球进球数`。

    不走"按受益方计乌龙球"的 shotmap 全量推断(那需要 Is_Own_Goal/xG IS
    NULL 的乌龙球判定,是 CLAUDE.md §11.3 明确记过事故的一类易错逻辑)——
    实测 fact_team_match_stats.Goals 这个核心列本身就已经正确处理了乌龙球
    归属(与 dim_match 的比分逐场比对一致,2026-09-14 用真实含乌龙球比赛
    验证过)。点球的分子分母都不可能是乌龙球(Situation='Penalty' 的行 xG
    恒为常数 0.7884、Team_ID 就是罚球队)。

    这不只是图省事:实测还发现 shotmap 全量推断法比 Goals 核心列**更不
    可靠**——英超 2025/2026 Match_ID=4813612(8472 主场 3:0 完胜 8191,
    Goals 核心列与 dim_match 比分一致)里,8191 队有一条 shotmap 行
    Outcome='Goal'、xG=0.0(非 NULL)、Is_Own_Goal 未采集,如果按"xG IS
    NULL 推断乌龙球"这条判据,这条行会被误判成 8191 的一个真实非点球进球
    (is_og=0,因为 0.0 不是 NULL),但真实比分是 8191 一球未进——这条
    shotmap 行大概率是 VAR 吹掉的进球,射门事件本身仍标记 Outcome='Goal'
    但没有真的计分。Goals 核心列不受这类"射门事件类型 vs 最终是否计分"
    的偏差影响,是更值得信任的真源。"""
    pen_goals_expr = (
        f"(SELECT COUNT(*) FROM fact_shotmap {shot_alias} "
        f"WHERE {shot_alias}.Match_ID = {team_alias}.Match_ID "
        f"AND {shot_alias}.Team_ID = {team_alias}.Team_ID "
        f"AND {shot_alias}.Situation = 'Penalty' AND {shot_alias}.Outcome = 'Goal')"
    )
    return f"({team_alias}.Goals - {pen_goals_expr})"


def _rows_for_goal_delta_spec(
    conn: sqlite3.Connection, league_id: int, season: str, spec: GoalDeltaSpec,
    *, window_pairs: list[tuple[int, int]] | None = None,
) -> list[tuple]:
    """返回 (Team_ID, numerator_sum, denominator_sum, paired_matches, sample_count)。
    denominator_sum 在这里就是 paired_matches 本身(每场贡献 1),"场均"展示
    直接复用既有的 value = scale × numerator_sum / denominator_sum 公式。

    sample_count 复用「场均门将扑救超额」已经加过的同一个字段,这里存的是
    "赛季累计非点球射门数"——方案要求 finishing_delta 的最小样本门槛是
    "≥15 场 ∧ ≥150 次非点球射门",后半句是射门量而不是比赛场次,match 计数
    (denominator_sum/paired_matches)本身覆盖不了,需要这个额外字段。

    `window_pairs` 同 `_rows_for_spec` 的同名参数。"""
    window_join_sql, window_params = match_window_join_sql(
        window_pairs, team_col="fts.Team_ID", match_col="fts.Match_ID"
    )
    goals_expr = _non_penalty_goals_expr("fts")
    npxg_expr = f"json_extract(fts.extra_json, '{_json_path(spec.npxg_extra_json_key)}')"
    shots_expr = (
        "(SELECT COUNT(*) FROM fact_shotmap s "
        "WHERE s.Match_ID = fts.Match_ID AND s.Team_ID = fts.Team_ID "
        "AND s.Situation != 'Penalty')"
    )
    sql = f"""
        SELECT t.Team_ID AS Team_ID,
               SUM(t.num) AS numerator_sum,
               SUM(t.den) AS denominator_sum,
               COUNT(*) AS paired_matches,
               SUM(t.shots) AS sample_count
        FROM (
            SELECT fts.Team_ID AS Team_ID,
                   ({goals_expr} - {npxg_expr}) AS num,
                   1 AS den,
                   {shots_expr} AS shots
            FROM fact_team_match_stats fts
            JOIN dim_match dm ON dm.Match_ID = fts.Match_ID
            WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
              AND fts.Period = 'All'
              {window_join_sql}
        ) t
        WHERE t.num IS NOT NULL
        GROUP BY t.Team_ID
    """
    return conn.execute(sql, (league_id, season, *window_params)).fetchall()


def _rows_for_gk_saves_spec(
    conn: sqlite3.Connection, league_id: int, season: str, spec: GkSavesAboveExpectedSpec,
    *, window_pairs: list[tuple[int, int]] | None = None,
) -> list[tuple]:
    """返回 (Team_ID, numerator_sum, denominator_sum, paired_matches,
    sample_count)——比其它聚合多一个 sample_count 列,调用方按 5 元组处理。

    `window_pairs` 同 `_rows_for_spec` 的同名参数,只约束 `fts`(本队),
    `opp` 自连接与内层射正相关子查询都跟着 `fts.Match_ID` 自动继承限定。

    "非点球失球" = 对手那场的 `_non_penalty_goals_expr`,通过自连接 opp 取
    对手行(与文件里其它 source="opponent"/"self_over_opponent" 的 spec
    同一个自连接写法)。区分两种"valid_count=0":
    ① 对手那场压根没有射正(qualifying_count=0)——xgot_sum 合法为 0,
       仍然计入(不是缺失,是真实的"零射正"结果);
    ② 对手那场有射正但一次有效 xGOT 都没有(qualifying_count>0 而
       valid_count=0,全部缺失)——这一场整场从聚合里排除(num=NULL),
       不按 0 计入,否则会把"数据缺失"读成"零预期失球",系统性夸大门将的
       扑救超额。两种情况都表现为 valid_count=0,必须另外看 qualifying_count
       才能分清,不能只用 valid_count 一个信号判断。"""
    window_join_sql, window_params = match_window_join_sql(
        window_pairs, team_col="fts.Team_ID", match_col="fts.Match_ID"
    )
    goals_against_expr = _non_penalty_goals_expr("opp")
    is_og = "COALESCE(s.Is_Own_Goal, CASE WHEN s.xG IS NULL THEN 1 ELSE 0 END)"
    # 射正 = Outcome ∈ {Goal, AttemptSaved}(Is_On_Target 列全库恒为 NULL,
    # 2026-08-23 后才计划采集但从未真正回填过,不能依赖它);非点球、非乌龙
    # 两个条件先框定"射正统计范畴"(qualifying),xGOT 非空再进一步框定
    # "计入 Σ 与 sample_count 的有效样本"(valid)——qualifying ⊇ valid。
    # 这里的乌龙球排除只是过滤统计范畴,不涉及归属判定,用 xG IS NULL
    # 简单推断足够(乌龙球射门本来就不该计入对手的射正质量)。
    qualifying_shot_filter = (
        "s.Match_ID = fts.Match_ID AND s.Team_ID != fts.Team_ID "
        "AND s.Outcome IN ('Goal','AttemptSaved') AND s.Situation != 'Penalty' "
        f"AND {is_og} = 0"
    )
    valid_shot_filter = f"{qualifying_shot_filter} AND s.xGOT IS NOT NULL"
    qualifying_count_expr = f"(SELECT COUNT(*) FROM fact_shotmap s WHERE {qualifying_shot_filter})"
    xgot_sum_expr = f"(SELECT COALESCE(SUM(s.xGOT), 0) FROM fact_shotmap s WHERE {valid_shot_filter})"
    valid_count_expr = f"(SELECT COUNT(*) FROM fact_shotmap s WHERE {valid_shot_filter})"
    sql = f"""
        SELECT t.Team_ID AS Team_ID,
               SUM(t.num) AS numerator_sum,
               SUM(t.den) AS denominator_sum,
               COUNT(*) AS paired_matches,
               SUM(t.valid_count) AS sample_count
        FROM (
            SELECT fts.Team_ID AS Team_ID,
                   CASE WHEN {qualifying_count_expr} = 0 THEN (0 - {goals_against_expr})
                        WHEN {valid_count_expr} > 0
                        THEN {xgot_sum_expr} - {goals_against_expr}
                        ELSE NULL END AS num,
                   1 AS den,
                   {valid_count_expr} AS valid_count
            FROM fact_team_match_stats fts
            JOIN dim_match dm ON dm.Match_ID = fts.Match_ID
            JOIN fact_team_match_stats opp
              ON opp.Match_ID = fts.Match_ID AND opp.Team_ID != fts.Team_ID
             AND opp.Period = 'All'
            WHERE dm.status = 'Finish' AND dm.League_ID = ? AND dm.Season = ?
              AND fts.Period = 'All'
              {window_join_sql}
        ) t
        WHERE t.num IS NOT NULL
        GROUP BY t.Team_ID
    """
    return conn.execute(sql, (league_id, season, *window_params)).fetchall()


def build_team_season_ratios(
    conn: sqlite3.Connection,
    league_id: int,
    season: str,
    matches_played_by_team: dict[int, int],
    *,
    window_pairs: list[tuple[int, int]] | None = None,
) -> list[dict]:
    """`silver_team_season_ratios` 的行,按 `RATIO_SPECS` + `SHOTMAP_RATIO_SPECS`
    逐个指标聚合(两份列表来源不同表、聚合函数不同,产出的行结构一致)。

    `matches_played_by_team` 由调用方传入(与 `build_team_season_stats` 同源,
    避免这里重复查一遍 `fact_team_match_stats` 数完赛场次)。

    `window_pairs`(2026-09-14,球队数据页"最近 N 场/主客场"筛选新增,可选)
    ——`backend.queries.window.resolve_match_window()` 的产物,原样透传给
    下面全部 5 个 `_rows_for_*` 函数。`None`(默认)时逐字节等价于筛选
    功能上线前的行为。
    """
    out: list[dict] = []
    for spec in RATIO_SPECS:
        if spec.eligible_seasons is not None and not spec.eligible_seasons(season):
            continue  # 该赛季数据源本身不可比(如 touches_opp_box 随机缺失),完全不产出行
        for team_id, numerator_sum, denominator_sum, paired_matches in _rows_for_spec(
            conn, league_id, season, spec, window_pairs=window_pairs
        ):
            out.append(
                {
                    "League_ID": league_id,
                    "Season": season,
                    "Team_ID": team_id,
                    "metric_key": spec.metric_key,
                    "numerator_sum": numerator_sum,
                    "denominator_sum": denominator_sum,
                    "paired_matches": paired_matches,
                    "matches_played": matches_played_by_team.get(team_id),
                    "methodology_version": spec.methodology_version,
                    "sample_count": None,
                }
            )
    for shotmap_spec in SHOTMAP_RATIO_SPECS:
        for team_id, numerator_sum, denominator_sum, paired_matches in _rows_for_shotmap_spec(
            conn, league_id, season, shotmap_spec, window_pairs=window_pairs
        ):
            out.append(
                {
                    "League_ID": league_id,
                    "Season": season,
                    "Team_ID": team_id,
                    "metric_key": shotmap_spec.metric_key,
                    "numerator_sum": numerator_sum,
                    "denominator_sum": denominator_sum,
                    "paired_matches": paired_matches,
                    "matches_played": matches_played_by_team.get(team_id),
                    "methodology_version": shotmap_spec.methodology_version,
                    "sample_count": None,
                }
            )
    for mixed_spec in MIXED_SHOTMAP_RATIO_SPECS:
        for team_id, numerator_sum, denominator_sum, paired_matches in _rows_for_mixed_spec(
            conn, league_id, season, mixed_spec, window_pairs=window_pairs
        ):
            out.append(
                {
                    "League_ID": league_id,
                    "Season": season,
                    "Team_ID": team_id,
                    "metric_key": mixed_spec.metric_key,
                    "numerator_sum": numerator_sum,
                    "denominator_sum": denominator_sum,
                    "paired_matches": paired_matches,
                    "matches_played": matches_played_by_team.get(team_id),
                    "methodology_version": mixed_spec.methodology_version,
                    "sample_count": None,
                }
            )
    for goal_delta_spec in GOAL_DELTA_SPECS:
        for (
            team_id,
            numerator_sum,
            denominator_sum,
            paired_matches,
            sample_count,
        ) in _rows_for_goal_delta_spec(conn, league_id, season, goal_delta_spec, window_pairs=window_pairs):
            out.append(
                {
                    "League_ID": league_id,
                    "Season": season,
                    "Team_ID": team_id,
                    "metric_key": goal_delta_spec.metric_key,
                    "numerator_sum": numerator_sum,
                    "denominator_sum": denominator_sum,
                    "paired_matches": paired_matches,
                    "matches_played": matches_played_by_team.get(team_id),
                    "methodology_version": goal_delta_spec.methodology_version,
                    "sample_count": sample_count,
                }
            )
    for gk_spec in GK_SAVES_ABOVE_EXPECTED_SPECS:
        for (
            team_id,
            numerator_sum,
            denominator_sum,
            paired_matches,
            sample_count,
        ) in _rows_for_gk_saves_spec(conn, league_id, season, gk_spec, window_pairs=window_pairs):
            out.append(
                {
                    "League_ID": league_id,
                    "Season": season,
                    "Team_ID": team_id,
                    "metric_key": gk_spec.metric_key,
                    "numerator_sum": numerator_sum,
                    "denominator_sum": denominator_sum,
                    "paired_matches": paired_matches,
                    "matches_played": matches_played_by_team.get(team_id),
                    "methodology_version": gk_spec.methodology_version,
                    "sample_count": sample_count,
                }
            )
    return out
