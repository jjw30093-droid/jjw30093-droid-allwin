"""Deterministic, source-scoped tactical judgments; never fetch replacement statistics."""
from collections import Counter
from math import isfinite
from .research import digest

VERSION = 'analyst-insights.v4'
LANES = ['右边路', '右肋部', '中路', '左肋部', '左边路']


def total(events):
    values=[e.get('xg') for e in events if isinstance(e.get('xg'),(int,float)) and isfinite(e['xg'])]
    return sum(values) if values else None


def shown(value):
    return '未提供' if value is None else f'{value:.2f}'


def person_id(person):
    return (person.get('identity_link') or {}).get('evidence_player_id') or (person.get('profile') or {}).get('id') or person.get('id')


def roster_ids(lineup, side):
    t=next((t for t in (lineup or {}).get('teams',[]) if t.get('side')==side),{})
    return {person_id(p) for p in t.get('players',[]) if person_id(p)}


def involved(chain, ids):
    # Direct chance creation/finishing only; incidental sequence touches are not equivalent.
    return chain.get('player_id') in ids or chain.get('creator_id') in ids


def player_summary(person, team):
    pid=person_id(person)
    p=next((p for p in team.get('players',[]) if p['id']==pid),None)
    if not p:return {'id':pid,'name':person['name'],'minutes':None,'small_sample':True,'metrics':{},'events':[]}
    metrics={k:p.get(k) for k in ('progressive','passes','xg','xa','model_xt')}
    events=[c for c in team['chains'] if c['direction']=='attack' and involved(c,{pid})]
    return {'id':pid,'name':p['name'],'position':p.get('position'),'minutes':p.get('minutes'),
            'small_sample':(p.get('minutes') or 0)<180,
            'metrics':metrics,'per90':{k:v*90/p['minutes'] if v is not None and p.get('minutes',0)>0 else None for k,v in metrics.items() if k!='xa'},
            'events':events,'direct_chances':len(events),'sample_matches':len(team.get('samples',[])),
            'xa_match_count':p.get('xa_match_count',0),'xa_sources':p.get('xa_sources',[])}


def scope(report):
    return {'periods':report.get('filters',{}).get('periods',[1,2]),
            'eleven':str(report.get('filters',{}).get('eleven',1))=='1',
            'penalties':bool(report.get('filters',{}).get('penalties',False)),
            'state':report.get('filters',{}).get('state','all')}


def base_card(report,team,kind,key):
    return {'id':digest([report['match']['id'],team['id'],kind,key,VERSION])[:20],
        'sample':{'matches':len(team['samples']),'minutes':team['minutes']},
        'scope':scope(report),'rule':{'kind':kind,'team_id':team['id']},
        'players':[],'events':[],'counter_events':[], 'alternative_events':[],
        'coverage':{},'limitation':'事件支持观察假设，不确认无球盯人责任或因果。'}


