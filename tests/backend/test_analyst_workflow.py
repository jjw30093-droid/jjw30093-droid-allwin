from copy import deepcopy
import json
from uuid import uuid4
import pytest
from backend.studio import analyst_workflow as f
from backend.db.connections import connect_rw


def report():
    teams=[]
    for i in range(2):
        chain={'event_id':str(uuid4()),'match_id':str(uuid4()),'direction':'attack','period':1,'home_players':11,'away_players':11,'penalty':False,'method':'传中参与','creator_zone':12,'xg':.2,'player_id':str(uuid4())}
        teams.append({'id':str(uuid4()),'name':str(i),'chains':[chain], 'methods':[{'name':'传中参与','xg':.2,'known_xg':1}],
                      'samples':[{}]*3,'minutes':280,'missing_layers':[]})
    return {'match':{'id':str(uuid4()),'home_name':'甲','away_name':'乙','kickoff_at':'2026-12-01T12:00:00Z'},'teams':teams,'revision':'a'*64,
            'phase':'prematch','cutoff_at':'2026-11-30T12:00:00Z','analysis_mode':'captured_pre_match','lineup':{}}


def test_hypothesis_checks_do_not_use_score():
    r=report();hs=f.findings(r)
    assert len(hs)==2
    checks=f.assess(hs,r)
    assert all(c['status']=='得到支持' for c in checks)
    r['teams'][0]['chains'][0]['method']='其他'
    assert f.assess(hs,r)[0]['status']=='未获支持'
    r['teams'][0]['missing_layers']=['missing']
    assert f.assess(hs,r)[0]['status']=='无法判断'


def test_lineup_unknown_is_not_removal():
    after={'teams':[{'side':'home','players':[{'id':'a','name':'A'},{'id':None,'name':'B'}]}]}
    delta=f.roster_diff({},after)
    assert delta['teams'][0]['added']==[]
    assert delta['teams'][0]['unresolved_after']==1


def test_snapshot_edit_review_and_idempotence(app,monkeypatch):
    from backend.db.util import utc_now_iso
    c=connect_rw('platform')
    monkeypatch.setattr(f,'report_anchor',lambda report:9001)
    uid=str(uuid4());now=utc_now_iso()
    c.execute("INSERT INTO users(id,display_name,role,status,created_at,updated_at) VALUES (?,'Analyst','admin','active',?,?)",(uid,now,now))
    r=report();r['hypotheses']=f.findings(r)
    did=f.save(c,uid,r)
    assert f.save(c,uid,deepcopy(r))==did
    f.research.change(c,did,uid,1,overrides={'editorial_notes':'修订：这里是待核查的区域假设。'})
    with pytest.raises(ValueError):f.research.change(c,did,uid,1,status='reviewed')
    f.research.change(c,did,uid,2,status='reviewed')
    assert f.load(c,uid,did)['status']=='reviewed'
    r2=deepcopy(r);r2['revision']='b'*64
    did2=f.save(c,uid,r2,f.load(c,uid,did))
    assert did2!=did and f.load(c,uid,did2)['status']=='draft'
    assert f.load(c,uid,did)['editorial_notes'].startswith('修订')
    with pytest.raises(Exception):c.execute('UPDATE content_drafts SET bundle_json=? WHERE id=?',('{}',did))
    with pytest.raises(LookupError):f.load(c,str(uuid4()),did)
    c.close()


def test_job_dedup_retry_and_completion(app,monkeypatch):
    c=connect_rw('platform');uid=str(uuid4());mid=str(uuid4())
    jid=f.enqueue(c,uid,mid,'prematch','source1')
    assert f.enqueue(c,uid,mid,'prematch','source1')==jid
    monkeypatch.setattr(f,'generate',lambda *args:'draft')
    assert f.run_one(c)==jid
    assert c.execute('SELECT status FROM job_runs WHERE id=?',(jid,)).fetchone()[0]=='succeeded'
    assert f.run_one(c) is None
    c.close()


