"""/api/v1 响应 DTO(Pydantic 单一真源,OpenAPI → TypeScript 代码生成)。

字段级门禁核心(CLAUDE.md §8.2):
- PredictionFreeDTO 物理上只有 top_outcome/top_probability——受限字段连 key 都不存在,
  不是 null 占位;
- PredictionFullDTO 才包含完整 home/draw/away。
服务端按 entitlement 选择 DTO 类构造,测试扫描完整 JSON 证明免费响应无受限字段。
"""

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict


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
    generated_at: str
    published_at: Optional[str] = None
    locked_at: Optional[str] = None
    input_cutoff_at: Optional[str] = None
    status: Literal["published", "locked", "retracted"]
    confidence: Optional[str] = None


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


class MatchListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    matches: list[MatchSummary]


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


class LeagueInfo(BaseModel):
    league_id: int
    code: str
    name_zh: str
    name_en: str
    entitlement: str                       # 访问所需 entitlement
    accessible: bool                       # 按当前请求者的 entitlement 计算


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


class StandingsResponse(BaseModel):
    """empty_reason 只在无数据时出现(端点用 response_model_exclude_unset 保持现状)。"""

    league_id: int
    season: Optional[str] = None
    available_seasons: list[str]
    rows: list[StandingRow]
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


class BundleOddsPoint(BaseModel):
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
    prediction_public: Optional[BundlePredictionPublic] = None
    prediction_member: Optional[BundlePredictionMember] = None   # 免费层置 None,受限字段不下发
    evidence: list[BundleEvidenceItem]
    counter_evidence: list[BundleEvidenceItem]
    uncertainty: list[BundleUncertaintyItem]
    odds_timeline: list[BundleOddsPoint]         # 仅 odds:history_full,否则空数组
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
    prediction_public: Optional[BundlePredictionPublic] = None
    prediction_member: Optional[BundlePredictionMember] = None
    evidence: list[BundleEvidenceItem]
    counter_evidence: list[BundleEvidenceItem]
    uncertainty: list[BundleUncertaintyItem]
    odds_timeline: list[BundleOddsPoint]
    cooccurring_events: list[BundleCoocEvent]
    chart_specs: list[BundleChartSpec]
    script_sections: list[BundleScriptSection]
    subtitle_cues: list[BundleSubtitleCue]
    source_notes: list[BundleSourceNote]
    bundle_hash: str


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
    home_away_inverted: bool
    snapshots: list[OddsSnapshotItem]


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


# ── 认证(JSON 响应端点;302 跳转在 responses={} 声明) ─────

class WechatCallbackApprovedDTO(BaseModel):
    """oa/callback 的 device_approve 分支(login 分支为 302 跳转,无 body)。"""

    status: Literal["approved"]
    message: str


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


class AdminPredictionsResponse(BaseModel):
    counts: dict[str, int]
    predictions: list[AdminPredictionItem]


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


class StudioExportServerDTO(BaseModel):
    """txt/json/srt:服务端生成可下载文件。"""

    model_config = ConfigDict(protected_namespaces=())

    job_id: str
    side: Literal["server"]
    download_url: str
    data_cutoff_at: Optional[str] = None
    model_version: Optional[str] = None


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
