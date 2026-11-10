"""Narrow website projection of the loopback market bridge."""
import json
import os
from datetime import datetime,timezone
from http.client import HTTPException
import math
import re
from urllib.request import build_opener,ProxyHandler
from pydantic import BaseModel,Field
from typing import Literal
from .predictions import _NoRedirect,_time,PredictionUnavailable


class MarketComparisonDTO(BaseModel):
    status: Literal['available','unavailable']
    display_name: Literal['喵弟模型']='喵弟模型'
    canonical_match_id: str | None=None
    reason: str | None=None
    model_total_goals: float | None=Field(default=None,ge=0,allow_inf_nan=False)
    market_line: float | None=Field(default=None,ge=0,allow_inf_nan=False)
    over_decimal: float | None=Field(default=None,gt=1,allow_inf_nan=False)
    under_decimal: float | None=Field(default=None,gt=1,allow_inf_nan=False)
    model_observed_at: str | None=None
    market_observed_at: str | None=None
    market_age_hours: float | None=Field(default=None,ge=0,allow_inf_nan=False)
    market_stale: bool=False
    as_of: str


def project(raw,match_id):
    if raw.get('schema_version')!='prediction-market-view.v1' or raw.get('opta_match_id',match_id)!=match_id:
        raise ValueError('comparison identity mismatch')
    now=_time(raw['as_of'])
    base={'status':raw['status'],'canonical_match_id':raw.get('canonical_match_id'),'as_of':raw['as_of']}
    if raw['status']=='unavailable':return MarketComparisonDTO(**base,reason=raw.get('reason','unavailable'))
    if raw['status']!='available' or now>=_time(raw['kickoff_utc']):raise ValueError('invalid comparison phase')
    goals=raw['expected_goals'];values=[goals[k] for k in ('home','away','total')]
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in values) or abs(values[0]+values[1]-values[2])>1e-8:
        raise ValueError('invalid goals')
    prediction=raw['prediction']
    if _time(prediction['observed_at'])>now or _time(prediction['stored_at'])>now:raise ValueError('future prediction')
    base.update(model_total_goals=goals['total'],model_observed_at=prediction['observed_at'])
    market=raw['market']
    if market['status']=='available':
        if _time(market['quote']['observed_at'])>now:raise ValueError('future market')
        base.update(market_age_hours=(now-_time(market['quote']['observed_at'])).total_seconds()/3600,market_stale=(now-_time(market['quote']['observed_at'])).total_seconds()>86400,market_line=market['market_line'],over_decimal=market['decimal_odds']['over'],under_decimal=market['decimal_odds']['under'],market_observed_at=market['quote']['observed_at'])
    return MarketComparisonDTO(**base)


def read_comparison(match_id):
    if os.environ.get('STUDIO_PREDICTIONS_ENABLED')!='1':raise PredictionUnavailable('disabled')
    if not re.fullmatch('[a-zA-Z0-9_-]{1,128}',match_id):raise ValueError('invalid match')
    try:
        with build_opener(ProxyHandler({}),_NoRedirect()).open('http://127.0.0.1:8769/v1/matches/'+match_id+'/comparison',timeout=7) as r:
            if r.status!=200 or r.headers.get_content_type()!='application/json':raise ValueError('invalid response')
            raw=r.read(100001)
        if len(raw)>100000:raise ValueError('response too large')
        document=json.loads(raw)
        if abs((datetime.now(timezone.utc)-_time(document['as_of'])).total_seconds())>60:raise ValueError('stale response')
        return project(document,match_id)
    except (OSError,HTTPException,ValueError,TypeError,KeyError,AttributeError):
        raise PredictionUnavailable('comparison unavailable') from None
