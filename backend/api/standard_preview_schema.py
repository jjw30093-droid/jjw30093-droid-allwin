"""Strict owned preview contracts; explicit overrides at every entity boundary."""
from copy import deepcopy
from typing import Literal,Union,get_args,get_origin
from types import UnionType
from pydantic import BaseModel,Field,create_model
from backend.vendor.miaodi_standard.api_contract import DTO,Id,Code
from backend.vendor.miaodi_standard.lineup_context import LineupContext
from . import schemas as legacy

class PreviewIssue(DTO):
    code: Literal['unresolved_identity','identity_reference_only','invalid_source_value','invalid_snapshot',
                  'incomplete_metric_coverage','unmapped_situation','non_playing_source_rows']
    field: Code
    record_id: Id | None = None

class PreviewPerson(DTO):
    record_id: Id
    player_id: Id | None
    name: str
    avatar_url: str | None
    issues: list[PreviewIssue]

class PreviewRosterPlayer(PreviewPerson):
    shirt_number: str | None
    pos_x: float | None = Field(ge=0,le=1)
    pos_y: float | None = Field(ge=0,le=1)

class PreviewCoach(DTO):
    reference_id: Id
    coach_id: None
    identity_status: Literal['snapshot_reference']
    name: str
    avatar_url: str | None

class PreviewRosterSide(DTO):
    team_id: Id | None
    formation: str | None
    coach: PreviewCoach | None
    starters: list[PreviewRosterPlayer] = Field(max_length=100)
    subs: list[PreviewRosterPlayer] = Field(max_length=100)
    bench_availability: Literal['observed','not_supplied_by_prediction_feed','unknown']

class PreviewLineups(DTO):
    availability: Literal['not_collected','observed','invalid']
    snapshot_id: Id | None
    context: LineupContext
    capture_phase: Literal['before_kickoff','at_or_after_kickoff','unknown']
    home: PreviewRosterSide | None
    away: PreviewRosterSide | None

class PreviewSidelinedPlayer(PreviewPerson):
    reason: str | None
    expected_return: str | None

class PreviewSidelinedSide(DTO):
    availability: Literal['not_collected','observed','invalid']
    observed_at: str | None
    source_updated_at: None
    players: list[PreviewSidelinedPlayer] = Field(max_length=100)

class PreviewSidelined(DTO):
    home: PreviewSidelinedSide
    away: PreviewSidelinedSide

class PreviewStylePoint(DTO):
    record_id: Id
    team_id: Id | None
    name: str
    x: float | None
    y: float | None
    crest_url: str | None

class PreviewPeer(DTO):
    record_id: Id
    team_id: Id | None
    name: str
    crest_url: str | None
    percentile: float

class PreviewShareRow(PreviewPerson):
    pct: float | None = Field(ge=0,le=100)
    appearances: int = Field(ge=0)
    minutes: float = Field(ge=0)
    count: float | None = Field(ge=0)
    observed_matches: int = Field(ge=0)

class PreviewShareBlock(DTO):
    id: Literal['dribbles','creating','defending']
    title: str
    metric: str
    metric_code: Literal['dribbles_completed','chances_created','tackles_plus_interceptions']
    unit: Literal['count']
    definition_version: Literal['miaodi-player-window-shares.v1']
    denominator_policy: Literal['reported_player_observations']
    coverage_complete: bool
    rows: list[PreviewShareRow] = Field(max_length=5)

class PreviewKeeper(PreviewPerson):
    appearances: int = Field(ge=0)
    saves: float | None = Field(ge=0)
    xgot_faced: float | None = Field(ge=0)
    xgot_faced_complete: bool
    goals_conceded: float | None = Field(ge=0)
    goals_prevented: float | None

# These unchanged analytical models are already website-owned typed projections.
# Copy their FieldInfo/aliases while recursively enforcing extra=forbid/finite scalars.
_OVERRIDES={legacy.MatchPreviewStylePointDTO:PreviewStylePoint,
            legacy.MatchProfilePeerDTO:PreviewPeer,
            legacy.MatchPreviewPlayerShareRowDTO:PreviewShareRow,
            legacy.MatchPreviewPlayerShareBlockDTO:PreviewShareBlock,
            legacy.MatchPreviewKeeperDTO:PreviewKeeper}
_CACHE={}
def strict_model(model):
    if model in _OVERRIDES:return _OVERRIDES[model]
    if model not in _CACHE:
        _CACHE[model]=create_model('Standard'+model.__name__,__base__=DTO,
            **{key:(strict_type(field.annotation),deepcopy(field)) for key,field in model.model_fields.items()})
    return _CACHE[model]
def strict_type(t):
    if isinstance(t,type) and issubclass(t,BaseModel):return strict_model(t)
    origin=get_origin(t);args=get_args(t)
    if origin in (Union,UnionType):return Union[tuple(strict_type(a) for a in args)]
    if origin is list:return list[strict_type(args[0])]
    return t

StandardPreviewStyleView=strict_model(legacy.MatchPreviewStyleViewDTO)
StandardDataProfile=strict_model(legacy.MatchDataProfileDTO)
StandardKeyPlayers=strict_model(legacy.MatchPreviewKeyPlayersDTO)
StandardKeepers=strict_model(legacy.MatchPreviewKeepersDTO)
StandardAttackSources=strict_model(legacy.MatchPreviewAttackSourcesDTO)
StandardMatchupProfiles=strict_model(legacy.MatchPreviewMatchupProfilesDTO)
StandardWindow=strict_model(legacy.MatchPreviewWindowDTO)

class StandardPreviewResponse(DTO):
    schema_version: Literal['miaodi-website-preview.v1']
    match_id: Id
    home_team_id: Id | None
    away_team_id: Id | None
    generated_at: str
    home_window: StandardWindow | None
    away_window: StandardWindow | None
    lineups: PreviewLineups
    sidelined: PreviewSidelined
    style_views: list[StandardPreviewStyleView]
    attack_sources: StandardAttackSources
    data_profile: StandardDataProfile
    matchup_profiles: StandardMatchupProfiles
    key_players: StandardKeyPlayers
    keepers: StandardKeepers
    issues: list[PreviewIssue]

class StandardDataProfileResponse(DTO):
    schema_version: Literal['miaodi-website-data-profile.v1']
    match_id: Id
    profile: StandardDataProfile
