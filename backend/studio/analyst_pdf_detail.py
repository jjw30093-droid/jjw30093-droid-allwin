"""Snapshot-only visual appendix for analyst-detail.v1 reports.

Coordinates are recorded attacking-frame positions. No source calls, model
execution, reconstructed off-ball positions, or missing-value imputation occur.
"""
from collections import defaultdict, Counter
from math import isfinite


def _spacer():
    from reportlab.platypus import Spacer
    return Spacer(1,12)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def known_sum(values):
    values=list(values);known=[v for v in values if finite(v)]
    return sum(known) if known else None


def per90(value, minutes):
    return value*90/minutes if finite(value) and finite(minutes) and minutes>0 else None


def grouped_xa(matches):
    result={}
    for match in matches:
        for p in match.get('xa',[]):
            key=p.get('player_id');row=result.setdefault(key,{'name':p.get('name') or key,'player_id':key,'total':[],'openplay':[],'setplay':[]})
            for k in ('total','openplay','setplay'):row[k].append(p.get(k))
    return [dict(player_id=key,name=row['name'],**{k:known_sum(row[k]) for k in ('total','openplay','setplay')},
                 coverage={k:sum(finite(v) for v in row[k]) for k in ('total','openplay','setplay')}) for key,row in result.items()]


def event_ref(e):
    period=e.get('period');minute=e.get('minute');second=e.get('second')
    base={1:45,2:90,3:105,4:120}.get(period)
    clock=f'{base}+{minute-base}' if base and isinstance(minute,(int,float)) and minute>base else str(minute if minute is not None else '?')
    return f"阶段{period or '?'} {clock}:{int(second or 0):02} · {e.get('match_id','?')} / {e.get('event_id','?')}"


def finding_evidence(finding,text):
    sample=finding.get('sample') or {}
    if sample:
        minutes=sample.get('minutes')
        text('样本：'+str(sample.get('matches','未提供'))+' 场；'+(f'{minutes:.1f}' if finite(minutes) else '未提供')+' 分钟。','small')
    for label,keys in [('支持过程',('support_events','events')),('反驳过程',('counter_events',)),('其他路线（不等同反驳）',('alternative_events',))]:
        events=next((finding[k] for k in keys if k in finding),[]) or []
        text(f'{label}：{len(events)} 条；'+('下列最多4条，完整引用见同版 JSON。' if events else '本版没有记录该类证据。'),'small')
        for e in events[:4]:text(event_ref(e),'small')


