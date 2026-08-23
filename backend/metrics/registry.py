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
        canonical_key="opp_half_pass_share", name_zh="进攻半场传球占比",
        # 命名红线(方案 §三 图2):不得冒充 Opta/StatsBomb 的正式 Field Tilt,
        # 这里只是同一批字段的比值代理,口径未经对方公开方法论核实。
        explanation_zh="传球里有多大比例发生在对方半场——数字越高,说明比赛越多时间是在对方门前打,不是我们自己倒球。这是本站用现有数据算的代理指标,不是 Opta/StatsBomb 的官方 Field Tilt。",
        numerator="opposition_half_passes", denominator="passes", unit="%",
        direction="style_only", semantic="style", min_sample=5,
        missing_policy="分子或分母该场缺失时整场排除,不按 0/0 计入",
        eligible_positions=None, venue_sensitive="home_away_split_required",
        opponent_adjustment_policy="not_validated",
        coverage_note="四大联赛 2025-08 起 2892 队场实测:opposition_half_passes 100.0%,passes 100.0%",
        source_field="fact_team_match_stats.extra_json.{opposition_half_passes,passes}",
        methodology_version="v1",
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


REGISTRY: dict[str, MetricDef] = {
    **ATTACK_CHAIN,
    **POSSESSION_CONTROL,
    **DEFENSIVE_PRESSURE,
    **MATCHUP_SITUATION,
    **GOALKEEPER,
}


def get_metric(canonical_key: str) -> MetricDef:
    """按 key 取指标定义;找不到直接抛错——不允许静默回退到一个未声明的
    字段(那正是本注册表要杜绝的"数据库字段直接推到页面")。"""
    return REGISTRY[canonical_key]


def metrics_by_semantic(semantic: Semantic) -> list[MetricDef]:
    return [m for m in REGISTRY.values() if m.semantic == semantic]
