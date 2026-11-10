"""Website report v2: canonical entities and explicit match-scoped joins."""
from typing import Literal
from pydantic import Field, create_model
from backend.vendor.miaodi_standard.api_contract import DTO, Id
from .schemas import MatchReportLineupPlayer, MatchReportLineupTeam, MatchReportShot, MatchReportTopRated
from .standard_report_schema import (StandardReportAvailable, StandardReportUnavailable,
                                    metric_fields)


class ReportParticipant(DTO):
    player_id: Id | None
    participant_id: Id
    participant_scope: Literal['match_participant', 'report_record']
    avatar_url: str | None


class ReportLineupPlayerV2(MatchReportLineupPlayer, ReportParticipant):
    player_id: Id | None


class ReportLineupTeamV2(MatchReportLineupTeam):
    team_id: Id | None
    starters: list[ReportLineupPlayerV2]
    bench: list[ReportLineupPlayerV2]


class ReportShotV2(MatchReportShot):
    player_id: Id | None
    team_id: Id | None
    participant_id: Id | None
    avatar_url: str | None
    # The chart already has a snapshot composite key. A provider event number
    # is neither a canonical event identity nor required by this presentation.
    shot_id: None = Field(default=None, exclude=True)


class ReportTopRatedV2(MatchReportTopRated, ReportParticipant):
    player_id: Id | None
    team_id: Id | None


class TeamPresentationV2(DTO):
    team_id: Id | None
    is_home: bool
    period: Literal['full_time', 'first_half', 'second_half']


class PlayerPresentationV2(ReportParticipant):
    team_id: Id | None
    name: str
    is_home: bool
    is_goalkeeper: bool
    rating: float | None


ReportTeamStatV2 = create_model('ReportTeamStatV2', __base__=TeamPresentationV2, **metric_fields('team'))
ReportPlayerStatV2 = create_model('ReportPlayerStatV2', __base__=PlayerPresentationV2, **metric_fields('player'))


class StandardReportAvailableV2(StandardReportAvailable):
    schema_version: Literal['miaodi-website-report.v2']
    lineups: list[ReportLineupTeamV2]
    shots: list[ReportShotV2]
    team_stats: list[ReportTeamStatV2]
    team_stats_by_half: list[ReportTeamStatV2]
    player_stats: list[ReportPlayerStatV2]
    top_rated: ReportTopRatedV2 | None


class StandardReportUnavailableV2(StandardReportUnavailable):
    schema_version: Literal['miaodi-website-report.v2']


StandardReportResponseV2 = StandardReportAvailableV2 | StandardReportUnavailableV2
