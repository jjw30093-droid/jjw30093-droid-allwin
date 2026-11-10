"""Analyst explanations derived from frozen evidence, with inspectable measurements.

The rules describe observed processes and conditional matchups, not causal estimates
of player replacements or a betting forecast. No network/model training happens here.
"""
from collections import Counter, defaultdict
from math import isfinite

from .analyst_insights import base_card, involved, person_id, regional_card, roster_ids, shown, total

VERSION = 'tactical-analysis.v2'
LANES = ['右边路','右肋部','中路','左肋部','左边路']


def finite(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and isfinite(v)


def unique(events):
    return list({(e.get('match_id'),e.get('event_id')):e for e in events if e.get('event_id') and e.get('match_id')}.values())


def attack(team):
    return [c for c in team.get('chains',[]) if c.get('direction')=='attack']


def named(team,pid):
    return next((p['name'] for p in team.get('players',[]) if p['id']==pid),'未关联球员')


def clock(e):
    m=e.get('minute',0);end={1:45,2:90,3:105,4:120}.get(e.get('period'),90)
    return (f'{end}+{m-end}' if m>end else str(m))+':'+str(e.get('second',0)).zfill(2)


def make(report,team,topic,key,title,text,events=(),measurements=None,priority=20,assessment='过程观察'):
    c=base_card(report,team,'descriptive',[topic,key])
    c.update(topic=topic,title=team['name']+'：'+title,text=text,events=unique(events),
             measurements=measurements or {},priority=priority,assessment=assessment,
             condition='比较限于本快照的比赛、阶段、人数及比分口径；点击事件核查具体过程。')
    c['coverage']={'evidence_events':len(c['events']),'sample_matches':len(team.get('samples',[]))}
    c['players']=sorted({e['player_id'] for e in c['events'] if e.get('player_id')})
    return c


def detail_rows(team,key):
    return [e for m in team.get('analyst_detail',{}).get('matches',[]) for e in m.get(key,[])]


def lineup_paths(report,team,old):
    """Locate duties of changed players on real chance routes, not aggregate subtraction."""
    card=dict(old);card.update(topic='lineup',priority=90)
    side='home' if report['teams'][0]['id']==team['id'] else 'away'
    current=roster_ids(report.get('lineup'),side)
    ch=next((x for x in report.get('lineup_changes',{}).get('teams',[]) if x['side']==side),{})
    removed={person_id(x) for x in ch.get('removed',[])}
    added={person_id(x) for x in ch.get('added',[])}
    lost=[x for x in attack(team) if x.get('creator_id') in removed]
    gained=[x for x in attack(team) if x.get('creator_id') in added]
    duty=Counter((x.get('creator_zone'),x.get('method')) for x in lost)
    if duty:
        (zone,method),n=duty.most_common(1)[0]
        label=('未定位区域' if zone is None else ['后场','中场','前场'][zone//5]+LANES[zone%5])
        preserved=[x for x in attack(team) if x.get('creator_id') in current and x.get('creator_zone')==zone and x.get('method')==method]
        actors=sorted({named(team,x['creator_id']) for x in preserved if x.get('creator_id')})
        card['text']+=f' 具体接替任务：退出人员从{label}以{method}关联 {n} 次射门、{shown(total([x for x in lost if (x.get("creator_zone"),x.get("method"))==(zone,method)]))} xG；当前首发中同一路线仍有 {len(preserved)} 次记录'+('，由'+'、'.join(actors)+'参与。' if actors else '，暂不能确认同角色接替者。')
        card['counter_events']=preserved
        card['condition']=f'重点核查{label}的{method}最后传球职责能否保留；当前首发的同路线记录是对“完全失去创造能力”的反向证据。'
    card['measurements']={'removed_creator_shots':len(lost),'added_creator_shots':len(gained),
                          'removed_creator_xg':total(lost),'added_creator_xg':total(gained)}
    return card


def matchups(report,team,other,index):
    cards=[]
    for zone in range(15):
        c=regional_card(report,team,other,index,zone)
        if not c:continue
        own=[x for x in attack(team) if x.get('creator_zone')==zone]
        opp=[x for x in other.get('chains',[]) if x.get('direction')=='defence' and x.get('creator_zone')==zone]
        methods=Counter(x.get('method') for x in own)
        method=methods.most_common(1)[0][0]
        same=[x for x in opp if x.get('method')==method]
        ours=[x for x in own if x.get('method')==method]
        c.update(topic='matchup',priority=70+(5 if same else 0),
                 measurements={'own_route_shots':len(ours),'opponent_route_conceded':len(same),
                               'own_route_xg':total(ours),'opponent_route_xga':total(same)})
        c['text']+=f' 路线细分：本队在此区主要以{method}形成 {len(ours)} 次射门、{shown(total(ours))} xG；对手在相同区、相同方式被创造 {len(same)} 次、{shown(total(same))} xG。'
        if not same:
            c['assessment']='区域重合，打法匹配证据不足'
            c['text']+=' 两队区域重合，但具体进攻手段尚未对上，不能直接认定可利用漏洞。'
        c['limitation']+=' 同方式少于3次为稀疏证据；不同对手及比赛状态没有被视为因果控制。'
        c['counter_events']=[x for x in own if x.get('xg') is not None and x['xg']<.05]
        c['counter_label']='该区域低价值机会：限制“进入此区便会高质量射门”的判断'
        c['_rank']=min(total(own) or 0,total(opp) or 0)
        cards.append(c)
    cards.sort(key=lambda c:c['_rank'],reverse=True)
    for c in cards:c.pop('_rank',None)
    return cards[:3]


def progression(report,team):
    cards=[];patterns=team.get('attack_patterns',[])
    routes=team.get('routes',[])
    shots=[c for c in attack(team) if not c.get('penalty')]
    penalties=[c for c in attack(team) if c.get('penalty')]
    if routes:
        main=max(routes,key=lambda r:r.get('possessions',0));n=main['possessions'];s=main['shot_possessions']
        text=f'最常使用的出球结构是{main["label"]}：{n} 个回合中 {s} 个形成射门（{s/n:.1%}）。'
        card=make(report,team,'progression',main['label'],'常用出球路线的射门转化'+('偏少' if n>=10 and s/n<.1 else '需结合进入区域检查'),text,shots,
                  {'possessions':n,'shot_possessions':s,'conversion':s/n},priority=55)
        card['limitation']='这里的分母是记录回合，不是控球时间；未射门不等于被压迫成功。无法由回合终止直接指定失误球员。'
        cards.append(card)
    # Use chain grouping: possession categories can include a penalty later in a sequence.
    grouped=defaultdict(list)
    for c in shots:grouped[c.get('method','未分类')].append(c)
    if grouped:
        method,items=max(grouped.items(),key=lambda kv:(total(kv[1]) or 0,len(kv[1])))
        c=make(report,team,'progression','chance-method','非点球机会主要来自'+method,
               f'{len(shots)} 次非点球射门、{shown(total(shots))} xG；其中{method} {len(items)} 次、{shown(total(items))} xG。点球 {len(penalties)} 次单列，不混入阵地创造能力。',items,
               {'shots':len(shots),'xg':total(shots),'method_shots':len(items),'method_xg':total(items),'penalties':len(penalties)},priority=58)
        c['rule'].update(kind='method',value=method,baseline_share=len(items)/len(shots))
        c['scope']['penalties']=False;c['alternative_events']=[x for x in shots if x not in items]
        cards.append(c)
    return cards


def transitions(report,team):
    if 'transitions' not in team:return []
    chains={(c['match_id'],c['event_id']):c for c in team.get('chains',[])}
    selected=unique([chains[(e['match_id'],e['event_id'])] for e in team['transitions']
                     if (e.get('match_id'),e.get('event_id')) in chains
                     and chains[(e['match_id'],e['event_id'])]['direction']==e.get('direction')])
    own=[e for e in selected if e['direction']=='attack'];opponent=[e for e in selected if e['direction']=='defence']
    values={'own_shots':len(own),'own_xg':total(own),'own_known_xg':sum(e.get('xg') is not None for e in own),
            'opponent_shots':len(opponent),'opponent_xg':total(opponent),'opponent_known_xg':sum(e.get('xg') is not None for e in opponent),
            'definition':'possession_regain_to_shot_within_15s_selected_events'}
    text=f'所选时段内，本队夺回球权后15秒内形成 {len(own)} 次射门、已记录 {shown(values["own_xg"])} xG（有值{values["own_known_xg"]}/{len(own)}）；对手对应为 {len(opponent)} 次、{shown(values["opponent_xg"])} xG（有值{values["opponent_known_xg"]}/{len(opponent)}）。'
    risk=values['own_xg'] is not None and values['opponent_xg'] is not None and values['opponent_xg']>values['own_xg']
    c=make(report,team,'transition','regain-15s','夺回后的机会收支偏向对手' if risk else '夺回球权后的快速射门需要结合质量判断',
           text+('应重点回查对手夺回后的推进路线与机会，不把全部防守风险归于阵地低位。' if risk else '次数和xG只反映已记录过程，不单独证明转换防守好坏。'),selected,values,priority=50 if risk else 20,assessment='转换风险' if risk else '过程观察')
    c['limitation']='仅统计同一比分区间内、由抢断/拦截/夺回记录开始的控球回合，首尾均在报告选区。15秒从本次夺回计时；不代表所有丢球后的进攻，也不等同于整场quick_transition_proxy或5秒反抢指标。'
    return [c]


def setpieces(report,team):
    rows=[e for m in team.get('report_extensions',{}).get('matches',[]) for e in m.get('restarts',[])]
    cards=[]
    for kind in ('角球','任意球','界外球','门球'):
        group=[r for r in rows if r.get('kind')==kind and r.get('direction')=='attack']
        if not group:continue
        follow=unique([s for r in group for s in r.get('subsequent_shots',[])])
        # The extension calls the linked list shots; remain compatible with older snapshots.
        if not follow:follow=unique([s for r in group for s in r.get('shots',[]) if isinstance(s,dict)])
        takers=Counter(r.get('player_id') for r in group if r.get('player_id'))
        who='、'.join(named(team,p) for p,_ in takers.most_common(2)) or '未关联'
        contacts=sum(bool(r.get('first_contact')) for r in group)
        c=make(report,team,'setpiece',kind,kind+'的主罚与后续威胁',
               f'{len(group)} 次{kind}由{who}等执行；{contacts} 次有首个记录接触；同一记录序列内后续射门 {len(follow)} 次、{shown(total(follow))} xG。'+('门球作为出球分析，不并入进攻定位球效率。' if kind=='门球' else '优先核查落点与接触者能否在本场复现；首次接触不等于赢得落点控制。'),group+follow,
               {'restarts':len(group),'contact_known':contacts,'shots':len(follow),'xg':total(follow)},priority=35 if follow else 15)
        c['limitation']='只关联至下一次重新开始前的已记录序列；没有录像确认的第二点控制不能当作争顶获胜。'
        cards.append(c)
    return cards


def pairs(report,team):
    rows=team.get('pairs',[])
    if not rows:return []
    parts=[];measure={}
    names={'both':'共同在场','only_a':'仅A','only_b':'仅B','neither':'均不在场'}
    for r in rows:
        minutes=r.get('minutes',0);value=(r['xg']-r['xga'])*90/minutes if minutes and finite(r.get('xg')) and finite(r.get('xga')) else None
        label=names.get(r['group'],r['group']);parts.append(f'{label} {r["matches"]}场/{minutes:.1f}分钟，净xG每90 {shown(value)}')
        measure[r['group']]={'minutes':minutes,'matches':r['matches'],'net_xg_per90':value,'small_sample':r.get('small_sample',True)}
    small=any(x.get('small_sample',True) for x in rows)
    c=make(report,team,'pair','four-groups','组合优劣仍受小样本限制' if small else '组合在不同在场情形下的表现有差别',
           '；'.join(parts)+'。'+('至少一组少于3场或180分钟，不能据此定为有害／优秀搭配。' if small else '结合各组对手和比分构成判断可重复性。'),measurements=measure,priority=25)
    c['limitation']='四组不是随机实验；共同出场与表现差异不能直接解释为搭配因果。'
    return [c]


def intervals(report,team):
    cards=[]
    for m in team.get('report_extensions',{}).get('matches',[]):
        rows=m.get('intervals',[])
        for a,b in zip(rows,rows[1:]):
            if a.get('period')!=b.get('period') or set(a.get('lineup_ids',[]))==set(b.get('lineup_ids',[])):continue
            added=set(b['lineup_ids'])-set(a['lineup_ids']);removed=set(a['lineup_ids'])-set(b['lineup_ids'])
            name='、'.join(named(team,p) for p in added) or '人员减少'
            same=(a.get('state'),a.get('own_players'),a.get('opponent_players'))==(b.get('state'),b.get('own_players'),b.get('opponent_players'))
            c=make(report,team,'substitution',[m['match_id'],b['start_ms']],name+'进入后的相邻区间',
                   f'换出／离场：'+('、'.join(named(team,p) for p in removed) or '未记录')+f'。此前 {a["minutes"]:.1f}分钟 {a["shots"]}射/{shown(a.get("xg"))} xG，之后 {b["minutes"]:.1f}分钟 {b["shots"]}射/{shown(b.get("xg"))} xG；被创造xG {shown(a.get("xga"))} → {shown(b.get("xga"))}。'+('两个相邻区间比分和人数相同。' if same else '比分或人数同时变化，不能把差异归于这次换人。'),
                   [e for e in team.get('chains',[]) if e['match_id']==m['match_id'] and a['start_ms']<e.get('elapsed_ms',-1)<=b['end_ms']],
                   {'before':{k:a.get(k) for k in ('minutes','shots','xg','xga','state','own_players','opponent_players')},'after':{k:b.get(k) for k in ('minutes','shots','xg','xga','state','own_players','opponent_players')},'same_context':same},priority=30)
            c['limitation']='仅比较相邻人员区间，区间长度不同且可能很短；不能由小段xG认定换人效果。'
            cards.append(c)
    return cards


def creators(report,team):
    cards=[];rows=detail_rows(team,'xa')
    if rows:
        sums={k:sum(x[k] for x in rows if finite(x.get(k))) for k in ('total','openplay','setplay')}
        known={k:sum(finite(x.get(k)) for x in rows) for k in sums}
        parts='；'.join(f'{label} {shown(sums[k]) if known[k] else "未提供"}（{known[k]}条球员-比赛记录）' for k,label in [('total','总xA'),('openplay','运动战xA'),('setplay','定位球xA')])
        c=make(report,team,'creation','xa-components','创造价值分开核查运动战与定位球',parts+'。分项只按来源有值记录相加；缺失分项不从总值倒推。',measurements={'sums':sums,'known_records':known},priority=35)
        c['limitation']='官方整场xA不能拆到比分、时段或区域；不同分项覆盖可能不同，不能把缺失当零计算占比。'
        cards.append(c)
    xt=detail_rows(team,'xt_actions')
    if xt:
        positive=[e for e in xt if finite(e.get('xt_added')) and e['xt_added']>0];negative=[e for e in xt if finite(e.get('xt_added')) and e['xt_added']<0]
        best=sorted(positive,key=lambda e:e['xt_added'],reverse=True)[:5]
        loss=sorted(negative,key=lambda e:e['xt_added'])[:5]
        c=make(report,team,'xt','actions','推进价值由具体动作定位',
               f'已评分 {len(xt)} 个动作：正向增值 {sum(e["xt_added"] for e in positive):.3f}，负向变化 {sum(e["xt_added"] for e in negative):.3f}。'+('最高增值记录由'+named(team,best[0].get('player_id'))+f'完成，单次 {best[0]["xt_added"]:.3f}。' if best else '当前样本无正向增值动作。'),best,
               {'actions':len(xt),'positive':sum(e['xt_added'] for e in positive),'negative':sum(e['xt_added'] for e in negative)},priority=30)
        c['counter_events']=loss;c['limitation']='采用已训练模型的动作评分；负向变化不是球员失误标签，也不等于失球概率。'
        cards.append(c)
    return cards


def recent(report,team):
    history=team.get('report_extensions',{}).get('history',{})
    a,b=history.get('recent5'),history.get('recent10')
    if not a or not b or not a.get('minutes') or not b.get('minutes'):return []
    c=make(report,team,'form','5-10','近期机会产量相对较长窗口的变化',
           f'最近{a["matches"]}场 xG每90 {shown(a.get("xg_per90"))}、xGA每90 {shown(a.get("xga_per90"))}；较长{b["matches"]}场窗口为 {shown(b.get("xg_per90"))} / {shown(b.get("xga_per90"))}。两窗口重叠，变化不能当作独立检验。',
           measurements={'recent5':a,'recent10':b},priority=20)
    if a.get('shots') and b.get('shots'):
        aq=a.get('xg',0)/a['shots'] if a.get('known_xg')==a['shots'] else None
        bq=b.get('xg',0)/b['shots'] if b.get('known_xg')==b['shots'] else None
        c['text']+=f' 射门每90 {a["shots"]*90/a["minutes"]:.1f} 对 {b["shots"]*90/b["minutes"]:.1f}；单次射门xG {shown(aq)} 对 {shown(bq)}，可分别检查产量和质量。'
    c['limitation']='同一截止与过滤下比较；对手强弱、主客场与人员变化仍需分开检查，不能单凭窗口变化断定状态因果。'
    return [c]


def goalkeeper(report,team):
    cards=[]
    groups=defaultdict(list)
    for row in detail_rows(team,'goalkeepers'):groups[row['player_id']].append(row)
    for pid,rows in groups.items():
        faced=unique([e for r in rows for e in r.get('shots',[]) if e.get('outcome') in ('goal','saved','saved_to_post','on_target')])
        known=[e for e in faced if finite(e.get('xgot'))]
        distribution=unique([e for r in rows for e in r.get('distribution',[])])
        known_pass=[e for e in distribution if e.get('success') is not None]
        if not faced and not distribution:continue
        won=sum(e['success'] for e in known_pass)
        c=make(report,team,'goalkeeper',pid,named(team,pid)+'的扑救与出球需要分别判断',
               f'本人在场区间可归属的射正来球 {len(faced)} 次，xGOT有值 {len(known)} 次、合计 {shown(sum(e["xgot"] for e in known) if known else None)}，同覆盖失球 {sum(bool(e.get("goal")) for e in known)}。出球成功 {won}/{len(known_pass)} 次。',faced+distribution,
               {'shots_on_target':len(faced),'known_xgot':len(known),'xgot':sum(e['xgot'] for e in known) if known else None,'goals_same_coverage':sum(bool(e.get('goal')) for e in known),'distribution_known':len(known_pass),'distribution_completed':won},priority=25)
        c['limitation']='只比较同一已知xGOT样本，不把偏出和封堵的零值当作门将扑救；传球结果也受接应与压迫影响。'
        cards.append(c)
    return cards


def actual_lineup(report,team):
    side='home' if report['teams'][0]['id']==team['id'] else 'away'
    delta=next((r for r in report.get('lineup_changes',{}).get('teams',[]) if r['side']==side),{})
    added={person_id(p) for p in delta.get('added',[])}
    rows=[p for p in team.get('players',[]) if p['id'] in added]
    if not rows:return []
    links=[c for c in attack(team) if involved(c,added)]
    text='；'.join(f'{p["name"]}实际有效{p.get("minutes",0):.1f}分钟、推进传球{p.get("progressive",0)}次、官方整场xA {shown(p.get("xa"))}' for p in rows)
    c=make(report,team,'lineup','actual-entrants','正式首发变化在本场承担了哪些任务',text+f'。新增人员直接创造或终结 {len(links)} 次射门、{shown(total(links))} xG。这些是实际参与，不等于证明替换优于预计人选。',links,
           {'entrants':[{k:p.get(k) for k in ('id','name','minutes','progressive','xa','xa_match_count')} for p in rows],'direct_chances':len(links),'xg':total(links)},priority=45)
    c['limitation']='预计首发未实际出场的反事实无法观察；不以实际进球证明阵容选择因果。'
    return [c]


def postmatch(report,team):
    cards=[];shots=attack(team);nonpen=[c for c in shots if not c.get('penalty')]
    for goal in [c for c in shots if c.get('goal')]:
        seq=goal.get('sequence',[]);passes=[e for e in seq if e.get('event_type')==1 and e.get('success') in (True,1)]
        actors=[named(team,e.get('player_id')) for e in passes]
        text=f'{clock(goal)}，{named(team,goal.get("player_id"))}以'+('点球' if goal.get('penalty') else goal.get('method','已记录进攻'))+f'进球，xG {shown(goal.get("xg"))}。'
        if goal.get('penalty'):text+='这是点球终结；不能据此证明运动战套路已打穿防线。获点过程仅以关联事件为依据。'
        elif passes:text+=f'已关联序列内 {len(passes)} 次成功传球，参与者依序为'+ ' → '.join(actors)+'；最后传球与终结可在事件链核查。'
        else:text+='射门前没有可确认的传球链，不补画创造过程。'
        c=make(report,team,'goal',goal['event_id'],'进球来自'+('点球' if goal.get('penalty') else goal.get('method','已记录机会')),text,[*seq,goal],
               {'xg':goal.get('xg'),'passes':len(passes),'penalty':goal.get('penalty'), 'chain_complete':goal.get('chain_complete')},priority=110,assessment='已发生的进球过程')
        c['limitation']='仅解释已记录动作顺序；不由一次成功推出持续可复制，也不指定无球防守责任。'
        cards.append(c)
    known=sum(c.get('xg') is not None for c in nonpen);value=total(nonpen)
    avg=value/known if known and value is not None else None
    low=[c for c in nonpen if finite(c.get('xg')) and c['xg']<.1]
    high=[c for c in nonpen if finite(c.get('xg')) and c['xg']>=.2]
    goals=sum(bool(c.get('goal')) for c in nonpen)
    low_quality=known==len(nonpen) and bool(nonpen) and avg<.1
    efficient=low_quality and goals>=2 and goals>value
    title='低质量尝试较多，但本场终结效率高' if efficient else '进攻主要受限于射门质量' if low_quality else '需区分创造机会与把握机会'
    interpretation=(f'实际非点球进球 {goals} 个，高于本场 {shown(value)} xG；机会质量与本场兑现率应分开评价，不能直接外推高转化率。' if efficient else '多数进攻没有转化为高质量射门，不能仅归结为射手运气差。' if low_quality else '高价值未进机会与低价值尝试分开核查，最终比分不能代替过程。')
    c=make(report,team,'finishing','nonpenalty',title,
           f'非点球 {len(nonpen)} 射、{shown(value)} xG（覆盖 {known}/{len(nonpen)}），已知单次均值 {shown(avg)}；低于0.10 xG的射门 {len(low)} 次，至少0.20 xG {len(high)} 次。'+interpretation,nonpen,
           {'shots':len(nonpen),'known_xg':known,'xg':value,'mean_xg':avg,'low_quality':len(low),'high_quality':len(high)},priority=95,assessment='高转化率的持续性待核查' if efficient else '机会质量风险' if low_quality else '创造与终结')
    c['counter_events']=high if low_quality else low;c['limitation']='0.10/0.20是本报告显示阈值，不是供应商“大机会”标签；单场不评价球员长期能力。'
    cards.append(c)
    return cards


def attach(report,source,legacy):
    cards=[]
    if report.get('phase')!='postmatch':
        for i,team in enumerate(source['teams']):
            for c in legacy:
                if c.get('rule',{}).get('team_id')==team['id'] and c.get('rule',{}).get('kind')=='lineup_involvement':cards.append(lineup_paths(report,team,c))
            cards.extend(matchups(report,team,source['teams'][1-i],i))
    for team in report['teams']:
        if report.get('phase')=='postmatch':
            cards.extend(postmatch(report,team));cards.extend(actual_lineup(report,team))
        for fn in (progression,transitions,setpieces,pairs,intervals,creators,recent,goalkeeper):cards.extend(fn(report,team))
    cards.sort(key=lambda c:c.get('priority',0),reverse=True)
    report['tactical_analysis']={'version':VERSION,'phase':report['phase'],'findings':cards,
                                'method':'固定证据规则；数字来自同版快照，因果与无球责任不自动推定。'}
    # Core synthetic test/old snapshots lacking extension layers remain compatible.
    if cards:
        if report.get('phase')=='postmatch':
            # Keep both teams and lineup verification visible even in high-scoring games.
            primary=[]
            goal=next((c for c in cards if c.get('assessment')=='已发生的进球过程'),None)
            if goal:primary.append(goal)
            for team in report['teams']:
                for topic in ('finishing','lineup'):
                    c=next((c for c in cards if c['rule']['team_id']==team['id'] and c['topic']==topic and c.get('assessment')!='已发生的进球过程'),None)
                    if c and len(primary)<5:primary.append(c)
            for c in cards:
                if len(primary)>=5:break
                if c not in primary:primary.append(c)
            report['hypotheses']=primary
        else:
            primary=[]
            for team in report['teams']:
                for topic in ('lineup','matchup'):
                    c=next((c for c in cards if c['rule']['team_id']==team['id'] and c['topic']==topic),None)
                    if c:primary.append(c)
            for c in cards:
                if len(primary)>=5:break
                if c not in primary:primary.append(c)
            report['hypotheses']=primary
