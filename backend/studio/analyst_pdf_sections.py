"""Expanded analyst spreads, built only from the frozen report's evidence."""
from collections import defaultdict,Counter
from math import isfinite


def known_sum(values):
    xs=[x for x in values if isinstance(x,(float,int)) and not isinstance(x,bool) and isfinite(x)]
    return sum(xs) if xs else None


def formations(team):
    grouped={}
    for match in team.get('report_extensions',{}).get('matches',[]):
        for x in match.get('intervals',[]):
            key=x.get('formation') or '未知阵型';a=grouped.setdefault(key,dict(formation=key,minutes=0,matches=set(),shots=0,conceded=0,xg=[],xga=[],known_xg=0,known_xga=0))
            a['minutes']+=x['minutes'];a['matches'].add(match['match_id'])
            for k in ('shots','conceded','known_xg','known_xga'):a[k]+=x.get(k,0)
            a['xg'].append(x.get('xg'));a['xga'].append(x.get('xga'))
    return [dict(a,matches=len(a['matches']),xg=known_sum(a['xg']),xga=known_sum(a['xga'])) for a in grouped.values()]


def official_rate(team,numerator,denominator):
    rows=[m['official'] for m in team.get('analyst_matches',[]) if m.get('official',{}).get(numerator) is not None and m.get('official',{}).get(denominator) is not None]
    n=sum(x[numerator] for x in rows);d=sum(x[denominator] for x in rows)
    return (n/d*100 if d>0 else None),len(rows)


def player_count(team,player,key):
    # Roles provide explicit zeros. Sparse provider fields remain unknown when
    # absent, rather than interpreting an omitted stat as observed zero.
    rolekeys={'assists':'assists','goals':'goals','key_passes':'key_passes','tackles':'official_total_tackles','interceptions':'official_interceptions'}
    if key in rolekeys:
        values=[p.get(rolekeys[key]) for m in team.get('analyst_detail',{}).get('matches',[]) for p in m.get('roles',[]) if p.get('player_id')==player['id']]
    elif key=='shots':return player.get('shots'),len(player.get('sample_ids',[]))
    elif key=='progressive':return player.get('progressive'),len(player.get('sample_ids',[]))
    else:values=[m.get('stats',{}).get(key) for m in player.get('official_by_match',[])]
    return known_sum(values),sum(v is not None for v in values)

def player_scope(key):
    return '所选时段；射门不含点球' if key=='shots' else '所选时段' if key=='progressive' else '整场；未按比分／人数拆分'


