"""/api/v1 响应 DTO(Pydantic 单一真源,OpenAPI → TypeScript 代码生成)。

预测字段(2026-08-16 产品权限口径修正,经用户批准):除"每日精选"外,网站
所有比赛内容全部免费,包括匿名用户——登录与内容分层彻底解耦。PredictionDTO
是唯一的预测 DTO,恒含完整 home/draw/away 胜平负概率,不再有按 entitlement
二选一的 free/full 两套 DTO。
"""

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ── 全站统一错误 DTO(唯一真源;所有 JSON 错误响应顶层只有这三个字段) ──
#
# 除 /readyz 的 503(独立运维 allowlist,ReadyzProblemsDTO)外,全部非 2xx JSON
# 响应——HTTPException(字符串或 dict detail)、RequestValidationError(422)、
# WechatDisabledException(503)、未捕获异常(500)、未知路由(404)——都经
# backend.api.error_handlers 的集中式异常处理器归一成同一顶层结构,不再有
# 裸 {"detail": ...}、{"code","message"}(缺 details)等并存的形状。

class ApiErrorDTO(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None


def error_responses(*codes: int) -> dict:
    """router/endpoint 的 responses={} 声明:4xx/5xx 统一 ApiErrorDTO。"""
    return {code: {"model": ApiErrorDTO} for code in codes}


class PredictionMeta(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_version_id: str
    probability_source: Literal["MODEL", "MARKET_BASELINE", "UNAVAILABLE"]
    generated_at: str
    published_at: Optional[str] = None
    locked_at: Optional[str] = None
    input_cutoff_at: Optional[str] = None
    status: Literal["published", "locked", "retracted"]
    confidence: Optional[str] = None
    edit_count: int = 0
    last_edited_at: Optional[str] = None


class PredictionDTO(BaseModel):
    """公开正式预测(2026-08-16 起恒为完整 WDL):除"每日精选"外普通比赛内容
    全部免费,包括匿名——不再有 free/full 两套 DTO,也不再有 tier 字段。"""

    top_outcome: Literal["home", "draw", "away"]
    home_probability: float
    draw_probability: float
    away_probability: float
    expected_home_goals: Optional[float] = None
    expected_away_goals: Optional[float] = None
    prediction_hash: str
    meta: PredictionMeta


class PredictionResponse(BaseModel):
    match_id: int
    available: bool
    reason: Optional[str] = None          # 不可用时的诚实说明
    prediction: Optional[PredictionDTO] = None


class MeUser(BaseModel):
    id: str
    display_name: str
    role: str


class MeDTO(BaseModel):
    authenticated: bool
    user: Optional[MeUser] = None
    plan: str
    entitlements: list[str]
    session_expires_at: Optional[str] = None


class TeamRef(BaseModel):
    team_id: Optional[int] = None
    name: str                              # 中文优先,无映射回退英文
    name_en: Optional[str] = None
    crest_url: Optional[str] = None         # 同源版本化媒体地址;无可验证本地文件时为 null


class WinProbabilityDTO(BaseModel):
    """Bet365 1x2 赔率去水后的胜平负概率(backend/queries/odds.py::latest_1x2_by_match)。

    只有一个 observed_at——这是某一时刻的快照,不是实时数据,前端必须展示
    这个时间戳(§6.2 不伪装)。字段缺失的比赛物理上不出现在列表里,不补 0。
    """

    p_home: float
    p_draw: float
    p_away: float
    observed_at: str


class MatchSummary(BaseModel):
    match_id: int
    league_id: int
    season: str
    date_utc: str                          # 比赛自然日(match_date)
    kickoff_at_utc: Optional[str] = None   # 精确 UTC 开球时刻,可空(§6.2.1,不伪装精确)
    round: Optional[str] = None
    status: str
    home: TeamRef
    away: TeamRef
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    sync_state: Optional[Literal["FRESH", "STALE", "UNAVAILABLE"]] = None
    data_updated_at: Optional[str] = None
    last_success_sync_at: Optional[str] = None
    next_planned_sync_at: Optional[str] = None
    probability_source: Optional[Literal["MODEL", "MARKET_BASELINE", "UNAVAILABLE"]] = None
    odds_observation_count: Optional[int] = None
    # 赔率覆盖档位(D8):full_timeline=完整观测时间线 / open_close_only=旧资产
    # 两点摘要 / none=无赔率。列表徽标据此渲染,避免 content=odds 把两档混为一谈。
    # 保持 Optional:联赛 fixtures 端点不算这个字段(None=未计算,不是"无赔率")。
    odds_coverage_tier: Optional[Literal["full_timeline", "open_close_only", "none"]] = None
    # 赔率新鲜度(P1.1,修复数据诚实性缺陷):odds_coverage_tier 只回答"这场比赛
    # 历史上有没有过赔率数据"——哪怕最后一次真实观测是好几天前,只要曾经采集
    # 成功过,tier 依然是 full_timeline。这两个字段回答"数据是不是新的":
    # odds_last_observed_at 是该场 NowGoal 完整时间线赔率最后一次真实 observed_at
    # (只覆盖 full_timeline;legacy 两点摘要是一次性历史导入,不伪造它的新鲜度,
    # 该场此字段为 None);odds_freshness_state 复用全仓库统一的
    # FRESH/STALE/UNAVAILABLE 三态(与 sync_state 同一套值但语义不同——sync_state
    # 描述"每日内容生成 run 的状态",这个字段描述"这场比赛赔率数据本身多久没刷新",
    # 二者不得混用)。
    odds_last_observed_at: Optional[str] = None
    odds_freshness_state: Optional[Literal["FRESH", "STALE", "UNAVAILABLE"]] = None
    # 首页/列表页胜平负概率条(由 Bet365 1x2 折算,见 WinProbabilityDTO)。
    # 缺赔率或去水失败的比赛此字段为 None——前端据此不画条,不补 0、不猜。
    win_probability: Optional[WinProbabilityDTO] = None


class MatchListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    # 回显赛季上下文(D4,对齐 LeagueFixturesResponse):?season=2019/2020 的
    # total:0 必须可解释——available_seasons 告诉客户端哪些赛季真的有数据。
    season: Optional[str] = None
    available_seasons: list[str] = []
    matches: list[MatchSummary]


class LeagueFixturesResponse(BaseModel):
    """/leagues/{id}/fixtures 专属响应——与 MatchListResponse 分开定义,不共用。

    刻意不用 response_model_exclude_unset:MatchSummary 的可选字段(如
    data_updated_at/probability_source)由内容状态投影按分支选择性写入,
    exclude_unset 会递归进嵌套模型,导致同一响应内不同行的键集不一致,
    且与 /api/v1/matches 复用同一个 MatchSummary 却呈现不同形状。
    """

    league_id: int
    season: Optional[str] = None
    available_seasons: list[str]
    total: int
    limit: int
    offset: int
    matches: list[MatchSummary]
    empty_reason: Optional[str] = None


class TeamFormEntry(BaseModel):
    match_id: int
    date_utc: str
    opponent: TeamRef
    venue: Literal["home", "away"]
    goals_for: int
    goals_against: int
    result: Literal["W", "D", "L"]


class MatchDetailSummary(MatchSummary):
    """详情页专属:在 MatchSummary 之上追加球场/天气/裁判字段(2026-08-20)。

    刻意不加进 MatchSummary 本体——那会让 /api/v1/matches 列表响应里每张
    卡片都多出这几个恒为 null 的字段(列表接口从不查询这些列,§10.3 契约
    纪律要求"每个 operation 声明自己的成功响应模型",不是全站共用一个胖
    DTO)。只有 /matches/{id} 详情端点用这个子类。

    全部可空:Referee/Temperature/Wind_Speed 是 dim_match 基线列,产线覆盖
    率分别约 71%/16%/16%(不是每场都有);Venue_*/Weather_Description 是本次
    新增列,历史比赛无 FotMob 原始快照可回填,恒为 NULL。前端按字段各自
    判空隐藏,不得为凑齐卡片而编造占位值。

    temperature_c/wind_speed_kmh 的单位是摄氏度/公里每小时——FotMob 原始
    payload 不带单位标注,这是其公开网页版的通行展示单位(metric 地区),
    非本仓库实测确认;如后续证明有出入,只改这两个字段的换算,不影响
    其它字段。
    """

    referee: Optional[str] = None
    temperature_c: Optional[int] = None
    wind_speed_kmh: Optional[int] = None
    weather_description: Optional[str] = None
    venue_name: Optional[str] = None
    venue_city: Optional[str] = None
    venue_country: Optional[str] = None


class MatchDetailResponse(BaseModel):
    match: MatchDetailSummary
    data_updated_at: Optional[str] = None
    home_form: list[TeamFormEntry] = []
    away_form: list[TeamFormEntry] = []
    # 每日精选"存在性":本场是否有已发布(赛前)推荐单。只有布尔,
    # 不含方向/赔率/内容(2026-08-11 站长授权公开存在性)。
    reco_published: bool = False


class LeagueInfo(BaseModel):
    """2026-08-16 起:除"每日精选"外全部联赛对任何人(含匿名)恒可访问——
    entitlement/accessible/requires_login 三个只为登录门禁服务的字段已彻底
    从响应模型删除(不是置为恒 true/false 留着)。"""

    league_id: int
    code: str
    name_zh: str
    name_en: str
    current_season: Optional[str] = None
    # 该联赛在 dim_match 里真实存在的全部赛季(升序,来自数据而非配置)。
    # 全站比赛列表用它渲染赛季筛选,避免前端写死赛季名单。
    available_seasons: list[str] = []
    data_status: Literal["AVAILABLE", "NOT_SYNCED"]
    data_updated_at: Optional[str] = None


class TrackRecordSample(BaseModel):
    """公开正式样本(永久资格,CLAUDE.md §9.1)。

    撤回(status='retracted')与被取代(superseded_by 非空)的正式样本同样出现
    在列表并计入指标分母;修正链通过 superseded_by / correction_of 双向可查。
    """

    model_config = ConfigDict(protected_namespaces=())

    snapshot_id: str                       # 登记簿快照 id(修正链中区分新旧版本)
    match_id: int
    kickoff_at_utc: str
    home: TeamRef
    away: TeamRef
    home_probability: float
    draw_probability: float
    away_probability: float
    predicted_outcome: Literal["home", "draw", "away"]
    actual_outcome: Optional[Literal["home", "draw", "away"]] = None
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    hit: Optional[bool] = None
    status: str                            # locked / retracted(撤回样本透明展示,不退出统计)
    superseded_by: Optional[str] = None    # 被哪条修正版快照取代(旧版仍公开、仍计入指标)
    correction_of: Optional[str] = None    # 本条是哪条旧快照的修正版
    superseded_note: Optional[str] = None  # 被取代时的人话说明
    edit_count: int = 0                    # 该快照被直接修正过的次数(公开可查,不暴露操作者/原因)
    last_edited_at: Optional[str] = None   # 最近一次修正时间
    model_version_id: str
    published_at: str
    locked_at: str
    prediction_hash: str


class TrackRecordMetrics(BaseModel):
    sample_size: int
    accuracy: Optional[float] = None
    brier: Optional[float] = None
    log_loss: Optional[float] = None
    rps: Optional[float] = None
    evaluated_at: Optional[str] = None


class TrackRecordResponse(BaseModel):
    total: int
    retracted_count: int
    superseded_count: int = 0              # 被修正版取代的正式样本数(透明标注,不从 total 扣除)
    limit: int
    offset: int
    metrics: Optional[TrackRecordMetrics] = None
    samples: list[TrackRecordSample]
    empty_reason: Optional[str] = None     # 无正式样本时的诚实说明


class FreshnessResponse(BaseModel):
    """首页「今日更新状态」:三条独立的最近成功时间戳,互不代表彼此。

    任一环节尚无成功记录时为 null(如实展示,不用当前时间顶替)。

    每条时间戳配一个仓库统一的 FRESH/STALE/UNAVAILABLE 三态(与 MatchSummary.
    sync_state/odds_freshness_state 同一套值,backend/queries/freshness.py::
    classify_freshness 计算)——裸 HH:mm 时间戳看不出这是今天还是好几天前,
    也看不出数据源当前是否已经失败,状态字段补上这两个判断。
    """

    schedule_updated_at: Optional[str] = None   # 最近一次赛程同步结论性成功(written/off_season)
    odds_updated_at: Optional[str] = None       # 最近一次赔率观测(observed_at)
    reco_updated_at: Optional[str] = None       # 最近一次推荐单发布(非 draft)
    schedule_state: Optional[Literal["FRESH", "STALE", "UNAVAILABLE"]] = None
    odds_state: Optional[Literal["FRESH", "STALE", "UNAVAILABLE"]] = None
    reco_state: Optional[Literal["FRESH", "STALE", "UNAVAILABLE"]] = None


# ── 通用 ───────────────────────────────────────────────────

class OkDTO(BaseModel):
    status: Literal["ok"]


class HealthzDTO(BaseModel):
    ok: bool


class ReadyzProblemsDTO(BaseModel):
    """/readyz 503 时的诚实故障说明。"""

    ok: bool
    problems: list[str]


# ── 联赛积分榜 ─────────────────────────────────────────────

class StandingRow(BaseModel):
    """fact_league_table 行 + team 引用(列名保留数据库原始大小写)。"""

    Team_ID: Optional[int] = None
    Team_Name: Optional[str] = None
    position: Optional[int] = None
    played: Optional[int] = None
    wins: Optional[int] = None
    draws: Optional[int] = None
    losses: Optional[int] = None
    goals_for: Optional[int] = None
    goals_against: Optional[int] = None
    goal_diff: Optional[int] = None
    points: Optional[int] = None
    qual_color: Optional[str] = None
    team: TeamRef
    # 分组赛制(如 J1 2026 的 "100 Year Vision League East/West")下该行所属分组;
    # 单表联赛为 None。见 queries/matches.py::standings 的分组回退。
    group_name: Optional[str] = None
    # 以下四项只有 table_type='xg' 的行才有值(FotMob 官方 xG 榜口径,非本站模型)
    xg: Optional[float] = None
    xg_conceded: Optional[float] = None
    x_points: Optional[float] = None
    x_position: Optional[int] = None
    # 实际积分 − 按 xG 应得积分(正=多拿分)。None 表示该档没有这个数据,不是 0。
    x_points_diff: Optional[float] = None
    x_position_diff: Optional[float] = None


class StandingsResponse(BaseModel):
    """empty_reason 只在无数据时出现(端点用 response_model_exclude_unset 保持现状)。"""

    table_type: str = "all"

    league_id: int
    season: Optional[str] = None
    available_seasons: list[str]
    rows: list[StandingRow]
    empty_reason: Optional[str] = None


# ── 联赛球队/球员赛季统计(免费字段投影,CLAUDE.md §3) ──────

class TeamSeasonStatRow(BaseModel):
    """silver_team_season_stats 全字段投影(2026-08-16 起,除"每日精选"外
    普通比赛内容全部免费——角球/红黄牌/零封/BTTS 与射门/xG 等字段同属免费
    内容,不再是付费深度报告字段)。"""

    team: TeamRef
    matches_played: Optional[int] = None
    avg_total_shots: Optional[float] = None
    avg_shots_on_target: Optional[float] = None
    avg_possession: Optional[float] = None
    avg_expected_goals: Optional[float] = None
    avg_expected_goals_on_target: Optional[float] = None
    # xG 拆解:运动战 + 定位球 ≈ 非点球 xG,总 xG − 非点球 xG = 点球 xG
    # (实测:阿森纳 2025/2026 运动战 1.102 + 定位球 0.542 = 1.644 ≈ 非点球 1.643)。
    avg_expected_goals_open_play: Optional[float] = None
    avg_expected_goals_set_play: Optional[float] = None
    avg_expected_goals_non_penalty: Optional[float] = None
    # 每场被创造 xG(fact_league_table 的 xg 档换算,与 xG 运气榜同源)。
    # 不是每个联赛赛季都有该档,缺失时为 None —— 不补 0。
    avg_expected_goals_conceded: Optional[float] = None
    # 原付费深度报告字段(2026-08-16 起全字段免费投影)
    avg_corners: Optional[float] = None
    avg_fouls: Optional[float] = None
    avg_yellow_cards: Optional[float] = None
    avg_red_cards: Optional[float] = None
    clean_sheets: Optional[int] = None
    btts_matches: Optional[int] = None
    btts_pct: Optional[float] = None


class TeamStatsResponse(BaseModel):
    league_id: int
    season: Optional[str] = None
    available_seasons: list[str]
    rows: list[TeamSeasonStatRow]
    empty_reason: Optional[str] = None


class LeagueSeasonSummary(BaseModel):
    """silver_league_season_summary 一行(百分比字段均为 0-100)。"""

    total_matches: Optional[int] = None
    home_win_pct: Optional[float] = None
    draw_pct: Optional[float] = None
    away_win_pct: Optional[float] = None
    btts_pct: Optional[float] = None
    clean_sheet_pct: Optional[float] = None
    avg_total_goals: Optional[float] = None
    home_away_goal_diff: Optional[float] = None


class GoalMinuteBucket(BaseModel):
    bucket: str                            # "0-15" … "76-90"
    goal_count: Optional[int] = None
    pct: Optional[float] = None


class ScoreDistributionRow(BaseModel):
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    match_count: Optional[int] = None
    pct: Optional[float] = None


class OverUnderRow(BaseModel):
    threshold: float                       # 0.5 / 1.5 / 2.5 / 3.5 / 4.5 / 5.5
    over_count: Optional[int] = None
    under_count: Optional[int] = None
    over_pct: Optional[float] = None
    under_pct: Optional[float] = None


class LeagueSeasonProfileResponse(BaseModel):
    """联赛速览:四张银层表的联合投影(免费面)。

    任一子块无数据时为空列表 / None —— 不补零,由前端渲染空态。
    """

    league_id: int
    season: Optional[str] = None
    available_seasons: list[str]
    summary: Optional[LeagueSeasonSummary] = None
    goal_minutes: list[GoalMinuteBucket] = []
    score_distribution: list[ScoreDistributionRow] = []
    over_under: list[OverUnderRow] = []
    empty_reason: Optional[str] = None


class PlayerBoardEntry(BaseModel):
    player_id: str
    name: str                              # 中文短名 > 中文全名 > 来源英文名 > id
    name_en: Optional[str] = None
    team: TeamRef
    rank: Optional[int] = None
    value: Optional[float] = None


class PlayerBoard(BaseModel):
    stat_name: str                         # goals / goal_assist / expected_goals / ...
    label_zh: str
    entries: list[PlayerBoardEntry]


class PlayersResponse(BaseModel):
    league_id: int
    season: Optional[str] = None
    available_seasons: list[str]
    boards: list[PlayerBoard]
    empty_reason: Optional[str] = None


# ── analysis_bundle(比赛详情公开投影 + Studio 完整版共用子模型) ──

class BundleEvidenceItem(BaseModel):
    side: str                              # home / away / draw
    kind: str                              # form / xg / draw_risk / ...
    text: str


class BundleUncertaintyItem(BaseModel):
    kind: str
    text: str


class BundlePredictionPublic(BaseModel):
    top_outcome: Literal["home", "draw", "away"]
    top_probability: float


class BundlePredictionMember(BaseModel):
    home_probability: float
    draw_probability: float
    away_probability: float
    expected_home_goals: Optional[float] = None
    expected_away_goals: Optional[float] = None
    status: str
    prediction_hash: str


class LegacyOddsPointItem(BaseModel):
    """旧项目历史赔率的两点摘要(初盘/临场),无观测时间戳(§6.2:不伪装)。

    /odds 端点的 summary_points 与 analysis/studio bundle 的
    odds_summary_points 共用本模型——刻意没有任何时间字段,绝不拿
    ingested_at 或开球时间顶替观测时间。
    """
    market: str                            # 1x2 / ah / ou
    period: Literal["initial", "latest"]
    source: str                            # asset_a_json / asset_b_footballdata / asset_b_nowgoal
    provider: str
    line: Optional[float] = None           # ah/ou 盘口线;line>0=主队让球
    home_or_over: float
    draw: Optional[float] = None           # 仅 1x2
    away_or_under: float


class BundleOddsPoint(BaseModel):
    # observed_at 必填:odds_timeline 只承载真实观测时间序列。旧资产两点摘要
    # 没有观测时间戳,走 odds_summary_points(LegacyOddsPointItem),不进本模型。
    market: str
    company: str
    observed_at: str
    payload: dict


class BundleCoocEvent(BaseModel):
    delta_seconds: int
    market: str
    field: str
    prev_value: Optional[str] = None
    new_value: Optional[str] = None
    moved_at: str
    event_type: str
    detail_json: str


class BundleChartSpec(BaseModel):
    id: str
    type: str
    title: str
    data: dict


class BundleScriptSection(BaseModel):
    id: str
    title: str
    text: str


class BundleSubtitleCue(BaseModel):
    start: float
    end: float
    text: str


class BundleSourceNote(BaseModel):
    kind: str
    text: str


class AnalysisBundleDTO(BaseModel):
    """GET /matches/{id}/analysis:公开投影(无 subtitle_cues,带 cooccurrence_count)。"""

    model_config = ConfigDict(protected_namespaces=())

    bundle_version: str
    built_at: str
    match: MatchSummary
    data_cutoff_at: Optional[str] = None
    model_version: Optional[str] = None
    probability_source: Literal["MODEL", "MARKET_BASELINE", "UNAVAILABLE"]
    prediction_public: Optional[BundlePredictionPublic] = None
    # None 仅表示该场尚无已发布预测(与权限无关,2026-08-16 起恒完整投影)
    prediction_member: Optional[BundlePredictionMember] = None
    evidence: list[BundleEvidenceItem]
    counter_evidence: list[BundleEvidenceItem]
    uncertainty: list[BundleUncertaintyItem]
    odds_timeline: list[BundleOddsPoint]         # 恒完整返回(2026-08-16 起不再按权限投影)
    # 赔率覆盖档位:full_timeline=真实观测时间序列 / open_close_only=旧资产
    # 两点摘要(8,336 场,审计 B6 前 bundle 对它们完全失明)/ none=无赔率
    odds_coverage_tier: Literal["full_timeline", "open_close_only", "none"] = "none"
    # 两点摘要(无时间戳,绝不混入 odds_timeline);None 仅表示该场没有两点摘要数据
    odds_summary_points: Optional[list[LegacyOddsPointItem]] = None
    cooccurring_events: list[BundleCoocEvent]    # 恒完整返回(2026-08-16 起不再按权限投影)
    chart_specs: list[BundleChartSpec]
    script_sections: list[BundleScriptSection]
    source_notes: list[BundleSourceNote]
    bundle_hash: str
    cooccurrence_count: int


class StudioBundleDTO(BaseModel):
    """GET /studio/matches/{id}/bundle:完整 bundle(含 subtitle_cues,不做投影)。"""

    model_config = ConfigDict(protected_namespaces=())

    bundle_version: str
    built_at: str
    match: MatchSummary
    data_cutoff_at: Optional[str] = None
    model_version: Optional[str] = None
    probability_source: Literal["MODEL", "MARKET_BASELINE", "UNAVAILABLE"]
    prediction_public: Optional[BundlePredictionPublic] = None
    prediction_member: Optional[BundlePredictionMember] = None
    evidence: list[BundleEvidenceItem]
    counter_evidence: list[BundleEvidenceItem]
    uncertainty: list[BundleUncertaintyItem]
    odds_timeline: list[BundleOddsPoint]
    odds_coverage_tier: Literal["full_timeline", "open_close_only", "none"] = "none"
    odds_summary_points: Optional[list[LegacyOddsPointItem]] = None
    cooccurring_events: list[BundleCoocEvent]
    chart_specs: list[BundleChartSpec]
    script_sections: list[BundleScriptSection]
    subtitle_cues: list[BundleSubtitleCue]
    source_notes: list[BundleSourceNote]
    team_style_profile: Optional[dict] = None
    social_profiles: dict[str, dict] = Field(default_factory=dict)
    bundle_hash: str


# ── 赛前市场卡(结论 + 折叠归因,见 backend/queries/market_cards.py) ──

class MarketFactorSideDTO(BaseModel):
    """某一侧(for=本队自己创造 / against=本队对手创造)的历史均值汇总。
    avg 为 None 表示样本不足(<3 场)或该指标缺失,不是 0——0 是"历史均值
    确实为 0"的合法值,不能拿来表示"没有数据"。"""

    avg: Optional[float] = None
    n: int


class MarketDriverFactorDTO(BaseModel):
    key: str
    for_: MarketFactorSideDTO = Field(alias="for")
    against: MarketFactorSideDTO

    model_config = ConfigDict(populate_by_name=True)


class MarketLineCalibrationDTO(BaseModel):
    """折叠区"本市场各盘口线的历史回测"一行:market.lines(该市场全部已标定
    线,不止默认线)中某一条的标定结果,与卡片顶层 line/signal_grade/
    hit_rate/lean(结论区,只看默认线,B6:不换线)完全独立的第二组数据,
    纯粹是"我们对这条线做过什么离线回测"的透明度展示。

    signal_grade 为 None 时(该线从未标定过,或标定了但外样本不单调),
    hit_rate 与 sample_size 必须同为 None——由后端在这里就置空,不是前端
    选择性隐藏(B2:未定级的线禁止展示命中率数值)。
    """

    line: float
    # 是否是卡片顶层结论区当前正在使用的那条线(真实盘口或统计参考线)——
    # 仅供前端提示"这行对应上面的结论",不影响 hit_rate/signal_grade 的取值。
    is_default: bool
    signal_grade: Optional[Literal["★★★", "★★", "★"]] = None
    hit_rate: Optional[float] = None
    sample_size: Optional[int] = None


class MarketCardDTO(BaseModel):
    market: str
    label: str
    line: float
    # "market" = 这场比赛真实抓到的 NowGoal 盘口线(goals/corners 有实时轮询时);
    # "statistical" = 统计参考线(calibrate_markets.py 选定的竞彩常见线,
    # yellow_cards 恒为这档——NowGoal 没有罚牌市场)。前端必须区分展示,
    # 不能把统计参考线包装成"这场比赛的真实盘口"。
    line_source: Literal["market", "statistical"]
    # 实际用于查 market_calibration 的线——corners/角球这类计数型市场,line
    # 若是整数(如 10.0)而 market.lines 只标定了半线,则等价映射到 line+0.5
    # (整数随机变量 X>L ⟺ X>L+0.5,精确等价,非估算)。等于 line 时说明未
    # 映射;不等于 line 时前端需要披露"本卡采用哪条线的回测口径"。
    # data_quality 不是 "ok" 时恒为 None。
    calibration_line: Optional[float] = None
    # 两队各自历史均值之和(与 calibrate_markets.py 标定时的定义完全一致)。
    estimate: Optional[float] = None
    bucket_index: Optional[int] = None
    # 该档在历史回测外样本里的真实命中率,0..1。
    hit_rate: Optional[float] = None
    sample_size: Optional[int] = None
    # 三星/两星/一星;外样本分档不单调时为 None——此时前端只展示数据面板,
    # 不渲染任何方向性文案。
    signal_grade: Optional[Literal["★★★", "★★", "★"]] = None
    lean: Optional[Literal["over", "under"]] = None
    calibration_scope: Optional[Literal["league", "all_leagues"]] = None
    data_quality: Literal["ok", "insufficient_sample", "no_history", "no_calibration"]
    driver_factors: list[MarketDriverFactorDTO]
    driver_factors_away: list[MarketDriverFactorDTO]
    # 该市场全部已标定盘口线(market.lines,不止默认线)各自的回测结果——
    # 折叠区"本市场各盘口线的历史回测"小节的数据源。默认线的判定字段
    # (line/signal_grade/hit_rate/lean,以上)一字不变(B6);这里纯粹是
    # 额外的透明度展示,data_quality 不是 "ok" 时恒为空列表。
    lines: list[MarketLineCalibrationDTO] = []
    # market_calibration.calibrated_at——卡片默认线所查到的那条标定记录的
    # 离线回测运行时间(UTC ISO)。没有查到任何标定档位时为 None。
    calibrated_at: Optional[str] = None
    # no_calibration 的原因细分(2026-08-19):真实盘口线常见整数线
    # (10.0/9.0/…),根本不在 market.lines 这个已标定线集合内
    # ("line_not_calibrated"),与"线本身在集合内、但这次查不到任何标定行"
    # ("line_unresolved",理论上不该发生)是两种不同的诚实降级,前端文案不
    # 应该混为一谈。data_quality 不是 "no_calibration" 时恒为 None。
    no_calibration_reason: Optional[Literal["line_not_calibrated", "line_unresolved"]] = None


class MatchMarketCardsResponse(BaseModel):
    match_id: int
    window: int
    cards: list[MarketCardDTO]


# ── 赔率与同期事件 ─────────────────────────────────────────

class OddsSnapshotItem(BaseModel):
    market: str
    company_id: str
    company_name: str
    market_phase: str                      # pre_match / in_play / unknown
    source_updated_at: Optional[str] = None
    observed_at: str
    payload: dict


class MatchOddsAvailableDTO(BaseModel):
    match_id: int
    available: Literal[True]
    # 2026-08-16 起恒为 "full"(不再有 delayed_summary 分层,除"每日精选"外
    # 全站比赛内容与登录/付费彻底解耦)。
    tier: Literal["full"]
    # full_timeline: 真实观测时间序列(bronze_ng_odds_snap);
    # open_close_only: 旧项目两点摘要(bronze_legacy_odds_summary),无时间戳,
    # 前端不得将其渲染为走势图。
    coverage_tier: Literal["full_timeline", "open_close_only"]
    home_away_inverted: bool
    observation_count: int
    display_mode: Literal["current_odds", "odds_changes"]
    snapshots: list[OddsSnapshotItem]
    summary_points: Optional[list[LegacyOddsPointItem]] = None
    note: Optional[str] = None


class MatchOddsUnavailableDTO(BaseModel):
    match_id: int
    available: Literal[False]
    reason: str


MatchOddsResponse = Union[MatchOddsAvailableDTO, MatchOddsUnavailableDTO]


class CooccurrenceItem(BaseModel):
    window_seconds: int
    delta_seconds: int
    computed_at: str
    market: str
    company_id: str
    field: str
    prev_value: Optional[str] = None
    new_value: Optional[str] = None
    odds_moved_at: str
    event_type: str                        # lineup_change / sideline_change
    detail_json: str
    event_moved_at: str


class CooccurrenceResponse(BaseModel):
    """同期事件(时间共现,不声称因果)。2026-08-16 起恒含明细,不再区分
    免费(仅计数)/付费(明细)两套 DTO。"""

    match_id: int
    count: int
    items: list[CooccurrenceItem]
    note: Optional[str] = None


# ── 单场完赛事实报告(/matches/{id}/report,详情页 阵容/统计/事件 tab) ──
#
# 全部为已完赛的历史事实(阵容/事件/射门/统计),不含模型输出、不含赔率方法论,
# 属"足球数据"面(CLAUDE.md §8:数据不收费),与积分榜/赛季球队统计同档匿名可见。
# Optional 字段的 null 语义是"来源该场没有这项统计",不是权限脱敏。
# 枚举形字段(event_type/outcome 等)用 str 不用 Literal:来源新增取值不该让
# 整个响应验证失败(真实库已见 9 种 event_type,曾被误以为只有 5 种)。


class MatchReportCoverage(BaseModel):
    lineup: bool
    events: bool
    shots: bool
    team_stats: bool
    player_stats: bool


class MatchReportLineupPlayer(BaseModel):
    player_id: str
    name: str                                    # 中文短名 > 中文全名 > 来源英文 > id
    name_en: Optional[str] = None
    shirt_number: Optional[str] = None
    position_group: Optional[str] = None         # GK / DEF / MID / FWD
    is_starter: bool
    is_captain: bool
    country_code: Optional[str] = None
    rating: Optional[float] = None               # fact_match_lineup.rating(评分唯一真源)
    sub_in_time: Optional[int] = None
    sub_out_time: Optional[int] = None
    pitch_x: Optional[float] = None              # 0..1 归一化站位(来源已解好,仅首发有)
    pitch_y: Optional[float] = None


class MatchReportLineupTeam(BaseModel):
    team_id: int
    is_home: bool
    formation: Optional[str] = None              # 如 "3-4-2-1"
    starters: list[MatchReportLineupPlayer]
    bench: list[MatchReportLineupPlayer]


class MatchReportEvent(BaseModel):
    event_index: int
    event_type: str    # Goal/Card/Substitution/Half/AddedTime/VAR/MissedPenalty/…
    minute: Optional[int] = None
    is_added_time: bool = False
    minutes_added: Optional[int] = None
    is_home: Optional[bool] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    player_name: Optional[str] = None            # 已按 i18n 回退链中文化
    card_type: Optional[str] = None              # Yellow / Red / YellowRed
    assist_player_name: Optional[str] = None
    sub_in_player_name: Optional[str] = None
    sub_out_player_name: Optional[str] = None
    half_kind: Optional[Literal["HT", "FT", "AET"]] = None
    """仅 event_type='Half' 有值:HT=中场,FT=常规时间结束,AET=加时赛结束。
    查询层已按白名单收窄,来源出现第四种值时投影为 None,不会撑破本 Literal。"""
    is_own_goal: bool = False
    """乌龙球。注意:本 DTO 的 is_home 是**受益方**(得分的一队),而
    player_name 是对方把球踢进本方球门的球员——这是来源(FotMob)的语义,
    全库 1066/1066 核验一致,不是数据错误。"""


class MatchReportShot(BaseModel):
    player_id: str
    player_name: Optional[str] = None
    team_id: int
    is_home: bool
    minute: Optional[int] = None
    period: Optional[str] = None                 # 含 PenaltyShootout(前端排除出射门图)
    x: Optional[float] = None                    # 原始坐标(0..105);主客队同朝一端记录,
    y: Optional[float] = None                    # 客队镜像是前端展示层变换,后端不改数据
    xg: Optional[float] = None
    xgot: Optional[float] = None
    situation: Optional[str] = None
    outcome: Optional[str] = None                # Goal / AttemptSaved / Miss / Post
    shot_type: Optional[str] = None


class MatchReportTeamStat(BaseModel):
    team_id: int
    is_home: bool
    goals: Optional[float] = None
    possession: Optional[float] = None
    expected_goals: Optional[float] = None
    expected_goals_open_play: Optional[float] = None
    expected_goals_set_play: Optional[float] = None
    expected_goals_non_penalty: Optional[float] = None
    expected_goals_on_target: Optional[float] = None
    total_shots: Optional[float] = None
    shots_on_target: Optional[float] = None
    shots_off_target: Optional[float] = None
    blocked_shots: Optional[float] = None
    shots_inside_box: Optional[float] = None
    shots_outside_box: Optional[float] = None
    shots_woodwork: Optional[float] = None
    big_chance: Optional[float] = None
    big_chance_missed: Optional[float] = None
    touches_opp_box: Optional[float] = None
    passes: Optional[float] = None
    accurate_passes: Optional[float] = None
    own_half_passes: Optional[float] = None
    opposition_half_passes: Optional[float] = None
    long_balls_accurate: Optional[float] = None
    accurate_crosses: Optional[float] = None
    player_throws: Optional[float] = None
    offsides: Optional[float] = None
    tackles: Optional[float] = None
    interceptions: Optional[float] = None
    shot_blocks: Optional[float] = None
    clearances: Optional[float] = None
    keeper_saves: Optional[float] = None
    duel_won: Optional[float] = None
    ground_duels_won: Optional[float] = None
    aerials_won: Optional[float] = None
    dribbles_succeeded: Optional[float] = None
    corners: Optional[float] = None
    fouls: Optional[float] = None
    yellow_cards: Optional[float] = None
    red_cards: Optional[float] = None


class MatchReportPlayerStat(BaseModel):
    player_id: str
    name: str
    team_id: int
    is_home: bool
    is_goalkeeper: bool
    minutes_played: Optional[float] = None
    rating: Optional[float] = None               # 来自 lineup.rating,非 rating_title
    goals: Optional[float] = None
    assists: Optional[float] = None
    expected_goals: Optional[float] = None
    expected_assists: Optional[float] = None
    shots_on_target: Optional[float] = None
    shots_off_target: Optional[float] = None
    accurate_passes: Optional[float] = None
    chances_created: Optional[float] = None
    touches: Optional[float] = None
    touches_opp_box: Optional[float] = None
    dribbles_succeeded: Optional[float] = None
    tackles: Optional[float] = None
    clearances: Optional[float] = None
    interceptions: Optional[float] = None
    duel_won: Optional[float] = None
    aerials_won: Optional[float] = None
    fouls: Optional[float] = None
    corners: Optional[float] = None
    offsides: Optional[float] = None
    saves: Optional[float] = None
    goals_conceded: Optional[float] = None
    goals_prevented: Optional[float] = None


class MatchReportAvailableDTO(BaseModel):
    match_id: int
    available: Literal[True]
    coverage: MatchReportCoverage
    lineups: list[MatchReportLineupTeam]
    events: list[MatchReportEvent]
    shots: list[MatchReportShot]
    team_stats: list[MatchReportTeamStat]
    player_stats: list[MatchReportPlayerStat]


class MatchReportUnavailableDTO(BaseModel):
    match_id: int
    available: Literal[False]
    reason: str


MatchReportResponse = Union[MatchReportAvailableDTO, MatchReportUnavailableDTO]


# ── 赛前预览(/matches/{id}/preview,详情页"数据"tab 阵容/风格/球员子tab) ──
#
# 与 MatchReport 不同:这里全是"赛前也能给"的历史聚合与已采集快照,不是赛后
# 事实——各模块各自独立降级(阵容缺快照/象限视角点数不足/球员未达出场门槛/
# 门将字段部分缺失),不设全局 available=False 开关。


class MatchPreviewWindowDTO(BaseModel):
    """单支球队"近 N 场"的真实场次数与日期区间(CLAUDE.md §11.2 措辞纪律:
    不能只写"近 5 场"三个字了事)。主客队各自独立的窗口,不合并成一个——
    历史场次不同(如升班马)时合并会掩盖其中一队样本更少的事实。"""

    matches: int
    from_: str = Field(alias="from")
    to: str

    model_config = ConfigDict(populate_by_name=True)


class MatchPreviewPlayerDTO(BaseModel):
    id: int
    name: str
    shirt_number: Optional[str] = None
    # 归一化球场坐标(0..1,y 越小离己方球门越近),来源没给(旧快照/替补席从不
    # 带坐标)时如实 None——前端据此判断能否画真实站位球场图,不得补 0
    # (backend/providers/fotmob_snapshots.py 的 pos_x/pos_y 字段取舍注释)。
    pos_x: Optional[float] = None
    pos_y: Optional[float] = None


class MatchPreviewCoachDTO(BaseModel):
    """FotMob content.lineup.{home,away}Team.coach 的最小子集(只有 id/name)。
    2026-08-18 之前写入的快照 payload 没有这个键,读侧如实 None,不回填猜测值。"""

    id: Optional[int] = None
    name: str


class MatchPreviewLineupSideDTO(BaseModel):
    team_id: Optional[int] = None
    formation: Optional[str] = None
    coach: Optional[MatchPreviewCoachDTO] = None
    starters: list[MatchPreviewPlayerDTO]
    subs: list[MatchPreviewPlayerDTO]


class MatchPreviewLineupsDTO(BaseModel):
    """lineup_type 绝大多数真实值是 "lastStarting11"(上一场首发,不是本场
    确认阵容)——必须原样透传,前端负责翻译成"预计首发·基于上一场"这类
    措辞,后端不得转译成"首发"造成误读。两队都无快照时 home/away 为 None。"""

    lineup_type: Optional[str] = None
    source: Optional[str] = None
    observed_at: Optional[str] = None
    home: Optional[MatchPreviewLineupSideDTO] = None
    away: Optional[MatchPreviewLineupSideDTO] = None


class MatchPreviewSidelinedPlayerDTO(BaseModel):
    id: int
    name: str
    reason: Optional[str] = None
    # 原样保留来源口径("A few days" / "Day to day"),不换算成具体日期
    expected_return: Optional[str] = None


class MatchPreviewSidelinedDTO(BaseModel):
    home: list[MatchPreviewSidelinedPlayerDTO]
    away: list[MatchPreviewSidelinedPlayerDTO]


class MatchPreviewStylePointDTO(BaseModel):
    team_id: int
    name: str
    x: Optional[float] = None
    y: Optional[float] = None


class MatchPreviewStyleViewDTO(BaseModel):
    id: str
    tab: str
    title: str
    x_label: str
    y_label: str
    digits: int
    quadrants: list[str]
    points: list[MatchPreviewStylePointDTO]
    # 前端象限判定(quadOf)靠这个字段决定"y 高是不是好",不能假定越高越好——
    # 见 backend/queries/team_style_preview.py 的方向语义注释。
    y_lower_is_better: bool = False


class MatchPreviewAttackSourceDTO(BaseModel):
    # 稳定的 Situation 枚举原文(如 "RegularPlay",无法识别的来源为 "其他")。
    # 2026-08-23 新增:此前前端只拿到中文 label 就当 key 用来查配色表,
    # 后端改中文措辞会让配色静默掉回灰色兜底(未触发任何报错)。
    key: str
    label: str
    shots: int
    shot_pct: float
    xg: Optional[float] = None


class MatchPreviewAttackSourcesDTO(BaseModel):
    home: list[MatchPreviewAttackSourceDTO]
    away: list[MatchPreviewAttackSourceDTO]


class MatchPreviewChainMetricDTO(BaseModel):
    value: Optional[float] = None
    # False 表示窗口内至少有一场缺这个字段——前端不得把 value 当"完整合计"
    # 展示,必须用 matches_with_data / complete 标注部分覆盖(同 Phase 0.2 教训)。
    complete: bool = False
    matches_with_data: int = 0


class MatchPreviewAttackChainDTO(BaseModel):
    tier: str
    matches: int
    label_zh: str
    # 显式声明分组——前端不用自己猜哪个字段属于"进攻产量"还是"转化效率"。
    volume_keys: list[str]
    conversion_keys: list[str]
    opp_half_pass_share: MatchPreviewChainMetricDTO
    touches_opp_box: MatchPreviewChainMetricDTO
    shots: MatchPreviewChainMetricDTO
    shots_on_target: MatchPreviewChainMetricDTO
    xg: MatchPreviewChainMetricDTO
    xgot: MatchPreviewChainMetricDTO
    # 转化效率:同场配对相除,零分母/无配对场次时 value=None(见
    # backend/queries/attack_chain.py 模块 docstring)。
    shots_per_100_box_touches: MatchPreviewChainMetricDTO
    shot_on_target_rate: MatchPreviewChainMetricDTO
    xg_per_shot: MatchPreviewChainMetricDTO
    xgot_per_sot: MatchPreviewChainMetricDTO


class MatchPreviewAttackChainsDTO(BaseModel):
    home: MatchPreviewAttackChainDTO
    away: MatchPreviewAttackChainDTO


class MatchPreviewPossessionControlDTO(BaseModel):
    tier: str
    matches: int
    label_zh: str
    possession: MatchPreviewChainMetricDTO
    pass_accuracy: MatchPreviewChainMetricDTO
    opp_half_pass_share: MatchPreviewChainMetricDTO
    touches_opp_box: MatchPreviewChainMetricDTO


class MatchPreviewPossessionControlsDTO(BaseModel):
    home: MatchPreviewPossessionControlDTO
    away: MatchPreviewPossessionControlDTO


class MatchPreviewDefensivePressureDTO(BaseModel):
    tier: str
    matches: int
    label_zh: str
    shots_faced: MatchPreviewChainMetricDTO
    shots_on_target_faced: MatchPreviewChainMetricDTO
    xga: MatchPreviewChainMetricDTO
    box_shots_faced: MatchPreviewChainMetricDTO


class MatchPreviewDefensivePressuresDTO(BaseModel):
    home: MatchPreviewDefensivePressureDTO
    away: MatchPreviewDefensivePressureDTO


class MatchPreviewMatchupSituationDTO(BaseModel):
    key: str
    label: str
    own_shots_pg: Optional[float] = None
    own_xg_pg: Optional[float] = None
    own_xg_complete: bool = False
    # 2026-08-23 站长决定:某场比赛只要有 1 脚射门缺 xG,就把那场整场从这一
    # 情境类型的 xG 统计里剔除(不是丢那一脚拿"部分已知"凑合计),用剩下的
    # "干净"场次重新算均值——own_xg_matches 就是这个干净场次数,可能小于
    # 比赛信息卡展示的窗口场次(profile.matches);own_xg_complete=False
    # (真的没有任何干净场次可用)时恒为 None。box_shots 的 xG 来自坐标法
    # 聚合(2026-08-23,约 98% 与官方射门次数计数一致,非官方直发字段),
    # 与其余三类同一套完整性规则,不代表精度完全相同。
    own_xg_matches: Optional[int] = None
    conceded_shots_pg: Optional[float] = None
    conceded_xg_pg: Optional[float] = None
    conceded_xg_complete: bool = False
    conceded_xg_matches: Optional[int] = None
    # 验收返工二(独立复核第二轮,P1):显式声明这一类该用哪个字段判定
    # "关键对位",前端不得再用 `own_xg_pg ?? own_shots_pg` 隐式猜单位——
    # 那在 xG 不完整时会把射门次数当 xG 用,去跟 xG 口径的联赛基准比
    # (真实复现:比赛 5868022/球队 10205)。四类(含禁区内射门)恒为 "xg"
    # (2026-08-23 起,禁区内射门的 xG 改用坐标法聚合,不再用 "shots")。
    comparison_metric: str = "xg"
    # 己方/对手的"比较值"——xg 类型时就是 own_xg_pg/conceded_xg_pg 本身
    # (且要求 xg_complete),shots 类型时就是 own_shots_pg/conceded_shots_pg。
    own_comparison_value: Optional[float] = None
    conceded_comparison_value: Optional[float] = None
    # 联赛在同主客场景、同 tier(venue_full 目标只用 venue_full 参考队,
    # venue_partial 目标只用 venue_partial 参考队,不跨档位混算)、同
    # Situation、同 comparison_metric 口径下的基准均值。目标自己是
    # mixed/unavailable 时恒为 None(不生成基准)。
    own_baseline_value: Optional[float] = None
    conceded_baseline_value: Optional[float] = None
    # 己方比较值、对手比较值、己方基准、对手基准四者是否都非空——只有
    # True 时前端才允许把这一类计入"关键对位"候选,不再用单一的
    # baseline_available 布尔糊住"己方/对手基准是否各自齐全"这两件事。
    comparison_complete: bool = False


class MatchPreviewMatchupProfileDTO(BaseModel):
    tier: str
    matches: int
    label_zh: str
    situations: list[MatchPreviewMatchupSituationDTO]


class MatchPreviewMatchupProfilesDTO(BaseModel):
    home: MatchPreviewMatchupProfileDTO
    away: MatchPreviewMatchupProfileDTO


class MatchPreviewPlayerShareRowDTO(BaseModel):
    player_id: str
    name: str
    pct: float
    appearances: int
    minutes: float
    count: float


class MatchPreviewPlayerShareBlockDTO(BaseModel):
    id: str
    title: str
    metric: str
    rows: list[MatchPreviewPlayerShareRowDTO]


class MatchPreviewKeyPlayersDTO(BaseModel):
    home: list[MatchPreviewPlayerShareBlockDTO]
    away: list[MatchPreviewPlayerShareBlockDTO]


class MatchPreviewKeeperDTO(BaseModel):
    player_id: str
    name: str
    appearances: int
    saves: float
    # None 表示该门将窗口内一场 expected_goals_on_target_faced 都没有(不是
    # "面对 0 次射正")。xgot_faced_complete 标注这个合计是否覆盖了全部出场
    # 场次——只有 True 时前端才能放心用它现算"阻止进球"估算,否则连估算
    # 都不该给(用不完整分母算出的数会看似精确、实则误导)。
    xgot_faced: Optional[float] = None
    xgot_faced_complete: bool = False
    goals_conceded: float
    goals_prevented: Optional[float] = None


class MatchPreviewKeepersDTO(BaseModel):
    home: list[MatchPreviewKeeperDTO]
    away: list[MatchPreviewKeeperDTO]


class MatchPreviewResponse(BaseModel):
    match_id: int
    observed_at: str
    home_window: Optional[MatchPreviewWindowDTO] = None
    away_window: Optional[MatchPreviewWindowDTO] = None
    lineups: MatchPreviewLineupsDTO
    sidelined: MatchPreviewSidelinedDTO
    style_views: list[MatchPreviewStyleViewDTO]
    attack_sources: MatchPreviewAttackSourcesDTO
    attack_chains: MatchPreviewAttackChainsDTO
    possession_controls: MatchPreviewPossessionControlsDTO
    defensive_pressures: MatchPreviewDefensivePressuresDTO
    matchup_profiles: MatchPreviewMatchupProfilesDTO
    key_players: MatchPreviewKeyPlayersDTO
    keepers: MatchPreviewKeepersDTO


# ── 模型指标与产品 ─────────────────────────────────────────

class ModelVersionDTO(BaseModel):
    id: str
    algorithm: str
    description: str
    trained_at: Optional[str] = None
    train_range: Optional[str] = None
    created_at: str
    params: dict
    dev_metrics: dict                      # 研发期回测指标(非正式样本口径)


class OfficialEvaluationDTO(BaseModel):
    sample_size: int
    accuracy: Optional[float] = None
    brier: Optional[float] = None
    log_loss: Optional[float] = None
    rps: Optional[float] = None
    calibration: list[dict]
    evaluated_at: str


class MarketBaselineDTO(BaseModel):
    status: str                            # UNVERIFIED:未完成可复现的收盘赔率评估
    note: str


class ModelMetricsResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_versions: list[ModelVersionDTO]
    official_evaluation: Optional[OfficialEvaluationDTO] = None
    official_evaluation_note: Optional[str] = None
    market_baseline: MarketBaselineDTO


class PlanDTO(BaseModel):
    id: str
    name_zh: str
    description: str
    rank: int
    entitlements: list[str]


class ProductDTO(BaseModel):
    id: str
    plan_id: str
    name_zh: str
    description: str
    duration_days: int
    price_cents: int
    currency: str
    sort_order: int


class ProductsResponse(BaseModel):
    plans: list[PlanDTO]
    products: list[ProductDTO]


# ── 登录用户(会员/账户) ───────────────────────────────────

class FavoriteItem(BaseModel):
    match_id: int
    created_at: str


class FavoritesResponse(BaseModel):
    favorites: list[FavoriteItem]


class AccountIdentity(BaseModel):
    provider: str
    provider_app_id: str
    created_at: str
    last_used_at: Optional[str] = None


class AccountSubscription(BaseModel):
    id: str
    plan_id: str
    status: str
    starts_at: str
    ends_at: str
    source: str
    created_at: str


class AccountSession(BaseModel):
    id: str
    created_at: str
    last_seen_at: Optional[str] = None
    expires_at: str
    user_agent: Optional[str] = None
    is_current: int                        # SQLite CASE 结果 0/1(保持原样,不转 bool)


class AccountRecovery(BaseModel):
    available: bool
    note: str


class AccountResponse(BaseModel):
    user: MeUser
    plan: str
    entitlements: list[str]
    identities: list[AccountIdentity]
    subscriptions: list[AccountSubscription]
    sessions: list[AccountSession]
    recovery: AccountRecovery


# ── 认证(JSON 响应端点;webhook 的 text/plain、application/xml
#    在 routes_auth 的 responses={} 声明) ────────────────────

class DeviceLoginCreatedDTO(BaseModel):
    request_id: str
    secret: str                            # 只留在浏览器内存,不进二维码
    qr_url: str
    expires_at: str


class DeviceClaimResultDTO(BaseModel):
    status: Literal["pending", "claimed"]


# ── Admin ─────────────────────────────────────────────────

class AdminUserItem(BaseModel):
    id: str
    display_name: str
    role: str
    status: str
    created_at: str
    last_login_at: Optional[str] = None
    plan_id: str
    plan_ends_at: Optional[str] = None


class AdminUsersResponse(BaseModel):
    total: int
    users: list[AdminUserItem]


class GrantResultDTO(BaseModel):
    subscription_id: str
    plan_id: str
    starts_at: str
    ends_at: str


class AdminPredictionItem(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    match_id: int
    kickoff_at_utc: Optional[str] = None
    model_version_id: str
    generated_at: str
    published_at: Optional[str] = None
    locked_at: Optional[str] = None
    status: str
    is_official: int
    visibility: str
    home_win: float
    draw: float
    away_win: float
    confidence: Optional[str] = None
    edit_count: int = 0
    last_edited_at: Optional[str] = None


class AdminPredictionsResponse(BaseModel):
    counts: dict[str, int]
    predictions: list[AdminPredictionItem]


class EditPredictionResponse(BaseModel):
    edit_id: Optional[str] = None    # None = 本次调用未产生实质变化(no-op)
    changed_fields: list[str]
    edit_count: int


class PublishUpcomingFailedItem(BaseModel):
    id: str
    reason: str


class PublishUpcomingResponse(BaseModel):
    published: int
    failed: list[PublishUpcomingFailedItem]


class AuditLogItem(BaseModel):
    id: int
    actor_user_id: Optional[str] = None
    actor_type: str
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    detail_json: str
    created_at: str


class AuditLogsResponse(BaseModel):
    logs: list[AuditLogItem]


# ── 任务健康(job_runs)与来源健康(source_health,只读,P1.5) ──────────

class JobRunItem(BaseModel):
    id: str
    job_name: str
    status: str
    attempt: int
    max_attempts: int
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    input_count: Optional[int] = None
    output_count: Optional[int] = None
    error_summary: Optional[str] = None    # 已经过 _sanitize_summary 脱敏
    created_at: str


class AdminJobsResponse(BaseModel):
    jobs: list[JobRunItem]


class SourceHealthItem(BaseModel):
    id: int
    source: str
    checked_at: str
    ok: int
    latency_ms: Optional[int] = None
    error_summary: Optional[str] = None    # 已经过 _sanitize_summary 脱敏


class AdminSourceHealthResponse(BaseModel):
    source_health: list[SourceHealthItem]


class XrefItem(BaseModel):
    id: int
    fotmob_match_id: int
    provider: str
    provider_match_id: str
    home_away_inverted: int
    confidence: float
    verified: int
    method: str                            # auto / manual
    kickoff_diff_seconds: Optional[int] = None
    review_status: str
    created_at: str
    updated_at: str


class XrefListResponse(BaseModel):
    counts: dict[str, int]
    xrefs: list[XrefItem]


class XrefReviewResultDTO(BaseModel):
    status: Literal["ok"]
    xref_id: int
    review_status: str


# ── Studio ─────────────────────────────────────────────────

class StudioDraftCreatedDTO(BaseModel):
    draft_id: str
    title: str
    bundle_hash: str


class StudioDraftListItem(BaseModel):
    id: str
    match_id: int
    title: str
    status: str                            # draft / reviewed / published
    created_at: str
    updated_at: str


class StudioDraftsResponse(BaseModel):
    drafts: list[StudioDraftListItem]


class StudioDraftDetailDTO(BaseModel):
    """bundle 为创建草稿时冻结的 analysis_bundle 快照(历史版本形状可能不同,故为 dict)。"""

    id: str
    match_id: int
    title: str
    status: str
    bundle: dict
    overrides: dict
    created_at: str
    updated_at: str


class StudioExportClientDTO(BaseModel):
    """PNG:前端 DOM→PNG 生成,服务端只登记审计。"""

    model_config = ConfigDict(protected_namespaces=())

    job_id: str
    side: Literal["client"]
    data_cutoff_at: Optional[str] = None
    model_version: Optional[str] = None
    profile_id: str


class StudioExportServerDTO(BaseModel):
    """txt/json/srt:服务端生成可下载文件。"""

    model_config = ConfigDict(protected_namespaces=())

    job_id: str
    side: Literal["server"]
    download_url: str
    data_cutoff_at: Optional[str] = None
    model_version: Optional[str] = None
    profile_id: str


StudioExportResponse = Union[StudioExportServerDTO, StudioExportClientDTO]


# ── legacy /api/league/* 契约收口(backend/api_server.py,deprecated 不再扩展)──
#
# 这 4 个端点此前完全没有 response_model(OpenAPI 200 body 为空 schema)。
# 以下模型逐字段对照 api_server.py 的真实 SQL 与字典组装逻辑精确建模,不改变任何
# 运行时业务语义;可空性依据实际来源判定:
#   - NotStarted 比赛的 home_score/away_score、Match_Round、qual_color 等确实可为 NULL
#     (schema.py 的 DIM_MATCH_COLUMNS 均无 NOT NULL 约束);
#   - silver 聚合表的 avg_*/*_pct 字段来自 backend/silver/build_silver.py 的
#     EXTRA_JSON_MEAN_FIELDS(`.get()`,可空)或除零保护(如 goal_minute_buckets.pct
#     在 total_goals=0 时显式为 None),按来源如实标注 Optional;
#   - score_distribution 的 home_score/away_score 来自仅 status='Finish' 的比赛
#     (build_silver.py 的 `_matches` 已过滤),此上下文内保证非空,故为必填 int。
#
# wdl-predictions 的核心诚实性(CLAUDE.md §3):'upcoming' / 'live+未付费' / 'live+已付费'
# 是三种物理上不同的 JSON 形状,不是同一个 dict 里若干字段可空/可选——用三个各自
# `extra="forbid"` 的模型组成 Union(LegacyWdlUpcomingMatch / LiveLockedMatch /
# LiveFullMatch),而不是把 tendency/locked/p_home 等标成模糊 Optional 掩盖差异。
# 三个变体的必填字段互不相同且禁止额外字段,故 Pydantic 对任一真实运行时字典只会
# 匹配唯一一个变体,不存在歧义;legacy 端点错误响应与 v1 共用同一个 ApiErrorDTO
# (backend.api.error_handlers 统一处理器归一,不再有 legacy 独立错误结构)。


class LegacyStanding(BaseModel):
    position: int
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    goal_diff: int
    points: int
    qual_color: Optional[str]           # key 总是存在(SELECT 直出),值可空
    team_id: int
    team_name_zh: Optional[str]         # key 总是存在(无 i18n 映射时值为 None)
    crest_url: Optional[str] = None      # legacy 排名页迁移完成前的同源队徽地址


class LegacyLeagueSummary(BaseModel):
    total_matches: int
    home_win_pct: Optional[float]
    draw_pct: Optional[float]
    away_win_pct: Optional[float]
    avg_total_goals: Optional[float]


class LegacyTeamSeasonStats(BaseModel):
    team_id: int
    team_name_zh: Optional[str]
    matches_played: int
    avg_total_shots: Optional[float]
    avg_shots_on_target: Optional[float]
    avg_possession: Optional[float]
    avg_expected_goals: Optional[float]
    avg_expected_goals_on_target: Optional[float]


class LegacyPlayerLeaderboardEntry(BaseModel):
    player_id: str
    Player_Name: Optional[str]
    Team_ID: Optional[int]
    Team_Name: Optional[str]
    rank: int
    value: float
    player_name_zh: Optional[str]
    player_name_zh_short: Optional[str]
    team_name_zh: Optional[str]


class LegacyPlayerLeaderboard(BaseModel):
    label_zh: str
    entries: list[LegacyPlayerLeaderboardEntry]


class LeagueOverviewResponse(BaseModel):
    league_id: int
    season: str
    standings: list[LegacyStanding]
    # key 总是存在(return 语句无条件赋值);值本身可空(无该赛季汇总行时为 None)。
    league_summary: Optional[LegacyLeagueSummary]
    team_stats: list[LegacyTeamSeasonStats]
    player_leaderboards: dict[str, LegacyPlayerLeaderboard]


class LegacyOverUnderThreshold(BaseModel):
    threshold: float
    over_count: int
    under_count: int
    over_pct: Optional[float]
    under_pct: Optional[float]


class LegacyScoreDistributionEntry(BaseModel):
    home_score: int
    away_score: int
    match_count: int
    pct: Optional[float]


class LegacyGoalMinuteBucket(BaseModel):
    bucket: str
    goal_count: int
    # key 总是存在;值在 total_goals=0 时显式为 None(build_silver.py 除零保护)。
    pct: Optional[float]


class LegacyTeamBettingStats(BaseModel):
    team_id: int
    team_name_zh: Optional[str]
    matches_played: int
    avg_corners: Optional[float]
    avg_yellow_cards: Optional[float]
    avg_red_cards: Optional[float]
    clean_sheets: int
    btts_matches: int
    btts_pct: Optional[float]


class LeagueBettingResponse(BaseModel):
    league_id: int
    season: str
    over_under: list[LegacyOverUnderThreshold]
    score_distribution: list[LegacyScoreDistributionEntry]
    goal_minute_buckets: list[LegacyGoalMinuteBucket]
    team_betting_stats: list[LegacyTeamBettingStats]


class LegacyMatchRow(BaseModel):
    Match_ID: int
    Date: Optional[str]
    home_score: Optional[int]           # NotStarted 比赛比分为 NULL
    away_score: Optional[int]
    status: Optional[str]
    Match_Round: Optional[str]
    home_team_id: int
    away_team_id: int
    home_team_name_zh: Optional[str]
    away_team_name_zh: Optional[str]


class LeagueMatchesResponse(BaseModel):
    league_id: int
    season: str
    matches: list[LegacyMatchRow]


class _LegacyWdlMatchBase(BaseModel):
    """三个 WDL variant 共有的基础字段(entry 字典无条件赋值,key 总是存在,
    值可能为 None——`Optional[X]` 无默认值,required 但 nullable)。"""

    model_config = ConfigDict(extra="forbid")

    match_id: int
    date: Optional[str]
    round: Optional[str]
    status: Optional[str]
    home_team_id: int
    away_team_id: int
    home_team_name_zh: Optional[str]
    away_team_name_zh: Optional[str]
    days_until_kickoff: Optional[int]


class LegacyWdlUpcomingMatch(_LegacyWdlMatchBase):
    """distance-to-kickoff >7 天:物理上没有 tendency/confidence/reason/
    p_home/p_draw/p_away——不是"这几个字段为 null",是这个 JSON 形状根本不含
    它们(这是数据就绪状态,不是付费墙,2026-08-16 起未改变这条既有规则)。"""

    availability: Literal["upcoming"]


class LegacyWdlLiveMatch(_LegacyWdlMatchBase):
    """distance-to-kickoff ≤7 天:恒下发完整 tendency/confidence/reason/
    p_home/p_draw/p_away(2026-08-16 起除"每日精选"外全站比赛内容全部免费,
    不再有 locked 字段区分付费/未付费)。"""

    availability: Literal["live"]
    tendency: Optional[Literal["home", "draw", "away"]]
    confidence: Optional[Literal["normal", "low"]]
    reason: Optional[str]
    p_home: Optional[float]
    p_draw: Optional[float]
    p_away: Optional[float]


LegacyWdlMatchEntry = Union[LegacyWdlUpcomingMatch, LegacyWdlLiveMatch]


class LeagueWdlPredictionsResponse(BaseModel):
    league_id: int
    season: str
    matches: list[LegacyWdlMatchEntry]


# ── 每日精选(reco;人工推荐板块,与模型预测 DTO 彻底分开) ──

class RecoLegDTO(BaseModel):
    id: str
    match_id: Optional[int] = None
    match_desc: str
    market: str
    selection: str
    odds: float
    result: Optional[Literal["win", "lose", "push", "half_win", "half_loss"]] = None


class RecoSlipDTO(BaseModel):
    id: str
    slip_date: str
    title: str
    note: Optional[str] = None
    combo_type: Literal["single", "parlay"]
    status: Literal["draft", "published", "settled", "voided"]
    result: Optional[Literal["win", "lose", "push", "half_win", "half_loss"]] = None
    return_units: Optional[float] = None      # 1 单位注;净盈亏 = return_units - 1
    published_at: Optional[str] = None
    settled_at: Optional[str] = None
    edit_count: int                            # 人工内容可修正,修正历史公开可查
    last_edited_at: str
    legs: list[RecoLegDTO]


class RecoDailyUnlockedSlipDTO(RecoSlipDTO):
    """当前用户对该 slip 持有 active 按场授权时的完整投影(RecoSlipDTO 原样
    +access_required=False,供前端统一判断是否需要引导授权)。"""

    access_required: Literal[False] = False


class RecoDailyLockedSlipDTO(BaseModel):
    """当前用户对该 slip 没有 active 按场授权时的中性投影(2026-08-16,
    取代旧的全局 reco:daily 布尔权益门禁):只暴露"存在性 + 状态",标题/
    摘要/腿的市场选择赔率/思路说明整体不出现——受限字段物理不下发,不是
    置 null(与 CLAUDE.md §8.2 匿名概率边界同一纪律)。"""

    id: str
    slip_date: str
    status: Literal["published", "settled", "voided"]
    access_required: Literal[True] = True


RecoDailySlipItem = Union[RecoDailyUnlockedSlipDTO, RecoDailyLockedSlipDTO]


class RecoDailyResponse(BaseModel):
    """登录面(2026-08-16 起按"用户 + 单条 slip"授权,取代旧的 reco:daily
    付费全局布尔权益):近 30 天推荐单存在性全部可见,内容按 slip 分别授权
    (见 RecoDailySlipItem)。"""

    window_days: int
    slips: list[RecoDailySlipItem]


class RecoSlipPreviewResponse(BaseModel):
    """admin 用:该单如果 published,一个真实会员在 /reco/daily 会看到的内容
    形状——与 RecoDailyResponse 里的单据字段完全一致(同一个 RecoSlipDTO,
    同一套投影函数 q_reco.slip_member_preview/_slip_dto),不重新发明一套
    会员端 DTO。"""

    slip: RecoSlipDTO


class RecoTrackRecordSummaryDTO(BaseModel):
    settled_count: int
    win_count: int
    lose_count: int
    push_count: int
    # 四分之一盘口半赢/半输(2026-08-16):必须独立可见,不得被并入 win/lose,
    # 也不得从任何分类桶里消失——win+lose+push+half_win+half_loss == settled_count。
    half_win_count: int
    half_loss_count: int
    voided_count: int                          # 作废单列,不进分母也不消失
    # (win + 0.5*half_win) / (win+lose+half_win+half_loss);push 不计分母。
    hit_rate: Optional[float] = None
    net_units: float


class RecoTrackRecordResponse(BaseModel):
    """匿名可见(2026-08-16 起不再要求登录):结算/作废归档全历史,命中未中
    全展示——站点自身的历史运营记录,不属于"每日精选"按场授权的约束范围。"""

    summary: RecoTrackRecordSummaryDTO
    total: int
    slips: list[RecoSlipDTO]


class RecoOverviewResponse(BaseModel):
    """匿名聚合面:今日发布状态 + 近 30 天已结算汇总(无任何单据内容)。"""

    today_date: str                            # 北京时间自然日
    today_published_count: int
    today_latest_published_at: Optional[str] = None
    window_days: int
    settled_count: int
    win_count: int
    lose_count: int
    push_count: int
    # 四分之一盘口半赢/半输(2026-08-16):同 RecoTrackRecordSummaryDTO,必须
    # 独立可见,不得并入 win/lose 也不得凭空消失。
    half_win_count: int
    half_loss_count: int
    voided_count: int                          # 作废单列,不进分母也不消失
    # (win + 0.5*half_win) / (win+lose+half_win+half_loss);push 不计分母。
    hit_rate: Optional[float] = None
    net_units: float
    # 当前有已发布(赛前)推荐的比赛 id 列表——只有存在性,不含内容
    # (2026-08-11 站长授权;首页比赛卡"推荐已发布/待发布"状态用)。
    published_match_ids: list[int] = []


class RecoLegInput(BaseModel):
    match_desc: str
    market: str
    selection: str
    odds: float
    match_id: Optional[int] = None
    # ── 赔率合约溯源(可选;2026-08-16)──────────────────────────
    # 齐全(match_id + source_odds + odds_format + snapshot_ref)时后端判定
    # entry_type='provenance_bound',canonical_decimal_odds 由
    # backend/commands/reco_odds_contract.py::to_canonical_decimal() 计算,
    # 不直接采信这里传入的 odds(NowGoal 的 ou/ah/corners_ou 是港赔,常 <1)。
    # 缺任一字段则整条腿退回 entry_type='legacy_manual',odds 按原语义直接
    # 当十进制赔率使用(向后兼容旧的手打赔率入口)。
    source_odds: Optional[float] = None
    odds_format: Optional[Literal["decimal", "hk"]] = None
    provider: Optional[str] = None
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    snapshot_ref: Optional[str] = None
    observed_at: Optional[str] = None
    line: Optional[float] = None
    side: Optional[str] = None
    payload_hash: Optional[str] = None


class RecoSlipCreateBody(BaseModel):
    slip_date: str                             # YYYY-MM-DD(北京时间自然日)
    title: str
    note: Optional[str] = None
    legs: list[RecoLegInput]


class RecoSlipEditBody(BaseModel):
    title: Optional[str] = None
    note: Optional[str] = None
    slip_date: Optional[str] = None
    legs: Optional[list[RecoLegInput]] = None  # 传入即整组替换


class RecoSettleBody(BaseModel):
    # leg_id → 结果(half_win/half_loss = 四分之一盘口半赢半输,2026-08-16)
    leg_results: dict[str, Literal["win", "lose", "push", "half_win", "half_loss"]]


class RecoVoidBody(BaseModel):
    reason: str


class RecoSlipCreatedDTO(BaseModel):
    id: str


class RecoSettledDTO(BaseModel):
    result: Literal["win", "lose", "push", "half_win", "half_loss"]
    return_units: float


class RecoLegMatchResultDTO(BaseModel):
    home_score: int
    away_score: int


class RecoLegCornersDTO(BaseModel):
    home: float
    away: float


class AdminRecoLegDTO(BaseModel):
    """admin 面在会员 RecoLegDTO 基础上补充的运营信息(2026-08-16):
    entry_type 供前端提前识别"这条腿缺乏真实溯源、发布会被拒绝";
    match_result/corners 是已结算腿的结算依据(现算,不新增存储,缺失时
    保持 None);needs_review 是"待确认"标记——绝不通过任何写路径把它
    自动变成确定结果,只读展示。"""

    id: str
    match_id: Optional[int] = None
    match_desc: str
    market: str
    selection: str
    odds: float
    result: Optional[Literal["win", "lose", "push", "half_win", "half_loss"]] = None
    entry_type: Literal["provenance_bound", "legacy_manual"]
    match_result: Optional[RecoLegMatchResultDTO] = None
    corners: Optional[RecoLegCornersDTO] = None
    needs_review: bool = False
    needs_review_reason: Optional[str] = None


class AdminRecoSlipDTO(BaseModel):
    id: str
    slip_date: str
    title: str
    note: Optional[str] = None
    combo_type: Literal["single", "parlay"]
    status: Literal["draft", "published", "settled", "voided"]
    result: Optional[Literal["win", "lose", "push", "half_win", "half_loss"]] = None
    return_units: Optional[float] = None
    published_at: Optional[str] = None
    settled_at: Optional[str] = None
    settle_source: Optional[Literal["auto", "manual"]] = None
    edit_count: int
    last_edited_at: str
    legs: list[AdminRecoLegDTO]


class AdminRecoSlipsResponse(BaseModel):
    total: int
    slips: list[AdminRecoSlipDTO]


class RecoMatchCandidateDTO(BaseModel):
    """admin 录入每日精选用的比赛候选(从真实比赛卡片选,替代自由文本描述)。"""

    match_id: int
    league_id: int
    league_name: str
    home_name: str
    away_name: str
    kickoff_at_utc: Optional[str] = None
    status: str


class RecoMatchCandidatesResponse(BaseModel):
    matches: list[RecoMatchCandidateDTO]


class RecoOddsOptionDTO(BaseModel):
    """真实盘口选项(不去水的原始赔率),admin 从中选而不是手打数字。

    odds 字段延续原有语义:来源原始数值(1x2 是十进制;ou/corners_ou 是港赔,
    常 <1)。新增字段供 admin 提交腿时一并回传,后端据此判定 provenance_bound
    并正确换算(见 RecoLegInput/backend/commands/reco_odds_contract.py)。
    """

    market: str
    market_label: str
    selection: str
    odds: float
    company_name: str
    observed_at: str
    # ── 溯源字段(2026-08-16)──────────────────────────────────
    snapshot_id: int              # bronze_ng_odds_snap.id,可当稳定引用
    company_id: str
    line: Optional[float] = None  # ou/corners_ou/ah 的盘口线
    side: str                     # 程序可读选边(home/away/draw/over/under)
    odds_format: Literal["decimal", "hk"]   # 按 market 从 MARKET_ODDS_FORMAT 查出
    # 新鲜度(2026-08-16):复用既有 backend/queries/odds.py::
    # classify_odds_freshness/ODDS_FRESHNESS_STALE_HOURS,不新发明阈值。
    # 沿用仓库里已经在用的 FRESH/STALE/UNAVAILABLE 三态词汇(而不是另造一个
    # is_stale 布尔),与其它新鲜度展示位保持同一套前端词汇表。这里的
    # observed_at 恒非空,UNAVAILABLE 理论上不会命中,但保留三态是为了不让
    # 前端认识两套不同粒度的新鲜度语言。
    freshness: Literal["FRESH", "STALE", "UNAVAILABLE"]


class RecoMatchOddsOptionsResponse(BaseModel):
    match_id: int
    options: list[RecoOddsOptionDTO]


# ── 每日精选按场授权(2026-08-16,取代旧的 reco:daily 全局布尔权益) ──

class RecoAccessGrantDTO(BaseModel):
    id: str
    user_id: str
    slip_id: str
    # 具体到场次(2026-08-16):标题 + 日期,供"每日精选权限查询"和 admin
    # 授权面把一条授权记录识别为"是哪一场",不是只给一个不透明 UUID——这不是
    # 按场授权保护的正文本身(腿/赔率/理由仍然只在完整 RecoSlipDTO 里)。
    slip_title: str
    slip_date: str
    status: Literal["active", "revoked"]
    granted_at: str
    granted_by: str
    revoked_at: Optional[str] = None
    revoked_by: Optional[str] = None
    note: Optional[str] = None
    created_at: str
    updated_at: str


class RecoAccessGrantBody(BaseModel):
    user_id: str
    slip_id: str
    note: Optional[str] = None


class RecoAccessRevokeBody(BaseModel):
    reason: Optional[str] = None


class AdminRecoAccessGrantsResponse(BaseModel):
    total: int
    grants: list[RecoAccessGrantDTO]


class RecoMyAccessResponse(BaseModel):
    """个人"每日精选权限查询"(CLAUDE.md §8.1 允许要求登录的账户类个人
    功能之一):只含当前用户自己的授权记录。"""

    grants: list[RecoAccessGrantDTO]
