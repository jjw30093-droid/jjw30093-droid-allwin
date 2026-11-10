"""Explicit website season projections; raw provider keys never form the API."""
from typing import Literal
from pydantic import Field, create_model

from backend.vendor.miaodi_standard.api_contract import DTO, Id, Code, Unit
from .schemas import TeamBrandColor, PlayerSeasonRatios
from backend.queries.player_ratio_definitions import PAIRED_PASS_INPUTS


class StandardCompetition(DTO):
    competition_id: Id
    code: str
    name: str
    name_en: str
    website_path: str


class StandardSeasonTeam(DTO):
    team_id: Id | None
    name: str
    name_en: str | None
    crest_url: str | None


class StandardSeasonPlayer(DTO):
    record_id: Id
    player_id: Id | None
    name: str
    name_en: str | None
    avatar_url: str | None


class StandardSeasonIssue(DTO):
    code: Literal['unresolved_identity', 'unverified_aggregation', 'unverified_unit', 'invalid_metric_value', 'conflicting_aggregation_evidence', 'unverified_derived_definition', 'unmapped_derived_metric', 'invalid_derived_metric_value', 'missing_pairing_count', 'incomplete_paired_inputs']
    field: Literal['player_id', 'team_id', 'value', 'aggregation', 'unit', 'ratios']


class StandardPlayerBoardEntry(StandardSeasonPlayer):
    team: StandardSeasonTeam
    rank: int | None
    value: float | int | None
    unit: Unit
    aggregation: Literal['season_total', 'per_match', 'per_90_minutes', 'mean', 'unverified']
    definition_version: Literal['miaodi-season-board-metrics.v1', 'miaodi-season-board-metrics.v2']
    team_color: TeamBrandColor | None
    issues: list[StandardSeasonIssue]


class StandardPlayerBoard(DTO):
    metric_code: Code
    series_id: Id
    label_zh: str
    direction: Literal['high_good', 'high_bad']
    entries: list[StandardPlayerBoardEntry]


class StandardPlayersResponse(DTO):
    schema_version: Literal['miaodi-website-player-boards.v1']
    competition_id: Id
    season: str | None
    available_seasons: list[str]
    boards: list[StandardPlayerBoard]
    empty_reason: str | None


class StandardPlayerRatioValue(DTO):
    value: float | None
    numerator: float | None
    denominator: float | None
    input_matches: int | None = Field(ge=0)


class IndependentPlayerRatioValue(StandardPlayerRatioValue):
    definition_version: Literal['miaodi-player-season-ratios.v1']
    sample_policy: Literal['independent_available_sums']


class PairedPlayerRatioValue(StandardPlayerRatioValue):
    definition_version: Literal['miaodi-player-season-ratios.v2']
    sample_policy: Literal['same_match_observed_inputs']


StandardPlayerSeasonRatios = create_model('StandardPlayerSeasonRatios', __base__=DTO,
    **{key: ((PairedPlayerRatioValue if key in PAIRED_PASS_INPUTS else IndependentPlayerRatioValue) | None, None)
       for key in PlayerSeasonRatios.model_fields})


class StandardPlayerQuadrantRow(DTO):
    player: StandardSeasonPlayer
    team: StandardSeasonTeam
    team_color: TeamBrandColor | None
    usual_position: Literal[0, 1, 2, 3] | None
    appearances: int | None
    minutes_played: int | None
    team_minutes: int | None
    minutes_share: float | None
    teams_count: int | None
    ratios: StandardPlayerSeasonRatios | None
    issues: list[StandardSeasonIssue]


class StandardPlayerQuadrantResponse(DTO):
    schema_version: Literal['miaodi-website-player-quadrant.v2']
    definition_version: Literal['miaodi-player-season-ratios.v2']
    competition_id: Id
    season: str | None
    available_seasons: list[str]
    rows: list[StandardPlayerQuadrantRow]
    excluded_below_floor: int
    empty_reason: str | None
