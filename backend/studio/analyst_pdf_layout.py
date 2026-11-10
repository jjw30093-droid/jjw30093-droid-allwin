"""A4 analyst spreads: related panels share a page; no metric recalculation."""
from copy import deepcopy
from xml.sax.saxutils import escape
from .analyst_pdf_language import reader_text, reader_time, GLOSSARY

def sequence_for_pitch(sequence,team_id,side):
    """Orient historical opponents relative to the analysed club, not target IDs."""
    return [dict(e,_display_side=(side if e['team_id']==team_id else 1-side) if e.get('team_id') else None) for e in sequence]

def compact_report(value, font, pitch, palette):
    from reportlab.platypus import Paragraph, Table, TableStyle, PageBreak, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Drawing, Rect, Circle, String
    r=value['report'];teams=r['teams'];story=[];width=495;cell=242
    styles={k:ParagraphStyle(k,fontName=font,fontSize=size,leading=size*1.45,spaceAfter=5,
                            wordWrap='CJK',textColor=colors.HexColor('#263445'))
            for k,size in [('title',19),('heading',11),('body',8.5),('small',7.5)]}
    def p(s,style='body',verbatim=False):return Paragraph(escape(str(s) if verbatim else reader_text(s)).replace('\n','<br/>'),styles[style])
    def num(v):return '暂无数据' if v is None else f'{v:.2f}' if isinstance(v,float) else str(v)
    def table(headers,rows,widths=None):
        d=[[p(x,'small') for x in headers]]+[[p(num(x),'small') for x in row] for row in rows]
        t=Table(d,colWidths=widths or [230/len(headers)]*len(headers),hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#edf1f5')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#dce2e9'))]))
        return t
    def card(title,*items):return [p(title,'heading'),*items]
    def grid(cards):
        for i in range(0,len(cards),2):
            row=cards[i:i+2]+([[]] if len(cards[i:i+2])==1 else [])
            t=Table([row],colWidths=[cell,cell],hAlign='LEFT')
            t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),('BOX',(0,0),(0,0),.4,colors.HexColor('#dce2e9')),('BOX',(1,0),(1,0),.4,colors.HexColor('#dce2e9'))]))
            story.extend([t,Spacer(1,8)])
    def page(title):
        if story:story.append(PageBreak())
        h=p(title,'title');h._bookmark=('spread-'+str(len(story)),title);story.extend([h,p(r['match']['home_name']+' vs '+r['match']['away_name'],'small')])
    def small_pitch(shots=None,sequence=None,roster=None,side=0,team_id=None):
        sequence=sequence_for_pitch(sequence or [],team_id,side) if team_id else sequence
        d=deepcopy(pitch(shots or [],sequence));d.contents=[x for x in d.contents if not isinstance(x,String) or str(x.text).isdigit()]
        for label in d.contents:
            if isinstance(label,String):label.fontSize=14
        if roster:
            from .analyst_pdf_detail import lineup_xy
            for j,player in enumerate(roster.get('players',[])):
                point=lineup_xy(player,side)
                if point:
                    x,y=point;d.add(Circle(22+4.5*x,25+2.88*y,9,fillColor=colors.HexColor(palette[side]),strokeColor=colors.white))
                    d.add(String(22+4.5*x-4,25+2.88*y-4,str(j+1),fontName=font,fontSize=14,fillColor=colors.white))
        scale=230/495;d.scale(scale,scale);d.width=230;d.height=335*scale
        return d
    def findings(topic,tid):return [x for x in r.get('tactical_analysis',{}).get('findings',[]) if x.get('topic')==topic and x.get('rule',{}).get('team_id')==tid]
    def summary(topic,t):
        xs=findings(topic,t['id']);return p(xs[0]['text'] if xs else '本报告没有足够证据给出此项判断。','small')
    def sources_note():return p('事件编号、完整球员数据、支持及限制条件均保留在后方证据附录。未展示记录不按零值处理。','small')

    page(('赛前' if r['phase']=='prematch' else '赛后')+'分析 · 关键判断')
    story.extend([p('资料截止：'+reader_time(r['cutoff_at'])+' · '+reader_text(r['analysis_mode']),'small'),p('比赛数据：CC Opta；阵容与伤停：VIP。以下判断适用于本报告的比赛样本、比分及人数筛选。','small')])
    story.append(p('统计样本：'+'；'.join(t['name']+f" {len(t.get('samples',[]))}场、{num(t.get('minutes'))}分钟" for t in teams)+'。完整筛选条件及数据覆盖见附录。','small'))
    cards=[]
    for h in r['hypotheses']:
        edit=value.get('judgments',{}).get(h['id'],{});manual=edit.get('text')
        body='已撤回：'+edit['reason'] if edit.get('withdrawn') else manual or h['text']
        cards.append(card(h['title'],p(body,verbatim=bool(manual or edit.get('withdrawn'))),p('适用条件、样本限制及具体事件见本条判断的证据附录。','small')))
    cards.append(card('统计范围与阅读方法',p('每队最近比赛数、有效统计分钟、xG有值射门数都会影响比较。整场xA不拆成任意时段数据。','small'),sources_note()))
    grid(cards)

    page('阵容 · 预计与正式首发对照')
    cards=[]
    for side,t in enumerate(teams):
        key='away' if side else 'home'
        for label,ctx in [('此前预计',r.get('lineup_baseline') or {}),('当前首发',r.get('lineup') or {})]:
            roster=next((x for x in ctx.get('teams',[]) if x.get('side')==key),{})
            players=roster.get('players',[])
            cards.append(card(t['name']+' · '+label+' '+str(roster.get('formation') or ''),small_pitch(roster=roster,side=side),
                p('；'.join(str(i+1)+' '+x['name'] for i,x in enumerate(players)) or '未保存这份名单','small')))
    grid(cards)
    story.append(p('位置仅使用来源记录：主队向右，客队向左。数字对应名单；没有来源位置时保留空场，不猜测站位。','small'))

    page('终结 · 射门位置、进攻方式与创造者')
    story.append(p('球场方向：主队（红）向右进攻，客队（紫）向左进攻。两队射门使用相同坐标与圆面积尺度。','small'))
    cards=[]
    for side,t in enumerate(teams):
        a=t['attack'];b=t['defence']
        cards.append(card(t['name']+' · 射门图',small_pitch([(side,x) for x in t['chains'] if x['direction']=='attack']),
            p(f"射门{a['shots']}次，xG {num(a['xg'])}，有值{a['known_xg']}/{a['shots']}；对手xG {num(b['xg'])}。空心未进，实心进球，圆面积按xG。",'small')))
    for t in teams:
        cards.append(card(t['name']+' · 机会来自哪里',table(['进攻方式','射门','xG'],[[x['name'],x['shots'],x['xg']] for x in t['methods']])))
    for t in teams:
        players=sorted([x for x in t['players'] if x.get('xa') is not None],key=lambda x:-x['xa'])[:4]
        cards.append(card(t['name']+' · 整场xA前4名',table(['球员','xA','有值场次'],[[x['name'],x['xa'],x.get('xa_match_count')] for x in players]),p('官方整场累计，不随比分／人数筛选拆分；出场时间和有值场次不同，不直接代表效率。','small')))
    grid(cards)

    page('区域对位 · 最后一传与关键机会')
    cards=[]
    for side,t in enumerate(teams):
        own=[x for x in t['chains'] if x['direction']=='attack']
        if r['phase']=='postmatch':
            baseline=(r.get('regional_baseline') or [{},{}])[side]
            other=[x for x in baseline.get('chains',[]) if x['direction']=='attack']
        else:other=[x for x in teams[1-side]['chains'] if x['direction']=='defence']
        maximum=max([sum(x.get('creator_zone')==z for x in g) for g in [own,other] for z in range(15)]+[1])
        d=Drawing(230,146)
        for j,g in enumerate([own,other]):
            label=('本场进攻' if j==0 else '赛前样本') if r['phase']=='postmatch' else ('本队进攻' if j==0 else '对手被创造')
            d.add(String(j*117,135,label,fontName=font,fontSize=8))
            for z in range(15):
                count=sum(x.get('creator_zone')==z for x in g);base=colors.HexColor(palette[side]);a=count/maximum;fill=colors.Color(1-(1-base.red)*a,1-(1-base.green)*a,1-(1-base.blue)*a)
                x=j*117+(z//5)*37;y=18+(z%5)*20;d.add(Rect(x,y,37,20,fillColor=fill,strokeColor=colors.white));d.add(String(x+14,y+6,str(count),fontName=font,fontSize=8))
        cards.append(card(t['name']+' · 最后一传区域',d,p('左右两图同尺度；横向为后场→前场，纵向自下而上为右边路→左边路。格内为关联射门数，缺少最后一传的不分摊。','small'),summary('matchup',t)))
    shots=sorted([(i,c) for i,t in enumerate(teams) for c in t['chains'] if c['direction']=='attack'],key=lambda x:(not x[1].get('goal'),-(x[1].get('xg') or 0)))[:2]
    for side,c in shots:
        sample=next((x for x in teams[side].get('samples',[]) if x['id']==c.get('match_id')),{})
        cards.append(card(teams[side]['name']+' · '+('进球' if c.get('goal') else '主要机会')+f" {c['minute']}:{c['second']:02}",p(str(sample.get('kickoff_at',''))[:10]+' vs '+str(sample.get('opponent','')),'small'),small_pitch(sequence=c.get('sequence',[]),team_id=teams[side]['id'],side=side),p(c['method']+' · xG '+num(c.get('xg'))+'；仅画已记录动作，缺失动作不连线。','small')))
    grid(cards)

    page('战术过程 · 推进、转换与定位球')
    cards=[]
    for t in teams:
        rows=t['progression'];cards.append(card(t['name']+' · 向前推进',table(['成功推进','进入前场','进入禁区'],[[rows.get('completed'),rows.get('third_entries'),rows.get('box_entries')]]),summary('progression',t)))
    for t in teams:cards.append(card(t['name']+' · 攻防转换',summary('transition',t),p('丢球后的危险与夺回后的威胁分别核查；事件推导不等于真实无球阵型。','small')))
    for t in teams:
        rows=[]
        for kind in ('角球','任意球','界外球','门球'):
            es=[x for x in t['restarts'] if x['kind']==kind];rows.append([kind,sum(x['direction']=='attack' for x in es),sum(x['direction']=='defence' for x in es)])
        cards.append(card(t['name']+' · 定位球',table(['类型','本队','对手'],rows),p('主罚、第一记录触球、争顶及后续射门在附录逐项核查；第一触球不等于已控制第二落点。','small')))
    grid(cards)

    page('人员与比赛状态 · 谁在承担任务')
    cards=[]
    for t in teams:
        players=sorted(t['players'],key=lambda x:-(x.get('minutes') or 0))[:6]
        cards.append(card(t['name']+' · 出场最多6人',table(['球员','分钟','xG','xA','xT'],[[x['name'],x['minutes'],x.get('xg'),x.get('xa'),x.get('model_xt')] for x in players],[91,33,35,35,36]),p('按出场时间选择，未列球员及来源覆盖保留在附录。','small')))
    for t in teams:
        cards.append(card(t['name']+' · 比分状态',table(['状态','分钟','射门','xG','xGA'],[[{'drawing':'平局','leading':'领先','trailing':'落后'}.get(x['name'],x['name']),x['minutes'],x['shots'],x['xg'],x['defence']['xg']] for x in t['states']],[43,45,35,52,55]),p('各状态时长不同；不能仅比较累计值便认定某种打法更好。','small')))
    cards.append(card('指标怎么读',*[p(a+'：'+b,'small') for a,b in GLOSSARY[:3]]))
    cards.append(card('统计限制与完整证据',p(GLOSSARY[-1][1],'small'),p('xGA为对手xG，xGOT辅助分析射正后的门将表现，PV为来源模型的控球价值。','small'),sources_note()))
    grid(cards)
    # Compact the specialist material into analyst spreads as well, rather than
    # shortening the report merely by moving every specialist topic to an appendix.
    def zone_map(values,side,maximum):
        d=Drawing(230,113);base=colors.HexColor(palette[side])
        for z,n in enumerate(values):
            a=n/max(1,maximum);fill=colors.Color(1-(1-base.red)*a,1-(1-base.green)*a,1-(1-base.blue)*a)
            x=12+z//5*68;y=8+z%5*19;d.add(Rect(x,y,68,19,fillColor=fill,strokeColor=colors.white));d.add(String(x+30,y+6,str(n),fontName=font,fontSize=8))
        return d
    def zone_values(t,key):return [sum(z.get('actions',{}).get(key,{}).get('count',0) for m in t.get('analyst_matches',[]) for z in m.get('zones',[]) if z.get('zone')==i) for i in range(15)]
    page('防守与球权 · 压迫、夺回和丢失')
    cards=[]
    for t in teams:
        matches=t.get('analyst_matches',[])
        keys=[('official_ppda','PPDA'),('high_regains_proxy','前场抢回'),('counterpress_regains_within_5s_proxy','丢球后5秒内抢回'),('losses_followed_by_opponent_shot_within_15s_proxy','丢球后15秒内被射门')]
        cards.append(card(t['name']+' · 防守过程（整场）',table(['指标','各场整场记录'],[[label,' / '.join(num(m.get('pressing',{}).get(k)) for m in matches)] for k,label in keys],[115,115]),p('这些指标为所选比赛整场记录，不随比分／人数筛选拆分。与下方按选区统计的事件区域分开解释；不能据此推断实际防线高度。','small')))
    for key,label in [('recoveries','夺回球权区域'),('losses','被断球及失误区域')]:
        values=[zone_values(t,key) for t in teams];maximum=max([v for xs in values for v in xs]+[1])
        for side,t in enumerate(teams):
            if not any(m.get('zones') for m in t.get('analyst_matches',[])):
                cards.append(card(t['name']+' · '+label,p('本版本没有区域记录，不能把缺失画成零次。')));continue
            loss_note='仅事件50/51，不代表全部球权丢失。' if key=='losses' else ''
            cards.append(card(t['name']+' · '+label,zone_map(values[side],side,maximum),p(f'共{sum(values[side])}次有位置记录；两队同色阶。{loss_note}横向后场→前场，纵向自下而上右路→左路。','small')))
    grid(cards)

    page('个人动作 · 过人、犯规与推进威胁')
    cards=[]
    def action_rows(t,kind):return [e for m in t.get('analyst_detail',{}).get('matches',[]) for e in m.get('action_zones',[]) if e.get('kind')==kind and e.get('team_id')==t['id'] and (kind!='foul' or e.get('foul_role')=='committed')]
    def counts(events):
        values=[0]*15
        for e in events:
            if e.get('x') is not None and e.get('y') is not None and 0<=e['x']<=100 and 0<=e['y']<=100:values[min(2,int(e['x']/100*3))*5+min(4,int(e['y']/100*5))]+=1
        return values
    for kind,label in [('dribble','过人动作'),('foul','实施犯规')]:
        es=[action_rows(t,kind) for t in teams];values=[counts(x) for x in es];maximum=max([v for xs in values for v in xs]+[1])
        for side,t in enumerate(teams):
            if not t.get('analyst_detail',{}).get('matches'):
                cards.append(card(t['name']+' · '+label,p('本版本未保存逐动作专项数据，不能判断该项次数。')));continue
            cards.append(card(t['name']+' · '+label,zone_map(values[side],side,maximum),p(f'有位置记录{sum(values[side])}/{len(es[side])}次；本队进攻方向统一为后场→前场，右路→左路。犯规不与对手的被犯规记录重复相加。','small')))
    for t in teams:
        actions=[e for m in t.get('analyst_detail',{}).get('matches',[]) for e in m.get('xt_actions',[]) if e.get('xt_added') is not None]
        if not actions:
            cards.append(card(t['name']+' · 逐动作xT',p('本版本没有已保存的逐动作xT评分，不将缺失视为零贡献。')));continue
        positive=sum(e['xt_added'] for e in actions if e['xt_added']>0);negative=sum(e['xt_added'] for e in actions if e['xt_added']<0)
        top=sorted(actions,key=lambda e:-abs(e['xt_added']))[:3]
        cards.append(card(t['name']+' · 逐动作xT',p(f'有评分动作{len(actions)}条；增加威胁{num(positive)}，减少威胁{num(negative)}。','small'),table(['高影响动作（按绝对值）','xT'],[[str(e.get('player_name') or '未知球员')+f" {e['minute']}:{e['second']:02}",e['xt_added']] for e in top],[180,50]),p('使用已保存模型评分，保留负值；事件、模型版本和完整路线见附录。','small')))
    grid(cards)
    from .analyst_pdf_sections import append_sections
    append_sections(value,story,p,table,card,grid,page,small_pitch,font,palette,num)
    if value.get('editorial_notes'):
        page('分析师补充');story.append(p(value['editorial_notes'],verbatim=True))
    if r.get('checks'):
        page('复盘 · 逐条核验赛前判断')
        grid([card(c['status']+' · '+c['title'],p(c['reason']),p(c.get('criterion',''),'small'),p('支持与不支持的具体事件见完整附录。','small')) for c in r['checks']])
    return story