def test_admin_only_csrf_export_and_stale_review(app,fresh_ip,monkeypatch):
    from fastapi.testclient import TestClient
    from .test_studio import _analyst_client,_csrf
    monkeypatch.setenv('STUDIO_REPORT_WORKFLOW_ENABLED','1')
    assert TestClient(app).get('/api/v1/studio/reports').status_code==401
    client=_analyst_client(app,fresh_ip)
    assert client.get('/api/v1/studio/reports').status_code==403
    uid=client.get('/api/v1/me').json()['user']['id']
    c=connect_rw('platform');c.execute("UPDATE users SET role='admin' WHERE id=?",(uid,))
    monkeypatch.setattr(f,'report_anchor',lambda report:9001)
    r=report();r['hypotheses']=f.findings(r);did=f.save(c,uid,r)
    base='/api/v1/studio/reports/'+did
    assert client.get(base).status_code==200
    assert client.get(base).headers['cache-control']=='private, no-store'
    def render(value, path, *, profile):
        assert value['id']==did and profile=='reader'
        path.write_bytes(b'%PDF-1.4')
    monkeypatch.setattr('backend.studio.analyst_pdf.render', render)
    assert TestClient(app).get(base+'/reader.pdf').status_code==401
    preview=client.get(base+'/reader.pdf')
    assert preview.status_code==200
    assert preview.content==b'%PDF-1.4'
    assert preview.headers['content-type']=='application/pdf'
    assert preview.headers['content-disposition'].startswith('inline;')
    assert preview.headers['cache-control']=='private, no-store'
    assert c.execute('SELECT count(*) FROM export_jobs WHERE draft_id=?',(did,)).fetchone()[0]==0
    assert client.post(base+'/review',json={'expected_revision':1,'status':'reviewed'}).status_code==403
    assert client.post(base+'/review',json={'expected_revision':1,'status':'reviewed'},headers=_csrf(client)).status_code==200
    assert client.post(base+'/edit',json={'expected_revision':1,'editorial_notes':'stale'},headers=_csrf(client)).status_code==409
    exported=client.post(base+'/export',json={'expected_revision':2,'kind':'json'},headers=_csrf(client))
    assert exported.status_code==200
    url=exported.json()['download_url']
    assert client.get(url).json()['review']['state']=='reviewed'
    c.execute("UPDATE users SET role='analyst' WHERE id=?",(uid,))
    assert client.get(base).status_code==403
    assert client.get(base+'/reader.pdf').status_code==403
    assert client.get(url).status_code==403
    assert client.get('/api/v1/studio/research/drafts').json()['drafts']==[]
    c.close()


def test_judgment_withdrawal_preserves_computed_evidence(app,monkeypatch):
    from backend.db.util import utc_now_iso
    c=connect_rw('platform');uid=str(uuid4());now=utc_now_iso()
    c.execute("INSERT INTO users(id,display_name,role,status,created_at,updated_at) VALUES (?,'Review','admin','active',?,?)",(uid,now,now))
    monkeypatch.setattr(f,'report_anchor',lambda report:9001)
    r=report();r['hypotheses']=f.findings(r);did=f.save(c,uid,r);hid=r['hypotheses'][0]['id']
    changes={'judgments':{hid:{'text':'仅在压迫成功时成立','withdrawn':True,'reason':'录像反例充分','video_note':'手工观察'}}}
    f.research.change(c,did,uid,1,overrides=changes)
    saved=f.load(c,uid,did)
    assert saved['report']==r and saved['judgments'][hid]['withdrawn']
    with pytest.raises(ValueError):f.research.change(c,did,uid,2,overrides={'judgments':{hid:{'xg':99}}})
    with pytest.raises(ValueError):f.research.change(c,did,uid,2,overrides={'judgments':{hid:{'withdrawn':True}}})
    c.close()


def test_worker_failure_retry_and_single_writer(app,monkeypatch):
    c=connect_rw('platform');uid=str(uuid4());jid=f.enqueue(c,uid,str(uuid4()),'postmatch','correction-1')
    monkeypatch.setattr(f,'generate',lambda *args: (_ for _ in ()).throw(OSError('secret source error')))
    assert f.run_one(c)==jid
    row=c.execute('SELECT * FROM job_runs WHERE id=?',(jid,)).fetchone()
    assert row['status']=='failed' and 'secret' not in row['error_summary']
    assert f.run_one(c) is None
    c.execute("UPDATE job_runs SET finished_at=datetime('now','-2 minutes') WHERE id=?",(jid,))
    monkeypatch.setattr(f,'generate',lambda *args:'fixed')
    assert f.run_one(c)==jid
    assert c.execute('SELECT status FROM job_runs WHERE id=?',(jid,)).fetchone()[0]=='succeeded'
    second=f.enqueue(c,uid,str(uuid4()),'prematch','source-2')
    c.execute("UPDATE job_runs SET status='running',started_at=datetime('now') WHERE id=?",(second,))
    assert f.run_one(c) is None
    c.close()


