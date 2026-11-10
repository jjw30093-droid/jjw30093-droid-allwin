"""Website presentation contract with shared standard statistical fields.

Legacy lineup/shot/media identities remain compatible in this transitional
report. Only statistics and the root match identity are standardized here.
"""
from typing import Literal

from pydantic import Field, create_model

from backend.vendor.miaodi_standard.api_contract import DTO, Id, Quality, Unit
from backend.vendor.miaodi_standard.metrics import DEFINITIONS, SCOPED_DEFINITIONS
from .schemas import MatchReportAvailableDTO


class TeamPresentation(DTO):
    team_id: int
    canonical_team_id: Id | None
    is_home: bool
    period: Literal['full_time', 'first_half', 'second_half']


class PlayerPresentation(DTO):
    player_id: str
    canonical_player_id: Id | None
    team_id: int
    canonical_team_id: Id | None
    name: str
    is_home: bool
    is_goalkeeper: bool
    # Presentation rating remains the lineup rating, independent of the
    # standard player_rating observation in the statistical feed.
    rating: float | None


def metric_fields(scope):
    definitions = [d for (provider, _), d in DEFINITIONS.items() if provider == 'fotmob']
    definitions += [d for (provider, s, _), d in SCOPED_DEFINITIONS.items()
                    if provider == 'fotmob' and s == scope]
    return {code: ((int if kind == 'count' else float) | None,
                   Field(default=None, ge=low, le=high))
            for code, unit, kind, low, high in definitions}


StandardReportTeamStat = create_model('StandardReportTeamStat', __base__=TeamPresentation,
                                     **metric_fields('team'))
StandardReportPlayerStat = create_model('StandardReportPlayerStat', __base__=PlayerPresentation,
                                       **metric_fields('player'))


class ReportMetricDefinition(DTO):
    entity_scope: Literal['team', 'player']
    metric_code: str
    unit: Unit
    definition_version: Literal['miaodi-observed-metrics.v1', 'miaodi-observed-metrics.v2']
    definition_status: Literal['mapped', 'unverified']


class ReportStatisticsQuality(DTO):
    team: Quality
    player: Quality


class StandardReportAvailable(MatchReportAvailableDTO):
    schema_version: Literal['miaodi-website-report.v1']
    match_id: Id
    team_stats: list[StandardReportTeamStat]
    team_stats_by_half: list[StandardReportTeamStat]
    player_stats: list[StandardReportPlayerStat]
    metric_definitions: list[ReportMetricDefinition]
    statistics_quality: ReportStatisticsQuality


class StandardReportUnavailable(DTO):
    schema_version: Literal['miaodi-website-report.v1']
    match_id: Id
    available: Literal[False]
    reason: str


StandardReportResponse = StandardReportAvailable | StandardReportUnavailable
