"""Roster provenance is distinct from match-specific confirmed participation."""
from typing import Literal
from .api_contract import DTO, Id
from .contract import stable_id, utc
from .codec import digest

LINEUP_BASES={'lastStarting11':'previous_match','predicted':'third_party_prediction',
              'confirmed':'source_reported_confirmed'}

class LineupContext(DTO):
    schema_version: Literal['miaodi-lineup-context.v1']
    definition_version: Literal['miaodi-lineup-basis.v1']
    series_id: Id
    basis: Literal['previous_match', 'third_party_prediction', 'source_reported_confirmed', 'unknown']
    observed_at: str | None
    source_updated_at: None
    issues: list[Literal['unmapped_lineup_basis', 'invalid_observation_time']]


def lineup_context(provider, source_type, *, observed_at=None, source=None):
    if provider != 'fotmob':raise ValueError('unsupported_lineup_context_provider')
    if source_type is not None and (not isinstance(source_type,str) or len(source_type)>128):raise ValueError('invalid_lineup_basis')
    if source is not None and (not isinstance(source,str) or len(source)>128):raise ValueError('invalid_lineup_source')
    basis=LINEUP_BASES.get(source_type,'unknown')
    issues=[]
    if basis=='unknown':issues.append('unmapped_lineup_basis')
    at=None
    if observed_at is not None:
        try:at=utc(observed_at)
        except ValueError:issues.append('invalid_observation_time')
    # Source attribution and roster basis independently determine the series.
    # A supplier's "confirmed" claim is not independent proof of an official XI.
    result={'schema_version':'miaodi-lineup-context.v1','definition_version':'miaodi-lineup-basis.v1',
            'series_id':stable_id('series',provider,digest({'dataset':'prematch-lineup','basis':basis,'source_type':source_type,'source':source})),
            'basis':basis,'observed_at':at,'source_updated_at':None,'issues':issues}
    return LineupContext.model_validate(result).model_dump()


def bench_availability(source, present):
    if present is not None and type(present) is not bool:
        raise ValueError('invalid_bench_presence')
    if present is True:return 'observed'
    if present is None:return 'unknown'
    return 'not_supplied_by_prediction_feed' if source=='enetpulse' else 'not_observed'
