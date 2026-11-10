from types import SimpleNamespace
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import routes_prematch as routes
from backend.api.deps import require_user
import pytest

def test_analysis_source_can_use_a_separate_loopback_tunnel(monkeypatch):
    from backend.studio.prematch import analyst_source_base
    monkeypatch.delenv('STUDIO_ANALYST_SOURCE_BASE',raising=False)
    assert analyst_source_base()=='http://127.0.0.1:18768'
    monkeypatch.setenv('STUDIO_ANALYST_SOURCE_BASE','http://127.0.0.1:18769/')
    assert analyst_source_base()=='http://127.0.0.1:18769'
    for bad in ('https://example.org','http://127.0.0.1:80/path','http://user:pass@127.0.0.1:80'):
        monkeypatch.setenv('STUDIO_ANALYST_SOURCE_BASE',bad)
        with pytest.raises(ValueError):analyst_source_base()

def client(role):
    app=FastAPI();app.include_router(routes.router)
    app.dependency_overrides[require_user]=lambda:SimpleNamespace(role=role)
    return TestClient(app)

@pytest.mark.parametrize('role',['member','analyst'])
def test_admin_gate_before_cc_request(monkeypatch,role):
    calls=[]
    monkeypatch.setattr(routes,'read_report',lambda *a:calls.append(a))
    assert client(role).get('/api/v1/studio/prematch/fixtures').status_code==403
    assert calls==[]

def test_anonymous_does_not_reach_source(app,monkeypatch):
    calls=[]
    monkeypatch.setenv('STUDIO_PREMATCH_ENABLED','1')
    monkeypatch.setattr(routes,'read_report',lambda *a:calls.append(a))
    response=TestClient(app).get('/api/v1/studio/prematch/fixtures')
    assert response.status_code==401 and calls==[]
    assert response.headers['cache-control']=='private, no-store'

def test_flag_and_query_validation(monkeypatch):
    monkeypatch.delenv('STUDIO_PREMATCH_ENABLED',raising=False)
    c=client('admin')
    assert c.get('/api/v1/studio/prematch/fixtures').status_code==404
    monkeypatch.setenv('STUDIO_PREMATCH_ENABLED','1')
    monkeypatch.setattr(routes,'read_report',lambda *a:{'matches':[]})
    response=c.get('/api/v1/studio/prematch/fixtures')
    assert response.status_code==200
    assert response.headers['cache-control']=='private, no-store'
    mid=str(uuid4())
    assert c.get(f'/api/v1/studio/prematch/matches/{mid}?window=6').status_code==422
    assert c.get(f'/api/v1/studio/prematch/matches/{mid}?pair_a={uuid4()}').status_code==422

def test_source_failure_is_private(monkeypatch):
    monkeypatch.setenv('STUDIO_PREMATCH_ENABLED','1')
    def fail(*a):raise routes.PrematchError('private upstream address')
    monkeypatch.setattr(routes,'read_report',fail)
    r=client('admin').get('/api/v1/studio/prematch/fixtures')
    assert r.status_code==503 and 'upstream' not in r.text
    assert r.headers['cache-control']=='private, no-store'

def test_combined_snapshots_keep_prior_lineups(tmp_path):
    from backend.cli.materialize_prematch import freeze
    mid=str(uuid4())
    first={'match':{'id':mid},'revision':'a'*64,'lineup':{'snapshot_id':'predicted'}}
    path=freeze(tmp_path,first);before=path.read_bytes()
    assert freeze(tmp_path,first)==path and path.read_bytes()==before
    second=dict(first,lineup={'snapshot_id':'confirmed'})
    assert freeze(tmp_path,second)!=path
    assert path.read_bytes()==before

def test_injuries_do_not_require_a_lineup_and_future_observations_are_excluded(tmp_path,monkeypatch):
    import json,sqlite3,hashlib
    from backend.studio import prematch as source
    core=tmp_path/'core.db';odds=tmp_path/'odds.db'
    with sqlite3.connect(core) as c:
        c.execute('CREATE TABLE dim_match(Match_ID,Home_Team_ID,Away_Team_ID)')
        c.execute('INSERT INTO dim_match VALUES (1,10,20)')
    with sqlite3.connect(odds) as c:
        c.executescript('''CREATE TABLE bronze_fm_lineup_snap(id,fotmob_match_id,payload_json,payload_hash,observed_at,ingested_at,lineup_type);
            CREATE TABLE bronze_fm_sideline_snap(id,fotmob_match_id,fotmob_team_id,payload_json,observed_at,ingested_at);''')
        for i,date,name in [(1,'2026-11-08T12:00:00Z','已知伤员'),(2,'2026-11-10T12:00:00Z','未来伤员')]:
            c.execute('INSERT INTO bronze_fm_sideline_snap VALUES (?,1,10,?,?,?)',(i,json.dumps({'sidelined':[{'name':name,'reason':'injury','private_url':'secret'}]}),date,date))
    def connect(kind):
        c=sqlite3.connect(f'file:{core if kind=="core" else odds}?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c
    monkeypatch.setattr(source,'connect_ro',connect)
    monkeypatch.setattr(source,'configured_store',lambda:'configured')
    monkeypatch.setattr(source,'source_match_id',lambda *a:1)
    before=hashlib.sha256(odds.read_bytes()).hexdigest()
    value=source.lineup_context({'match':{'id':str(uuid4())},'cutoff_at':'2026-11-09T12:00:00Z','revision':'a'*64,'findings':[]})
    assert value['lineup']['status']=='unavailable'
    assert value['lineup']['injuries'][0]['players'][0]['name']=='已知伤员'
    assert '未来伤员' not in json.dumps(value,ensure_ascii=False) and 'private_url' not in json.dumps(value)
    assert hashlib.sha256(odds.read_bytes()).hexdigest()==before

def test_unadopted_match_uses_exact_kickoff_and_published_teams_only(tmp_path,monkeypatch):
    import sqlite3
    from fastapi import HTTPException
    from backend.studio import prematch as source
    home,away,target,fotmob=(str(uuid4()) for _ in range(4))
    db=tmp_path/'core.db';registry=tmp_path/'registry.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE dim_match(Match_ID,League_ID,Home_Team_ID,Away_Team_ID,kickoff_precision,kickoff_source,kickoff_at_utc)')
        c.execute("INSERT INTO dim_match VALUES (1,47,10,20,'exact','source','2026-11-09T12:00:00Z')")
    with sqlite3.connect(registry) as c:
        c.execute('CREATE TABLE source_ids(kind,provider,source_id,entity_id)')
        c.execute("INSERT INTO source_ids VALUES ('match','fotmob','1',?)",(fotmob,))
    def resolve(identity,*args):
        if str(identity)==target:raise HTTPException(404,'unadopted')
        assert str(identity)==fotmob
        return 1
    monkeypatch.setattr(source,'source_match_id',resolve)
    monkeypatch.setattr(source,'source_team_id',lambda ident,*a:10 if str(ident)==home else 20)
    monkeypatch.setattr(source,'connect_ro',lambda *a:sqlite3.connect(f'file:{db}?mode=ro',uri=True))
    report={'match':{'id':target,'home_id':home,'away_id':away,'kickoff_at':'2026-11-09T12:00:00+00:00'}}
    mid,link=source.resolve_lineup_match(report,str(registry))
    assert mid==1 and link['lineup_match_id']==fotmob
    with sqlite3.connect(db) as c:c.execute("INSERT INTO dim_match SELECT 2,League_ID,Home_Team_ID,Away_Team_ID,kickoff_precision,kickoff_source,kickoff_at_utc FROM dim_match")
    with pytest.raises(ValueError,match='ambiguous'):source.resolve_lineup_match(report,str(registry))

