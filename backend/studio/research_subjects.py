"""Build fixed prematch/round research packets from trusted server projections.

No client-supplied metrics. Legacy drafts keep a real representative match_id;
subject.kind and match_ids define a round rather than pretending it is one match.
"""
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from . import research
from .predictions import read_prediction,_time
from .weekly_research import read_weekly


def finish(packet,source):
    packet['schema_version']='video-evidence.v2'
    packet['review_state']='draft';packet['analysis_mode']='current'
    packet['source_capture_sha256']={'fixed_input':research.digest(source)}
    packet['provenance']=source
    packet['snapshot_id']=research.digest(packet);packet['data_version']=packet['snapshot_id']
    return packet


def prematch(match_id):
    view=read_prediction(match_id)
    if view.status!='available':raise ValueError('prediction_not_available')
    root=Path(os.environ['STUDIO_CANONICAL_REGISTRY']).resolve(strict=True)
    with closing(sqlite3.connect(root.as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row
        row=c.execute('''SELECT s.match_id,v.source_id FROM match_sources s JOIN match_sources v ON v.match_id=s.match_id AND v.provider='fotmob'
            WHERE s.provider='opta' AND s.source_id=?''',(match_id,)).fetchone()
        if not row:raise ValueError('canonical_mapping_required')
        canonical_id=row['match_id'];anchor=int(row['source_id'])
        identities={}
        for team in view.teams:
            linked=c.execute('SELECT team_id FROM team_sources WHERE provider=? AND source_id=?',('opta',team.team_id)).fetchone()
            if not linked:raise ValueError('team_mapping_required')
            identities[team.side+'_team_id']=linked[0]
        fixed=c.execute('SELECT home_team_id,away_team_id,kickoff_utc FROM matches WHERE id=?',(canonical_id,)).fetchone()
        if any(fixed[k]!=v for k,v in identities.items()) or _time(fixed['kickoff_utc'])!=_time(view.kickoff_utc):raise ValueError('identity_mismatch')
    teams={t.side:t for t in view.teams};byid={t.team_id:t.side for t in view.teams}
    metrics=[{'evidence_id':'prediction.'+m.metric_code+'.'+byid[m.team_id],'label':m.label+'（预测）','value':m.target,
        'unit':'','side':byid[m.team_id],'definition_version':'tm15-team-target-v1'} for m in view.team_predictions if m.registered and m.target is not None]
    if not metrics:raise ValueError('registered_metrics_required')
    selected_codes=list(dict.fromkeys(m['evidence_id'].rsplit('.',1)[0] for m in metrics))[:2]
    selected_refs=[m['evidence_id'] for m in metrics if m['evidence_id'].rsplit('.',1)[0] in selected_codes][:4]
    names={side:team.name or ('主队' if side=='home' else '客队') for side,team in teams.items()}
    packet={'kind':'prematch','title':names['home']+' vs '+names['away']+' · 赛前研究','cutoff_at':view.stored_at,
        'identities':{'match_id':canonical_id,**identities},'display_names':names,
        'subject':{'kind':'prematch','match_ids':[canonical_id],'kickoff_utc':view.kickoff_utc},
        'match':{'home_team':names['home'],'away_team':names['away'],'home_score':None,'away_score':None},
        'metrics':metrics,'claims':[{'topic_id':'expected-profile','title':'两队的预计技术表现有哪些差异？',
          'limitation':'统计均值不等于比分、胜平负概率或投注优势；需要结合阵容和比赛背景。','evidence_refs':selected_refs}],
        'event_timeline':[],'quality':{'warnings':['赛前统计预测；并非确定赛果。'],'review_required':True}}
    return finish(packet,view.model_dump(exclude={'as_of','age_hours'})),anchor


def round_packet(object_sha):
    research._hash(object_sha);read_weekly()  # Verify current index and every linked dispatch.
    root=Path(os.environ['STUDIO_WEEKLY_RESEARCH']).resolve(strict=True)
    index=json.loads((root/'index.json').read_text())
    if not any(r['object_sha256']==object_sha for r in index['rounds']):raise ValueError('round_not_in_current_index')
    path=root/'objects'/f'{object_sha}.json'
    if path.is_symlink() or path.parent.is_symlink():raise ValueError('symlink_not_allowed')
    with path.open('rb') as f:raw=f.read(4_000_001)
    if len(raw)>4_000_000 or hashlib.sha256(raw).hexdigest()!=object_sha:raise ValueError('round_integrity_failed')
    selection=json.loads(raw)['selection'];matches=selection['matches'];coverage=selection['coverage']
    if not matches or len(matches)!=coverage['available']:raise ValueError('round_evidence_required')
    packets=[];anchors=[]
    for m in matches:
        packet,anchor,_=research.load_packet(m['snapshot_id']);packets.append(packet);anchors.append(anchor)
    if len(set(anchors))!=len(anchors):raise ValueError('duplicate_match')
    summary=selection['score_summary'];scores=[p['match'] for p in packets]
    if any(type(m.get(k)) not in (int,float) for m in scores for k in ('home_score','away_score')):raise ValueError('score_missing')
    if sum(m['home_score']+m['away_score'] for m in scores)!=summary['total_goals'] or summary['available_matches']!=len(scores):raise ValueError('round_score_mismatch')
    metrics=[]
    for key,label,unit in [('total_goals','总进球','球'),('goals_per_match','场均进球','球/场'),('home_wins','主胜','场'),('draws','平局','场'),('away_wins','客胜','场')]:
        metrics.append({'evidence_id':'round.'+key,'label':label,'value':summary[key],'unit':unit,'side':'round','definition_version':'round-score-v1'})
    scope=selection['scope'];label=scope['competition_code']+' · '+scope['season']+' · '+scope['round_label']
    packet={'kind':'round','title':label+' · 整轮观察','cutoff_at':max(p['cutoff_at'] for p in packets),
      'identities':{},'display_names':{'home':label,'away':''},'match':{'home_team':label,'away_team':'','home_score':None,'away_score':None},
      'subject':{'kind':'round','match_ids':[p['identities']['match_id'] for p in packets],'scope':{k:scope[k] for k in ('competition_code','season','round_label')},'coverage':coverage},
      'metrics':metrics,'claims':[{'topic_id':'round-goals','title':'这一轮的进球与赛果有什么特点？','limitation':f"证据覆盖 {coverage['available']}/{coverage['expected']} 场；只描述已覆盖比赛，不证明趋势或因果。",'evidence_refs':[m['evidence_id'] for m in metrics[:4]]}],
      'event_timeline':[],'quality':{'warnings':[] if coverage['complete'] else ['轮次覆盖不完整，不可当作全轮结论。'],'review_required':True}}
    return finish(packet,{'dispatch_object_sha256':object_sha,'selection':selection,'match_snapshot_ids':[p['snapshot_id'] for p in packets]}),anchors[0]
