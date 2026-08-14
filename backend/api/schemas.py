"""/api/v1 响应 DTO(Pydantic 单一真源,OpenAPI → TypeScript 代码生成)。

字段级门禁核心(CLAUDE.md §8.2):
- PredictionFreeDTO 物理上只有 top_outcome/top_probability——受限字段连 key 都不存在,
  不是 null 占位;
- PredictionFullDTO 才包含完整 home/draw/away。
服务端按 entitlement 选择 DTO 类构造,测试扫描完整 JSON 证明免费响应无受限字段。
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


class PredictionFreeDTO(BaseModel):
    """匿名/Free:只有最高一项。禁止出现另外两项概率的任何形式。"""

    tier: Literal["free"] = "free"
    top_outcome: Literal["home", "draw", "away"]
    top_probability: float
    meta: PredictionMeta


class PredictionFullDTO(BaseModel):
    """Pro/Premium:完整 WDL。"""

    tier: Literal["full"] = "full"
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
    prediction: Optional[Union[PredictionFullDTO, PredictionFreeDTO]] = None


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
    # 首页/列表页胜平负概率条(由 Bet365 1x2 折算,见 WinProbabilityDTO)。
    # 缺赔率或去水失败的比赛此字段为 None——前端据此不画条,不补 0、不猜。
    win_probability: Optional[WinProbabilityDTO] = None
    # 2026-08-13:匿名/免费用户现在能在列表里看到所有联赛的比赛(不再整场隐藏
    # 未持有的联赛),但内容仍按权限投影——本场所属联赛不在请求者权限内时为
    # True,此时 win_probability 恒为 None(不下发再靠前端遮挡),点击详情页
    # 走既有登录门禁。默认 False:仅 /matches 列表端点按权限显式计算,其余复用
    # MatchSummary 的端点(如 /leagues/{id}/fixtures)本身已在端点层做整体
    # 403 门禁,能返回的比赛必然不受限。
    requires_login: bool = False


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


class MatchDetailResponse(BaseModel):
    match: MatchSummary
    data_updated_at: Optional[str] = None
    home_form: list[TeamFormEntry] = []
    away_form: list[TeamFormEntry] = []
    # 每日精选"存在性":本场是否有已发布(赛前)推荐单。只有布尔,
    # 不含方向/赔率/内容(2026-08-11 站长授权公开存在性)。
    reco_published: bool = False


class LeagueInfo(BaseModel):
    league_id: int
    code: str
    name_zh: str
    name_en: str
    entitlement: str                       # 访问所需 entitlement
    accessible: bool                       # 按当前请求者的 entitlement 计算
    requires_login: bool                   # 三段可见性:该联赛是否需登录(league:top5)
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
    """

    schedule_updated_at: Optional[str] = None   # 最近一次赛程同步结论性成功(written/off_season)
    odds_updated_at: Optional[str] = None       # 最近一次赔率观测(observed_at)
    reco_updated_at: Optional[str] = None       # 最近一次推荐单发布(非 draft)


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
    """silver_team_season_stats 免费字段投影。角球/红黄牌/零封/BTTS 是付费深度
    报告字段,物理上不在本 DTO(不是 null 占位,更不是取了再藏)。"""

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
    prediction_member: Optional[BundlePredictionMember] = None   # 免费层置 None,受限字段不下发
    evidence: list[BundleEvidenceItem]
    counter_evidence: list[BundleEvidenceItem]
    uncertainty: list[BundleUncertaintyItem]
    odds_timeline: list[BundleOddsPoint]         # 仅 odds:history_full,否则空数组
    # 赔率覆盖档位:full_timeline=真实观测时间序列 / open_close_only=旧资产
    # 两点摘要(8,336 场,审计 B6 前 bundle 对它们完全失明)/ none=无赔率
    odds_coverage_tier: Literal["full_timeline", "open_close_only", "none"] = "none"
    # 两点摘要(无时间戳,绝不混入 odds_timeline);仅 odds:history_full,否则 None
    odds_summary_points: Optional[list[LegacyOddsPointItem]] = None
    cooccurring_events: list[BundleCoocEvent]    # 仅 report:deep,否则空数组
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


class MarketCardDTO(BaseModel):
    market: str
    label: str
    line: float
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
    tier: Literal["full", "delayed_summary"]
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


class CooccurrenceFullDTO(BaseModel):
    """report:deep:含明细。"""

    match_id: int
    count: int
    items: list[CooccurrenceItem]