def lineup_card(report,team,change,baseline):
    if not change.get('comparable') or not (change.get('added') or change.get('removed')):return None,None
    added=[player_summary(p,team) for p in change['added']]
    removed=[player_summary(p,team) for p in change['removed']]
    side=change['side'];old_ids=roster_ids(baseline,side);new_ids=roster_ids(report.get('lineup'),side)
    attacks=[c for c in team['chains'] if c['direction']=='attack']
    old=[c for c in attacks if involved(c,old_ids)];new=[c for c in attacks if involved(c,new_ids)]
    no_longer=[c for c in old if not involved(c,new_ids)]
    entrants={p['id'] for p in added if p['id']};entrant_chains=[c for c in attacks if involved(c,entrants)]
    details={'team_id':team['id'],'side':side,'added':added,'removed':removed,
        'before_direct_chances':len(old),'after_direct_chances':len(new),'total_chances':len(attacks),
        'not_retained_chances':len(no_longer),'information_type':'lineup_change',
        'note':'这是两份名单的差异，不是新伤停；比较使用同一份赛前历史样本。预计名单不是实际出场事实。'}
    uncertain=[p for p in added if p['small_sample']]
    card=base_card(report,team,'lineup_involvement',sorted(entrants))
    card['title']=team['name']+'：'+('新增人员样本偏少，替代效果需验证' if uncertain else '首发职责重新分配，核查机会路线能否延续')
    names=lambda ps:'、'.join(p['name'] for p in ps) or '无'
    card['text']=f"进入首发：{names(added)}；退出首发：{names(removed)}。同一历史样本中，两份名单直接创造或终结的机会覆盖为 {len(old)} → {len(new)} / {len(attacks)} 次；其中 {len(no_longer)} 次不再由当前首发直接创造或终结。"
    card['condition']='若继续使用近期进攻路线，重点检查新增人员是否承担最后传球／终结职责；名单贡献差额不等于本场预期增减。'
    card['limitation']=('新增人员中 '+names(uncertain)+' 的历史有效分钟不足 180，不能据此判断升级或降级。' if uncertain else '出场时间与对手不同，历史累计贡献不能直接相减预测本场。')+' 未首发不等于伤停，不以既知伤停解释新变化。'
    observations=[]
    progressors=[p for p in added if (p['minutes'] or 0)>=180 and (p['metrics'].get('progressive') or 0)>0 and p.get('position')!='Goalkeeper']
    if progressors:
        p=max(progressors,key=lambda p:p['metrics']['progressive'])
        observations.append(f"{p['name']} 在同一历史窗口有 {p['minutes']:.0f} 分钟、{p['metrics']['progressive']} 次推进传球，提供推进延续的依据，不能把这项人员变化笼统解释为进攻削弱。")
    creators=[p for p in removed if p.get('direct_chances',0)>0]
    if creators:
        p=max(creators,key=lambda p:p['direct_chances'])
        observations.append(f"退出人员 {p['name']} 曾直接创造或终结 {p['direct_chances']} 条机会；主要风险是相关创造／终结职责的接替，需通过本场事件核验。")
    if any(p.get('position')=='Goalkeeper' for p in added+removed):
        observations.append('门将变化应分别核查出球与扑救；不以普通射门 xG 或少量出场推断门将能力下降。')
    xa_people=[p for p in removed+added if p['metrics'].get('xa') is not None]
    if xa_people:
        observations.append('官方创造价值：'+'；'.join(f"{p['name']} 整场 xA {shown(p['metrics']['xa'])}（{p.get('xa_match_count',0)} 场有值）" for p in xa_people)+
            '。这是各自历史样本的整场统计，不作为换人后的预期增减量。')
    details['role_observations']=observations
    card['text']+=' '+' '.join(observations[:2])
    card['players']=sorted(entrants|{p['id'] for p in removed if p['id']})
    card['events']=entrant_chains;card['alternative_events']=no_longer
    card['coverage']={'shots':len(attacks),'entrant_direct_chances':len(entrant_chains),'unresolved':change.get('unresolved_before',0)+change.get('unresolved_after',0)}
    card['rule'].update(player_ids=sorted(entrants),baseline_count=len(entrant_chains),baseline_share=len(entrant_chains)/len(attacks) if attacks else None)
    card['assessment']='人员变化风险' if uncertain or no_longer else '人员变化观察'
    return card,details