def append_sections(value,story,p,table,card,grid,page,small_pitch,font,palette,num):
    from reportlab.graphics.shapes import Drawing,Rect,Line,Circle,String
    from reportlab.lib import colors
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from .analyst_pdf_detail import grouped_xa,legacy_on_target_shots
    r=value['report'];teams=r['teams'];context=r.get('competition_context') or {};names={x['id']:x['name'] for t in teams for x in t['players']}
    def metric(t,key):return next((x for x in t.get('official_metrics',[]) if x['key']==key),{})
    def rate(v,minutes):return v*90/minutes if v is not None and minutes and minutes>0 else None
    def bars(labels,values,color,maximum=None,unit=''):
        maximum=maximum or max([v for v in values if v is not None]+[1]);maximum=max(maximum,.001)
        d=Drawing(230,35+len(labels)*22);height=d.height
        for i,(label,v) in enumerate(zip(labels,values)):
            y=height-20-i*22;label=str(label)
            while stringWidth(label,font,7.5)>95:label=label[:-2]+'…'
            d.add(String(0,y,label,fontName=font,fontSize=7.5))
            if v is None:d.add(String(102,y,'暂无数据',fontName=font,fontSize=7.5));continue
            d.add(Rect(101,y-2,max(0,v)/maximum*100,8,fillColor=colors.HexColor(color),strokeColor=None));d.add(String(205,y,num(v),fontName=font,fontSize=7.5))
        d.add(String(0,4,unit,fontName=font,fontSize=7.5));return d
    def chart(t,labels,vals,maxi=None,unit=''):return bars(labels,vals,palette[teams.index(t)],maxi,unit)
    def details(t,key):return [e for m in t.get('analyst_detail',{}).get('matches',[]) for e in m.get(key,[])]
    def match_label(team,event):
        match=next((x for x in team.get('samples',[]) if x['id']==event.get('match_id')),None)
        return str(match.get('kickoff_at',''))[:10]+' vs '+str(match.get('opponent','')) if match else str(event.get('match_id',''))[:8]
    def paths(events,side,title):
        es=[e for e in events if all(e.get(k) is not None and 0<=e[k]<=100 for k in ('x','y','end_x','end_y'))]
        d=small_pitch();color=colors.HexColor(palette[side]);d=d.copy();scale=230/495
        # The supplied pitch already carries a transform. Add source positions in
        # its original coordinate frame so all marks share the same transform.
        for e in es:
            x,y,ex,ey=[e[k] for k in ('x','y','end_x','end_y')]
            if side:x,y,ex,ey=100-x,100-y,100-ex,100-ey
            ok=e.get('outcome',e.get('success')) in (1,True)
            d.add(Line(22+x*4.5,25+y*2.88,22+ex*4.5,25+ey*2.88,strokeColor=color,strokeWidth=1,strokeDashArray=None if ok else [3,2]));d.add(Circle(22+ex*4.5,25+ey*2.88,3,fillColor=color if ok else colors.white,strokeColor=color))
        return card(title,d,p(f'记录{len(events)}条，起终点完整{len(es)}条。实线成功，虚线为未成功或未知；主队向右、客队向左。仅连接同一动作的已知起终点。','small'))

    page('近期状态 · 逐场走势与比赛背景')
    cards=[]
    for side,t in enumerate(teams):
        hist=t.get('report_extensions',{}).get('history',{});ms=hist.get('matches',[])[:10]
        maximum=max([x.get(k) or 0 for team in teams for x in team.get('report_extensions',{}).get('history',{}).get('matches',[]) for k in ('xg','xga')]+[1])
        labels=[str(x.get('kickoff_at',''))[:10]+' '+x.get('opponent','') for x in ms]
        cards.append(card(t['name']+' · 最近比赛与5/10场背景',chart(t,labels,[x.get('xg') for x in ms],maximum,'每场xG；按日期由近到远'),table(['窗口','场次','分钟','xG/90','xGA/90'],[[label,h.get('matches'),h.get('minutes'),h.get('xg_per90'),h.get('xga_per90')] for key,label in [('recent5','5场'),('recent10','10场')] for h in [hist.get(key,{})]],[35,28,55,56,56]),p('5/10场沿用相同截止时间、日期、主客场、对手、比分及人数筛选；5场属于10场的子集，不是独立样本。','small')))
    for t in teams:
        row=next((x for x in context.get('recent',[]) if x['team_id']==t['id']),{})
        cards.append(card(t['name']+' · 比分与赛程间隔',table(['日期／赛事','主队–客队','比分'],[[str(x['kickoff_at'])[:10]+' '+x['competition'],x['home']+'–'+x['away'],str(x['home_score'])+'–'+str(x['away_score'])] for x in row.get('matches',[])[:5]],[55,140,35]) if row.get('matches') else p('本版本未保存完整比分背景。'),p('距上一场开球'+num(row.get('hours_since_last_kickoff'))+'小时；这是开球间隔，不是净休息时间。','small')))
    grid(cards)

    page('联赛背景 · 入库比分积分与整体参照')
    cards=[];standings=context.get('standings',[])
    for chunk in [standings[:10],standings[10:]]:
        cards.append(card('按入库比分计算的积分',table(['序','球队','场','分','进–失'],[[x['rank'],x['name'],x['matches'],x['points'],str(x['gf'])+'–'+str(x['ga'])] for x in chunk],[20,110,25,25,50]) if chunk else p('没有可用积分背景。'),p('仅统计截止前已入库的本赛季联赛比分，不含扣分及缺失比赛，不冒充官方积分榜。','small')))
    for t in teams:
        rows=t.get('league_reference',[]);labels={'possessionPercentage':'控球率','totalLongBalls':'长传次数','totalCross':'传中次数','ballRecovery':'夺回球权','totalScoringAtt':'射门','aerialWon':'争顶成功','duelWon':'对抗成功'}
        xs=[x for x in rows if x['key'] in labels]
        cards.append(card(t['name']+' · 联赛参照',table(['指标','联赛场均','本队分位'],[[labels[x['key']],x.get('mean'),x.get('value_percentile')] for x in xs],[100,65,65]),p('联赛为赛季场均，本队为选中近期样本；分位越高只表示数值更大，不自动表示表现更好。','small')))
    grid(cards)

    maximum_matches=max(len(t.get('analyst_detail',{}).get('matches',[])) for t in teams)
    for start in range(0,maximum_matches,2):
        page('人员在场 · 最近比赛的出场时间带 '+str(start+1)+'–'+str(min(start+2,maximum_matches)));cards=[]
        for side,t in enumerate(teams):
            for match in t.get('analyst_detail',{}).get('matches',[])[start:start+2]:
                rows=match.get('on_pitch',[]);height=45+len(rows)*13;d=Drawing(230,height);maximum=max([x['end_ms']/60000 for x in rows]+[90]);left=95;plot=128
                for minute in [0,45,90]:
                    x=left+plot*minute/maximum;d.add(Line(x,20,x,height-15,strokeColor=colors.HexColor('#dce2e9')));d.add(String(x-4,6,str(minute),fontName=font,fontSize=7))
                for i,row in enumerate(rows):
                    y=height-24-i*13;name=str(row.get('name') or names.get(row.get('player_id'),'未匹配'))
                    while stringWidth(name,font,7)>90:name=name[:-2]+'…'
                    d.add(String(0,y,name,fontName=font,fontSize=7));a=row['start_ms']/60000;b=row['end_ms']/60000;d.add(Rect(left+plot*a/maximum,y-2,plot*(b-a)/maximum,6,fillColor=colors.HexColor(palette[side]),strokeColor=None))
                sample=next((x for x in t['samples'] if x['id']==match['match_id']),{})
                title=t['name']+' · '+str(sample.get('kickoff_at',''))[:10]+' '+sample.get('opponent','')
                cards.append(card(title,d,p('横轴为累计比赛分钟，包含补时；色带表示在场，空白表示不在场。姓名较长时用缩写，全名见球员表。','small')))
        grid(cards)

    page('两队总览 · 攻防产量与终结效率')
    cards=[]
    for t in teams:
        a,b=t['attack'],t['defence'];n=len(t['samples']);minutes=t.get('minutes',0)
        labels=['射门/90','xG/90','被射门/90','xGA/90'];vals=[rate(a['shots'],minutes),rate(a['xg'],minutes),rate(b['shots'],minutes),rate(b['xg'],minutes)]
        cards.append(card(t['name']+' · 选区机会产量',table(['指标','值'],list(zip(labels,vals)),[145,85]),p(f'统计{n}场、{num(minutes)}分钟；xG有值{a["known_xg"]}/{a["shots"]}，对手{b["known_xg"]}/{b["shots"]}。','small')))
        shots=[e for e in details(t,'shots') if e.get('direction')=='attack'];inside=[e for e in shots if e.get('x') is not None and e['x']>=83 and e.get('y') is not None and 21.1<=e['y']<=78.9]
        on=[e for e in shots if e.get('outcome') in ('goal','on_target','saved','saved_to_post')]
        cards.append(card(t['name']+' · 终结表现',table(['指标','值'],[['平均每射xG',a['xg']/a['known_xg'] if a['known_xg'] and a['xg'] is not None else None],['进球/射门%',a['goals']/a['shots']*100 if a['shots'] else None],['射正/射门%',len(on)/len(shots)*100 if shots else None],['禁区内/外（有坐标）',str(len(inside))+' / '+str(sum(e.get('x') is not None and e.get('y') is not None for e in shots)-len(inside))]],[145,85]),p('禁区按标准事件坐标判断；点球是否纳入沿用报告筛选。高转化率与创造高质量机会分开评价。','small')))
    for t in teams:
        ps,coverage=official_rate(t,'accuratePass','totalPass');asum=metric(t,'aerialWon').get('mean');lsum=metric(t,'aerialLost').get('mean')
        cards.append(card(t['name']+' · 官方整场表现',table(['指标','场均／成功率','有值场'],[[label,metric(t,key).get('mean'),metric(t,key).get('coverage')] for key,label in [('possessionPercentage','控球率%'),('totalPass','传球'),('touchesInOppBox','禁区触球'),('aerialWon','争顶成功'),('aerialLost','争顶失败')]]+[['传球成功率%',ps,coverage]],[95,80,55]),p('官方整场值不按11对11或比分时段拆分。传球率只用分子、分母同时有值的比赛计算。','small')))
    grid(cards)

    page('阵型与换人 · 使用比例和机会收支')
    cards=[]
    for t in teams:
        fs=formations(t);total=sum(x['minutes'] for x in fs)
        cards.append(card(t['name']+' · 阵型使用比例',chart(t,[x['formation'] for x in fs],[x['minutes']/total*100 if total else None for x in fs],100,'%：选中区间中的分钟占比')))
        cards.append(card(t['name']+' · 阵型下的机会',table(['阵型','分钟','xG/90','xGA/90','覆盖'],[[x['formation'],x['minutes'],rate(x['xg'],x['minutes']),rate(x['xga'],x['minutes']),str(x['known_xg'])+'/'+str(x['shots'])] for x in fs],[40,50,50,50,40]),p('相同阵型可能对应不同球员、对手、比分；这些差异不能直接解释为阵型或换人的效果。','small')))
        xs=[x for x in r.get('tactical_analysis',{}).get('findings',[]) if x.get('topic')=='substitution' and x.get('rule',{}).get('team_id')==t['id']][:2]
        cards.append(card(t['name']+' · 人员变化前后',*[p(x['text'],'small') for x in xs] or [p('没有可比的人员变化区间。')]))
    grid(cards)

    page('区域基准 · 球权得失与创造机会')
    baseline=context.get('region_baseline',{});cards=[];base=baseline.get('mean_per90',{})
    for key,label in [('recoveries','夺回球权'),('losses','被断球及失误'),('creator_xg','最后一传关联xG')]:
        shared_max=max([abs(rate(v,x['minutes'])-base[key][z]) for x in baseline.get('teams',[]) if x['team_id'] in {t['id'] for t in teams} and x.get('minutes') and base.get(key) for z,v in enumerate(x[key]) if v is not None and base[key][z] is not None]+[.01])
        for t in teams:
            own=next((x for x in baseline.get('teams',[]) if x['team_id']==t['id']),None);means=base.get(key)
            if not own or not means:cards.append(card(t['name']+' · '+label,p('本版本缺少同条件联赛区域基准，未用整体联赛指标代替。')));continue
            values=[rate(v,own['minutes']) for v in own[key]];diff=[values[i]-means[i] if values[i] is not None and means[i] is not None else None for i in range(15)];mx=shared_max;d=Drawing(230,145)
            for z,v in enumerate(diff):
                x=16+z//5*66;y=22+z%5*20;a=abs(v)/mx if v is not None else 0;color=colors.Color(1,1-a*.5,1-a*.5) if v is not None and v>=0 else colors.Color(1-a*.5,1-a*.4,1)
                d.add(Rect(x,y,66,20,fillColor=color,strokeColor=colors.white));d.add(String(x+20,y+6,format(v,'+.2f' if key=='creator_xg' else '+.1f') if v is not None else '未提供',fontName=font,fontSize=8))
            unit='xG' if key=='creator_xg' else '次数'
            cover=('；已知xG/关联射门 '+str(sum(own.get('creator_known_xg',[])))+'/'+str(sum(own.get('creator_shots',[])))+'；每格只比较完整xG覆盖的球队') if key=='creator_xg' else ''
            cells=baseline.get('mean_teams_per_cell',{}).get(key,[])
            if cells:cover+='；各格有效球队 '+str(min(cells))+'–'+str(max(cells))+'/'+str(baseline.get('team_count'))
            cards.append(card(t['name']+' · '+label,d,p(f'格内=本队每90分钟{unit}减联赛各队均值。正数偏多，负数偏少。联赛{baseline.get("team_count")}队，同近期窗口及筛选；本队{own["matches"]}场、{num(own["minutes"])}分钟'+cover+'。','small')))
    grid(cards);story.append(p('横向后场→前场；纵向自下而上为右边路→左边路。被断球及失误仅指事件50/51，不代表所有球权丢失。数值偏多不自动代表优点，夺回多也可能因为长期防守。','small'))

    page('传球结构 · 向前、横向、回传与网络')
    cards=[]
    for side,t in enumerate(teams):
        # Geometry categories are explicit; they are not Wyscout's proprietary bins.
        located=[]
        for m in t.get('analyst_matches',[]):
            for z in m.get('zones',[]):
                located.extend(z.get('actions',{}).get('passes',{}).get('events',[]))
        directions=[m.get('pass_directions') for m in t.get('analyst_detail',{}).get('matches',[]) if m.get('pass_directions')]
        direction_keys=[('forward','向前'),('backward','回传'),('lateral','横向')];total=sum(x['located'] for x in directions);allpasses=sum(x['passes'] for x in directions)
        rows=[]
        for k,label in direction_keys:
            count=sum(x['groups'][k]['attempts'] for x in directions);known=sum(x['groups'][k]['known_outcomes'] for x in directions);completed=sum(x['groups'][k]['completed'] for x in directions)
            rows.append([label,count,count/total*100 if total else None,completed/known*100 if known else None])
        cards.append(card(t['name']+' · 传球方向结构',chart(t,[x[0] for x in rows],[x[2] for x in rows],100,'占已定位传球的比例%'),table(['方向','次数','占比%','成功率%'],rows,[45,55,65,65]),p(f'起终点完整{total}/{allpasses}。按标准场长宽换算后以45度角分向前、回传、横向；属于透明几何分类。成功率只用结果已知的传球。','small')))
        positions={x['id']:x.get('average_action_position') for x in t['players']};eligible_edges=[x for x in t.get('passing_network',[]) if x['count']>=3 and positions.get(x['from_id']) and positions.get(x['to_id'])];edges=sorted(eligible_edges,key=lambda x:(-x['count'],x['from_id'],x['to_id']))[:15];d=small_pitch();color=colors.HexColor(palette[side]);maximum=max([x['count'] for team in teams for x in team.get('passing_network',[])]+[1]);used=set()
        def xy(pos):
            x,y=pos['x'],pos['y']
            if side:x,y=100-x,100-y
            return 22+4.5*x,25+2.88*y
        for e in edges:d.add(Line(*xy(positions[e['from_id']]),*xy(positions[e['to_id']]),strokeColor=color,strokeWidth=.8+4*e['count']/maximum));used.update([e['from_id'],e['to_id']])
        mapping=sorted(used)
        for i,pid in enumerate(mapping):
            x,y=xy(positions[pid]);d.add(Circle(x,y,8,fillColor=color,strokeColor=colors.white));d.add(String(x-4,y-4,str(i+1),fontName=font,fontSize=14,fillColor=colors.white))
        cards.append(card(t['name']+' · 传接网络',d,p('；'.join(str(i+1)+' '+names.get(pid,pid) for i,pid in enumerate(mapping)),'small'),p(f'边为所选{len(t.get("samples",[]))}场整场成功互传累计，未按比分／人数拆分。≥3次的{len(eligible_edges)}条关系中画出最频繁{len(edges)}条；线粗表示次数。节点为选区球上动作均位，二者统计范围不同，不代表同一时段阵型。完整关系见附录。','small')))
    grid(cards)

    for kind,title in [('cross','传中'),('through','直塞'),('门球','门球')]:
        page(title+' · 本队进攻与对手进攻路线');cards=[]
        for side,t in enumerate(teams):
            for direction,label in [('attack','本队'),('defence','对手')]:
                events=[e for m in t.get('report_extensions',{}).get('matches',[]) for e in m.get('restarts' if kind=='门球' else 'passes',[]) if e.get('kind')==kind and e.get('direction')==direction]
                cards.append(paths(events,side if direction=='attack' else 1-side,t['name']+' · '+label+title))
        grid(cards)

    page('定位球 · 第一次接触与后续射门');cards=[]
    for t in teams:
        ext=t.get('report_extensions',{}).get('matches',[])
        for direction,label in [('attack','进攻'),('defence','防守')]:
            rows=[e for m in ext for e in m.get('restarts',[]) if e.get('direction')==direction and e.get('kind')!='门球']
            cards.append(card(t['name']+' · '+label+'定位球',table(['类型','次数','接触可识别','后续射门'],[[kind,len(xs),sum(bool(x.get('first_contact') or x.get('first_recorded_contact')) for x in xs),sum(len(x.get('shots',[])) for x in xs)] for kind in ('角球','任意球','界外球') for xs in [[x for x in rows if x['kind']==kind]]],[55,40,75,60]),p('“可识别”只表示有记录的第一触球，不表示争顶获胜或控制第二点。','small')))
        seconds=details(t,'second_phases')
        cards.append(card(t['name']+' · 二次进攻',table(['方向','射门','有xG','xG'],[[label,sum(x.get('shots',0) for x in xs),sum(x.get('known_xg',0) for x in xs),known_sum(x.get('xg') for x in xs)] for direct,label in [('attack','本队'),('defence','对手')] for xs in [[x for x in seconds if x.get('direction')==direct]]],[55,45,55,75]),p('来源定义的后续阶段，不与上面同回合的射门再次相加。第二落点控制仍需录像核查。','small')))
    grid(cards)

    page('门将 · 扑救质量与出球');cards=[]
    for side,t in enumerate(teams):
        keepers=defaultdict(lambda:{'name':'','shots':[],'passes':[]})
        for g in details(t,'goalkeepers'):
            k=keepers[g['player_id']];k['name']=g['name'];k['shots']+=g.get('shots',[]);k['passes']+=g.get('distribution',[])
        for k in keepers.values():
            on=[x for x in k['shots'] if x.get('outcome') in ('goal','on_target','saved','saved_to_post')];known=[x for x in on if x.get('xgot') is not None]
            cards.append(card(t['name']+' · '+k['name'],table(['指标','值'],[['射正xGOT',known_sum(x['xgot'] for x in known)],['同批射门进球',sum(bool(x.get('goal')) for x in known) if known else None],['xGOT有值/射正',str(len(known))+'/'+str(len(on))]],[150,80]),p('射偏、封堵不算射正；差值只辅助分析，不直接当作门将能力评分。','small')))
            cards.append(paths(k['passes'],side,k['name']+' · 出球路线'))
    grid(cards)

    page('对抗 · 球员与对手的实际配对');cards=[]
    for t in teams:
        rows=[x for m in t.get('report_extensions',{}).get('matches',[]) for x in m.get('contest_matrix',[])]
        for kind,label in [('aerial','争顶'),('take_on','过人／防守动作')]:
            xs=sorted([x for x in rows if x['kind']==kind],key=lambda x:-x['contests'])[:6]
            cards.append(card(t['name']+' · '+label+'配对前6组',table(['本队/对手','次数','成功','未成功'],[[x.get('player_name','')+'/'+x.get('opponent_name',''),x['contests'],x['won'],x['lost']] for x in xs],[125,35,35,35]),p('仅已识别的配对关系；成功是本方记录结果，不以取反方式补算对手胜负。','small')))
    grid(cards)

    for group in [[('shots','射门'),('goals','进球'),('assists','助攻')],[('key_passes','关键传球'),('totalCross','传中'),('aerialWon','争顶成功')],[('progressive','推进传球'),('tackles','抢断'),('interceptions','拦截')]]:
        page('核心球员 · '+'、'.join(label for _,label in group));cards=[]
        for key,label in group:
            allrows=[]
            for t in teams:
                xs=[(x,*player_count(t,x,key)) for x in t['players']];xs=sorted([x for x in xs if x[1] is not None],key=lambda x:-x[1])[:4];allrows.append(xs)
            maximum=max([x[1] for xs in allrows for x in xs]+[1])
            for t,xs in zip(teams,allrows):cards.append(card(t['name']+' · '+label+'前4名',chart(t,[x[0]['name'] for x in xs],[x[1] for x in xs],maximum,'累计次数；两队同尺度'),p(player_scope(key)+'。'+('整场进球不可与选区射门直接计算转化率。' if key=='goals' else ''),'small'),p('；'.join(x[0]['name']+'：'+str(x[2])+'场有记录' for x in xs),'small')))
        grid(cards)

    for start in range(0,max(len(t['players']) for t in teams),14):
        page('全部球员 · 近期出场与价值贡献 '+str(start+1)+'–'+str(start+14));cards=[]
        for t in teams:
            xs=sorted(t['players'],key=lambda x:-(x.get('minutes') or 0))[start:start+14]
            cards.append(card(t['name'],table(['球员','分钟','xG','xA','xT'],[[x['name'],x['minutes'],x.get('xg'),x.get('xa'),x.get('model_xt')] for x in xs],[90,35,35,35,35]),p('xG/xT按所选事件；xA为有值比赛的整场总值，缺项不填零。每90分钟指标及数据来源见完整附录。','small')))
        grid(cards)

    page('xA分项 · 运动战与定位球创造');cards=[]
    xa=[grouped_xa(t.get('analyst_detail',{}).get('matches',[])) for t in teams]
    maxi=max([x[k] for xs in xa for x in xs for k in ('openplay','setplay') if x.get(k) is not None]+[.01])
    for key,label in [('openplay','运动战'),('setplay','定位球')]:
        for t,xs in zip(teams,xa):
            top=sorted([x for x in xs if x.get(key) is not None],key=lambda x:-x[key])[:5]
            cards.append(card(t['name']+' · '+label+'xA',chart(t,[x['name'] for x in top],[x[key] for x in top],maxi,'前5名；累计官方整场xA'),p('；'.join(x['name']+'：'+str(x['coverage'][key])+'场有值' for x in top),'small')))
    grid(cards)

    page('射门细分 · 结果、部位与场景');cards=[]
    fields=[('outcome','射门结果'),('body_part','身体部位'),('situation','进攻情境')]
    labels={'goal':'进球','off_target':'偏出','on_target':'射正未进','blocked':'封堵','head':'头球','left_foot':'左脚','right_foot':'右脚','other_body_part':'其他','regular':'常规进攻','corner':'角球','free_kick':'任意球','set_piece':'定位球','fast_break':'反击','penalty':'点球','throw_in':'界外球','unknown':'未知'}
    for key,label in fields:
        groups=[Counter(x.get(key) or 'unknown' for x in details(t,'shots') if x.get('direction')=='attack') for t in teams];maximum=max([n for g in groups for n in g.values()]+[1])
        for t,g in zip(teams,groups):cards.append(card(t['name']+' · '+label,chart(t,[labels.get(k,k) for k in g],[g[k] for k in g],maximum,'次；两队相同尺度')))
    grid(cards)

    page('纪律与越位 · 风险来自谁');cards=[]
    for t in teams:
        metrics=[('totalOffside','越位'),('totalYellowCard','黄牌'),('totalRedCard','红牌'),('fkFoulLost','犯规'),('fkFoulWon','被犯规')]
        cards.append(card(t['name']+' · 官方记录',table(['指标','有值场均','有值场'],[[label,metric(t,key).get('mean'),metric(t,key).get('coverage')] for key,label in metrics],[100,65,65]),p('供应商未给值的场次不推定为零；不按这些稀疏有值场均直接外推本场。','small')))
        for key,label in [('totalOffside','越位'),('totalYellowCard','黄牌')]:
            xs=[(x,*player_count(t,x,key)) for x in t['players']];xs=sorted([x for x in xs if x[1] is not None],key=lambda x:-x[1])[:4]
            cards.append(card(t['name']+' · '+label+'记录较多球员',table(['球员','合计','有值场'],[[x['name'],n,c] for x,n,c in xs],[130,50,50])))
    grid(cards)

    page('区域效率 · 过人成功率与被犯规位置');cards=[]
    for side,t in enumerate(teams):
        es=[e for e in details(t,'action_zones') if e.get('team_id')==t['id']];dribbles=[e for e in es if e.get('kind')=='dribble'];won=[e for e in es if e.get('kind')=='foul' and e.get('foul_role')=='won']
        for items,label,success in [(dribbles,'过人成功率',True),(won,'被犯规区域',False)]:
            d=Drawing(230,130);groups=defaultdict(list)
            for e in items:
                if e.get('x') is not None and e.get('y') is not None and 0<=e['x']<=100 and 0<=e['y']<=100:groups[min(2,int(e['x']/100*3))*5+min(4,int(e['y']/100*5))].append(e)
            for z in range(15):
                records=groups[z];known=[e for e in records if e.get('success') is not None];value=sum(e['success'] for e in known)/len(known)*100 if known else None
                text=(f'{value:.0f}%' if value is not None else '—') if success else str(len(records));x=15+z//5*65;y=16+z%5*21
                d.add(Rect(x,y,65,21,fillColor=colors.HexColor('#f2f4f7'),strokeColor=colors.white));d.add(String(x+17,y+6,text,fontName=font,fontSize=8))
            cards.append(card(t['name']+' · '+label,d,p('横向后场→前场；纵向自下而上右路→左路。'+('格内为结果已知动作的成功率；无样本用横线，不填0%。总有结果'+str(sum(e.get('success') is not None for e in dribbles))+'/'+str(len(dribbles))+'。' if success else '格内为被犯规记录次数；与实施犯规分开，不合并计数。'),'small')))
    grid(cards)

    page('推进威胁 · 高影响xT动作的实际路线');cards=[]
    for side,t in enumerate(teams):
        actions=[e for e in details(t,'xt_actions') if e.get('xt_added') is not None]
        for positive,label in [(True,'增加威胁'),(False,'减少威胁')]:
            xs=sorted([x for x in actions if (x['xt_added']>0 if positive else x['xt_added']<0)],key=lambda x:-abs(x['xt_added']))[:15]
            item=paths(xs,side,t['name']+' · '+label+'绝对值前15条');item.append(p('展示'+str(len(xs))+'条高影响动作，不冒充全部动作。未记录的带球或传球不补画。','small'));cards.append(item)
    grid(cards)

    story.append(p('门框Y/Z仅保留在技术证据附录：物理坐标换算尚未核实，暂不据此解释射门打向球门的具体位置。','small'))

    # Every shot remains discoverable; full trajectories are shown for all goals
    # and the explicitly selected high-value chances, not just two examples.
    allshots=[(i,x) for i,t in enumerate(teams) for x in t['chains'] if x.get('direction')=='attack']
    important=[(i,x) for i,x in allshots if x.get('goal') or x.get('xg') is not None and x['xg']>=.20]
    for start in range(0,len(important),4):
        page('机会链 · 所有进球与xG≥0.20的机会 '+str(start+1)+'–'+str(min(start+4,len(important))));cards=[]
        for side,x in important[start:start+4]:
            seq=[e for e in x.get('sequence',[]) if e.get('event_type') in (1,2,13,14,15,16)];actors=[names.get(e.get('player_id'),'未匹配') for e in seq if e.get('event_type') in (1,2)]
            cards.append(card(teams[side]['name']+f" · {x['minute']}:{x['second']:02} "+names.get(x.get('player_id'),''),p(match_label(teams[side],x),'small'),small_pitch(sequence=seq,team_id=teams[side]['id'],side=side),p(x['method']+' · xG '+num(x.get('xg'))+(' · 进球' if x.get('goal') else ''),'small'),p('记录中的传球人：'+' → '.join(actors[-5:]) if actors else '射门前没有可确认的传球记录。','small'),p('仅画已有传球与射门起终点；阈值是报告选择条件，不是供应商“大机会”标签。','small')))
        grid(cards)
    shot_count=max(len([x for i,x in allshots if i==side]) for side in (0,1))
    starts=list(range(0,shot_count,14))
    if len(starts)>1 and shot_count-starts[-1]<5:starts.pop()
    for index,start in enumerate(starts):
        end=starts[index+1] if index+1<len(starts) else shot_count
        page('全部射门索引 · 第'+str(start+1)+'–'+str(end)+'次');cards=[]
        for side,t in enumerate(teams):
            xs=[x for i,x in allshots if i==side][start:end]
            rows=[[match_label(t,x)+f" / {'上半场' if x['period']==1 else '下半场'} {x['minute']}:{x['second']:02}",names.get(x.get('player_id'),''),x.get('xg'),'进' if x.get('goal') else '未进'] for x in xs]
            cards.append(card(t['name'],table(['日期/对手/时间','球员','xG','结果'],rows,[82,78,40,30]),p('完整比赛ID、事件ID、原始动作和版本均保留在证据附件。','small')))
        grid(cards)

    page('双人组合 · 共同在场与分开时的机会');cards=[]
    pairlabels={'both':'共同在场','only_a':'仅A在场','only_b':'仅B在场','neither':'均不在场'}
    maximum=max([rate(x.get(k),x.get('minutes')) or 0 for t in teams for x in t.get('pairs',[]) for k in ('xg','xga')]+[.1])
    for t in teams:
        ps=t.get('pairs',[]);selection=t.get('pair_selection',{}).get('names',[])
        for key,label in [('xg','本队创造'),('xga','对手创造')]:
            cards.append(card(t['name']+' · '+label+'xG/90',p('A / B：'+' / '.join(selection),'small') if selection else p('组合尚未选定。','small'),chart(t,[pairlabels.get(x['group'],x['group'])+('*' if x.get('small_sample') else '') for x in ps],[rate(x.get(key),x.get('minutes')) for x in ps],maximum,'* 少于3场或180分钟；零分钟不画数值')))
        cards.append(card(t['name']+' · 组合样本构成',table(['组别','场/分钟','对手'],[[pairlabels.get(x['group'],x['group']),str(x['matches'])+' / '+num(x['minutes']),'、'.join(x.get('opponents',[]))] for x in ps],[60,60,110]),p('各组可能面对不同对手和比分；不是随机实验，不能据此认定组合一定更好或更差。','small')))
    grid(cards)

    page('组合与时段 · 样本是否足够');cards=[]
    for t in teams:
        pairs=t.get('pairs',[])
        labels={'both':'共同在场','only_a':'仅A在场','only_b':'仅B在场','neither':'均不在场'}
        selection=t.get('pair_selection',{}).get('names',[])
        cards.append(card(t['name']+' · 双人组合',p('A / B：'+' / '.join(selection),'small') if selection else p('球员选择见报告筛选条件。','small'),table(['状态','分钟','xG','xGA'],[[labels.get(x['group'],x['group']),x['minutes'],x.get('xg'),x.get('xga')] for x in pairs],[80,50,50,50]) if pairs else p('本报告未选定两名球员，不能生成共同在场／仅一人在场／均不在场四组比较。不是没有数据，也不填假零。'),p('少于3场或180分钟标小样本；组合差异不直接解释为人员因果。','small')))
        seg=[x for m in t.get('report_extensions',{}).get('matches',[]) for x in m.get('segments',[])]
        grouped=defaultdict(list)
        for x in seg:grouped[x.get('label','未标注')].append(x)
        cards.append(card(t['name']+' · 15分钟过程',table(['时段','xG','xGA','分钟'],[[k,known_sum(x.get('xg') for x in xs),known_sum(x.get('xga') for x in xs),sum(x['minutes'] for x in xs)] for k,xs in grouped.items()],[60,55,55,60]),p('保留补时所属半场；不同时间段的实际分钟不相等。','small')))
    grid(cards)
