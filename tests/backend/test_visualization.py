import json
import sqlite3
from uuid import uuid4
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import routes_visualization as routes
from backend.api.deps import require_user
from backend.studio import visualization as source
import pytest

def client(role=None):
    app=FastAPI();app.include_router(routes.router)
    if role:app.dependency_overrides[require_user]=lambda:SimpleNamespace(role=role)
    return TestClient(app)

def test_admin_gate_precedes_reads(monkeypatch):
    calls=[];monkeypatch.setattr(routes,'read',lambda *args:calls.append(args))
    assert client('member').get('/api/v1/studio/visualization/matches').status_code==403
    assert client('analyst').get('/api/v1/studio/visualization/matches').status_code==403
    assert not calls

def test_anonymous_gate_with_real_auth_dependency(app,monkeypatch):
    calls=[];monkeypatch.setattr(routes,'read',lambda *args:calls.append(args))
    response=TestClient(app).get('/api/v1/studio/visualization/matches')
    assert response.status_code==401 and not calls
    assert response.headers['cache-control']=='private, no-store'

def test_catalog_and_validation(monkeypatch):
    calls=[]
    def read(path,params,model):
        calls.append((path,params));return source.Catalog(schema_version='match-visualization.v1',matches=[],offset=0,limit=20,has_more=False)
    monkeypatch.setattr(routes,'read',read);c=client('admin')
    response=c.get('/api/v1/studio/visualization/matches?q=Roma&date=2026-11-03')
    assert response.status_code==200 and response.headers['cache-control']=='private, no-store'
    assert calls[0][1]['date']=='2026-11-03'
    assert c.get('/api/v1/studio/visualization/matches?offset=-1').status_code==422
    assert c.get('/api/v1/studio/visualization/matches?limit=10000').status_code==422
    assert c.get('/api/v1/studio/visualization/matches/not-an-id').status_code==422

@pytest.mark.parametrize('status',[404,409,503])
def test_failure_is_private(monkeypatch,status):
    def fail(*args):raise source.VisualizationError(status)
    monkeypatch.setattr(routes,'read',fail)
    r=client('admin').get('/api/v1/studio/visualization/matches')
    assert r.status_code==status and r.headers['cache-control']=='private, no-store'
    assert '127.0.0.1' not in r.text

def test_transport_fixed_target_and_bounded_body(monkeypatch):
    class Headers:
        def get_content_type(self):return 'application/json'
    class Response:
        status=200;headers=Headers()
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):
            assert limit==4_000_001
            return json.dumps(dict(schema_version='match-visualization.v1',matches=[],offset=0,limit=20,has_more=False)).encode()
    class Opener:
        def open(self,url,timeout):
            assert url=='http://127.0.0.1:18768/v1/visualization/matches?limit=20'
            assert timeout==10
            return Response()
    monkeypatch.setattr(source,'build_opener',lambda *a:Opener())
    assert source.read('matches',{'limit':20},source.Catalog).matches==[]

def test_response_contract_rejects_extra_and_nonfinite():
    with pytest.raises(ValueError):source.ValueMetric(code='x',label='x',value=float('nan'))
    with pytest.raises(ValueError):source.ValueMetric(code='x',label='x',value=0,raw_json='secret')


def test_periods_accept_old_and_new_projection():
    required = source.Bundle.model_json_schema()['required']
    assert 'periods' not in required
    assert source.Bundle.model_fields['periods'].get_default(call_default_factory=True) == []
    assert source.MatchPeriod(id=2, duration_seconds=2869).model_dump() == {'id': 2, 'duration_seconds': 2869}
    with pytest.raises(ValueError):
        source.MatchPeriod(id=5, duration_seconds=1)


def test_detail_resolves_crest_only_for_a_mapped_team(monkeypatch, tmp_path):
    match_id, home, away = (str(uuid4()) for _ in range(3))
    store = tmp_path / 'registry.sqlite'
    with sqlite3.connect(store) as conn:
        conn.executescript('''
            CREATE TABLE entities (entity_id TEXT, kind TEXT);
            CREATE TABLE identity_aliases (alias_id TEXT, entity_id TEXT, kind TEXT);
            CREATE TABLE source_ids (kind TEXT, provider TEXT, source_id TEXT, entity_id TEXT);
        ''')
        conn.executemany("INSERT INTO entities VALUES (?, 'team')", [(home,), (away,)])
        conn.execute("INSERT INTO source_ids VALUES ('team', 'fotmob', '10260', ?)", (home,))
    summary = source.Match(id=match_id, competition=None, date=None, kickoff_at=None,
                           home_id=home, away_id=away, home_name='Home', away_name='Away',
                           home_score=0, away_score=0)
    bundle = source.Bundle(
        schema_version='match-visualization.v1', match=summary, version='a'*64, updated_at='',
        players=[], events=[], offset=0, limit=500, event_total=0, has_more=False,
        excluded_events=0, markers=[], pass_edges=[], pass_nodes=[], player_stats=[],
        momentum=[], pv_teams=[], pv_players=[], pv_observed_at=None,
        capabilities={name: False for name in source.Capabilities.model_fields}, notes=[])
    monkeypatch.setenv('VIP_STANDARD_STORE', str(store))
    monkeypatch.setattr(routes, 'read', lambda *args: bundle)
    monkeypatch.setattr(routes, 'resolve_team_crest_url', lambda provider, team_id: f'/asset/{provider}/{team_id}.png')
    response = client('admin').get(f'/api/v1/studio/visualization/matches/{match_id}')
    assert response.status_code == 200
    assert response.json()['match']['home_crest_url'] == '/asset/fotmob/10260.png'
    assert response.json()['match']['away_crest_url'] is None