def append_detail_sections(r,story,text,table,new,pitch,font,palette,number):
    from reportlab.graphics.shapes import Drawing,Line,Rect,Circle,String,Polygon
    from reportlab.lib import colors
    from reportlab.pdfbase.pdfmetrics import stringWidth
    teams=r['teams'];muted=colors.HexColor('#64748b');grid=colors.HexColor('#dce2e9')
    labels={'attack':'进攻','defence':'防守','drawing':'平局','leading':'领先','trailing':'落后',
            'both':'共同在场','only_a':'仅 A 在场','only_b':'仅 B 在场','neither':'均不在场',
            'cross':'传中','through':'直塞','dribble':'过人','foul':'犯规'}
    names={p['id']:p['name'] for t in teams for p in t.get('players',[])}
    details=[(t.get('analyst_detail') or {}).get('matches',[]) for t in teams]

    def shortened(value,width,size=8):
        value=str(value)
        while stringWidth(value,font,size)>width:value=value[:-2]+'…'
        return value

    def compare_chart(row_labels,series,unit,extent=None):
        """Signed, shared axes. A missing observation has no zero-length bar."""
        rowh=max(22,len(series)*10+5);height=66+max(1,len(row_labels))*rowh
        d=Drawing(495,height);left=155;width=320
        values=[v for _,vs,_ in series for v in vs if finite(v)]
        low,high=extent or (min([0,*values]),max([.01,*values]))
        if high<=low:high=low+1
        xx=lambda v:left+width*(v-low)/(high-low)
        for k in range(5):
            v=low+(high-low)*k/4;x=xx(v)
            d.add(Line(x,28,x,height-29,strokeColor=grid,strokeWidth=.5));d.add(String(x-8,15,number(round(v,3)),fontSize=7,fillColor=muted))
        d.add(Line(xx(0),28,xx(0),height-29,strokeColor=muted,strokeWidth=.8))
        for j,(name,_,color) in enumerate(series):
            x=left+j*(320/max(len(series),1));d.add(Rect(x,height-15,6,6,fillColor=colors.HexColor(color),strokeColor=None));d.add(String(x+9,height-14,shortened(name,95,7),fontName=font,fontSize=7,fillColor=muted))
        for i,label in enumerate(row_labels):
            y=height-47-i*rowh;d.add(String(0,y,str(shortened(label,145)),fontName=font,fontSize=8,fillColor=muted))
            for j,(_,vs,color) in enumerate(series):
                value=vs[i] if i<len(vs) else None;yy=y+7-j*10
                if not finite(value):d.add(String(left+3,yy,'未提供',fontName=font,fontSize=7,fillColor=muted));continue
                d.add(Rect(min(xx(0),xx(value)),yy,abs(xx(value)-xx(0)),7,fillColor=colors.HexColor(color),strokeColor=None))
        d.add(String(0,15,shortened(unit,145,7),fontName=font,fontSize=7,fillColor=muted));story.append(d);story.append(_spacer())

    def refs(events,limit=5):
        text(f'事件证据 {len(events)} 条；以下列出最多{limit}条，完整记录见同版 JSON。','small')
        for e in events[:limit]:text(event_ref(e),'small')

    def located(e):return all(finite(e.get(k)) and 0<=e[k]<=100 for k in ('x','y'))
    def point(e,side,direction='attack',end=False):
        x,y=(e.get('end_x'),e.get('end_y')) if end else (e.get('x'),e.get('y'))
        if not finite(x) or not finite(y) or not 0<=x<=100 or not 0<=y<=100:return None
        mirror=side if direction=='attack' else 1-side
        if mirror:x,y=100-x,100-y
        return 22+x*4.5,25+y*2.88

    def event_map(events,side,direction,paths=False,value_key=None):
        d=pitch([]);color=colors.HexColor(palette[side]);plotted=0;paths_known=0
        max_value=max([abs(e.get(value_key,0)) for e in events if value_key and finite(e.get(value_key))]+[.001])
        for e in events:
            a=point(e,side,direction)
            if a is None:continue
            plotted+=1;b=point(e,side,direction,True);outcome=e.get('success',e.get('outcome'))
            if paths and b is not None:
                paths_known+=1;d.add(Line(*a,*b,strokeColor=color,strokeWidth=.75,strokeDashArray=None if outcome in (True,1) else [3,2]))
                d.add(Circle(*b,2.6,fillColor=color if outcome in (True,1) else colors.white,strokeColor=color))
            radius=2.2 if not value_key else 2+5*(abs(e.get(value_key) or 0)/max_value)**.5
            d.add(Circle(*a,radius,fillColor=color if not value_key or (e.get(value_key) or 0)>=0 else colors.white,strokeColor=color,strokeWidth=.8))
        story.append(d);story.append(_spacer())
        text(f'记录{len(events)}条，起点有效{plotted}条'+(f'，起终点均有效{paths_known}条' if paths else '')+'。按报告球队归一化：主队进攻向右，客队向左；防守图保留对手进攻方向。','small')
        if paths:text('实线/实心终点为记录成功；虚线/空心终点为未成功或结果未知。只画来源已有起终点，不补画后续动作。','small')

    def match_title(team,match):
        sample=next((s for s in team.get('samples',[]) if s.get('id')==match.get('match_id')),{})
        return (str(sample.get('kickoff_at',''))[:10]+' '+str(sample.get('opponent') or match.get('away_name') or match.get('match_id',''))).strip()

    new('图表附录 · 阅读范围')
    text('本附录使用同一固定报告快照。关键结论、汇总与主要图表用于阅读；完整逐事件记录、来源回执、模型版本及所有链条保存在同版证据 JSON。图中抽样会写明范围，未抽中的记录仍计入标注的完整汇总。')
    text('阵容图仅使用来源提供的位置，球员在场时间来自记录区间；没有无球追踪位置时，不绘制假想跑位。动作 xT 是已保存模型推断，导出不触发训练或重新评分。')
    extra=[x for x in (r.get('tactical_analysis') or {}).get('findings',[]) if x.get('id') not in {h.get('id') for h in r.get('hypotheses',[])}]
    if extra:text('专项判断 · 全部优势与风险','heading')
    topics={'lineup':'阵容变化','matchup':'区域对位','progression':'出球推进','transition':'攻防转换','setpiece':'定位球','pair':'组合关系','substitution':'换人前后','creation':'机会创造','xt':'动作价值','goal':'进球过程','finishing':'终结质量','form':'近期状态'}
    grouped=defaultdict(list)
    for finding in extra:grouped[finding.get('topic','other')].append(finding)
    for topic,findings in grouped.items():
        text(topics.get(topic,topic)+f' · {len(findings)}条专项判断','heading')
        for finding in findings:
            text(str(finding.get('title','未命名判断')),'heading')
            for k in ('assessment','text','personnel','condition','limitation'):
                if finding.get(k):text(finding[k],'small' if k=='limitation' else 'body')
            finding_evidence(finding,text)

    # Whole-match official xA components retain their independent coverage.
    xa_groups=[grouped_xa(rows) for rows in details]
    xa_max=max([x[k] for rows in xa_groups for x in rows for k in ('total','openplay','setplay') if finite(x.get(k))]+[.01])
    for side,t in enumerate(teams):
        matches=details[side];ext=(t.get('report_extensions') or {}).get('matches',[])
        if xa_groups[side]:
            new(t['name']+' · xA 的运动战与定位球构成')
            rows=sorted(xa_groups[side],key=lambda p:-(p.get('total') or 0))[:8]
            compare_chart([x['name'] for x in rows],[('整场 xA',[x['total'] for x in rows],palette[side]),('运动战',[x['openplay'] for x in rows],'#4f6b82'),('定位球',[x['setplay'] for x in rows],'#a3adb8')],'官方 xA 合计',(0,xa_max))
            table(['球员','整场 / 覆盖','运动战 / 覆盖','定位球 / 覆盖'],[[x['name'],*[number(x[k])+' / '+str(x['coverage'][k])+'场' for k in ('total','openplay','setplay')]] for x in rows],[195,100,100,100])
            text('展示总 xA 前8人；两队共用尺度。缺失分项不补零，也不以总值减去已知分项来制造另一分项。来源定义及逐场收据在 JSON。','small')

        # Defending cross/through routes and goal kicks are separate from the
        # attacking cross/through appendix already rendered by the main module.
        for kind in ('cross','through','门球'):
            for direction in (('attack','defence') if kind=='门球' else ('defence',)):
                source=[e for m in ext for e in m.get('restarts' if kind=='门球' else 'passes',[]) if e.get('kind')==kind and e.get('direction')==direction]
                if not source:continue
                new(t['name']+' · '+labels[direction]+' '+labels.get(kind,kind)+'路线')
                event_map(source,side,direction,True)
                counts=Counter(e.get('taker_name') or e.get('player_name') or names.get(e.get('player_id'),'未关联') for e in source)
                table(['主罚 / 传球人','记录次数'],counts.most_common(8),[370,125])
                refs(source,2)

        second=[x for m in matches for x in m.get('second_phases',[])] or t.get('second_phases',[])
        if second:
            new(t['name']+' · 定位球二次进攻')
            rows=[]
            for direction in ('attack','defence'):
                items=[x for x in second if x.get('direction')==direction]
                rows.append((direction,sum(x.get('shots',0) for x in items),sum(x.get('known_xg',0) for x in items),known_sum(x.get('xg') for x in items)))
            maximum=max([known_sum(x.get('xg') for x in ([q for m in details[i] for q in m.get('second_phases',[])] or team.get('second_phases',[])) if x.get('direction')==d) or 0 for i,team in enumerate(teams) for d in ('attack','defence')]+[.01])
            compare_chart([labels[x[0]] for x in rows],[(t['name']+' 已记录 xG',[x[3] for x in rows],palette[side])],'二次阶段 xG',(0,maximum))
            table(['方向','射门','xG有值','已记录xG'],[[labels[d],shots,known,value] for d,shots,known,value in rows],[195,100,100,100])
            text('这是来源定义的定位球后续阶段，不等同录像确认的第二点控制；不能把前一章节同回合后续射门与此处再相加。','small')
            refs([e for x in second for e in x.get('events',[])],4)

        transitions=[x for m in matches for x in m.get('transitions',[])]
        if transitions:
            new(t['name']+' · 转换进攻与转换防守')
            for direction in ('attack','defence'):
                rows=[x for x in transitions if x.get('direction')==direction]
                if not rows:continue
                text(labels[direction]+f'：记录{len(rows)}条转换链；射门{sum(x.get("shots",0) for x in rows)}次；已知 xG '+number(known_sum(x.get('xg') for x in rows)),'heading')
                chosen=max(rows,key=lambda x:x.get('xg') or 0)
                event_map(chosen.get('events',[]),side,direction,True)
                text('绘制该方向已知 xG 最高的一条记录链。只连接每个动作已有的起终点，不连接缺失动作。全部转换链留在同版 JSON。','small')
                refs(chosen.get('events',[]),2)
                if direction=='attack' and any(x.get('direction')=='defence' for x in transitions):new(t['name']+' · 转换防守链')

        pairs=t.get('pairs',[]);states=t.get('states',[])
        for topic,rows in [('双人组合',pairs),('比分状态',states)]:
            if not rows:continue
            new(t['name']+' · '+topic+'的机会产量')
            own=[per90(x.get('xg'),x.get('minutes')) for x in rows]
            against=[per90(x.get('xga',(x.get('defence') or {}).get('xg')),x.get('minutes')) for x in rows]
            max_rate=max([per90(x.get(key,(x.get('defence') or {}).get('xg')),x.get('minutes')) or 0 for team in teams for x in team.get('pairs' if topic=='双人组合' else 'states',[]) for key in ('xg','xga')]+[.1])
            compare_chart([labels.get(x.get('group',x.get('name')),x.get('group',x.get('name'))) for x in rows],[(t['name']+' xG/90',own,palette[side]),('xGA/90',against,'#64748b')],'已知 xG / 90分钟',(0,max_rate))
            table(['分组','分钟','场次','xG/90','xGA/90'],[[labels.get(x.get('group',x.get('name')),x.get('group',x.get('name'))),x.get('minutes'),x.get('matches'),a,b] for x,a,b in zip(rows,own,against)],[155,85,85,85,85])
            text('仅按保存的有效分钟计算；无有效分钟不作零。两队共用尺度。组合少于3场或180分钟为小样本，且对手和比分构成不同，不作为因果效果。','small')

        # Role plots compare like units only; never mix xG and counts on one axis.
        for key,label in [('progressive','推进传球 / 90分钟'),('model_xt','模型 xT / 90分钟')]:
            rows=[p for p in t.get('players',[]) if (p.get('minutes') or 0)>=180 and finite((p.get('per90') or {}).get(key))]
            if not rows:continue
            rows=sorted(rows,key=lambda p:abs(p['per90'][key]),reverse=True)[:10]
            values=[(p.get('per90') or {}).get(key) for team in teams for p in team.get('players',[]) if (p.get('minutes') or 0)>=180 and finite((p.get('per90') or {}).get(key))]
            new(t['name']+' · 球员职责：'+label)
            compare_chart([p['name'] for p in rows],[(label,[p['per90'][key] for p in rows],palette[side])],label,(min([0,*values]),max([.01,*values])))
            table(['球员','角色','有效分钟',label],[[p['name'],p.get('role') or p.get('position') or '未提供',p.get('minutes'),p['per90'][key]] for p in rows],[200,105,90,100])
            text('达到180分钟的样本中，按该指标绝对值列最多10人。排名用于定位历史贡献，不代表不同职责球员可以直接互换。','small')

        role_fields=[('progressive_passes_completed_proxy','推进传球'),('key_passes','关键传球'),('penalty_area_entries_completed','进入禁区'),('shots','射门'),('official_total_tackles','抢断'),('official_interceptions','拦截')]
        role_groups=[]
        for rows in details:
            people=defaultdict(list)
            for match in rows:
                for row in match.get('roles',[]):people[row.get('player_id')].append(row)
            aggregated=[]
            for pid,records in people.items():
                minutes=known_sum(x.get('minutes_played') for x in records)
                values={}
                for key,label in role_fields:
                    covered=[x for x in records if finite(x.get(key)) and finite(x.get('minutes_played')) and x['minutes_played']>0]
                    values[key]=per90(known_sum(x[key] for x in covered),known_sum(x['minutes_played'] for x in covered))
                aggregated.append({'id':pid,'minutes':minutes,**values})
            role_groups.append(aggregated)
        if role_groups[side]:
            new(t['name']+' · 职责数据矩阵')
            rows=sorted(role_groups[side],key=lambda x:-(x['minutes'] or 0))[:14]
            d=Drawing(495,75+len(rows)*25);height=d.height;left=150;cw=345/len(role_fields)
            for j,(key,label) in enumerate(role_fields):
                maximum=max([x[key] for group in role_groups for x in group if finite(x.get(key))]+[.001])
                d.add(String(left+j*cw+3,height-18,label,fontName=font,fontSize=7))
                for i,row in enumerate(rows):
                    value=row.get(key);alpha=value/maximum if finite(value) else 0;base=colors.HexColor(palette[side]);y=height-48-i*25
                    fill=colors.Color(1-(1-base.red)*alpha,1-(1-base.green)*alpha,1-(1-base.blue)*alpha)
                    d.add(Rect(left+j*cw,y,cw-2,22,fillColor=fill,strokeColor=None))
                    d.add(String(left+j*cw+5,y+7,number(value),fontName=font,fontSize=7,fillColor=colors.white if alpha>.6 else muted))
            for i,row in enumerate(rows):d.add(String(0,height-41-i*25,shortened(names.get(row['id'],row['id']),140,7),fontName=font,fontSize=7))
            story.append(d);story.append(_spacer())
            text('各格为每90分钟；每项仅用该字段有值的比赛及对应分钟计算。每列两队统一颜色尺度，各列单位不同，不横向比颜色判断能力。按分钟列最多14人；原始职责和覆盖见 JSON。','small')

        # Source-recorded action xT, including negative values.
        xt=[e for m in matches for e in m.get('xt_actions',[]) if finite(e.get('xt_added'))]
        if xt:
            new(t['name']+' · 动作 xT：增加与损失')
            totals=defaultdict(list)
            for e in xt:totals[e.get('player_id')].append(e['xt_added'])
            ids=sorted(totals,key=lambda p:abs(sum(totals[p])),reverse=True)[:10]
            all_totals=[]
            for ms in details:
                sums=defaultdict(float)
                for m in ms:
                    for e in m.get('xt_actions',[]):
                        if finite(e.get('xt_added')):sums[e.get('player_id')]+=e['xt_added']
                all_totals+=list(sums.values())
            compare_chart([names.get(p,p or '未关联') for p in ids],[('动作净 xT',[sum(totals[p]) for p in ids],palette[side])],'已记录模型 xT',(min([0,*all_totals]),max([.001,*all_totals])))
            text(f'已记录{len(xt)}次评分；正贡献 {number(sum(max(0,e["xt_added"]) for e in xt))}，负贡献 {number(sum(min(0,e["xt_added"]) for e in xt))}，净值 {number(sum(e["xt_added"] for e in xt))}。','small')
            text('模型版本：'+'、'.join(sorted({str(e.get('model_version') or '未记录') for e in xt})),'small')
            new(t['name']+' · 高影响动作 xT 位置')
            chosen=sorted(xt,key=lambda e:abs(e['xt_added']),reverse=True)[:20]
            event_map(chosen,side,'attack',True,'xt_added')
            text(f'仅画绝对值最大的{len(chosen)}/{len(xt)}次动作。起点实心表示非负增量、空心表示负增量；大小按绝对值平方根缩放。传球终点样式仍表示动作结果。','small')
            table(['球员','时间','xT增量'],[[e.get('player_name') or names.get(e.get('player_id'),'未关联'),f"{e.get('minute')}:{int(e.get('second') or 0):02}",e['xt_added']] for e in chosen[:8]],[260,115,120])

        for kind in ('dribble','foul'):
            actions=[e for m in matches for e in m.get('action_zones',[]) if e.get('kind')==kind]
            for direction in ('attack','defence'):
                rows=[e for e in actions if e.get('direction')==direction]
                if not rows:continue
                new(t['name']+' · '+labels[direction]+' '+labels[kind]+'事件位置')
                event_map(rows,side,direction)
                counts=Counter(e.get('player_name') or names.get(e.get('player_id'),'未关联') for e in rows)
                table(['球员','事件次数'],counts.most_common(8),[370,125]);refs(rows,2)
                text('事件位置表示有球动作发生处，不能推断盯人责任；犯规是来源动作行，含犯规与被犯规双方记录，不能加总为独立犯规事件，也不判为对抗胜负。','small')

        # One explicitly identified match per team's print appendix; other saved
        # matches remain navigable in the interactive report and evidence export.
        if matches:
            selected=matches[0]
            render_presence(t,selected,side,story,text,table,new,font,palette,number,match_title)
            render_formations(t,selected,side,story,text,table,new,pitch,font,palette,number,match_title)
        if ext:
            render_segments(t,ext[0],side,story,text,new,font,palette,number,teams)
        shot_rows=[e for m in matches for e in m.get('shots',[]) if e.get('direction')=='attack']
        if shot_rows:
            render_shots(t,shot_rows,side,story,text,table,new,font,palette,number,details)

    render_goalkeepers(r,details,story,text,table,new,font,palette,number,compare_chart,event_map)
    injuries=(r.get('lineup') or {}).get('injuries')
    if injuries:
        new('阵容与伤停来源快照')
        for group in injuries:
            text(('主队' if group.get('side')=='home' else '客队')+' · '+str(group.get('observed_at') or '观察时间未提供'),'heading')
            table(['球员','原因 / 状态'],[[p.get('name') or '未关联',p.get('reason') or p.get('status') or '未提供'] for p in group.get('players',[])],[245,250])
        text('伤停独立采用报告截止日前的 VIP 快照；没有名单不代表没有伤停。','small')