class CooccurrenceSummaryDTO(BaseModel):
    """匿名/free:只有计数,items 显式为 null。"""

    match_id: int
    count: int
    items: None = None
    note: str


CooccurrenceResponse = Union[CooccurrenceFullDTO, CooccurrenceSummaryDTO]


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

class RedeemResponse(BaseModel):
    status: Literal["ok"]
    subscription_id: str
    plan_id: str
    starts_at: str
    ends_at: str


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


class RedeemCodeCreatedItem(BaseModel):
    id: str
    code: str                              # 明文只在创建响应展示一次


class AdminCodesCreatedResponse(BaseModel):
    codes: list[RedeemCodeCreatedItem]


class AdminRedeemCodeItem(BaseModel):
    id: str
    plan_id: str
    duration_days: int
    batch_id: Optional[str] = None
    status: str
    created_at: str
    expires_at: Optional[str] = None
    used_by: Optional[str] = None
    used_at: Optional[str] = None


class AdminCodesListResponse(BaseModel):
    codes: list[AdminRedeemCodeItem]


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
    """distance-to-kickoff >7 天:物理上没有 tendency/confidence/reason/locked/
    p_home/p_draw/p_away——不是"这几个字段为 null",是这个 JSON 形状根本不含它们。"""

    availability: Literal["upcoming"]


class LegacyWdlLiveLockedMatch(_LegacyWdlMatchBase):
    """distance-to-kickoff ≤7 天且未付费:有 tendency/confidence/reason/locked=true,
    物理上没有 p_home/p_draw/p_away(不是放了真值再指望前端隐藏)。"""

    availability: Literal["live"]
    tendency: Optional[Literal["home", "draw", "away"]]
    confidence: Optional[Literal["normal", "low"]]
    reason: Optional[str]
    locked: Literal[True]


class LegacyWdlLiveFullMatch(_LegacyWdlMatchBase):
    """distance-to-kickoff ≤7 天且已付费:locked=false,额外带完整三项概率。"""

    availability: Literal["live"]
    tendency: Optional[Literal["home", "draw", "away"]]
    confidence: Optional[Literal["normal", "low"]]
    reason: Optional[str]
    locked: Literal[False]
    p_home: Optional[float]
    p_draw: Optional[float]
    p_away: Optional[float]


LegacyWdlMatchEntry = Union[
    LegacyWdlUpcomingMatch, LegacyWdlLiveLockedMatch, LegacyWdlLiveFullMatch
]


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
    result: Optional[Literal["win", "lose", "push"]] = None   # 命中/未中/走水


class RecoSlipDTO(BaseModel):
    id: str
    slip_date: str
    title: str
    note: Optional[str] = None
    combo_type: Literal["single", "parlay"]
    status: Literal["draft", "published", "settled", "voided"]
    result: Optional[Literal["win", "lose", "push"]] = None
    return_units: Optional[float] = None      # 1 单位注;净盈亏 = return_units - 1
    published_at: Optional[str] = None
    settled_at: Optional[str] = None
    edit_count: int                            # 人工内容可修正,修正历史公开可查
    last_edited_at: str
    legs: list[RecoLegDTO]


class RecoDailyResponse(BaseModel):
    """付费面(reco:daily):近 30 天推荐单(含未结算)。"""

    window_days: int
    slips: list[RecoSlipDTO]


class RecoTrackRecordSummaryDTO(BaseModel):
    settled_count: int
    win_count: int
    lose_count: int
    push_count: int
    voided_count: int                          # 作废单列,不进分母也不消失
    hit_rate: Optional[float] = None           # win/(win+lose),push 不计分母
    net_units: float


class RecoTrackRecordResponse(BaseModel):
    """登录面(reco:track_record):结算/作废归档全历史,命中未中全展示。"""

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
    voided_count: int                          # 作废单列,不进分母也不消失
    hit_rate: Optional[float] = None           # win/(win+lose),push 不计分母
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
    leg_results: dict[str, Literal["win", "lose", "push"]]   # leg_id → 结果


class RecoVoidBody(BaseModel):
    reason: str


class RecoSlipCreatedDTO(BaseModel):
    id: str


class RecoSettledDTO(BaseModel):
    result: Literal["win", "lose", "push"]
    return_units: float


class AdminRecoSlipsResponse(BaseModel):
    total: int
    slips: list[RecoSlipDTO]
