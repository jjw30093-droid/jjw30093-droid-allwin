"""Evidence-first report lifecycle. Source reads never write CC or VIP databases."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from urllib.request import build_opener, ProxyHandler
from uuid import UUID

from backend.db.connections import tx
from backend.db.util import new_uuid, utc_now_iso
from . import research
from .prematch import read_report, lineup_context, analyst_source_base
from .predictions import _NoRedirect

VERSION = 'analyst-workflow.v8'


def source_read(path):
    with build_opener(ProxyHandler({}),_NoRedirect()).open(analyst_source_base()+'/v1/report-workflow/'+path,timeout=30) as response:
        if response.status!=200 or response.headers.get_content_type()!='application/json':
            raise ValueError('分析来源响应无效')
        raw=response.read(20_000_001)
    if len(raw)>20_000_000:raise ValueError('报告超过大小限制')
    return json.loads(raw)


def roster_diff(before, after):
    result=[]
    for side in ('home','away'):
        a=next((t for t in (before or {}).get('teams',[]) if t['side']==side),None)
        b=next((t for t in (after or {}).get('teams',[]) if t['side']==side),None)
        def people(t):
            return {(p.get('identity_link') or {}).get('evidence_player_id') or p.get('id'):p
                    for p in (t or {}).get('players',[]) if (p.get('identity_link') or {}).get('evidence_player_id') or p.get('id')}
        aa,bb=people(a),people(b)
        unknown_a=len((a or {}).get('players',[]))-len(aa)
        unknown_b=len((b or {}).get('players',[]))-len(bb)
        result.append({'side':side,'comparable':bool(a and b),
            'added':[p for k,p in bb.items() if k not in aa] if a and not unknown_a else [],
            'removed':[p for k,p in aa.items() if k not in bb] if b and not unknown_b else [],
            'identity_complete':bool(a and b) and not unknown_a and not unknown_b,
            'retained':len(aa.keys()&bb.keys()),
            'unresolved_before':len((a or {}).get('players',[]))-len(aa),
            'unresolved_after':len((b or {}).get('players',[]))-len(bb),
            'formation_before':(a or {}).get('formation'),'formation_after':(b or {}).get('formation')})
    return {'before_type':(before or {}).get('type'),'after_type':(after or {}).get('type'),
            'before_observed_at':(before or {}).get('observed_at'),'after_observed_at':(after or {}).get('observed_at'),
            'teams':result,'note':'仅比较身份可关联的人员；历史贡献变化不等于本场预测变化，未首发不等于伤停。'}


def findings(report):
    """Stable, testable hypotheses with samples, counterexamples and event evidence."""
    output=[]
    teams=report['teams']
    for team,other in ((teams[0],teams[1]),(teams[1],teams[0])):
        attacks=[c for c in team['chains'] if c['direction']=='attack']
        conceded=[c for c in other['chains'] if c['direction']=='defence']
        cells=[]
        for zone in range(15):
            own=[c for c in attacks if c.get('creator_zone')==zone]
            opp=[c for c in conceded if c.get('creator_zone')==zone]
            if own and opp:cells.append((min(len(own)/max(len(attacks),1),len(opp)/max(len(conceded),1)),zone,own,opp))
        if cells and report.get('phase')!='postmatch':
            _,zone,own,opp=max(cells,key=lambda x:x[0])
            counter=[c for c in attacks+conceded if c.get('creator_zone') is not None and c['creator_zone']!=zone]
            item={'id':research.digest([report['match']['id'],team['id'],'creator_zone',zone])[:20],
                'title':team['name']+'的机会传出区域与对方防守风险重合',
                'text':f"第 {zone//5+1} 纵区、第 {zone%5+1} 通道：本队 {len(own)} 次最后传球关联射门，对方 {len(opp)} 次在对应区域被创造机会。",
                'condition':'若本场仍采用近期的人员职责与推进路线，重点观察该通道。',
                'rule':{'kind':'creator_zone','value':zone,'team_id':team['id'],'baseline_share':len(own)/max(len(attacks),1)},
                'sample':{'matches':len(team['samples']),'minutes':team['minutes'],'opponent_matches':len(other['samples'])},
                'coverage':{'linked':sum(c.get('creator_zone') is not None for c in attacks),'shots':len(attacks)},
                'events':own+opp,'counter_events':counter,
                'players':sorted({p for c in own for p in (c.get('creator_id'),c.get('player_id')) if p}),
                'limitation':'区域来自进攻方坐标；属于战术观察假设，不代表已经确定的盯人关系。'}
            output.append(item)
        best=max(team.get('methods',[]),key=lambda m:m.get('xg') if m.get('xg') is not None else -1,default=None)
        if best:
            own=[c for c in attacks if c['method']==best['name']]
            output.append({'id':research.digest([report['match']['id'],team['id'],'method',best['name']])[:20],
                'title':team['name']+'：'+best['name']+'值得重点核查',
                'text':f"{len(team['samples'])} 场、{team['minutes']:.1f} 分钟中，该方式形成 {len(own)} 次射门；已记录 xG {format(best['xg'],'.2f') if best.get('xg') is not None else '未提供'}。",
                'condition':'观察该方式能否继续形成射门，而非只看最终进球。',
                'rule':{'kind':'method','value':best['name'],'team_id':team['id'],'baseline_share':len(own)/max(len(attacks),1)},
                'sample':{'matches':len(team['samples']),'minutes':team['minutes']},
                'coverage':{'known_xg':best.get('known_xg',0),'shots':len(own)},
                'events':own,'counter_events':[c for c in attacks if c['method']!=best['name']],
                'players':sorted({c['player_id'] for c in own if c.get('player_id')}),
                'limitation':'进攻方式为事件属性分类；低样本和对手变化会影响可重复性。'})
        if report.get('phase')=='postmatch':
            known=[c['xg'] for c in attacks if c.get('xg') is not None]
            goals=[c for c in attacks if c.get('goal')]
            output.append({'id':research.digest([report['match']['id'],team['id'],'finishing'])[:20],
                'title':team['name']+'：终结结果与机会质量',
                'text':f"{len(attacks)} 次射门、其中 {len(goals)} 次进球；已记录 xG {sum(known):.2f}，覆盖 {len(known)}/{len(attacks)} 次射门。" if known else f"{len(attacks)} 次射门、其中 {len(goals)} 次进球；xG 未提供。",
                'condition':'逐个查看进球与高价值未进球机会，区分创造和终结。',
                'rule':{'kind':'finishing','team_id':team['id']},
                'sample':{'matches':1,'minutes':team['minutes']},'coverage':{'known_xg':len(known),'shots':len(attacks)},
                'events':goals,'counter_events':[c for c in attacks if not c.get('goal')],
                'players':sorted({c['player_id'] for c in attacks if c.get('player_id')}),
                'limitation':'乌龙球单列；xG 不等于应得比分，单场进球差异不证明终结能力改变。'})
    if report.get('phase')=='postmatch':
        for item in output:
            item['condition']='本场已记录过程；结合具体进球、未进球机会和比赛状态回查。'
    for item in output:
        item['scope']={'periods':[1,2] if report.get('phase')!='postmatch' else [1,2,3,4],
                       'eleven':str(report.get('filters',{}).get('eleven','1'))=='1',
                       'penalties':report.get('phase')=='postmatch',
                       'state':report.get('filters',{}).get('state','all')}
    return output[:5]


def assess(hypotheses, actual):
    checks=[]
    for h in hypotheses:
        rule=h.get('rule') or {}
        if rule.get('kind') not in ('creator_zone','method','lineup_involvement'):
            checks.append({'hypothesis_id':h['id'],'title':h['title'],'status':'无法判断',
                'support_events':[],'counter_events':[],'alternative_events':[],
                'observed':0,'shots':0,'scope':h.get('scope',{}),
                'reason':'此条是综合过程观察，没有保存可自动验证的预测条件；保留人工复盘入口。',
                'criterion':'不把没有结构化判定规则的分析文字按射门占比误判。'})
            continue
        team=next((t for t in actual['teams'] if t['id']==rule.get('team_id')),None)
        scope=h.get('scope',{})
        all_shots=[c for c in (team or {}).get('chains',[]) if c['direction']=='attack']
        scope_known=all(not scope.get('eleven') or (c.get('home_players') is not None and c.get('away_players') is not None) for c in all_shots)
        shots=[c for c in all_shots if (not scope.get('periods') or c.get('period') in scope['periods'])
               and (scope.get('penalties',True) or not c.get('penalty'))
               and (not scope.get('eleven') or c.get('home_players')==c.get('away_players')==11)
               and (scope.get('state','all')=='all' or c.get('state')==scope['state'])]
        key='creator_zone' if rule.get('kind')=='creator_zone' else 'method'
        if rule.get('kind')=='lineup_involvement':
            ids=set(rule.get('player_ids',[]))
            support=[c for c in shots if c.get('player_id') in ids or c.get('creator_id') in ids]
            observed=[c for c in shots if c in support or c.get('creator_linked')]
            counter=[c for c in observed if c not in support]
        else:
            observed=[c for c in shots if c.get(key) is not None]
            support=[c for c in observed if c[key]==rule.get('value')]
            counter=[c for c in observed if c[key]!=rule.get('value')]
        complete=bool(team) and scope_known and not team['missing_layers'] and len(observed)==len(shots)
        if not shots or not scope_known or not team or team.get('missing_layers'):status='无法判断'
        elif not complete:status='部分支持' if support else '无法判断'
        elif not support:status='未获支持'
        elif len(support)/len(shots)>=(rule.get('baseline_share') if rule.get('baseline_share') is not None else 1):status='得到支持'
        else:status='部分支持'
        personnel=rule.get('kind')=='lineup_involvement'
        if personnel:
            # Participation can support the observable part, not prove an upgrade,
            # equal replacement or realization of the qualitative lineup risk.
            status='部分支持' if support and scope_known and team and not team.get('missing_layers') else '无法判断'
        known=[c for c in support if c.get('xg') is not None]
        low_quality=[c for c in known if c['xg']<.05] if rule.get('kind')=='creator_zone' else []
        quality_note=(f' 同一区域实际机会中 {len(low_quality)} 次低于0.05 xG，属于对“进入该区域就能形成高质量机会”的限制证据；不以其他路线当反驳。' if low_quality else '')
        checks.append({'hypothesis_id':h['id'],'title':h['title'],'status':status,
            'support_events':support,'counter_events':low_quality,'alternative_events':counter,'observed':len(observed),'shots':len(shots),
            'reason':f"可识别 {len(observed)}/{len(shots)} 次射门；符合原观察条件 {len(support)} 次。"+(' 已核查新增人员参与；是否等量接替、升级或风险兑现仍需人工判断。' if personnel else '')+(' 覆盖不完整，仅说明已观察到的支持过程。' if not complete else '')+quality_note,
            'quality_evidence':{'known_xg':len(known),'xg':sum(c['xg'] for c in known) if known else None,'low_quality':len(low_quality)},
            'scope':scope,'criterion':'按赛前保存的阶段、人数、点球及比分口径，核查原方式、区域或新增人员直接创造／终结的射门；其他路线不是反驳证据，不验证盯人责任或能力因果。'})
    return checks


def validate_report(report):
    UUID(report['match']['id'])
    if len(report['teams'])!=2:raise ValueError('需要两队数据')
    def refs_in(value):
        if isinstance(value,dict):
            if value.get('event_id') and value.get('match_id'):yield (value['match_id'],value['event_id'])
            for child in value.values():yield from refs_in(child)
        elif isinstance(value,list):
            for child in value:yield from refs_in(child)
    # Findings themselves must not be allowed to introduce their own evidence.
    refs=set(refs_in([report['teams'],report.get('regional_baseline'),report.get('evidence')]))
    for f in [*report['hypotheses'],*report.get('tactical_analysis',{}).get('findings',[])]:
        if any((e.get('match_id'),e.get('event_id')) not in refs for e in f['events']+f['counter_events']+f.get('alternative_events',[])):
            raise ValueError('发现引用了报告以外的事件')
    research.canonical(report)  # Reject NaN/Infinity; never coerce to zero.


def load(conn,user,draft_id):
    row=conn.execute('SELECT d.*,a.phase,a.match_id canonical_match_id,a.prematch_draft_id,a.previous_draft_id FROM content_drafts d JOIN analyst_report_links a ON a.draft_id=d.id WHERE d.id=? AND d.user_id=?',(draft_id,user)).fetchone()
    if not row:raise LookupError('报告不存在')
    envelope=research.export_packet(conn,row)
    return {'id':draft_id,'title':row['title'],'status':row['status'],'revision':research.revision(conn,draft_id),
        'previous_draft_id':row['previous_draft_id'],'prematch_draft_id':row['prematch_draft_id'],
        'report':envelope['packet']['analyst_report'],'editorial_notes':json.loads(row['overrides_json']).get('editorial_notes',''),
        'judgments':json.loads(row['overrides_json']).get('judgments',{}),
        'review':envelope['review']}


def latest(conn,user,mid,phase):
    row=conn.execute('SELECT a.draft_id FROM analyst_report_links a JOIN content_drafts d ON d.id=a.draft_id WHERE d.user_id=? AND a.match_id=? AND a.phase=? ORDER BY a.captured_at DESC,a.rowid DESC LIMIT 1',(user,mid,phase)).fetchone()
    return load(conn,user,row[0]) if row else None


def save(conn,user,report,previous=None,prematch=None):
    validate_report(report)
    packet={'schema_version':'video-evidence.v2','kind':report['phase'],'cutoff_at':report['cutoff_at'],
        'analysis_mode':report['analysis_mode'],'title':report['match']['home_name']+' vs '+report['match']['away_name']+' · '+('赛前' if report['phase']=='prematch' else '赛后'),
        'match':{'home_team':report['match']['home_name'],'away_team':report['match']['away_name']},
        'claims':[{'topic_id':h['id'],'title':h['title'],'limitation':h['limitation'],'evidence_refs':[]} for h in report['hypotheses']],
        'metrics':[],'event_timeline':[],'analyst_report':report}
    # Wall clock is provenance, not content identity. Repeated polls are idempotent.
    stable=deepcopy(packet)
    for k in ('cutoff_at','generated_at','captured_at'):stable['analyst_report'].pop(k,None)
    stable.pop('cutoff_at',None)
    packet['snapshot_id']=research.digest(stable)
    existing=conn.execute('SELECT draft_id FROM research_draft_links WHERE user_id=? AND snapshot_id=?',(user,packet['snapshot_id'])).fetchone()
    if existing and conn.execute('SELECT 1 FROM analyst_report_links WHERE draft_id=?',(existing[0],)).fetchone():return existing[0]
    created=research.store_packet(conn,user,packet,report_anchor(report))
    with tx(conn):
        conn.execute('INSERT OR IGNORE INTO analyst_report_links VALUES (?,?,?,?,?,?,?,?)',
            (created['draft_id'],report['match']['id'],report['phase'],report['revision'],
             previous['id'] if previous else None,prematch['id'] if prematch else None,
             utc_now_iso(),report['match']['kickoff_at']))
    return created['draft_id']


def report_anchor(report):
    from .prematch import resolve_lineup_match, configured_store
    store=configured_store()
    if not store:raise ValueError('标准比赛身份来源未配置')
    mid,_=resolve_lineup_match(report,store)
    return mid


def predicted_context(report):
    """Read the last actually captured prediction, not the last-match XI."""
    from contextlib import closing
    import sqlite3
    from backend.db.connections import connect_ro
    try:
        mid=report_anchor(report)
        cutoff=min(report['cutoff_at'],report['match']['kickoff_at'],key=lambda x:datetime.fromisoformat(x.replace('Z','+00:00')))
        with closing(connect_ro('odds')) as c:
            row=c.execute("""SELECT observed_at,ingested_at FROM bronze_fm_lineup_snap
                WHERE fotmob_match_id=? AND lineup_type='predicted'
                AND julianday(observed_at)<=julianday(?) AND julianday(ingested_at)<=julianday(?)
                ORDER BY julianday(observed_at) DESC,id DESC LIMIT 1""",(mid,cutoff,cutoff)).fetchone()
        if not row:return None
        historical=deepcopy(report)
        historical['cutoff_at']=max(row['observed_at'],row['ingested_at'])
        historical['findings']=[]
        return lineup_context(historical).get('lineup')
    except (OSError,ValueError,sqlite3.Error):return None


def regional_context(report,pre=None):
    """Historical reconstruction is a baseline, never an invented prior judgment."""
    basis=pre['report'] if pre else None
    mode='saved_prematch' if basis else 'reconstructed'
    if basis is None:
        try:basis=read_report('matches/'+report['match']['id'],{})
        except (OSError,ValueError,RuntimeError):basis=None
    report['regional_baseline']=None
    report['regional_baseline_info']={'mode':mode,'status':'unavailable','cutoff_at':report['match']['kickoff_at']}
    if basis:
        filters=basis.get('filters',{})
        comparable=all(str(filters.get(k,v))==v for k,v in [('eleven','1'),('state','all')])
        report['regional_baseline']=[{k:t.get(k) for k in ('id','name','chains','samples','minutes','missing_layers')} for t in basis['teams']]
        report['regional_baseline_info'].update(status='available' if comparable and report.get('regional_current') else 'incomparable',
            cutoff_at=basis.get('cutoff_at'),filters=filters,revision=basis.get('revision'),draft_id=pre['id'] if pre else None,
            note='保存的赛前样本' if pre else '赛前历史基准·事后重建，非当时保存的判断')
    return report

def attach_actual_positions(report,context):
    """Only schematic coordinates cross sources; actual IDs/names/shirts stay Opta."""
    if context.get('type') not in ('confirmed','standard'):return
    from .analyst_insights import person_id
    for side in ('home','away'):
        src=next((t for t in context.get('teams',[]) if t.get('side')==side),{})
        dst=next((t for t in report.get('lineup',{}).get('teams',[]) if t.get('side')==side),{})
        positions={person_id(p):p for p in src.get('players',[]) if person_id(p)}
        for player in dst.get('players',[]):
            pos=positions.get(person_id(player),{})
            if pos.get('coordinate_frame')=='provider_vertical_0_1':
                player.update({k:pos.get(k) for k in ('pos_x','pos_y','coordinate_frame')})
    report['lineup']['observed_at']=context.get('observed_at')
    report['lineup']['positions_source']={'source':'VIP lineup schematic','snapshot_id':context.get('snapshot_id'),'observed_at':context.get('observed_at')}


def generate(conn,user,mid,phase,params=None):
    mid=str(UUID(mid))
    previous=latest(conn,user,mid,phase)
    pre=latest(conn,user,mid,'prematch') if phase=='postmatch' else None
    if phase=='prematch':
        report=lineup_context(read_report('matches/'+mid,{**(params or {}),'context':'1'}))
        report['phase']=phase
    elif phase=='postmatch':
        from urllib.parse import urlencode
        pair={k:v for k,v in (params or {}).items() if k in ('pair_a','pair_b') and v}
        report=source_read('postmatch/'+mid+('?' + urlencode(pair) if pair else ''))
        # Opta remains the actual XI authority; VIP supplies only source-recorded
        # schematic coordinates for players independently matched to that XI.
        position_report=deepcopy(report);position_report.setdefault('findings',[])
        context=lineup_context(position_report).get('lineup',{})
        attach_actual_positions(report,context)
    else:raise ValueError('报告阶段无效')
    if report['match']['id']!=mid:raise ValueError('比赛身份不一致')
    if any(i.get('component')=='core' for i in report.get('quality',{}).get('detail',{}).get('issues',[])):
        raise ValueError('核心数据质量检查未通过，暂不能生成分析判断')
    from backend.api.standard_identity import configured_store
    from backend.api.routes_visualization import team_crest
    store=configured_store()
    if store:
        for side in ('home','away'):
            report['match'][side+'_crest_url']=team_crest(report['match'][side+'_id'],store)
    report['analysis_mode']='captured_pre_match' if phase=='prematch' and datetime.now(timezone.utc)<datetime.fromisoformat(report['match']['kickoff_at'].replace('Z','+00:00')) else 'current_postmatch' if phase=='postmatch' else 'reconstructed'
    report['hypotheses']=findings(report)
    prior_report=(previous or pre or {}).get('report',{})
    before=prior_report.get('lineup')
    if before and before==report.get('lineup'):
        before=prior_report.get('lineup_baseline')
    if not before or report.get('lineup',{}).get('type')!='predicted':
        before=predicted_context(pre['report'] if pre else report) or before
    if before==report.get('lineup'):before=None
    report['lineup_baseline']=before
    report['lineup_changes']=roster_diff(before,report.get('lineup'))
    if phase=='postmatch':regional_context(report,pre)
    from .analyst_insights import enrich
    enrich(report,pre['report'] if pre else None)
    report['checks']=assess(pre['report']['hypotheses'],report) if pre else []
    for check in report['checks']:
        edit=pre.get('judgments',{}).get(check['hypothesis_id'],{})
        if edit.get('withdrawn'):
            check.update(status='无法判断',reason='赛前判断已被分析师撤回：'+edit.get('reason',''),support_events=[],counter_events=[],alternative_events=[])
        elif edit.get('text'):
            check['reason']+=' 本条有人工修订，自动核验只覆盖原始结构化条件；修订文字需人工复核。'
        check['editorial']=edit
    report['prematch_basis']={'draft_id':pre['id'],'analysis_mode':pre['report']['analysis_mode'],'review_revision':pre['revision']} if pre else None
    if pre and pre['report']['analysis_mode']=='reconstructed':
        report.setdefault('notes',[]).append('对应赛前版本为历史重建；以下核验属于回放实验，不证明当时已有这些判断。')
    if phase=='postmatch' and not pre:report.setdefault('notes',[]).append('没有保存的赛前判断，不能补写为当时已作出的预测。')
    report.setdefault('quality',{'status':'ready_for_review' if all(t['samples'] and not t['missing_layers'] for t in report['teams']) else 'incomplete','warnings':[]})
    report['narrative_source']='validated_evidence_templates'
    report['workflow_version']=VERSION
    report['sample_match_ids']={t['id']:[s['id'] for s in t['samples']] for t in report['teams']}
    return save(conn,user,report,previous,pre)


def enqueue(conn,user,mid,phase,input_revision,params=None):
    UUID(mid)
    if phase not in ('prematch','postmatch'):raise ValueError('无效阶段')
    key=research.digest([user,mid,phase,input_revision,params or {}])
    with tx(conn):
        row=conn.execute("SELECT id FROM job_runs WHERE job_name='analyst_report' AND idempotency_key=?",(key,)).fetchone()
        if row:return row[0]
        jid=new_uuid()
        conn.execute("INSERT INTO job_runs(id,job_name,idempotency_key,status,max_attempts,meta_json,created_at) VALUES (?,'analyst_report',?,'pending',3,?,?)",(jid,key,json.dumps({'user_id':user,'match_id':mid,'phase':phase,'params':params or {}}),utc_now_iso()))
    return jid


def run_one(conn):
    with tx(conn):
        # Recover interrupted workers, without stealing a live analysis.
        conn.execute("UPDATE job_runs SET status='failed',finished_at=?,error_summary='worker lease expired' WHERE job_name='analyst_report' AND status='running' AND julianday(started_at)<julianday('now','-10 minutes')",(utc_now_iso(),))
        if conn.execute("SELECT 1 FROM job_runs WHERE job_name='analyst_report' AND status='running'").fetchone():return None
        row=conn.execute("SELECT * FROM job_runs WHERE job_name='analyst_report' AND (status='pending' OR (status='failed' AND attempt<max_attempts AND julianday(finished_at)<julianday('now','-1 minute'))) ORDER BY created_at LIMIT 1").fetchone()
        if not row:return None
        conn.execute("UPDATE job_runs SET status='running',attempt=attempt+?,started_at=? WHERE id=?",(int(row['status']=='failed'),utc_now_iso(),row['id']))
    meta=json.loads(row['meta_json'])
    try:
        did=generate(conn,meta['user_id'],meta['match_id'],meta['phase'],meta.get('params'))
        meta['draft_id']=did
        with tx(conn):conn.execute("UPDATE job_runs SET status='succeeded',finished_at=?,output_count=1,meta_json=?,error_summary=NULL WHERE id=?",(utc_now_iso(),json.dumps(meta),row['id']))
    except Exception:
        # Source responses can contain private details; expose a bounded error category.
        with tx(conn):conn.execute("UPDATE job_runs SET status='failed',finished_at=?,error_summary='分析失败；检查来源连接、映射及数据覆盖后重试' WHERE id=?",(utc_now_iso(),row['id']))
    return row['id']