def render_presence(team,match,side,story,text,table,new,font,palette,number,match_title):
    from reportlab.graphics.shapes import Drawing,Line,Rect,String
    from reportlab.lib import colors
    rows=match.get('on_pitch',[])
    if not rows:return
    grouped=defaultdict(list)
    for x in rows:
        if finite(x.get('start_ms')) and finite(x.get('end_ms')) and x['end_ms']>=x['start_ms']:grouped[(x.get('player_id'),x.get('name'))].append(x)
    if not grouped:return
    maximum=max([x['end_ms']/60000 for xs in grouped.values() for x in xs]+[1]);height=80+len(grouped)*17
    d=Drawing(495,height);color=colors.HexColor(palette[side]);left=155;width=320
    new(team['name']+' · 球员在场时间带');text(match_title(team,match),'small')
    for tick in range(5):
        x=left+width*tick/4;d.add(Line(x,30,x,height-20,strokeColor=colors.HexColor('#dce2e9'),strokeWidth=.5));d.add(String(x-8,15,number(maximum*tick/4),fontSize=7))
    for i,((pid,name),xs) in enumerate(grouped.items()):
        y=height-38-i*17;d.add(String(0,y,str(name or pid)[:25],fontName=font,fontSize=8))
        for x in xs:d.add(Rect(left+width*(x['start_ms']/60000)/maximum,y-2,width*((x['end_ms']-x['start_ms'])/60000)/maximum,8,fillColor=color,strokeColor=None))
    story.append(d);story.append(_spacer());text('累计比赛分钟，含此前补时。条形只表示已存起止区间，不把空白解释为位置或体能变化。PDF展示该队本版第一场样本；其他样本区间完整保存在 JSON。','small')