def test_roster_link_needs_exact_full_name_shirt_and_unique_team_candidate():
    from backend.studio.prematch import match_roster_profile
    row={'id':'opta-id','full_names':['Christos Tzolis'],'shirt_numbers':[17]}
    person={'name':'Christos Tzolis','shirt_number':'17'}
    profile,receipt=match_roster_profile(person,[row],'fotmob-id')
    assert profile==row and receipt['scope']=='this_report_roster_only'
    assert match_roster_profile(dict(person,shirt_number=18),[row],None)[0] is None
    assert match_roster_profile({'name':'C. Tzolis','shirt_number':17},[row],None)[0] is None
    assert match_roster_profile(person,[row,dict(row,id='another-id')],None)[0] is None
    alias=dict(row,provider_name_aliases=['C. Tzolis'])
    assert match_roster_profile({'name':'C. Tzolis','shirt_number':17},[alias],None)[1]['method']=='exact_provider_name_shirt_within_mapped_team'
    middle={'id':'source','name':'M. Sels','surname':'Sels','full_names':['Matz Willy Els Sels'],'shirt_numbers':[26]}
    assert match_roster_profile({'name':'Matz Sels','shirt_number':26},[middle],None)[1]['method']=='surname_initial_shirt_within_mapped_team'
    assert match_roster_profile({'name':'Other Sels','shirt_number':26},[middle],None)[0] is None
    for short,legal,display,number in [('Erling Haaland','Erling Braut Haaland','E. Haaland',9),('Daniel Muñoz','Daniel Muñoz Mejía','D. Muñoz',27)]:
        profile={'id':'p','full_names':[legal],'name':display,'shirt_numbers':[number]}
        person={'name':short,'shirt_number':number}
        assert match_roster_profile(person,[profile],None)[1]['method']=='exact_given_display_surname_shirt_within_mapped_team'
        assert match_roster_profile(dict(person,shirt_number=99),[profile],None)[0] is None
        assert match_roster_profile(person,[profile,dict(profile,id='other')],None)[0] is None

def test_historical_snapshot_replays_frozen_lineup_without_refresh(tmp_path,monkeypatch):
    from backend.cli.materialize_prematch import freeze
    mid=str(uuid4())
    value={'schema_version':'prematch-studio.v1','definition_version':'test','match':{'id':mid},
           'cutoff_at':'2026-11-09T12:00:00Z','generated_at':'2026-11-09T12:00:00Z','historical_as_known_proven':False,
           'filters':{'window':'5'},'teams':[{},{}],'modules':[{} for _ in range(8)],'findings':[],
           'references':[],'lineup':{'type':'predicted','players':[]},'notes':[],'revision':'a'*64}
    old=freeze(tmp_path,value)
    freeze(tmp_path,dict(value,lineup={'type':'confirmed','players':[]}))
    monkeypatch.setenv('STUDIO_PREMATCH_ENABLED','1')
    monkeypatch.setenv('STUDIO_PREMATCH_SNAPSHOT_DIR',str(tmp_path))
    def fail(*args):raise AssertionError('historical replay must not refresh the lineup')
    monkeypatch.setattr(routes,'lineup_context',fail)
    c=client('admin')
    listed=c.get(f'/api/v1/studio/prematch/matches/{mid}/snapshots')
    assert listed.status_code==200 and len(listed.json()['snapshots'])==2
    replay=c.get(f'/api/v1/studio/prematch/matches/{mid}/snapshots/{old.stem}')
    assert replay.status_code==200 and replay.json()['lineup']['type']=='predicted'
    assert replay.headers['cache-control']=='private, no-store'
    monkeypatch.setenv('STUDIO_PREMATCH_ENABLED','0')
    assert c.get(f'/api/v1/studio/prematch/matches/{mid}/snapshots').status_code==404
