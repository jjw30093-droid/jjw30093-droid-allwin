"""Admin-only typed projection of the existing private CC read service."""
import json
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, ProxyHandler
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from .predictions import _NoRedirect

class VisualizationError(RuntimeError):
    def __init__(self,status=503): self.status=status

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Match(Model):
    id: str
    competition: str | None
    date: str | None
    kickoff_at: str | None
    home_id: str
    away_id: str
    home_name: str
    away_name: str
    home_score: int | None
    away_score: int | None
    home_crest_url: str | None = None
    away_crest_url: str | None = None

class Catalog(Model):
    schema_version: Literal['match-visualization.v1']
    matches: list[Match]=Field(max_length=50)
    offset: int
    limit: int
    has_more: bool

class Player(Model):
    id: str
    team_id: str
    name: str
    number: int | None
    minutes: float | None
    starter: bool

class ChalkboardOffside(Model):
    player_id: str
    player_name: str | None
    x: float=Field(ge=0,le=100)
    y: float=Field(ge=0,le=100)

class Event(Model):
    id: str
    team_id: str | None
    player_id: str | None
    player_name: str | None
    period: int
    minute: int
    second: int
    clock: str
    elapsed: float | None
    kind: str
    label: str
    success: bool | None
    x: float | None=Field(ge=0,le=100)
    y: float | None=Field(ge=0,le=100)
    end_x: float | None=Field(ge=0,le=100)
    end_y: float | None=Field(ge=0,le=100)
    key_pass: bool
    assist: bool
    xg: float | None
    xgot: float | None
    goal: bool
    own_goal: bool
    event_type_id: int | None = None
    details_only: bool = False
    ordinary_pass: bool | None = None
    chalkboard_offside: ChalkboardOffside | None = None
    cross_phase: Literal['restart', 'open_play'] | None = None
    shot_outcome: Literal['goal', 'on_target', 'off_target', 'blocked'] | None = None
    penalty: bool | None
    pv_value: float | None
    pv_added: float | None

class Edge(Model):
    from_id: str
    to_id: str
    team_id: str
    count: int=Field(ge=0)

class Node(Model):
    id: str
    team_id: str
    x: float | None=Field(ge=0,le=100)
    y: float | None=Field(ge=0,le=100)

class Stat(Model):
    player_id: str
    code: str
    label: str
    unit: str
    value: float | None

class Momentum(Model):
    period: int
    minute: int
    second: int
    home: float | None
    away: float | None
    net: float | None

class ValueMetric(Model):
    code: str
    label: str
    value: float | None

class ValueRow(Model):
    team_id: str
    player_id: str | None
    metrics: list[ValueMetric]=Field(max_length=30)

class Capabilities(Model):
    events: bool
    shots: bool
    passes: bool
    player_stats: bool
    momentum: bool
    pv: bool

class MatchPeriod(Model):
    id: Literal[1, 2, 3, 4]
    duration_seconds: int = Field(gt=0)

class Bundle(Model):
    schema_version: Literal['match-visualization.v1']
    match: Match
    version: str=Field(pattern='^[a-f0-9]{64}$')
    updated_at: str
    players: list[Player]=Field(max_length=100)
    events: list[Event]=Field(max_length=1000)
    offset: int
    limit: int
    event_total: int=Field(ge=0,le=10000)
    has_more: bool
    excluded_events: int
    periods: list[MatchPeriod] = Field(default_factory=list, max_length=4)
    markers: list[Event]=Field(max_length=200)
    pass_edges: list[Edge]=Field(max_length=3000)
    pass_nodes: list[Node]=Field(max_length=100)
    player_stats: list[Stat]=Field(max_length=10000)
    momentum: list[Momentum]=Field(max_length=500)
    pv_teams: list[ValueRow]=Field(max_length=2)
    pv_players: list[ValueRow]=Field(max_length=100)
    pv_observed_at: str | None
    capabilities: Capabilities
    notes: list[str]=Field(max_length=20)

def read(path,params,model):
    url='http://127.0.0.1:18768/v1/visualization/'+path+'?'+urlencode(params)
    try:
        opener=build_opener(ProxyHandler({}),_NoRedirect())
        with opener.open(url,timeout=10) as response:
            if response.status!=200 or response.headers.get_content_type()!='application/json':raise ValueError('bad_response')
            raw=response.read(4_000_001)
        if len(raw)>4_000_000:raise ValueError('oversized_response')
        return model.model_validate(json.loads(raw))
    except HTTPError as e:
        raise VisualizationError(e.code if e.code in (404,409) else 503) from None
    except (OSError,HTTPException,ValueError,TypeError):
        raise VisualizationError() from None