def render_formations(team,match,side,story,text,table,new,pitch,font,palette,number,match_title):
    from reportlab.graphics.shapes import Drawing,Rect,String
    from reportlab.lib import colors
    from reportlab.pdfbase.pdfmetrics import stringWidth
    forms=match.get('formations',[])
    if not forms:return
    if match.get('on_pitch'):text(team['name']+' · 阵型与记录角色','heading')
    else:new(team['name']+' · 阵型与记录角色')
    text(match_title(team,match),'small')
    for form in forms[:3]:
        text(str(form.get('formation') or '阵型未识别')+' · '+number((form.get('start_ms') or 0)/60000)+'-'+number((form.get('end_ms') or 0)/60000)+'分钟','heading')
        # Formation-place is an identifier, not a metric coordinate. Without an
        # explicit source layout, group positions instead of inventing pitch dots.
        groups=defaultdict(list)
        for p in form.get('players',[]):groups[p.get('position') or '未提供'].append(p)
        if not groups:text('该阵型没有可关联的球员角色记录。','small');continue
        height=35+max(len(v) for v in groups.values())*23;d=Drawing(495,height)
        for j,(role,players) in enumerate(groups.items()):
            width=495/len(groups);x=j*width;d.add(Rect(x+2,0,width-4,height,fillColor=colors.HexColor('#f5f7fa'),strokeColor=colors.HexColor('#dce2e9')))
            d.add(String(x+8,height-16,str(role).replace('Attacking Midfielder','前腰').replace('Defensive Midfielder','后腰').replace('Midfielder','中场').replace('Defender','后卫').replace('Goalkeeper','门将').replace('Forward','前锋').replace('Striker','中锋')[:16],fontName=font,fontSize=8,fillColor=colors.HexColor(palette[side])))
            for i,p in enumerate(players):
                label=str(p.get('name') or p.get('player_id'))+' · '+str(p.get('formation_place','?'))
                while stringWidth(label,font,7)>width-16:label=label[:-2]+'…'
                d.add(String(x+8,height-36-i*23,label,fontName=font,fontSize=7))
        story.append(d);story.append(_spacer())
    text(f'本页最多3段／共{len(forms)}段。分栏按记录角色组织，数字为 Opta 比赛名单槽位编号；不代表该阵型区间内每个人的动态位置，也不是无球位置。完整阵型与人员列表见 JSON。','small')


