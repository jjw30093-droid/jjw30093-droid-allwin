"""指标注册表 —— 赛前可视化(PREMATCH_MOBILE_DATA_VISUALIZATION_V2)的设计态元数据。

⚠️ 2026-08-23 对照 FotMob 安卓包审计核实的现状:本文件**没有真正被查询层
或前端接线**。`backend/queries/attack_chain.py` / `possession_control.py` /
`defensive_pressure.py` / `matchup.py` 各自独立实现了自己的中文名、缺失策略、
最小样本——与本文件里声明的同名指标已经出现分叉(如 `touches_opp_box` 这里
写"禁区触球",`frontend/components/matches/zh.ts` TEAM_STAT_LABELS 写"对方
禁区内触球")。除本文件开头两行文档注释和 `scripts/recompute_metric_coverage.py`
外,没有任何代码 import 本模块的 `REGISTRY`/`get_metric`。

不要把本文件当成运行时真源来查——它是当初设计阶段写的目标态文档,
下面这句"查询层与前端都从这里取元数据"目前不是事实。如果要让它重新
成为真源,需要让上述查询模块改为从这里读取标签/口径,而不是各自硬编码;
在那之前,改动任何一处指标定义都必须同时检查上述四个查询模块与
frontend/components/matches/zh.ts 是否也需要跟着改,本文件不会自动同步。

不把数据库字段直接推到页面:每个要在图表里出现的指标都必须先在这里声明
canonical_key / 中文名 / 中文一句话解释 / 分子分母 / 单位 / 方向 / 最小样本 /
缺失策略 / 适用位置 / 主客场敏感性 / 对手校正策略 / 覆盖率 / 来源字段 /
方法论版本——这是设计意图,尚未接线到运行时。

四类语义(决定页面措辞,§一 措辞纪律):
- performance:可比优劣的表现指标(如 xG、射正数)。
- style:打法/风格指标,**不代表强弱**,页面只能写"偏向/较多",不能写"更强"。
- outcome_variance:结果偏差型指标(如阻止进球),**不代表可持续能力**,
  必须标注是短期窗口的描述性结果,不是预测。
- unavailable:数据当前不能可靠生成——不在这里声明的字段一律不上页面,
  不是"暂时没做",是"明确判定做不出来"(见 docs/design-brief-*.md 的
  「明确不做」清单与其实测理由)。

覆盖率全部来自 2026-08-15 的全量重算(四大联赛:英超 47 / 西甲 87 / 德甲 54 /
意甲 55,口径统一为上赛季 2025-08-01 起、Period='All' 的球队场,2892 队场;
球员/门将口径见各自 coverage_note),可重跑脚本见
scripts/recompute_metric_coverage.py(Phase 1.1 交付的同一批产物)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Direction = Literal["higher_better", "lower_better", "style_only"]
Semantic = Literal["performance", "style", "outcome_variance", "unavailable"]
VenueSensitivity = Literal["home_away_split_required", "not_applicable"]
OpponentAdjustment = Literal["none", "not_validated", "applied"]


@dataclass(frozen=True)
class MetricDef:
    canonical_key: str
    name_zh: str
    # 给用户看的一句话人话解释,格式"这个数(越高/越低)代表 XXX"——
    # 不写"该指标衡量球队的进攻效率"这种同义反复的废话。
    explanation_zh: str
    numerator: str
    denominator: str | None  # None = 直接均值/计数,不是比率
    unit: str
    direction: Direction
    semantic: Semantic
    min_sample: int
    missing_policy: str
    eligible_positions: tuple[str, ...] | None  # None = 球队级或不限位置
    venue_sensitive: VenueSensitivity
    opponent_adjustment_policy: OpponentAdjustment
    coverage_note: str
    source_field: str
    methodology_version: str
    # 前端 minVolume.atLeast 的文档真源(可选,带默认值不影响既有条目)。
    # 跨语言重复一个数字,两侧互相注释交叉引用;长期正解是让它随 API 下发,
    # 但本次不做那层接线,先把数字的"唯一解释"钉在这里。
    min_denominator: float | None = None
    # backend/queries/league_stats.py::_team_ratios 用它把 numerator_sum/
    # denominator_sum 的原始比值换算成展示值——**不要从 unit 字符串猜**
    # (曾经的写法是 `100.0 if unit=="%" else 1.0`,def_action_density 的
    # unit 是"次/百次"、不是字面"%",导致显示值少乘了 100,真实产线 bug,
    # 2026-09-14 改正)。默认 100.0(绝大多数是百分比/每百次类);
    # 逐脚 xG、逐次触球 npxG 这类"就是原始比值本身"的指标显式传 1.0。
    display_scale: float = 100.0


# ── 图1:进攻转化链(进攻半场传球 → 禁区触球 → 射门 → 射正 → xG)────────
ATTACK_CHAIN: dict[str, MetricDef] = {
    "opp_half_passes": MetricDef(
        canonical_key="opp_half_passes", name_zh="进攻半场传球",
        explanation_zh="场均有多少次传球发生在对方半场——不是看控球多不多,是看球有没有真的往前推进。",
        numerator="opposition_half_passes", denominator=None, unit="次/场",
        direction="higher_better", semantic="style", min_sample=5,
        missing_policy="该场缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测 100.0%",
        source_field="fact_team_match_stats.extra_json.opposition_half_passes",
        methodology_version="v1",
    ),
    "touches_opp_box": MetricDef(
        canonical_key="touches_opp_box", name_zh="禁区触球",
        explanation_zh="场均有多少次在对方禁区内触球——推进到这一步才算真正逼近球门,不是随便传到前场就算。",
        numerator="touches_opp_box", denominator=None, unit="次/场",
        direction="higher_better", semantic="performance", min_sample=5,
        missing_policy="该场缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测 100.0%",
        source_field="fact_team_match_stats.extra_json.touches_opp_box",
        methodology_version="v1",
    ),
    "shots": MetricDef(
        canonical_key="shots", name_zh="射门",
        explanation_zh="场均射门次数——推进到禁区之后,有多少次真的完成了射门动作。",
        numerator="total_shots", denominator=None, unit="次/场",
        direction="higher_better", semantic="performance", min_sample=5,
        missing_policy="该场缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测 100.0%",
        source_field="fact_team_match_stats.extra_json.total_shots",
        methodology_version="v1",
    ),
    "shots_on_target": MetricDef(
        canonical_key="shots_on_target", name_zh="射正",
        explanation_zh="场均射正次数——射门里有多少真的威胁到门将,不是踢飞或被封堵。",
        numerator="ShotsOnTarget", denominator=None, unit="次/场",
        direction="higher_better", semantic="performance", min_sample=5,
        missing_policy="该场缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测 100.0%",
        source_field="fact_team_match_stats.extra_json.ShotsOnTarget",
        methodology_version="v1",
    ),
    "xg": MetricDef(
        canonical_key="xg", name_zh="预期进球(xG)",
        explanation_zh="按射门位置和方式估算的「理论上该进几个球」——比单纯数射门次数更能反映机会质量。",
        numerator="expected_goals", denominator=None, unit="球/场",
        direction="higher_better", semantic="performance", min_sample=5,
        missing_policy="该场缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测 100.0%",
        source_field="fact_team_match_stats.extra_json.expected_goals",
        methodology_version="v1",
    ),
    "xgot": MetricDef(
        canonical_key="xgot", name_zh="射正预期进球(xGOT)",
        explanation_zh="只看「射正」的那些球,按落点算出的理论进球值——比 xG 更聚焦「已经威胁到门将的机会」有多致命。",
        numerator="expected_goals_on_target", denominator=None, unit="球/场",
        direction="higher_better", semantic="performance", min_sample=5,
        missing_policy="该场缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测 100.0%",
        source_field="fact_team_match_stats.extra_json.expected_goals_on_target",
        methodology_version="v1",
    ),
}

# ── 图2:控球与场面控制 ─────────────────────────────────────────────
POSSESSION_CONTROL: dict[str, MetricDef] = {
    "possession": MetricDef(
        canonical_key="possession", name_zh="控球率",
        explanation_zh="场均控球时间占比——只说明球权在谁脚下的时间更多,不直接等于踢得更好(见「进攻半场传球占比」区分控球是否转化成推进)。",
        numerator="BallPossesion", denominator=None, unit="%",
        direction="style_only", semantic="style", min_sample=5,
        missing_policy="该场缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测 100.0%",
        source_field="fact_team_match_stats.extra_json.BallPossesion",
        methodology_version="v1",
    ),
    "pass_completion": MetricDef(
        canonical_key="pass_completion", name_zh="传球成功率",
        explanation_zh="传球里有多少次准确送到队友脚下——高不代表进攻犀利,可能只是横传倒脚多。",
        numerator="accurate_passes", denominator="passes", unit="%",
        direction="style_only", semantic="style", min_sample=5,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测:accurate_passes 100.0%,passes 100.0%",
        source_field="fact_team_match_stats.extra_json.{accurate_passes,passes}",
        methodology_version="v1",
    ),
    "opp_half_pass_share": MetricDef(
        canonical_key="opp_half_pass_share", name_zh="前场成功传球占比",
        # 命名红线(方案 §三 图2):不得冒充 Opta/StatsBomb 的正式 Field Tilt,
        # 这里只是同一批字段的比值代理,口径未经对方公开方法论核实。
        # 分母改正(2026-09-14,站长已批准):原分母是 passes(传球尝试总数),
        # 但 own_half_passes+opposition_half_passes 实测等于 accurate_passes
        # (成功传球,26095/26096 命中),不等于 passes——分子只数成功、分母却数
        # 全部尝试,旧口径的比值永远到不了 100%。改用 accurate_passes 后,
        # 前场占比+本方半场占比恒为 100%,name_zh 同步改为「前场成功传球占比」
        # 以准确反映分子分母都是成功传球这件事。
        explanation_zh="成功传球里有多大比例发生在对方半场——数字越高,说明比赛越多时间是在对方门前打,不是我们自己倒脚。这是本站用现有数据算的代理指标,不是 Opta/StatsBomb 的官方 Field Tilt。",
        numerator="opposition_half_passes", denominator="accurate_passes", unit="%",
        direction="style_only", semantic="style", min_sample=5,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测:opposition_half_passes 100.0%,accurate_passes 100.0%",
        source_field="fact_team_match_stats.extra_json.{opposition_half_passes,accurate_passes}",
        methodology_version="v2",
    ),
}

# ── 图3:防守承压与限制能力(对手在同一场比赛里的进攻数据)────────────
# 全部通过对手联结取值(同 team_style_preview.py::_xg_for_against_points
# 的按主客定位对手手法),不是自己队伍的字段——被射门数/被射正数/让出xG
# 描述的是"对手在这场创造了多少",不是本队自己的动作。
DEFENSIVE_PRESSURE: dict[str, MetricDef] = {
    "shots_faced": MetricDef(
        canonical_key="shots_faced", name_zh="被射门",
        explanation_zh="场均被对手射门多少次——次数只说明被逼近门前的频率,不说明质量高低(配合「被射正」「让出xG」一起看)。",
        numerator="opponent.total_shots", denominator=None, unit="次/场",
        direction="lower_better", semantic="performance", min_sample=5,
        missing_policy="该场对手缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测:total_shots 100.0%(对手联结同源)",
        source_field="fact_team_match_stats.extra_json.total_shots(对手行)",
        methodology_version="v1",
    ),
    "shots_on_target_faced": MetricDef(
        canonical_key="shots_on_target_faced", name_zh="被射正",
        explanation_zh="场均被对手射正多少次——这些是真正考验门将的射门,比「被射门」更能说明防线让对方打出了多少真威胁。",
        numerator="opponent.ShotsOnTarget", denominator=None, unit="次/场",
        direction="lower_better", semantic="performance", min_sample=5,
        missing_policy="该场对手缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测:ShotsOnTarget 100.0%(对手联结同源)",
        source_field="fact_team_match_stats.extra_json.ShotsOnTarget(对手行)",
        methodology_version="v1",
    ),
    "xga": MetricDef(
        canonical_key="xga", name_zh="让出预期进球(xGA)",
        explanation_zh="按对手射门位置和方式估算「对手理论上该进几个球」——比单纯数被射门次数更能反映防守让出的机会有多危险。",
        numerator="opponent.expected_goals", denominator=None, unit="球/场",
        direction="lower_better", semantic="performance", min_sample=5,
        missing_policy="该场对手缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测:expected_goals 100.0%(对手联结同源)",
        source_field="fact_team_match_stats.extra_json.expected_goals(对手行)",
        methodology_version="v1",
    ),
    "box_shots_faced": MetricDef(
        canonical_key="box_shots_faced", name_zh="禁区内被射门",
        explanation_zh="对手场均有多少次射门是在禁区内完成的——禁区内射门转化率远高于禁区外,这个数直接反映「防线有没有把对手挡在门前」。",
        numerator="opponent.shots_inside_box", denominator=None, unit="次/场",
        direction="lower_better", semantic="performance", min_sample=5,
        missing_policy="该场对手缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测:shots_inside_box 100.0%(对手联结同源)",
        source_field="fact_team_match_stats.extra_json.shots_inside_box(对手行)",
        methodology_version="v1",
    ),
}

# ── 图4:本场攻防对位(按 fact_shotmap.Situation 拆的"创造 vs 让出")────
# for(团队自己在该情境下的射门与xG,复用 team_style_preview.team_attack_sources
# 的既有实现)与 against(对手在该情境下打进来的射门与xG,新查询,§Phase2.4 实现)
# 是同一份 Situation 枚举下的两个方向,不是两套指标定义,这里只登记一次公用元数据。
MATCHUP_SITUATION: dict[str, MetricDef] = {
    "situation_shots_for": MetricDef(
        canonical_key="situation_shots_for", name_zh="按情境拆分的射门(创造方)",
        explanation_zh="这类打法(运动战/反击/定位球/角球等)贡献了多少次射门与多少 xG——数字越高说明这是这队最常用、最有效的进攻方式。",
        numerator="fact_shotmap 分 Situation 的 shots/xG(本队为射门方)", denominator=None,
        unit="次 · xG/次", direction="higher_better", semantic="performance", min_sample=5,
        missing_policy="某来源某场缺 xG 时该来源 xg=None,不按 0 计入;不凑满全部 8 种来源",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="none",
        coverage_note="四大联赛 2025-08 起 1446/1446 场(100%)有射门数据,Situation 100%、xG 99.8%",
        source_field="fact_shotmap.{Situation,xG,Team_ID}",
        methodology_version="v1",
    ),
    "situation_shots_against": MetricDef(
        canonical_key="situation_shots_against", name_zh="按情境拆分的被射门(承受方)",
        explanation_zh="对手用这类打法(运动战/反击/定位球/角球等)打进来多少次射门与多少 xG——数字越高说明这队最怕被这样打。",
        numerator="fact_shotmap 分 Situation 的 shots/xG(本队为对手,同场对方球队为射门方)",
        denominator=None, unit="次 · xG/次", direction="lower_better", semantic="performance",
        min_sample=5,
        missing_policy="某来源某场缺 xG 时该来源 xg=None,不按 0 计入;不凑满全部 8 种来源",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="none",
        coverage_note="四大联赛 2025-08 起 1446/1446 场(100%)有射门数据,Situation 100%、xG 99.8%",
        source_field="fact_shotmap.{Situation,xG,Team_ID}(取同场对方球队的行)",
        methodology_version="v1",
    ),
}

# ── 门将(既有实现 backend/queries/player_form.py::team_goalkeepers)──────
GOALKEEPER: dict[str, MetricDef] = {
    "goals_prevented": MetricDef(
        canonical_key="goals_prevented", name_zh="阻止进球",
        explanation_zh="面对的射正预期进球(xGOT)减去实际失球——正数代表比「平均水平」多扑出了几个球,但这是短期窗口的结果记录,不是稳定的扑救能力评价。",
        numerator="expected_goals_on_target_faced", denominator="goals_conceded", unit="球",
        direction="higher_better", semantic="outcome_variance", min_sample=5,
        missing_policy="直接来源字段缺失时给 None;仅在窗口内每场都有 xGOT 时才允许现算兜底,"
                        "并标注「估算」区分口径",
        eligible_positions=("GK",), venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="2026-08-15 重算(is_goalkeeper=1 且 minutes_played>0,10 联赛,"
                       "2020-08-21~2026-08-10,26,402 行):直接来源 goals_prevented 39.0%,"
                       "xGOT(expected_goals_on_target_faced) 97.2%",
        source_field="fact_player_match_stats.{goals_prevented,expected_goals_on_target_faced,goals_conceded}",
        methodology_version="v1",
    ),
    "keeper_sweeper": MetricDef(
        canonical_key="keeper_sweeper", name_zh="出击",
        explanation_zh="场均出击次数——反映门将愿不愿意离开门线参与解围,是风格描述,不是「谁更强」的排名。",
        numerator="keeper_sweeper", denominator=None, unit="次/场",
        direction="style_only", semantic="style", min_sample=3,
        missing_policy="该场缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=("GK",), venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="GK 行 100% 覆盖(26,402 行),同 team_goalkeepers 现有实现",
        source_field="fact_player_match_stats.keeper_sweeper",
        methodology_version="v1",
    ),
    "keeper_high_claim": MetricDef(
        canonical_key="keeper_high_claim", name_zh="高球处理",
        explanation_zh="场均高球摘接次数——反映门将处理传中球、角球的主动性,是风格描述,不是「谁更强」的排名。",
        numerator="keeper_high_claim", denominator=None, unit="次/场",
        direction="style_only", semantic="style", min_sample=3,
        missing_policy="该场缺该字段时整场排除出均值分母,不按 0 计入",
        eligible_positions=("GK",), venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="GK 行 100% 覆盖(26,402 行),同 team_goalkeepers 现有实现",
        source_field="fact_player_match_stats.keeper_high_claim",
        methodology_version="v1",
    ),
}


# ── 球队象限图复合指标(2026-09-14,backend/silver/ratio_metrics.py 的赛季
# 聚合;与上面几组"窗口尺度"指标是同一批字段、不同聚合尺度,不重复实现配对
# 逻辑,只是分开登记方便查找)──────────────────────────────────────────
TEAM_QUADRANT_RATIOS: dict[str, MetricDef] = {
    "set_piece_xg_share": MetricDef(
        canonical_key="set_piece_xg_share", name_zh="定位球 xG 占比",
        # 口径红线(2026-09-14,站长拍板):分母是"运动战 xG + 定位球 xG"两项
        # 相加,不是 expected_goals_non_penalty 字段本身(两者 99.7% 场次一致,
        # 差异在三位小数舍入量级,但"两项相加"能让定位球占比+运动战占比恒为
        # 100%)。绝不能写成"占总 xG"——FotMob 的运动战/定位球 xG 都不含点球,
        # 与含点球的总 xG 不同口径,实测英超本赛季 总1.398/运动战0.921/
        # 定位球0.381,开放+定位=1.302 ≠ 1.398,差的 0.096 正是点球。
        explanation_zh="进攻威胁里有多大比例来自定位球(角球/任意球/界外球后的机会)——数字越高不代表更强,只代表进球路径更依赖死球,不是运动战推进。分母是运动战 xG + 定位球 xG 之和(均不含点球),不是总 xG。",
        numerator="expected_goals_set_play", denominator="expected_goals_open_play+expected_goals_set_play",
        unit="%", direction="style_only", semantic="style", min_sample=8,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="expected_goals_set_play 全库实测 99.7%,expected_goals_open_play 99.99%",
        source_field="fact_team_match_stats.extra_json.{expected_goals_set_play,expected_goals_open_play}",
        methodology_version="v1",
    ),
    "set_piece_xga_share": MetricDef(
        canonical_key="set_piece_xga_share", name_zh="失球端定位球占比",
        explanation_zh="对手打进来的威胁里有多大比例来自定位球——数字越高说明这支球队更容易在死球中被威胁,不直接等于防守差(可能是防空体系的风格选择)。取自同场对手的定位球/运动战 xG,口径与「定位球 xG 占比」相同。",
        numerator="opponent.expected_goals_set_play",
        denominator="opponent.expected_goals_open_play+opponent.expected_goals_set_play",
        unit="%", direction="style_only", semantic="style", min_sample=8,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="none",
        coverage_note="同 set_piece_xg_share,取自对手行(同源同覆盖率)",
        source_field="fact_team_match_stats.extra_json.{expected_goals_set_play,expected_goals_open_play}(对手行)",
        methodology_version="v1",
    ),
    "opp_xg_per_shot": MetricDef(
        canonical_key="opp_xg_per_shot", name_zh="对手每脚射门 xG",
        explanation_zh="对手每次射门平均能换来多少预期进球——数字越低,说明我们的防线把对手逼到了更差的射门位置,不只是少挨射门,是挨的射门质量也更差。取自同场对手的 xG 与射门数,与「每脚射门 xG」(我方口径)同构。",
        numerator="opponent.expected_goals", denominator="opponent.total_shots",
        unit="球/脚", direction="lower_better", semantic="performance", min_sample=6,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="none",
        coverage_note="expected_goals 全库实测 99.99%,total_shots 100.0%(对手联结同源)",
        source_field="fact_team_match_stats.extra_json.{expected_goals,total_shots}(对手行)",
        methodology_version="v1",
        display_scale=1.0,  # 每脚 xG 本身就是要展示的值,不是百分比,不乘 100
    ),
    "shot_accuracy": MetricDef(
        canonical_key="shot_accuracy", name_zh="射正率",
        # 分母口径:total_shots − blocked_shots,实现上用 ShotsOnTarget+
        # ShotsOffTarget 两项相加等价代入(实测 ShotsOnTarget+ShotsOffTarget+
        # blocked_shots=total_shots 100.00% 精确成立,26096/26096),
        # 不是除以未剔除被封堵射门的 total_shots。
        explanation_zh="射门里有多大比例真正威胁到门将(排除被封堵的射门)——射正率高不代表进攻犀利,可能只是射门位置正、力量小。",
        numerator="ShotsOnTarget", denominator="ShotsOnTarget+ShotsOffTarget(=total_shots-blocked_shots)",
        unit="%", direction="higher_better", semantic="performance", min_sample=6,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="ShotsOnTarget/ShotsOffTarget 全库实测均 100.0%(核心字段,非分组头)",
        source_field="fact_team_match_stats.extra_json.{ShotsOnTarget,ShotsOffTarget}",
        methodology_version="v1",
    ),
    "box_shot_share": MetricDef(
        canonical_key="box_shot_share", name_zh="禁区内射门占比",
        explanation_zh="射门里有多大比例是在禁区内完成的——禁区内射门转化率远高于禁区外,这个数直接反映球队是打进去了再射,还是远射为主。",
        numerator="shots_inside_box", denominator="shots_inside_box+shots_outside_box",
        unit="%", direction="style_only", semantic="style", min_sample=6,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="shots_inside_box/shots_outside_box 全库实测均 100.0%",
        source_field="fact_team_match_stats.extra_json.{shots_inside_box,shots_outside_box}",
        methodology_version="v1",
    ),
    "big_chance_conversion": MetricDef(
        canonical_key="big_chance_conversion", name_zh="绝佳机会把握率",
        explanation_zh="绝佳机会里有多大比例真的打进了(大机会−错失大机会)/大机会——数字低不一定是终结能力差,样本小(一季没几个大机会)时噪音很大。",
        numerator="big_chance-big_chance_missed_title", denominator="big_chance",
        unit="%", direction="higher_better", semantic="performance", min_sample=10,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="big_chance/big_chance_missed_title 全库实测均 100.0%;实测 missed 从未超过 created(0/26096 反常),减法结果恒非负",
        source_field="fact_team_match_stats.extra_json.{big_chance,big_chance_missed_title}",
        methodology_version="v1",
        min_denominator=25.0,
    ),
    "aerial_win_share": MetricDef(
        canonical_key="aerial_win_share", name_zh="争顶优势占比",
        explanation_zh="全场争顶里本队赢下的比例——没有单独的\"争顶总数\"字段,分母用双方 aerials_won 之和近似。数字越高不直接等于身体更强,也可能是这队本来争顶次数就少、赢的都是关键几次。",
        numerator="aerials_won", denominator="aerials_won+opponent.aerials_won",
        unit="%", direction="style_only", semantic="style", min_sample=6,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="none",
        coverage_note="aerials_won 全库实测 100.0%(双方联结同源)",
        source_field="fact_team_match_stats.extra_json.aerials_won(本队+对手两行)",
        methodology_version="v1",
        min_denominator=300.0,
    ),
    "fast_break_xg_share": MetricDef(
        canonical_key="fast_break_xg_share", name_zh="快速反击 xG 占比",
        explanation_zh="进攻威胁里有多大比例来自快速反击——数字越高不代表更强,只代表进球路径更依赖转换,不是阵地组织。取自射门级数据按 Situation 分类,分母是全部非点球 xG(与「定位球 xG 占比」同一惯例)。",
        numerator="fact_shotmap.xG(Situation=FastBreak)", denominator="fact_shotmap.xG(Situation!=Penalty)",
        unit="%", direction="style_only", semantic="style", min_sample=12,
        missing_policy="乌龙球(xG=NULL)的射门不计入分子分母;该场一脚都没射时整场排除",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="fact_shotmap.Situation 全库实测 100.0% non-null,xG 99.7%",
        source_field="fact_shotmap.{Situation,xG,Team_ID}",
        methodology_version="v1",
        min_denominator=15.0,
    ),
    "box_touch_share": MetricDef(
        canonical_key="box_touch_share", name_zh="禁区触球占比",
        explanation_zh="双方在对方禁区内触球的次数里,本队占多大比例——数字高不直接等于强,可能只是节奏快、触球次数本来就多。这是本站目前最接近 Field Tilt 的字段,但不是 Opta/StatsBomb 的官方 Field Tilt,界面上不得这么叫。",
        numerator="touches_opp_box", denominator="touches_opp_box+opponent.touches_opp_box",
        unit="%", direction="style_only", semantic="style", min_sample=6,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="none",
        # touches_opp_box 实测 2024 年起覆盖率稳定 98%+,更早的赛季随机缺失
        # (13%~87%不等),球队间不可比——该指标只对 2024 年起的赛季开放
        # (backend/silver/ratio_metrics.py::touches_opp_box_eligible 同一份判定)。
        coverage_note="仅 2024 年起的赛季开放(更早的赛季 touches_opp_box 随机缺失,球队间不可比)",
        source_field="fact_team_match_stats.extra_json.touches_opp_box(本队+对手两行)",
        methodology_version="v1",
        min_denominator=40.0,
    ),
    "npxg_per_box_touch": MetricDef(
        canonical_key="npxg_per_box_touch", name_zh="每次禁区触球 npxG",
        explanation_zh="每次在对方禁区内触球平均换来多少非点球预期进球——数字越高说明触球质量越高,不只是触到球就算数。同样只在 touches_opp_box 覆盖率 100% 的赛季提供。",
        numerator="expected_goals_non_penalty", denominator="touches_opp_box",
        unit="球/次", direction="higher_better", semantic="performance", min_sample=6,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="仅 2024 年起的赛季开放(理由同 box_touch_share)",
        source_field="fact_team_match_stats.extra_json.{expected_goals_non_penalty,touches_opp_box}",
        methodology_version="v1",
        min_denominator=20.0,
        display_scale=1.0,  # 每次触球 npxG 本身就是要展示的值,不乘 100
    ),
    "def_action_density": MetricDef(
        canonical_key="def_action_density", name_zh="防守动作密度(每百次对手传球)",
        # 命名红线,照抄 Field Tilt 红线的模式:这不是 PPDA。真正的 PPDA 需要
        # 防守动作坐标来限定"前场逼抢"这个区域限制,本站没有任何传球/防守
        # 动作的坐标数据,这里算出来的只是"防守动作数量相对对手传球量"的
        # 粗代理,铁桶阵和高位压迫可能拿到相近的数字——界面上绝不能出现
        # PPDA 字样。刻意不含 clearances(解围是低位防守的产物,计入会把
        # 摆大巴的球队伪装成压迫型)。
        explanation_zh="本队抢断+拦截+犯规,相对对手每百次传球的密度——不是 PPDA(没有动作坐标,无法限定逼抢发生在哪个区域),数字高也可能是深度防守被迫多解围之外的动作,不是高位压迫的证据。",
        numerator="matchstats.headers.tackles+interceptions+fouls", denominator="opponent.passes",
        unit="次/百次", direction="style_only", semantic="style", min_sample=6,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="tackles/interceptions/fouls 全库实测均 100.0%;passes 全库实测 100.0%(对手联结同源)",
        source_field="fact_team_match_stats.extra_json.{matchstats.headers.tackles,interceptions,fouls}(本队);passes(对手行)",
        methodology_version="v1",
        min_denominator=2000.0,
    ),
    "opp_territory_share": MetricDef(
        canonical_key="opp_territory_share", name_zh="对手前场成功传球占比",
        explanation_zh="对手把球玩到我方半场的比例——与「前场成功传球占比」同一公式,取自同场对手。数字越高说明对手更多时间是在我方门前控球,不直接等于我方防守差。",
        numerator="opponent.opposition_half_passes", denominator="opponent.accurate_passes",
        unit="%", direction="style_only", semantic="style", min_sample=6,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入;无一场配对成功时 value=None,不是 0",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="none",
        coverage_note="同 opp_half_pass_share,取自对手行",
        source_field="fact_team_match_stats.extra_json.{opposition_half_passes,accurate_passes}(对手行)",
        methodology_version="v1",
        min_denominator=2000.0,
    ),
    "corner_shot_rate": MetricDef(
        canonical_key="corner_shot_rate", name_zh="角球成射率",
        explanation_zh="本队开出的角球里,有多大比例真的形成了射门——分母(角球数)通常比较小,单赛季样本有限,界面上按原始计数(如 11/48)展示,不只给百分比。",
        numerator="shotmap.Situation=FromCorner 的射门次数", denominator="corners",
        unit="%", direction="style_only", semantic="style", min_sample=10,
        missing_policy="该场角球数缺失或为 0 时整场排除;射门次数本身不会缺失(没有该 Situation 的射门就是 0 次,不是未知)",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="corners 字段全库实测覆盖率 100%",
        source_field="fact_shotmap.Situation(分子,计次)+fact_team_match_stats.extra_json.corners(分母)",
        methodology_version="v1",
        min_denominator=50.0,
    ),
    "finishing_delta": MetricDef(
        canonical_key="finishing_delta", name_zh="场均终结超额",
        explanation_zh="场均(非点球进球 − 非点球预期进球),进球按受益方计乌龙球。正值代表这个赛季射门转化得比预期进球更多,但这不是稳定的终结能力,只是短期窗口的结果记录——调研认为这类差值跨赛季的相关性接近零。",
        numerator="非点球进球(按受益方计乌龙)− 非点球xG", denominator="matches",
        unit="球", direction="higher_better", semantic="outcome_variance", min_sample=15,
        missing_policy="该场非点球xG缺失时整场排除,不按 0 计入;乌龙球判定优先用采集值,否则按 xG IS NULL 且 Outcome=Goal 推断",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="none",
        coverage_note="expected_goals_non_penalty 全库实测覆盖率 100%;乌龙球推断口径全库 1022/1022 命中且与 fact_match_events 交叉验证过",
        source_field="fact_team_match_stats.Goals/extra_json.expected_goals_non_penalty + fact_shotmap(乌龙球/点球剔除)",
        methodology_version="v1",
        min_denominator=150.0,  # 口径是"赛季累计非点球射门数"——前端读 TeamRatioValue.sample_count,不是 denominator(那是场次数)
        display_scale=1.0,  # 场均进球差值本身就是展示值,不是百分比
    ),
    "gk_saves_above_expected": MetricDef(
        canonical_key="gk_saves_above_expected", name_zh="场均门将扑救超额",
        explanation_zh="场均(对手射正的预期进球质量 − 实际非点球失球),只统计非点球、非乌龙、且有真实 xGOT 数据的射正。正值代表门将扑救的比射门质量预期的要多,但这不是稳定的门将能力评价,只是短期窗口的近期记录,不代表未来表现。被扑救射门的 xGOT 有约三分之一缺失,缺失的场次样本一律排除、不按 0 计入。",
        numerator="对手射正(非点球、非乌龙、xGOT非空)Σ xGOT − 非点球失球", denominator="matches",
        unit="球", direction="higher_better", semantic="outcome_variance", min_sample=10,
        missing_policy="该场对手射正里一次有效 xGOT 都没有时整场排除,不按 0 计入(与'对手确实没射正'区分开)",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="被扑救射门(AttemptSaved)xGOT 全库实测缺失 33.15%,进球(Goal)xGOT 缺失 2.81%——界面必须公示纳入求和的有效射正次数(TeamRatioValue.sample_count),不能只给差值不给覆盖率",
        source_field="fact_shotmap.xGOT(对手行,自连接)+ fact_team_match_stats.Goals/自连接(非点球失球,乌龙球剔除)",
        methodology_version="v1",
        min_denominator=60.0,  # 口径是"赛季累计有效xGOT射正次数"——前端读 sample_count,不是 denominator(那是场次数)
        display_scale=1.0,  # 场均扑救超额本身就是展示值,不是百分比
    ),
}


# ── 联赛球员象限图复合指标(2026-09-15,backend/silver/player_season.py 的
# 赛季聚合)。与 TEAM_QUADRANT_RATIOS 同一批"A 比 B"叙事,但字段来自球员级
# fact_player_match_stats(真实列宽表,不是队级 extra_json)——xA、真实对抗
# 成功率、三区传球这些字段队级根本不下发,是本次扩展的直接动机。
PLAYER_QUADRANT_RATIOS: dict[str, MetricDef] = {
    "npxg_per90": MetricDef(
        canonical_key="npxg_per90", name_zh="每90分钟非点球xG",
        explanation_zh="每 90 分钟能制造多少非点球预期进球——反映射门机会的质量与数量,不含点球(点球不是靠个人持续创造机会拿到的)。",
        numerator="expected_goals_non_penalty", denominator="minutes_played",
        unit="球/90分钟", direction="higher_better", semantic="performance", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="按赛季累计求和,SUM 天然跳过缺失场次,不按 0 计入",
        eligible_positions=None, venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="expected_goals_non_penalty 生产实测 minutes_played>=60 时 46.7% 非空(事件条件性字段,无射门的场次不下发),缺失即该场零非点球xG,COALESCE 语义安全",
        source_field="fact_player_match_stats.expected_goals_non_penalty",
        methodology_version="v1", display_scale=90.0,
    ),
    "finishing_delta_per90": MetricDef(
        canonical_key="finishing_delta_per90", name_zh="每90分钟终结超额",
        explanation_zh="每 90 分钟(非点球进球 − 非点球预期进球)——正值代表射门转化得比预期进球更多,但这不是稳定的终结能力,只是短期窗口的结果记录,不代表未来表现。",
        numerator="goals - shotmap点球进球数 - expected_goals_non_penalty", denominator="minutes_played",
        unit="球/90分钟", direction="higher_better", semantic="outcome_variance", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="点球进球数从 fact_shotmap 按 Player_ID+Situation=Penalty+Outcome=Goal 精确聚合(生产实测球员级点球可精确统计)",
        eligible_positions=None, venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="goals/expected_goals_non_penalty 全库高覆盖,与球队侧 finishing_delta 同一「短期窗口」免责声明",
        source_field="fact_player_match_stats.{goals,expected_goals_non_penalty} + fact_shotmap(点球剔除)",
        methodology_version="v1", display_scale=90.0,
    ),
    "chances_created_per90": MetricDef(
        canonical_key="chances_created_per90", name_zh="每90分钟创造机会数",
        explanation_zh="每 90 分钟为队友创造多少次射门机会——只数次数,不看含金量(配合「每90分钟预期助攻」一起看,能分出「量大质低」和「少而精」)。",
        numerator="chances_created", denominator="minutes_played",
        unit="次/90分钟", direction="higher_better", semantic="performance", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="按赛季累计求和,SUM 天然跳过缺失场次,不按 0 计入",
        eligible_positions=None, venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="chances_created 生产实测 minutes_played>=60 时 90.8% 非空",
        source_field="fact_player_match_stats.chances_created",
        methodology_version="v1", display_scale=90.0,
    ),
    "xa_per90": MetricDef(
        canonical_key="xa_per90", name_zh="每90分钟预期助攻(xA)",
        explanation_zh="每 90 分钟创造的机会按转化概率估算「理论上该有几次助攻」——只有球员维度才有这个字段,球队维度算不出来。",
        numerator="expected_assists", denominator="minutes_played",
        unit="次/90分钟", direction="higher_better", semantic="performance", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="按赛季累计求和,SUM 天然跳过缺失场次,不按 0 计入",
        eligible_positions=None, venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="expected_assists 生产实测 minutes_played>=60 时 68.9%~74.8%(随联赛浮动),事件条件性字段,缺失=该场无关键传球机会",
        source_field="fact_player_match_stats.expected_assists",
        methodology_version="v1", display_scale=90.0,
    ),
    "defensive_actions_per90": MetricDef(
        canonical_key="defensive_actions_per90", name_zh="每90分钟防守动作(CBIRT)",
        explanation_zh="每 90 分钟的抢断+拦截+解围+封堵+回收球——行业标准 Defensive Contribution 口径(FPL/Opta 的 CBIRT),不是本站发明的代理指标。",
        numerator="matchstats.headers.tackles+interceptions+clearances+shot_blocks+recoveries",
        denominator="minutes_played",
        unit="次/90分钟", direction="higher_better", semantic="performance", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="五项分别 SUM 再相加(不是逐行相加),任一字段单场缺失不会连坐其它字段",
        eligible_positions=None, venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="tackles/interceptions/recoveries/clearances 生产实测 minutes_played>=60 时 100% 非空,shot_blocks 93.3%",
        source_field="fact_player_match_stats.{matchstats.headers.tackles,interceptions,clearances,shot_blocks,recoveries}",
        methodology_version="v1", display_scale=90.0,
    ),
    "duel_win_rate": MetricDef(
        canonical_key="duel_win_rate", name_zh="对抗成功率",
        explanation_zh="全部对抗(地面+空中)里赢下的比例——数字高不代表身体最强,也可能是挑对抗次数少、赢的都是关键几次。",
        numerator="duel_won", denominator="duel_won+duel_lost",
        unit="%", direction="higher_better", semantic="performance", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="分子分母各自 SUM 后相除,SUM 天然跳过缺失场次",
        eligible_positions=None, venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="duel_won/duel_lost 生产实测 minutes_played>=60 时 83%~84% 非空",
        source_field="fact_player_match_stats.{duel_won,duel_lost}",
        methodology_version="v1",
    ),
    "touches_per90": MetricDef(
        canonical_key="touches_per90", name_zh="每90分钟触球数",
        explanation_zh="每 90 分钟触球多少次——反映球权经手的频率,数字高不直接等于踢得好,可能只是位置靠近球权枢纽。",
        numerator="touches", denominator="minutes_played",
        unit="次/90分钟", direction="style_only", semantic="style", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="按赛季累计求和,SUM 天然跳过缺失场次,不按 0 计入",
        eligible_positions=None, venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="touches 生产实测 minutes_played>=60 时 100% 非空",
        source_field="fact_player_match_stats.touches",
        methodology_version="v1", display_scale=90.0,
    ),
    "progression_rate": MetricDef(
        canonical_key="progression_rate", name_zh="每百次触球送进前场传球数",
        explanation_zh="每一百次触球里,有多少次传球把球送进了前场——数字高说明触球更多转化成向前的推进,不只是倒脚控球。",
        numerator="passes_into_final_third", denominator="touches",
        unit="次/百次触球", direction="higher_better", semantic="style", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="分子分母各自 SUM 后相除,SUM 天然跳过缺失场次",
        eligible_positions=None, venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="passes_into_final_third 生产实测 minutes_played>=60 时 79.6% 非空,touches 100%",
        source_field="fact_player_match_stats.{passes_into_final_third,touches}",
        methodology_version="v1",
    ),
    "xgot_faced_per90": MetricDef(
        canonical_key="xgot_faced_per90", name_zh="每90分钟面对射正预期进球",
        explanation_zh="门将每 90 分钟面对的射正球,按落点估算「理论上该丢几个球」——反映承压程度,不是扑救能力本身(配合「扑救超额」一起看)。",
        numerator="expected_goals_on_target_faced", denominator="minutes_played",
        unit="球/90分钟", direction="lower_better", semantic="performance", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="非门将球员该字段恒为 NULL,SUM 后为 NULL,不产出该指标的行",
        eligible_positions=("GK",), venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="真正门将实测覆盖率接近 100%(生产样本 34/34、32/32、37/38 场次全部有值)",
        source_field="fact_player_match_stats.expected_goals_on_target_faced",
        methodology_version="v1", display_scale=90.0,
    ),
    "goals_prevented_per90": MetricDef(
        canonical_key="goals_prevented_per90", name_zh="每90分钟扑救超额",
        explanation_zh="每 90 分钟(面对射正预期进球 − 实际失球)——正值代表扑救的比预期要多,但这是短期窗口的结果记录,不是稳定的门将能力评价,不代表未来表现。",
        numerator="goals_prevented", denominator="minutes_played",
        unit="球/90分钟", direction="higher_better", semantic="outcome_variance", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="直接用 FotMob 自带的 goals_prevented 字段(不用 xGOT−失球 重推——生产实测两者对部分门将差 2~4 球,是点球口径差异)",
        eligible_positions=("GK",), venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="真正门将实测覆盖率接近 100%,历史 39.0% 的低覆盖率是被非门将行稀释的统计假象",
        source_field="fact_player_match_stats.goals_prevented",
        methodology_version="v1", display_scale=90.0,
    ),
    "pass_completion_rate": MetricDef(
        canonical_key="pass_completion_rate", name_zh="传球成功率",
        explanation_zh="成功传球 ÷ 传球尝试总数——只对 2026/2027 起的赛季开放,更早赛季 accurate_passes_total(传球尝试总数)历史上几乎不下发。",
        numerator="accurate_passes", denominator="accurate_passes_total",
        unit="%", direction="higher_better", semantic="performance", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="分子分母各自 SUM 后相除,SUM 天然跳过缺失场次;accurate_passes_total 该赛季不可信时整个指标不产出行",
        eligible_positions=None, venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="仅 2026/2027 起的赛季开放(accurate_passes_total 2020/2021~2025/2026 五大联赛门将行几乎 0% 覆盖,2026/2027 陡然跳到 75%~100%,见 backend/silver/player_season.py::pass_completion_eligible)",
        source_field="fact_player_match_stats.{accurate_passes,accurate_passes_total}",
        methodology_version="v1",
    ),
    "long_ball_share": MetricDef(
        canonical_key="long_ball_share", name_zh="长传占比",
        explanation_zh="成功长传 ÷ 传球尝试总数——反映出球风格是偏向长传解围还是短传出球,不是强弱评价。与「传球成功率」共用同一个分母,不会出现两种口径打架。",
        numerator="long_balls_accurate", denominator="accurate_passes_total",
        unit="%", direction="style_only", semantic="style", min_sample=0,  # 球员象限图门槛走 minutes_share(前端 40% 出场占比),不是这里的"最少场次"语义,占位不使用
        missing_policy="分子分母各自 SUM 后相除,SUM 天然跳过缺失场次;accurate_passes_total 该赛季不可信时整个指标不产出行",
        eligible_positions=None, venue_sensitive="not_applicable",
        opponent_adjustment_policy="none",
        coverage_note="仅 2026/2027 起的赛季开放(理由同 pass_completion_rate);long_balls_accurate 实测从不超过 accurate_passes_total",
        source_field="fact_player_match_stats.{long_balls_accurate,accurate_passes_total}",
        methodology_version="v1",
    ),
}


REGISTRY: dict[str, MetricDef] = {
    **ATTACK_CHAIN,
    **POSSESSION_CONTROL,
    **DEFENSIVE_PRESSURE,
    **MATCHUP_SITUATION,
    **GOALKEEPER,
    **TEAM_QUADRANT_RATIOS,
    **PLAYER_QUADRANT_RATIOS,
}


def get_metric(canonical_key: str) -> MetricDef:
    """按 key 取指标定义;找不到直接抛错——不允许静默回退到一个未声明的
    字段(那正是本注册表要杜绝的"数据库字段直接推到页面")。"""
    return REGISTRY[canonical_key]


def metrics_by_semantic(semantic: Semantic) -> list[MetricDef]:
    return [m for m in REGISTRY.values() if m.semantic == semantic]
