"""Strict allowlists for website standings and team season consumers."""
from typing import Literal
from pydantic import Field, create_model
from backend.vendor.miaodi_standard.api_contract import DTO, Id, Code, Unit
from backend.queries.team_season_definitions import AGGREGATES,RATIO_UNITS,STANDINGS_FIELDS
from .standard_player_season_schema import StandardSeasonTeam
from .schemas import TeamBrandColor

class TeamSeasonIssue(DTO):
    code: Literal['unresolved_identity','invalid_metric_value','unverified_aggregation','unverified_unit',
                  'conflicting_aggregation_evidence','unverified_derived_definition','unmapped_derived_metric',
                  'missing_pairing_count','invalid_pairing_count']
    field: Code

class TeamSeasonReference(StandardSeasonTeam):
    record_id: Id

class StandingBase(DTO):
    record_id: Id
    team: TeamSeasonReference
    group_name: str | None
    qual_color: str | None
    issues: list[TeamSeasonIssue]

_EXPECTED={'expected_goals_for','expected_goals_against','expected_points','points_above_expected','position_difference'}
_SIGNED={'goal_difference','points','points_above_expected','position_difference'}
StandardStandingRow=create_model('StandardStandingRow',__base__=StandingBase,
    **{k:((float if k in _EXPECTED else int)|None,Field(default=None,**({} if k in _SIGNED else {'ge':0}))) for k in STANDINGS_FIELDS})

class StandardStandingsResponse(DTO):
    schema_version: Literal['miaodi-website-standings.v1']
    competition_id: Id
    season: str | None
    available_seasons: list[str]
    table_type: Literal['all','home','away','form','xg']
    season_finished: bool
    rows: list[StandardStandingRow]
    empty_reason: str | None

class StandardTeamRatio(DTO):
    value: float | None
    numerator: float | None
    denominator: float | None = Field(ge=0)
    paired_matches: int | None = Field(ge=0)
    matches_played: int | None = Field(ge=0)
    sample_count: int | None = Field(ge=0)
    unit: Code
    definition_version: str
    sample_policy: Literal['paired_source_observations']

StandardTeamRatios=create_model('StandardTeamRatios',__base__=DTO,
    **{key:(StandardTeamRatio|None,None) for key in RATIO_UNITS})

class TeamAggregateDefinition(DTO):
    metric_code: Code
    unit: Unit
    aggregation: Literal['season_total','per_match','mean','ratio_of_totals']
    definition_version: Literal['miaodi-team-season-aggregates.v1']
    sample_policy: Literal['source_derived_metric_coverage_unavailable']

class TeamAggregateBase(DTO):
    record_id: Id
    team: TeamSeasonReference
    team_color: TeamBrandColor | None
    ratios: StandardTeamRatios | None
    issues: list[TeamSeasonIssue]

StandardTeamAggregateRow=create_model('StandardTeamAggregateRow',__base__=TeamAggregateBase,
    **{k:((int if unit=='count' and aggregation=='season_total' else float)|None,
          Field(default=None,ge=0,**({'le':100} if unit=='percent' else {})))
       for k,(unit,aggregation) in AGGREGATES.items()})

class StandardTeamBoardEntry(DTO):
    record_id: Id
    team: TeamSeasonReference
    rank: int | None = Field(ge=1)
    value: float | int | None
    unit: Unit
    aggregation: Literal['season_total','per_match','per_90_minutes','mean','unverified']
    definition_version: Literal['miaodi-season-board-metrics.v1','miaodi-season-board-metrics.v2']
    display_decimals: int | None = Field(ge=0,le=6)
    team_color: TeamBrandColor | None
    issues: list[TeamSeasonIssue]

class StandardTeamBoard(DTO):
    metric_code: Code
    series_id: Id
    label_zh: str
    category: Literal['focus','attack','defence','discipline','unclassified']
    entries: list[StandardTeamBoardEntry]

class StandardTeamStatsResponse(DTO):
    schema_version: Literal['miaodi-website-team-season.v1']
    competition_id: Id
    season: str | None
    available_seasons: list[str]
    rows: list[StandardTeamAggregateRow]
    boards: list[StandardTeamBoard]
    metric_definitions: list[TeamAggregateDefinition]
    empty_reason: str | None
    recency: int | None
    venue: Literal['all','home','away']