def render_segments(team,match,side,story,text,new,font,palette,number,teams):
    from reportlab.graphics.shapes import Drawing,Line,Rect,String
    from reportlab.lib import colors
    rows=match.get('segments',[])
    if not rows:return
    new(team['name']+' · 分段指标图');text('比赛 '+match['match_id']+'；PDF绘制该队第一场样本，各历史场完整分段见前文表格与 JSON。','small')
    d=Drawing(495,480);metrics=[('xg','已记录 xG'),('pass','传球成功率 %'),('long','≥30米传球占比 %'),('duel','已配对动作成功率 %')]
    max_xg=max([x.get('xg') or 0 for t in teams for m in (t.get('report_extensions') or {}).get('matches',[]) for x in m.get('segments',[])]+[1])
    for j,(key,label) in enumerate(metrics):
        ox=(j%2)*250+27;oy=260 if j<2 else 30;width=205;height=160;maximum=max_xg if key=='xg' else 100
        d.add(String(ox,oy+height+24,label,fontName=font,fontSize=9))
        for tick in range(3):
            y=oy+height*tick/2;d.add(Line(ox,y,ox+width,y,strokeColor=colors.HexColor('#dce2e9')));d.add(String(ox-24,y-2,number(maximum*tick/2),fontSize=6))
        for i,row in enumerate(rows):
            value=row.get('xg') if key=='xg' else per90(row.get({'pass':'completed_passes','long':'long_passes','duel':'duels_won'}[key]),row.get('duels' if key=='duel' else 'passes'))
            if key!='xg' and value is not None:value=value/90*100
            x=ox+(i+.15)*width/len(rows)
            if value is not None:d.add(Rect(x,oy,width/len(rows)*.55,height*max(0,value)/maximum,fillColor=colors.HexColor(palette[side]),strokeColor=None))
            else:d.add(String(x,oy+4,'?',fontSize=8))
            d.add(String(x,oy-12,str(i+1),fontSize=7))
    story.append(d);story.append(_spacer())
    text('分段编号：'+'；'.join(str(i+1)+' '+str(x.get('label')) for i,x in enumerate(rows)),'small')
    text('百分比图共用0-100尺度，xG在两队之间共用尺度；缺失用问号。长传是≥30米坐标距离代理，缺少终点会影响覆盖；不是供应商长传分类。','small')


