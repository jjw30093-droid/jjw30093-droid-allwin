"""Typed, bounded projection of the private prediction service for Studio."""
import json
import os
from datetime import datetime, timezone
from http.client import HTTPException
from typing import Literal
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from pydantic import BaseModel, Field, ValidationError


class PredictionUnavailable(RuntimeError):
    pass


class TeamPrediction(BaseModel):
    team_id: str
    side: Literal['home', 'away']
    name: str | None = None


class PredictionMetric(BaseModel):
    metric_code: str
    label: str
    target: float | None = Field(default=None, allow_inf_nan=False, strict=True)
    season_average: float | None = Field(default=None, allow_inf_nan=False, strict=True)
    team_id: str
    registered: bool


class StudioPredictionDTO(BaseModel):
    schema_version: Literal['tm15-display.v1']
    status: Literal['available', 'unavailable']
    display_name: Literal['喵弟模型']
    match_id: str
    as_of: str
    reason: Literal['no_capture_database', 'no_proven_prematch_capture_at_cutoff',
                    'match_has_started_at_cutoff'] | None = None
    kickoff_utc: str | None = None
    snapshot_id: str | None = None
    receipt_id: str | None = None
    observed_at: str | None = None
    stored_at: str | None = None
    age_hours: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    teams: list[TeamPrediction] = Field(default_factory=list, max_length=2)
    team_predictions: list[PredictionMetric] = Field(default_factory=list, max_length=1000)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class PredictionFixture(BaseModel):
    match_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    competition_code: Literal['EPL', 'J1L', 'ALM', 'UCL']
    kickoff_utc: str
    home_team_id: str | None
    away_team_id: str | None
    home_team_name: str | None
    away_team_name: str | None
    capture_status: Literal['not_captured', 'capture_recorded']


class StudioPredictionFixturesDTO(BaseModel):
    schema_version: Literal['prediction-fixtures.v1']
    as_of: str
    horizon_hours: Literal[72]
    matches: list[PredictionFixture] = Field(max_length=64)
    truncated: bool


def read_prediction_fixtures() -> StudioPredictionFixturesDTO:
    if os.environ.get('STUDIO_PREDICTIONS_ENABLED') != '1':
        raise PredictionUnavailable('disabled')
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open('http://127.0.0.1:18768/v1/prediction-fixtures', timeout=3) as response:
            if response.status != 200 or response.headers.get_content_type() != 'application/json':
                raise ValueError('invalid response')
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError('oversized response')
        view = StudioPredictionFixturesDTO.model_validate(json.loads(raw))
        start = _time(view.as_of)
        if abs((datetime.now(timezone.utc) - start).total_seconds()) > 60:
            raise ValueError('stale fixture catalogue')
        seen = set()
        for match in view.matches:
            age = (_time(match.kickoff_utc) - start).total_seconds()
            if match.match_id in seen or not 0 < age <= 72 * 3600:
                raise ValueError('invalid fixture identity or clock')
            seen.add(match.match_id)
        return view
    except (OSError, HTTPException, ValueError, TypeError, AttributeError):
        raise PredictionUnavailable('fixture_source_unavailable') from None


def _time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed


def validate_projection(payload, match_id, cutoff):
    view = StudioPredictionDTO.model_validate(payload)
    if view.match_id != match_id or _time(view.as_of) != cutoff:
        raise ValueError('identity or cutoff mismatch')
    if view.status == 'unavailable':
        if not view.reason or view.teams or view.team_predictions or view.snapshot_id:
            raise ValueError('invalid empty state')
    else:
        if (view.reason or not view.snapshot_id or not view.receipt_id or view.age_hours is None
                or not (_time(view.observed_at) <= cutoff < _time(view.kickoff_utc))
                or _time(view.stored_at) > cutoff):
            raise ValueError('invalid observation')
        if {t.side for t in view.teams} != {'home', 'away'}:
            raise ValueError('invalid team direction')
        team_ids = {t.team_id for t in view.teams}
        if len(team_ids) != 2:
            raise ValueError('duplicate teams')
        keys = set()
        for metric in view.team_predictions:
            key = (metric.team_id, metric.metric_code)
            if key in keys or metric.team_id not in team_ids:
                raise ValueError('invalid metric identity')
            keys.add(key)
    return view


def read_prediction(match_id: str) -> StudioPredictionDTO:
    if os.environ.get('STUDIO_PREDICTIONS_ENABLED') != '1':
        raise PredictionUnavailable('disabled')
    if not match_id.isascii() or not match_id.replace('-', '').replace('_', '').isalnum() or len(match_id) > 128:
        raise ValueError('invalid match id')
    cutoff = datetime.now(timezone.utc)
    url = 'http://127.0.0.1:18768/v1/matches/' + match_id + '/prediction?' + urlencode({'as_of': cutoff.isoformat()})
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(url, timeout=3) as response:
            if response.status != 200 or response.headers.get_content_type() != 'application/json':
                raise ValueError('invalid response')
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError('oversized response')
        return validate_projection(json.loads(raw), match_id, cutoff)
    except (OSError, HTTPException, ValueError, TypeError, AttributeError, ValidationError):
        raise PredictionUnavailable('source_unavailable') from None
