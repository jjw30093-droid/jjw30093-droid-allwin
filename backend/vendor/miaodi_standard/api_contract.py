"""Public standard DTOs. Pydantic owns OpenAPI and generated client types.

No catch-all object fields: unknown nested source fields reject the response.
Unavailable measurements and unresolved identities remain explicit nulls.
"""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Code = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,95}$')]
Number = int | float
Count = Annotated[int, Field(ge=0)]
Unit = Literal['count','minutes','percent','expected_count','rating_points','points',
               'unverified','unverified_currency','metres','people','model_points','ratio']
Period = Literal['full_time','first_half','second_half','extra_time_first_half',
                 'extra_time_second_half','penalty_shootout','non_playing']


class DTO(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Issue(DTO):
    code: Literal['invalid_source_value','outside_pitch_bounds','unmapped_period',
                  'unresolved_identity','source_event_id_missing','ambiguous_source_event_id',
                  'unmapped_event_kind','invalid_metric_value','unmapped_team_identity',
                  'unmapped_table_scope','unverified_aggregation','unverified_unit',
                  'identity_reference_only','unverified_metric_definition','conflicting_aggregation_evidence']
    field: Code | None = None
    record_id: Id | None = None
    player_id: Id | None = None
    observation_id: Id | None = None
    metric_code: Code | None = None
    series_id: Id | None = None


class Quality(DTO):
    issues: list[Issue] = Field(max_length=100000)
    unmapped_field_count: Count | None = None


class Match(DTO):
    match_id: Id
    series_id: Id
    revision_id: Digest
    competition_id: Id
    competition_code: str | None
    season: str
    round: str | None
    match_date: str
    kickoff_at: str | None
    kickoff_precision: Literal['exact','date','unknown']
    status: Literal['scheduled','live','completed','postponed','cancelled','abandoned']
    home_team_id: Id
    away_team_id: Id
    home_score: Count | None
    away_score: Count | None
    score_basis: Literal['reported_score','full_time_score']
    observed_at: str
    source_updated_at: str | None


class MatchList(DTO):
    schema_version: Literal['miaodi-standard-catalog.v1']
    dataset: Literal['core','advanced']
    matches: list[Match] = Field(max_length=50)
    next_cursor: Id | None


class MatchResponse(DTO):
    schema_version: Literal['miaodi-standard-match.v1']
    dataset: Literal['core','advanced']
    match: Match


class EntityResponse(DTO):
    schema_version: Literal['miaodi-entity.v1']
    entity_id: Id
    kind: Literal['match','team','player','competition']
    names: list[str] = Field(max_length=1000)


class LineupPlayer(DTO):
    team_id: Id
    player_id: Id | None
    player_name: str | None
    shirt_number: str | None
    is_starter: bool | None
    is_captain: bool | None
    identity_status: Literal['mapped','reference_only','unresolved']


class Lineups(DTO):
    schema_version: Literal['miaodi-lineup.v1']
    match_id: Id
    players: list[LineupPlayer] = Field(max_length=200)


class Metric(DTO):
    metric_code: Code
    value: Number | None
    unit: Unit
    definition_version: Literal['miaodi-observed-metrics.v1','miaodi-observed-metrics.v2','miaodi-observed-metrics.v3']

    @model_validator(mode='after')
    def check_measurement(self):
        if self.metric_code == 'passes_per_defensive_action':
            if self.unit != 'ratio' or self.definition_version != 'miaodi-observed-metrics.v3':
                raise ValueError('invalid_ppda_definition')
            if self.value is not None and self.value < 0:
                raise ValueError('invalid_ppda_ratio')
        if self.value is not None:
            if self.unit in ('count','minutes') and (type(self.value) is not int or self.value < 0):
                raise ValueError('invalid_count')
            if self.unit == 'percent' and not 0 <= self.value <= 100:
                raise ValueError('invalid_percentage')
        return self


class TeamMetric(Metric):
    team_id: Id | None
    series_id: Id
    period: Period


class PlayerMetric(TeamMetric):
    player_id: Id | None


class TeamStatistics(DTO):
    schema_version: Literal['miaodi-team-statistics.v2']
    match_id: Id
    observations: list[TeamMetric] = Field(max_length=10000)
    quality: Quality


class PlayerStatistics(DTO):
    schema_version: Literal['miaodi-player-statistics.v1']
    match_id: Id
    series_id: Id
    observations: list[PlayerMetric] = Field(max_length=40000)
    quality: Quality


class Clock(DTO):
    minute: Count | None
    second: Annotated[int, Field(ge=0, le=59)] | None
    added_minutes: Count | None
    period: Period | None
    elapsed_ms: Count | None


class Location(DTO):
    x: Number | None
    y: Number | None
    unit: Literal['percent_of_pitch']
    frame: Literal['team_attacking']
    physical_direction: None


class EventIdentity(DTO):
    record_id: Id
    event_id: Id | None
    identity_basis: Literal['source_event','snapshot_record']
    team_id: Id | None
    player_id: Id | None
    clock: Clock


class Shot(EventIdentity):
    location: Location
    expected_goals: Annotated[float, Field(ge=0, le=1)] | None
    expected_goals_on_target: Annotated[float, Field(ge=0, le=1)] | None
    outcome: Literal['goal','saved','miss','woodwork'] | None
    is_own_goal: bool | None
    is_blocked: bool | None
    is_on_target: bool | None
    is_penalty: bool | None


class RelatedPlayers(DTO):
    assist: Id | None = None
    substitution_in: Id | None = None
    substitution_out: Id | None = None


class Event(EventIdentity):
    event_kind: Literal['goal','card','substitution','added_time','period_marker','comment',
                        'video_review','own_goal','structural_correction_candidate',
                        'initial_formation','formation_change','substitution_off','substitution_on',
                        'video_review_decision','assist_action','key_pass','pass_candidate','unknown']
    location: Location | None
    end_location: Location | None
    related_players: RelatedPlayers
    card: Literal['yellow','red','second_yellow'] | None
    is_structural_correction_candidate: bool | None


class Shots(DTO):
    schema_version: Literal['miaodi-shots.v1']
    match_id: Id
    series_id: Id
    observations: list[Shot] = Field(max_length=1000)
    quality: Quality


class Events(DTO):
    schema_version: Literal['miaodi-events.v1']
    match_id: Id
    series_id: Id
    observations: list[Event] = Field(max_length=5000)
    quality: Quality


class Standing(DTO):
    team_id: Id | None
    team_name: str | None
    scope: Literal['overall','home','away','recent_form','expected_goals','unknown']
    group: str | None
    series_id: Id
    position: Annotated[int, Field(ge=1)] | None
    matches_played: Count | None
    wins: Count | None
    draws: Count | None
    losses: Count | None
    goals_for: Count | None
    goals_against: Count | None
    goal_difference: int | None
    points: int | None
    points_deduction: int | None
    expected_goals_for: Number | None
    expected_goals_against: Number | None
    expected_points: Number | None
    expected_position: Annotated[int, Field(ge=1)] | None


class Standings(DTO):
    schema_version: Literal['miaodi-standings.v1']
    competition_id: Id
    season: str
    status: Literal['available','unavailable']
    reason: Literal['source_dataset_unavailable'] | None = None
    observations: list[Standing] = Field(max_length=2000)
    quality: Quality


class SeasonMetric(DTO):
    team_id: Id | None
    player_id: Id | None = None
    rank: Annotated[int, Field(ge=1)] | None
    series_id: Id
    metric_code: Code
    value: Number | None
    unit: Unit
    aggregation: Literal['season_total','per_match','per_90_minutes','mean','unverified']
    definition_version: Literal['miaodi-season-board-metrics.v1','miaodi-season-board-metrics.v2']


class SeasonStatistics(DTO):
    schema_version: Literal['miaodi-season-statistics.v1']
    competition_id: Id
    season: str
    entity_scope: Literal['team','player']
    status: Literal['available','unavailable']
    reason: Literal['source_dataset_unavailable'] | None = None
    observations: list[SeasonMetric] = Field(max_length=20000)
    quality: Quality


class PredictionMetric(DTO):
    observation_id: Id
    entity_scope: Literal['team','player']
    team_id: Id | None
    player_id: Id | None
    metric_code: Code
    expected_value: Number | None
    season_average: Number | None
    unit: Literal['model_points','expected_count']


class Predictions(DTO):
    schema_version: Literal['miaodi-predictions.v1']
    match_id: Id
    display_name: Literal['喵弟模型']
    status: Literal['available','unavailable']
    as_of: str
    prediction_kind: Literal['statistical_expectation']
    observations: list[PredictionMetric] = Field(max_length=10000)
    reason: Literal['not_collected','not_observed_at_cutoff','match_started','unavailable'] | None = None
    snapshot_id: Digest | None = None
    observed_at: str | None = None
    stored_at: str | None = None
    kickoff_at: str | None = None
    source_updated_at: None = None
    model_version_id: Id | None = None
    quality: Quality | None = None
    definition_version: Literal['miaodi-statistical-expectations.v1'] | None = None


class ResultOutcomes(DTO):
    home: float
    draw: float
    away: float


class TotalsOutcomes(DTO):
    over: float
    under: float


class MarketQuote(DTO):
    schema_version: Literal['miaodi-market-quote.v1']
    match_id: Id
    market: Literal['match_result','total_goals']
    bookmaker: Literal['bet365']
    as_of: str
    status: Literal['available','unavailable']
    odds_format: Literal['decimal']
    period: Literal['regulation_time']
    reason: Literal['exact_kickoff_unavailable','no_eligible_observed_quote'] | None = None
    quote_id: Id | None = None
    observed_at: str | None = None
    source_updated_at: str | None = None
    decimal_odds: ResultOutcomes | TotalsOutcomes | None = None
    implied_probabilities: ResultOutcomes | None = None
    probability_method: Literal['proportional_margin_removal.v1'] | None = None
    line: Number | None = None
    settlement_lines: list[Number] = Field(default_factory=list, max_length=2)
    overround: Number | None = None
    mapping_basis: Literal['current_verified_mapping'] | None = None
    definition_version: Literal['miaodi-observed-market.v1'] | None = None

    @model_validator(mode='after')
    def check_quote(self):
        if self.status == 'available':
            expected = ResultOutcomes if self.market == 'match_result' else TotalsOutcomes
            if not isinstance(self.decimal_odds, expected):
                raise ValueError('market_outcome_mismatch')
            if any(v <= 1 for v in self.decimal_odds.model_dump().values()):
                raise ValueError('invalid_decimal_odds')
            if self.market == 'total_goals' and self.implied_probabilities is not None:
                raise ValueError('totals_probability_unavailable')
            if self.implied_probabilities:
                values = list(self.implied_probabilities.model_dump().values())
                if any(not 0 <= v <= 1 for v in values) or abs(sum(values)-1) > 1e-9:
                    raise ValueError('invalid_probability_sum')
        return self


MODELS = {'matches':MatchList, 'match':MatchResponse, 'entity':EntityResponse,
          'lineups':Lineups, 'team-statistics':TeamStatistics, 'player-statistics':PlayerStatistics,
          'shots':Shots, 'events':Events, 'standings':Standings, 'season-statistics':SeasonStatistics,
          'prediction':Predictions, 'market':MarketQuote}


def validate(kind, body):
    return MODELS[kind].model_validate(body).model_dump(mode='json', exclude_unset=True)