def render_shots(team,rows,side,story,text,table,new,font,palette,number,details):
    from reportlab.graphics.shapes import Drawing,Rect,String
    from reportlab.lib import colors
    new(team['name']+' · 射门方式与结果明细')
    labels={'off_target':'射偏','on_target':'射正（含未进球）','regular':'常规进攻','fast_break':'快速反击','corner':'角球','free_kick':'任意球','scramble':'混战','throw_in':'界外球','unknown':'未识别','other_body_part':'其他部位','goal':'进球','saved':'被扑','blocked':'封堵','miss':'射偏','missed':'射偏','post':'门框','left_foot':'左脚','right_foot':'右脚','head':'头球','header':'头球','open_play':'运动战','penalty':'点球','set_piece':'定位球'}
    for key,title in [('outcome','射门结果'),('body_part','触球部位'),('situation','进攻场景')]:
        counts=Counter(labels.get(str(e.get(key)),str(e.get(key))) if e.get(key) is not None else '未提供' for e in rows)
        d=Drawing(495,40+len(counts)*23);maximum=max([n for matches in details for n in Counter(str(e.get(key)) for m in matches for e in m.get('shots',[]) if e.get('direction')=='attack').values()]+[1]);height=d.height
        for i,(label,n) in enumerate(counts.items()):
            y=height-25-i*23;d.add(String(0,y,label,fontName=font,fontSize=8));d.add(Rect(120,y-2,330*n/maximum,8,fillColor=colors.HexColor(palette[side]),strokeColor=None));d.add(String(458,y,str(n),fontSize=8))
        text(title,'heading');story.append(d);story.append(_spacer())
    selected=sorted(rows,key=lambda x:(not x.get('goal'),-(x.get('xg') or 0)))[:12]
    table(['球员 / 时间','结果 / 部位 / 场景','xG','xGOT'],[[str(e.get('player_name') or '未关联')+f" / {e.get('minute')}:{int(e.get('second') or 0):02}",' / '.join(labels.get(str(e.get(k)),str(e.get(k))) if e.get(k) is not None else '未提供' for k in ('outcome','body_part','situation')),e.get('xg'),e.get('xgot')] for e in selected],[200,175,60,60])
    text(f'图统计全部{len(rows)}次射门，同一类别在两队使用相同次数尺度；表格按进球优先及xG列最多12次。缺失部位/场景明确保留；完整逐射门记录在 JSON。','small')


