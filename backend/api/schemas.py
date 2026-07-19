"""/api/v1 响应 DTO(Pydantic 单一真源,OpenAPI → TypeScript 代码生成)。

字段级门禁核心(CLAUDE.md §8.2):
- PredictionFreeDTO 物理上只有 top_outcome/top_probability——受限字段连 key 都不存在,
  不是 null 占位;
- PredictionFullDTO 才包含完整 home/draw/away。
服务端按 entitlement 选择 DTO 类构造,测试扫描完整 JSON 证明免费响应无受限字段。
"""

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict


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
    date_utc: str                          # 日期粒度(dim_match 无开球时刻)
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
    status: str                            # locked / retracted(撤回样本透明展示)
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
    limit: int
    offset: int
    metrics: Optional[TrackRecordMetrics] = None
    samples: list[TrackRecordSample]
    empty_reason: Optional[str] = None     # 无正式样本时的诚实说明