def regional_card(report,team,other,index,zone=None):
    attacks=[c for c in team['chains'] if c['direction']=='attack']
    conceded=[c for c in other['chains'] if c['direction']=='defence']
    cells=[]
    for z in range(15):
        if zone is not None and zone!=z:continue
        own=[c for c in attacks if c.get('creator_zone')==z];opp=[c for c in conceded if c.get('creator_zone')==z]
        if own and opp:
            # Rank only recorded chance value, not unsupported value imputation.
            ox,dx=total(own),total(opp)
            strength=min((ox or 0)*90/max(team['minutes'],1),(dx or 0)*90/max(other['minutes'],1))
            cells.append((strength,z,own,opp))
    if not cells:return None
    _,z,own,opp=max(cells,key=lambda c:(c[0],-c[1]))
    xg,xga=total(own),total(opp)
    known=sum(c.get('xg') is not None for c in opp)
    overall_known=sum(c.get('xg') is not None for c in conceded)
    quality=xga/known if known else None
    overall=total(conceded)/overall_known if overall_known else None
    side=('home','away')[index];current=roster_ids(report.get('lineup'),side)
    creators=Counter(c['creator_id'] for c in own if c.get('creator_id'))
    available=[p for p in team.get('players',[]) if p['id'] in current and p['id'] in creators]
    missing=[p for p in team.get('players',[]) if p['id'] not in current and p['id'] in creators]
    complete=known==len(opp) and all(c.get('xg') is not None for c in own)
    favorable=complete and quality is not None and overall is not None and quality>overall and bool(available)
    label=['后场','中场','前场'][z//5]+' · '+LANES[z%5]
    card=base_card(report,team,'creator_zone',z)
    card['assessment']='可利用的对位' if favorable else '不能确认明显优势'
    card['title']=team['name']+'：'+label+('值得优先尝试' if favorable else '有路线重合，但不宜夸大优势')
    card['text']=f"本队从该区域最后传球形成 {len(own)} 次射门、{shown(xg)} xG；{other['name']}在对应区域被创造 {len(opp)} 次射门、{shown(xga)} xG。对手该区单次已知射门 xG {shown(quality)}，其全部被射门均值 {shown(overall)}。"
    card['text']+=('该区机会质量高于对手整体被射门均值，且当前首发保留相关创造者。' if favorable else '次数重合本身不能证明存在高质量机会缺口。')
    card['condition']='相关创造者需获得与历史类似的接球和最后传球条件；这是进攻路线假设，不是已确定的盯人对位。'
    names=lambda ps:'、'.join(p['name'] for p in ps) or '未关联到当前首发'
    card['personnel']='当前首发相关创造者：'+names(available)+'。历史相关创造者未进入首发：'+('、'.join(p['name'] for p in missing) or '无已识别项')+'。'
    # Counter-side personnel are evidence candidates, not invented marking assignments.
    defenders=roster_ids(report.get('lineup'),('away','home')[index]);defrefs=[]
    for match in other.get('analyst_matches',[]):
        for cell in match.get('zones',[]):
            if cell['zone']==14-z:
                defrefs.extend(e for e in cell.get('actions',{}).get('recoveries',{}).get('events',[]) if e.get('player_id') in defenders)
    dnames={p['id']:p['name'] for p in other.get('players',[])}
    card['opponent_personnel']=[dnames[p] for p in sorted({e['player_id'] for e in defrefs}) if p in dnames]
    card['personnel']+=' 对方该区有夺回球权记录的本场首发：'+('、'.join(card['opponent_personnel']) or '没有可关联样本')+'；不据此指定防守责任。'
    card['players']=[p['id'] for p in available]
    card['events']=own+opp
    card['alternative_events']=[c for c in attacks if c.get('creator_zone') is not None and c['creator_zone']!=z]
    card['rule'].update(value=z,baseline_share=len(own)/len(attacks) if attacks else None)
    card['sample']['opponent_matches']=len(other['samples']);card['sample']['opponent_minutes']=other['minutes']
    card['coverage']={'own_linked':sum(c.get('creator_zone') is not None for c in attacks),'own_shots':len(attacks),'opponent_linked':sum(c.get('creator_zone') is not None for c in conceded),'opponent_shots':len(conceded)}
    card['limitation']='区域选取按两边已记录 xG/90 的较小值排序，是观察优先级，不是预测模型。关联不全、对手差异及小样本限制结论；其他路线机会不是反驳证据。'
    return card


def enrich(report, history=None):
    source=history or report
    teams=source['teams']
    report['source_policy']={'match_statistics':'cc_opta_only','lineups_injuries':'vip_snapshots','fotmob_statistics_fallback':False}
    report['metric_coverage']={'xa':{'source':'CC · Opta','scope':'whole_match','teams':[{'name':t['name'],'known_players':sum(p.get('xa') is not None for p in t.get('players',[])),'players':len(t.get('players',[]))} for t in report['teams']],
        'note':'官方 xA 读取 CC Opta 球员统计及当前入库版本对应的 xgmap 原始归档；归档需通过内容哈希、比赛及球员身份校验。整场值保留逐场来源，不分摊到区域或选区。'}}
    report['insight_version']=VERSION
    if not all(t.get('players') for t in teams):return
    cards=[];impacts=[]
    for i,t in enumerate(teams):
        c,impact=lineup_card(report,t,report['lineup_changes']['teams'][i],report.get('lineup_baseline'))
        if c:cards.append(c);impacts.append(impact)
        region=regional_card(report,t,teams[1-i],i)
        if region:cards.append(region)
    report['lineup_impacts']=impacts
    if cards:
        # Preserve one directly measured method/finishing summary if space remains.
        fallback=[h for h in report['hypotheses'] if h.get('rule',{}).get('kind') in ('method','finishing')]
        if history:fallback.sort(key=lambda h:h.get('rule',{}).get('kind')!='finishing')
        cards.extend(fallback[:max(0,5-len(cards))])
        for c in cards:
            if c.get('rule',{}).get('kind') in ('creator_zone','lineup_involvement'):
                c['scope']=scope(source)
            if history and c.get('rule',{}).get('kind') in ('creator_zone','lineup_involvement'):
                c['basis']='保存的赛前历史样本，正式名单对照；不使用本场表现回写赛前能力'
                c['condition']='赛前视角回看：'+c['condition']
        report['hypotheses']=cards[:5]
    from .analyst_tactical import attach
    attach(report, source, cards)