def legacy_on_target_shots(rows,teams):
    # Event 15 includes both saves and blocks. Legacy numeric outcome=1 does
    # not distinguish them, so only use the same snapshot's classified detail.
    classified={(e.get('match_id'),e.get('event_id')):e.get('outcome')
                for team in teams for match in (team.get('analyst_detail') or {}).get('matches',[])
                for e in match.get('shots',[])}
    confirmed=[];unknown=[]
    for e in rows:
        if e.get('event_type') not in (15,16):continue
        outcome=classified.get((e.get('match_id'),e.get('event_id')))
        if e.get('event_type')==16 or outcome in ('goal','on_target','saved','saved_to_post'):confirmed.append(e)
        elif outcome not in ('blocked','off_target'):unknown.append(e)
    return confirmed,unknown


def render_goalkeepers(r,details,story,text,table,new,font,palette,number,compare_chart,event_map):
    groups=[]
    for side,t in enumerate(r['teams']):
        g=defaultdict(lambda:{'name':None,'shots':[],'distribution':[],'minutes':[]})
        for m in details[side]:
            for k in m.get('goalkeepers',[]):
                row=g[k.get('player_id')];row['name']=k.get('name');row['shots']+=k.get('shots',[]);row['distribution']+=k.get('distribution',[]);row['minutes'].append(k.get('minutes'))
        for pid,k in g.items():groups.append(dict(k,player_id=pid,side=side,team=t['name']))
    if not groups:return
    new('门将 · 同覆盖射后机会质量对比')
    for k in groups:
        k['on_target_shots']=[e for e in k['shots'] if e.get('outcome') in ('goal','on_target','saved','saved_to_post')]
    values=[known_sum(e.get('xgot') for e in k['on_target_shots']) for k in groups]
    goals=[sum(bool(e.get('goal')) for e in k['on_target_shots'] if finite(e.get('xgot'))) if any(finite(e.get('xgot')) for e in k['on_target_shots']) else None for k in groups]
    compare_chart([k['team']+' '+str(k['name']) for k in groups],[('已记录 xGOT',values,'#4f6b82'),('同覆盖进球',goals,'#a3adb8')],'同一覆盖样本')
    table(['球队 / 门将','射后xG','同覆盖失球','覆盖 / 射正'],[[k['team']+' / '+str(k['name']),v,g,str(sum(finite(e.get('xgot')) for e in k['on_target_shots']))+'/'+str(len(k['on_target_shots']))] for k,v,g in zip(groups,values,goals)],[255,75,75,90])
    text('射正仅按来源结果 goal、on_target、saved、saved_to_post 识别；覆盖为射后xG有值的射正／全部射正。射偏、封堵即使xGOT=0也不进入分母或合计，未知结果同样排除；真实射正的0值保留。失球仅比较相同覆盖样本。差值不直接等于门将个人能力；完整记录与版本见同版 JSON。','small')
    # Stored goalkeeper distribution uses the keeper's own attack frame.
    for k in groups:
        if k['distribution']:
            new(k['team']+' · '+str(k['name'])+' 出球路线')
            event_map(k['distribution'],k['side'],'attack',True)
            text('仅绘制已保存的门将出球；颜色归属报告球队。没有对手压迫位置，不能判断每次出球是否受到高压。','small')
        points=[e for e in k['shots'] if finite(e.get('goalmouth_y')) and finite(e.get('goalmouth_z'))]
        if not points:continue
        from reportlab.graphics.shapes import Drawing,Line,Circle,String
        from reportlab.lib import colors
        new(k['team']+' · '+str(k['name'])+' 射门目标原始坐标')
        # Raw supplier axes only: no unverified physical goal rectangle.
        all_points=[e for keeper in groups for e in keeper['shots'] if finite(e.get('goalmouth_y')) and finite(e.get('goalmouth_z'))]
        lo=min([0,*[e['goalmouth_y'] for e in all_points]]);hi=max([1,*[e['goalmouth_y'] for e in all_points]])
        zlo=min([0,*[e['goalmouth_z'] for e in all_points]]);zhi=max([1,*[e['goalmouth_z'] for e in all_points]])
        d=Drawing(495,330);color=colors.HexColor(palette[k['side']])
        for j in range(5):
            xx=50+400*j/4;yy=45+250*j/4
            d.add(Line(xx,45,xx,295,strokeColor=colors.HexColor('#dce2e9')));d.add(String(xx-8,28,number(lo+(hi-lo)*j/4),fontSize=7))
            d.add(Line(50,yy,450,yy,strokeColor=colors.HexColor('#dce2e9')));d.add(String(5,yy,number(zlo+(zhi-zlo)*j/4),fontSize=7))
        for e in points:
            x=50+400*(e['goalmouth_y']-lo)/(hi-lo);y=45+250*(e['goalmouth_z']-zlo)/(zhi-zlo)
            d.add(Circle(x,y,3.5,fillColor=color if e.get('goal') else None,strokeColor=color))
        d.add(String(175,8,'Opta Q102 / raw Y',fontSize=8));d.add(String(52,310,'Opta Q103 / raw Z',fontSize=8));story.append(d);story.append(_spacer())
        text(f'有原始目标坐标{len(points)}／射门记录{len(k["shots"])}。进球实心、未进球空心；两队统一原始坐标尺度。尚未核实物理换算，因此不画球门框，也不把坐标解释为米或门内百分比。','small')