def test_generation_filter_validation():
    from backend.api.routes_analyst_reports import Generate,JudgmentEdit
    from pydantic import ValidationError
    mid=str(uuid4())
    assert Generate(match_id=mid,phase='prematch',since='2026-10-01',state='drawing').since.isoformat()=='2026-10-01'
    with pytest.raises(ValidationError):Generate(match_id=mid,phase='prematch',pair_a=uuid4())
    with pytest.raises(ValidationError):Generate(match_id=mid,phase='postmatch',state='leading')
    assert Generate(match_id=mid,phase='postmatch',pair_a=uuid4(),pair_b=uuid4()).pair_a
    with pytest.raises(ValidationError):JudgmentEdit(withdrawn=True)


def test_checks_use_prematch_scope_not_full_match_totals():
    r=report();hs=f.findings(r);normal=deepcopy(r['teams'][0]['chains'][0])
    r['teams'][0]['chains']+=[dict(normal,penalty=True,method='点球'),dict(normal,period=3,method='其他'),dict(normal,home_players=10,method='其他')]
    check=f.assess(hs,r)[0]
    assert check['shots']==1 and check['status']=='得到支持'


def test_single_given_name_roster_link_requires_shirt_team_and_uniqueness():
    from backend.studio.prematch import match_roster_profile
    profile={'id':'canonical','full_names':['Example Legal Surname'],'provider_name_aliases':[],'shirt_numbers':[17]}
    person={'name':'Example','shirt_number':17}
    result,receipt=match_roster_profile(person,[profile],None)
    assert result==profile and receipt['scope']=='this_report_roster_only'
    assert match_roster_profile(dict(person,shirt_number=7),[profile],None)[0] is None
    assert match_roster_profile(person,[profile,dict(profile,id='ambiguous')],None)[0] is None


def test_roster_full_given_prefix_and_conflicting_full_names():
    from backend.studio.prematch import match_roster_profile
    alysson={'id':'a','name':'Alysson','full_names':['Alysson Edward Franco da Rocha dos Santos'],'shirt_numbers':[47]}
    person={'name':'Alysson Edward','shirt_number':47}
    assert match_roster_profile(person,[alysson],None)[0]==alysson
    assert match_roster_profile(person,[alysson,dict(alysson,id='b')],None)[0] is None
    assert match_roster_profile(dict(person,shirt_number=7),[alysson],None)[0] is None
    matz={'id':'m','name':'M. Sels','surname':'Sels','full_names':['Matz Willy Els Sels'],'shirt_numbers':[26]}
    assert match_roster_profile({'name':'Mark Sels','shirt_number':26},[matz],None)[0] is None
    harry={'id':'h','name':'H. Maguire','surname':'Maguire','full_names':['Jacob Harry Maguire'],'shirt_numbers':[5]}
    assert match_roster_profile({'name':'Harry Maguire','shirt_number':5},[harry],None)[0]==harry
    assert match_roster_profile({'name':'M. Sels','shirt_number':26},[matz],None)[0]==matz


def test_regional_reconstruction_is_not_prior_prediction(monkeypatch):
    r=report();r['regional_current']=deepcopy(r['teams'])
    monkeypatch.setattr(f,'read_report',lambda *args:dict(report(),filters={'eleven':'1','state':'all'}))
    f.regional_context(r)
    assert r['regional_baseline_info']['mode']=='reconstructed'
    assert r['regional_baseline_info']['status']=='available'
    assert r['regional_baseline_info']['draft_id'] is None
    monkeypatch.setattr(f,'read_report',lambda *args:(_ for _ in ()).throw(ValueError('source unavailable')))
    f.regional_context(r)
    assert r['regional_baseline'] is None
    assert r['regional_baseline_info']['status']=='unavailable'


def test_actual_lineup_coordinates_cannot_replace_opta_shirt_or_identity():
    report={'lineup':{'teams':[{'side':'away','players':[{'id':'a','name':'Opta name','shirt_number':47}]}]}}
    context={'type':'standard','teams':[{'side':'away','players':[{'id':'other','identity_link':{'evidence_player_id':'a'},'shirt_number':99,'name':'VIP name','pos_x':.4,'pos_y':.8,'coordinate_frame':'provider_vertical_0_1'}]}]}
    f.attach_actual_positions(report,context)
    player=report['lineup']['teams'][0]['players'][0]
    assert player['id']=='a' and player['name']=='Opta name' and player['shirt_number']==47
    assert player['pos_x']==.4 and player['pos_y']==.8