def lineup_xy(player,side):
    """Only explicit provider coordinates; never infer a missing position."""
    value=player.get('lineup_position') or player
    if value.get('coordinate_frame',value.get('frame'))!='provider_vertical_0_1':return None
    x=value.get('pos_x');y=value.get('pos_y')
    if not finite(x) or not finite(y) or not (0<=x<=1 and 0<=y<=1):return None
    # Provider y is progress toward the opposite goal, x is across the pitch.
    xx,yy=100*y,100*x
    return (100-xx,100-yy) if side else (xx,yy)


def render_lineup_layout(r,story,text,table,new,pitch,font,palette):
    from reportlab.graphics.shapes import Circle,String
    from reportlab.lib import colors
    for side,team in enumerate(r['teams']):
        side_name='home' if side==0 else 'away'
        prior=next((t for t in (r.get('lineup_baseline') or {}).get('teams',[]) if t.get('side')==side_name),{})
        current=next((t for t in (r.get('lineup') or {}).get('teams',[]) if t.get('side')==side_name),{})
        for name,roster in [('此前预计首发',prior),('当前首发',current)]:
            players=roster.get('players',[]);located=[p for p in players if lineup_xy(p,side) is not None]
            if not located:continue
            new(team['name']+' · '+name+'位置图')
            text(str(roster.get('formation') or '阵型未提供'),'small');d=pitch([]);color=colors.HexColor(palette[side])
            # Numbered nodes retain unambiguous labels without collision-prone
            # long surnames painted over the pitch.
            for i,p in enumerate(located):
                x,y=lineup_xy(p,side);x=22+4.5*x;y=25+2.88*y
                d.add(Circle(x,y,9,strokeColor=color,fillColor=colors.white));d.add(String(x-(3 if i<9 else 5),y-3,str(i+1),fontSize=8,fillColor=color))
            story.append(d);story.append(_spacer())
            table(['标记','球员','号码 / 历史角色'],[[i+1,p.get('name') or p.get('id'),str(p.get('shirt_number') or '未提供')+' / '+str(p.get('position') or p.get('role') or (p.get('profile') or {}).get('position') or '未提供')] for i,p in enumerate(located)],[55,285,155])
            text(f'直接使用 VIP 来源位置，已定位{len(located)}/{len(players)}人；x=pos_y、y=pos_x，主队向右、客队向左。图是供应商名单布局，不代表平均活动位置或实时盯人安排。','small')
