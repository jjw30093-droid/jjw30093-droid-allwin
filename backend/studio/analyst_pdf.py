"""Paginated PDF from the exact immutable report and editorial revision."""
from math import sqrt, pi
from xml.sax.saxutils import escape
from pathlib import Path
import os
from .analyst_pdf_detail import append_detail_sections, finding_evidence, render_lineup_layout, legacy_on_target_shots
from .analyst_pdf_language import reader_text, reader_time, GLOSSARY


def _append_analysis_sections(r, story, text, table, new, pitch, font, palette, number):
    """Render the same saved analysis fields as the interactive report.

    No source reads or metric substitution occur during export. Older snapshots
    retain their recorded fields and explicitly lack the newer interval layers.
    """
    from collections import Counter, defaultdict
    from reportlab.graphics.shapes import Drawing, Rect, Line, Circle, String
    from reportlab.lib import colors
    from reportlab.pdfbase.pdfmetrics import stringWidth
    teams=r['teams']; names={p['id']:p['name'] for t in teams for p in t['players']}
    muted=colors.HexColor('#64748b'); grid=colors.HexColor('#dce2e9')
    labels={'home':'主场','away':'客场','attack':'进攻','defence':'防守',
            'both':'共同在场','only_a':'仅 A 在场','only_b':'仅 B 在场','neither':'均不在场',
            'drawing':'平局','leading':'领先','trailing':'落后','cross':'传中','through':'直塞',
            'aerial':'争顶','take_on':'过人／防守对抗','foul':'犯规关系'}

    def time(e):
        return f"阶段{e.get('period','?')} {e.get('minute','?')}:{e.get('second',0):02}"

    def ref(e):
        return f"{e.get('match_id','')} / {e.get('event_id','')}"

    def ratio(a,b):
        return None if a is None or not b else round(a/b*100,1)

    def bars(row_labels, series, unit, maximum=None, row_height=24):
        """Fixed shared scale; missing observations have no invented bar."""
        n=len(row_labels);height=55+max(n,1)*row_height
        d=Drawing(495,height);left=155;plot=315
        max_v=maximum or max([v for _,values,_ in series for v in values if isinstance(v,(float,int))]+[1])
        for j,(name,_,color) in enumerate(series):
            d.add(Rect(left+j*160,height-12,7,7,fillColor=colors.HexColor(color),strokeColor=None))
            d.add(String(left+11+j*160,height-11,name,fontName=font,fontSize=7,fillColor=muted))
        for tick in range(5):
            x=left+plot*tick/4;d.add(Line(x,28,x,height-22,strokeColor=grid,strokeWidth=.5))
            d.add(String(x-7,14,number(max_v*tick/4),fontSize=7,fillColor=muted))
        for i,label in enumerate(row_labels):
            y=height-40-i*row_height
            label=str(label)
            while stringWidth(label,font,8)>145:label=label[:-2]+'…'
            d.add(String(2,y+2,label,fontName=font,fontSize=8,fillColor=muted))
            for j,(_,values,color) in enumerate(series):
                v=values[i] if i<len(values) else None;yy=y+7-j*11
                if v is None:d.add(String(left+3,yy,'未提供',fontName=font,fontSize=7,fillColor=muted));continue
                d.add(Rect(left,yy,max(0,v)*plot/max_v,7,fillColor=colors.HexColor(color),strokeColor=None))
        d.add(String(2,14,unit,fontName=font,fontSize=8,fillColor=muted))
        story.append(d)
        text('；'.join(name for name,_,_ in series)+'。数值见下表；同一坐标尺度。','small')

    def grid_map(values,color,title,maximum):
        d=Drawing(495,175);base=colors.HexColor(color)
        for z,v in enumerate(values):
            x=80+(z//5)*115;y=22+(z%5)*25;a=v/max(1,maximum)
            fill=colors.Color(1-(1-base.red)*a,1-(1-base.green)*a,1-(1-base.blue)*a)
            d.add(Rect(x,y,115,25,fillColor=fill,strokeColor=colors.white))
            d.add(String(x+49,y+8,str(v),fontSize=9,fillColor=colors.white if a>.6 else muted))
        for j,label in enumerate(('后场','中场','前场')):d.add(String(120+j*115,155,label,fontName=font,fontSize=8))
        for j,label in enumerate(('右边路','右肋部','中路','左肋部','左边路')):d.add(String(25,30+j*25,label,fontName=font,fontSize=8))
        story.append(d);text(title,'small')

    def network(t,side):
        d=pitch([]);pos={p['id']:p.get('average_action_position') for p in t['players']}
        edges=[e for e in t.get('passing_network',[]) if e['count']>=3 and pos.get(e['from_id']) and pos.get(e['to_id'])]
        def xy(p):
            x,y=p['x'],p['y']
            if side:x,y=100-x,100-y
            return 22+x*4.5,25+y*2.88
        color=colors.HexColor(palette[side]);mx=max([e['count'] for team in teams for e in team.get('passing_network',[])]+[1])
        for e in edges:d.add(Line(*xy(pos[e['from_id']]),*xy(pos[e['to_id']]),strokeColor=color,strokeWidth=.5+3*e['count']/mx,strokeOpacity=.55))
        used={pid for e in edges for pid in (e['from_id'],e['to_id'])}
        for index,pid in enumerate(sorted(used),1):
            x,y=xy(pos[pid]);d.add(Circle(x,y,7,fillColor=color,strokeColor=colors.white));d.add(String(x-3,y-3,str(index),fontSize=7,fillColor=colors.white))
        story.append(d)
        text('节点为球上动作平均位置，非无球站位；连线为至少3次的整场传接，粗细表示次数。','small')
        text('；'.join(f'{i} {names.get(pid,pid)}' for i,pid in enumerate(sorted(used),1)),'small')
        table(['传球人','接球人','次数'],[[names.get(e['from_id'],'未关联'),names.get(e['to_id'],'未关联'),e['count']] for e in sorted(edges,key=lambda x:-x['count'])[:10]],[210,210,75])
        if len(edges)>10:text(f'列出最频繁10条／共{len(edges)}条合格连接；完整关系保留在证据 JSON。','small')

    for side,t in enumerate(teams):
        ext=t.get('report_extensions') or {};matches=ext.get('matches',[])
        history=ext.get('history') or {}
        new(t['name']+(' · 近期比赛与背景' if history else ' · 本版本比赛样本'))
        sample=history.get('matches',[]) if history else t.get('samples',[])
        recent=sample[:10]
        bars([str(x.get('kickoff_at',''))[:10]+' '+x.get('opponent','') for x in recent],
             [(t['name']+' xG',[x.get('xg') for x in recent],palette[side]),('对手 xG',[x.get('xga') for x in recent],'#64748b')],'逐场 xG',max([x.get(key) for team in teams for x in ((team.get('report_extensions') or {}).get('history',{}).get('matches',[]) if (team.get('report_extensions') or {}).get('history') else team.get('samples',[])) for key in ('xg','xga') if x.get(key) is not None]+[1]))
        table(['日期／对手','场地','射门','xG','xGA'],[[str(x.get('kickoff_at',''))[:10]+' '+x.get('opponent',''),labels.get(x.get('venue'),x.get('venue','')),x.get('shots'),x.get('xg'),x.get('xga')] for x in recent],[235,50,60,75,75])
        summaries=[(label,history.get(key)) for key,label in [('recent5','最近5场'),('recent10','最近10场')]]
        if any(v for _,v in summaries):
            table(['窗口','实际场次','分钟','xG/90','xGA/90'],[[label,v.get('matches'),v.get('minutes'),v.get('xg_per90'),v.get('xga_per90')] for label,v in summaries if v],[115,90,110,90,90])
            text('两个窗口按同一截止时间、赛季及口径汇总；可用场次不足时使用实际样本，不补足为10场。','small')
        else:text('该固定版本没有另存最近5场／10场共同口径汇总，不能在导出时回填当前数据。','small')

        new(t['name']+' · 官方 xA 创造贡献排行')
        xa_players=sorted([p for p in t['players'] if p.get('xa') is not None],key=lambda p:-p['xa'])[:12]
        if xa_players:
            xa_max=max([p['xa'] for team in teams for p in team['players'] if p.get('xa') is not None]+[.01])
            bars([p['name'] for p in xa_players],[(t['name']+' 官方 xA',[p['xa'] for p in xa_players],palette[side])],'已覆盖比赛的官方 xA 合计',xa_max,row_height=18)
            table(['球员','官方 xA','xA覆盖场次'],[[p['name'],p['xa'],p.get('xa_match_count')] for p in xa_players],[295,100,100])
        else:text('本快照没有可用官方 xA，未绘制零值排行。')
        text('按已记录官方 xA 合计列前12人；两队统一横轴。覆盖场次不同的累计值不能直接当作创造效率；整场 xA 不按比分、人数、区域或选区分摊。完整球员值及逐场来源见同版 JSON。','small')

        new(t['name']+' · 推进路线与机会效率')
        routes=t.get('routes',[])
        if routes:
            bars([x['label'] for x in routes],[(t['name'],[ratio(x.get('shot_possessions'),x.get('possessions')) for x in routes],palette[side])],'形成射门回合 / 全部回合 %',100)
            table(['起点／传球次数','回合','射门回合','转化 %','已记录xG'],[[x['label'],x.get('possessions'),x.get('shot_possessions'),ratio(x.get('shot_possessions'),x.get('possessions')),x.get('xg')] for x in routes],[215,65,75,65,75])
        patterns=t.get('attack_patterns',[])
        if patterns:
            text('按进攻方式的尝试与产出','heading')
            text('传中／直塞参与等属性可能重叠，不能把不同方式简单相加当作总进攻数。','small')
            table(['方式','进攻','射门回合','转化 %','已记录xG'],[[x['name'],x.get('attacks'),x.get('shot_attacks'),ratio(x.get('shot_attacks'),x.get('attacks')),x.get('xg')] for x in patterns],[215,65,75,65,75])

        new(t['name']+' · 带球推进与转换')
        carries=t.get('carries',[]);count=Counter(e.get('player_id') for e in carries);progressive=Counter(e.get('player_id') for e in carries if e.get('progressive'))
        ids=sorted(count,key=lambda p:(-progressive[p],-count[p]))[:8]
        if ids:
            bars([names.get(pid,'未关联') for pid in ids], [('带球代理',[count[p] for p in ids],palette[side]),('其中向前推进',[progressive[p] for p in ids],'#64748b')],'次数',max([n for team in teams for n in Counter(e.get('player_id') for e in team.get('carries',[])).values()]+[1]))
            table(['球员','带球代理','其中向前推进'],[[names.get(pid,'未关联'),count[pid],progressive[pid]] for pid in ids],[295,100,100])
        text(f'共{len(carries)}条相邻受控动作推导记录；不是追踪数据或完整带球距离。显示最多8名球员。','small')
        transitions=t.get('transitions',[])
        if transitions:new(t['name']+' · 转换机会及事件证据')
        text('夺回后形成机会／丢球后被创造机会','heading')
        table(['方向','次数','xG有值','已记录xG'],[[labels[d],len(es),sum(x.get('xg') is not None for x in es),sum(x['xg'] for x in es if x.get('xg') is not None) if any(x.get('xg') is not None for x in es) else None] for d in ('attack','defence') for es in [[x for x in transitions if x.get('direction')==d]]],[165,100,100,130])
        if len(transitions)>4:text(f'列出前4条／共{len(transitions)}条，完整过程见同版 JSON。','small')
        for e in transitions[:4]:text(f"{labels.get(e.get('direction'),'')} · {time(e)} · xG {number(e.get('xg'))} · {ref(e)}",'small')

        new(t['name']+' · 双人组合与阵型阶段')
        pairs=t.get('pairs',[])
        if pairs:
            table(['共同状态','场次','分钟','射门','xG','xGA'],[[labels.get(x.get('group',x.get('name')),x.get('group',x.get('name'))),x.get('matches'),x.get('minutes'),x.get('shots'),x.get('xg'),x.get('xga',x.get('defence',{}).get('xg'))] for x in pairs],[120,60,85,60,85,85])
            for x in pairs:text(f"{labels.get(x.get('group',x.get('name')),x.get('group',x.get('name')))}：对手 {', '.join(str(v) for v in x.get('opponents',[]))}；比分分钟 {x.get('state_minutes',{})}",'small')
            text('少于3场或180分钟属于小样本；历史共同在场差异不直接等于球员因果效果。','small')
        else:text('本快照未选定双人组合。需在报告生成时选择球员，导出不补写四组数值。')
        intervals=[dict(x,match_id=m['match_id']) for m in matches for x in m.get('intervals',[])]
        if intervals:
            table(['比赛／阶段／分钟','阵型／比分／人数','射门','xG','xGA','传球成功'],[[x['match_id'][:8]+f" / {x.get('period')} / {number(x.get('start_ms',0)/60000)}-{number(x.get('end_ms',0)/60000)}",str(x.get('formation') or '未识别')+' / '+labels.get(x.get('state'),str(x.get('state','')))+f" / {x.get('own_players','?')}v{x.get('opponent_players','?')}",x.get('shots'),x.get('xg'),x.get('xga'),str(x.get('completed_passes','?'))+'/'+str(x.get('passes','?'))] for x in intervals],[135,145,40,55,55,65])
            text('边界来自阶段、比分、人数、阵型和球员在场区间；不把前后差异直接解释为换人的因果效果。','small')
        else:
            for m in t.get('analyst_matches',[]):
                text(m['match_id']+'：'+'；'.join(str(f.get('formation'))+' '+number(f.get('minutes'))+'分钟' for f in m.get('formations',[])),'small')
            text('该版本没有保存带事件产出的阵型／换人区间，不能在 PDF 中制造前后效果。','small')

        new(t['name']+' · 比赛分段过程')
        segments=[dict(x,match_id=m['match_id']) for m in matches for x in m.get('segments',[])]
        if segments:
            table(['比赛／阶段','分钟','射门／被射门','xG／xGA','传准 %','长传 %','配对动作成功 %'],[[x['match_id'][:8]+' / '+str(x.get('label')),x.get('minutes'),f"{x.get('shots','?')} / {x.get('conceded','?')}",number(x.get('xg'))+' / '+number(x.get('xga')),ratio(x.get('completed_passes'),x.get('passes')),ratio(x.get('long_passes'),x.get('passes')),ratio(x.get('duels_won'),x.get('duels'))] for x in segments],[120,45,75,85,55,55,60])
            if len(matches)==1:
                bars([x.get('label','') for x in segments],[(t['name']+' xG',[x.get('xg') for x in segments],palette[side]),('对手 xG',[x.get('xga') for x in segments],'#64748b')],'分段 xG',max([x.get(key) for team in teams for m in (team.get('report_extensions') or {}).get('matches',[]) for x in m.get('segments',[]) for key in ('xg','xga') if x.get(key) is not None]+[1]))
        else:
            for m in t.get('analyst_matches',[]):
                text('比赛 '+m['match_id'],'small')
                table(['阶段','射门','被射门'],[[x.get('segment'),x.get('shots',0),x.get('conceded',0)] for x in m.get('distribution',[])],[295,100,100])
        text('分段保留补时所属阶段；xG 为已记录覆盖。长传为记录起终点至少30米的几何代理，占全部传球的比例，不等于官方长传定义；对抗仅使用成功配对的争顶和过人。净控球时间没有从回合跨度推算。','small')

        historic_contests=[x for m in matches for x in m.get('contest_matrix',[])]
        if historic_contests:
            new(t['name']+' · 近期对抗对象')
            merged={}
            for x in historic_contests:
                key=(x.get('kind'),x.get('player_id'),x.get('opponent_id'))
                row=merged.setdefault(key,dict(x,contests=0,won=0,lost=0,unknown=0))
                for key_stat in ('contests','won','lost','unknown'):row[key_stat]+=x.get(key_stat,0)
            rows=sorted(merged.values(),key=lambda x:-x['contests'])
            table(['类型','本队球员','对手球员','次数','成功','失败','未定'],[[labels.get(x.get('kind'),x.get('kind')),x.get('player_name') or names.get(x.get('player_id'),'未关联'),x.get('opponent_name') or '未关联',x['contests'],None if x.get('kind')=='foul' else x['won'],None if x.get('kind')=='foul' else x['lost'],x['contests'] if x.get('kind')=='foul' else x['unknown']] for x in rows[:20]],[85,125,125,40,40,40,40])
            text(f'列出次数最多20组／共{len(rows)}组已配对关系；对方球员来自各历史对手。犯规关系不等于对抗胜负。未配对样本不进入矩阵，完整引用与覆盖见同版 JSON。','small')

        new(t['name']+' · 传接网络')
        network(t,side)

        new(t['name']+' · 球权得失与威胁入口')
        for metric,title in [('recoveries','夺回球权'),('losses','被断球及失误'),('box_pass_conceded','对手传入禁区的发起位置')]:
            values=[sum(c.get('actions',{}).get(metric,{}).get('count',0) for m in t.get('analyst_matches',[]) for c in m.get('zones',[]) if c['zone']==z) for z in range(15)]
            maximum=max([sum(c.get('actions',{}).get(metric,{}).get('count',0) for m in other.get('analyst_matches',[]) for c in m.get('zones',[]) if c['zone']==z) for other in teams for z in range(15)]+[1])
            grid_map(values,palette[side],f'{title} · 共{sum(values)}次记录；两队相同色阶，按本队进攻方向。',maximum)
        text('这是球上事件计数；不是跑动、无球热区或实际防线高度。防守事件按本队视角转换。','small')

        for kind in ('cross','through'):
            passes=[e for m in matches for e in m.get('passes',[]) if e.get('kind')==kind and e.get('direction')=='attack']
            if not passes:continue
            new(t['name']+' · '+labels[kind]+'路线')
            # Draw all source-recorded start/end coordinates; never add a link to
            # the next event or equate an incomplete arrow with completion.
            d=pitch([]);color=colors.HexColor(palette[side])
            known=[e for e in passes if all(e.get(k) is not None for k in ('x','y','end_x','end_y'))]
            for e in known:
                x,y,ex,ey=e['x'],e['y'],e['end_x'],e['end_y']
                if side:x,y,ex,ey=100-x,100-y,100-ex,100-ey
                a=(22+x*4.5,25+y*2.88);b=(22+ex*4.5,25+ey*2.88)
                d.add(Line(*a,*b,strokeColor=color,strokeWidth=.8,strokeDashArray=None if e.get('outcome')==1 else [3,2]))
                d.add(Circle(*a,1.8,fillColor=color,strokeColor=color));d.add(Circle(*b,2.8,fillColor=color if e.get('outcome')==1 else colors.white,strokeColor=color))
            story.append(d)
            text(f'共{len(passes)}次，起终点完整{len(known)}次；实线／实心终点为成功，虚线／空心终点为未成功或结果未确认。主队攻右，客队攻左。','small')
            counts=Counter(e.get('player_name') or names.get(e.get('player_id'),'未关联') for e in passes)
            table(['球员','尝试','成功'],[[name,n,sum((e.get('player_name') or names.get(e.get('player_id'),'未关联'))==name and e.get('outcome')==1 for e in passes)] for name,n in counts.most_common(10)],[295,100,100])

        new(t['name']+' · 定位球落点与后续')
        restarts=[e for m in matches for e in m.get('restarts',[])] or t.get('restarts',[])
        for direction in ('attack','defence'):
            rows=[e for e in restarts if e.get('direction')==direction and e.get('kind')!='门球']
            text(labels[direction]+'定位球','heading')
            d=pitch([]);color=colors.HexColor(palette[side]);valid=[e for e in rows if e.get('end_x') is not None and e.get('end_y') is not None]
            for e in valid:
                # Restart coordinates are in the taker's attacking frame.
                taker_side=side if direction=='attack' else 1-side;x,y=e['end_x'],e['end_y']
                if taker_side:x,y=100-x,100-y
                d.add(Circle(22+x*4.5,25+y*2.88,3,fillColor=color if e.get('kind')=='角球' else None,strokeColor=color))
            story.append(d);text(f'记录落点{len(valid)}/{len(rows)}。实心为角球，其余为空心；这是传球终点，不能直接认定第一／第二落点控制。','small')
            table(['类型','次数','可识别首次争顶','后续射门／已知xG'],[[kind,len(es),sum(e.get('first_aerial_won') is not None or ((e.get('first_contact') or {}).get('event_type')==44 and (e.get('first_contact') or {}).get('outcome') in (0,1)) for e in es),str(sum(len(e.get('shots',[])) for e in es))+' / '+number(sum(e['xg'] for e in es if e.get('xg') is not None) if any(e.get('xg') is not None for e in es) else None) if any('shots' in e for e in es) else '本版本未关联'] for kind in ('角球','任意球','界外球') for es in [[e for e in rows if e.get('kind')==kind]]],[130,60,140,165])
            for e in rows[:4]:
                contact=e.get('first_contact') or e.get('first_recorded_contact')
                text(f"{time(e)} {e.get('kind')} · {e.get('taker_name') or names.get(e.get('taker'),'未关联')} → 首记录触球 {(contact or {}).get('player_name') or names.get((contact or {}).get('player_id'),'未关联')} · {ref(e)}",'small')
            text('逐次明细与完整后续射门引用保留于同版 JSON；门球在出球统计中单列。','small')

        new(t['name']+' · 角色、压迫与联赛背景')
        fields=[('possessionPercentage','控球率 %'),('totalLongBalls','长传尝试'),('accurateLongBalls','长传成功'),('totalCross','传中尝试'),('accurateCross','传中成功'),('aerialWon','争顶成功'),('aerialLost','争顶失败'),('touchesInOppBox','对方禁区触球'),('totalAttAssist','射门助攻'),('totalTackle','抢断'),('interception','拦截'),('ballRecovery','夺回球权'),('saves','扑救'),('totalClearance','解围')]
        metrics={x['key']:x for x in t.get('official_metrics',[])}
        table(['官方整场指标','每场平均','比赛覆盖'],[[label,metrics[key].get('mean'),metrics[key].get('coverage')] for key,label in fields if key in metrics],[295,100,100])
        press=[('official_ppda','PPDA'),('high_regains_proxy','前场抢回'),('counterpress_regains_within_5s_proxy','丢球后5秒内抢回'),('losses_followed_by_opponent_shot_within_15s_proxy','丢球后15秒内被射门')]
        table(['指标','逐场值','覆盖'],[[label,' / '.join(number(m.get('pressing',{}).get(key)) for m in t.get('analyst_matches',[])),sum(m.get('pressing',{}).get(key) is not None for m in t.get('analyst_matches',[]))] for key,label in press],[230,205,60])
        if t.get('league_reference'):
            league_labels=dict(fields,accurateGoalKicks='门球成功',goalKicks='门球',duelLost='对抗失败',duelWon='对抗成功',ontargetScoringAtt='射正',totalScoringAtt='射门')
            table(['联赛背景指标','联赛场均','球队数','本队数值分位 %'],[[league_labels.get(x['key'],x['key']),x.get('mean'),x.get('teams'),x.get('value_percentile')] for x in t['league_reference']],[220,90,70,115])
            text('联赛背景为截止日前赛季球队场均，与近期窗口不同；数值分位不自动代表表现优劣。','small')
        text('球员按职责检查：推进、创造、终结及防守','heading')
        players=sorted(t['players'],key=lambda p:-(p.get('minutes') or 0))[:18]
        table(['球员／角色','分钟','推进/90','xG/90','xA','xT/90'],[[p['name']+' / '+str(p.get('position') or '未识别'),p.get('minutes'),p.get('per90',{}).get('progressive'),p.get('per90',{}).get('xg'),p.get('xa'),p.get('per90',{}).get('model_xt')] for p in players],[225,50,60,55,50,55])
        text('按分钟列出最多18人；不足180分钟需谨慎。xA 保留官方整场来源与覆盖，不分摊到选区。全部已存官方字段见同版 JSON。','small')

    evidence=r.get('evidence') or {}
    timeline=evidence.get('markers',[])
    if timeline:
        new('比赛记事时间轴')
        kind={16:'进球',17:'牌',18:'换下',19:'换上',30:'阶段结束',32:'阶段开始'}
        for period in sorted({e['period'] for e in timeline}):
            events=sorted([e for e in timeline if e['period']==period],key=lambda x:(x['minute'],x['second']))
            text('阶段 '+str(period),'heading')
            lo={1:0,2:45,3:90,4:105}.get(period,0)
            hi=max(lo+(45 if period in (1,2) else 15),max(e['minute']+e['second']/60 for e in events));span=max(1,hi-lo)
            lane_ends=[[],[]];placed=[]
            for i,e in enumerate(events,1):
                side=0 if e.get('team_id')==r['match']['home_id'] else 1;x=25+445*(e['minute']+e['second']/60-lo)/span
                lane=next((j for j,end in enumerate(lane_ends[side]) if x-end>=13),len(lane_ends[side]))
                if lane==len(lane_ends[side]):lane_ends[side].append(x)
                else:lane_ends[side][lane]=x
                placed.append((i,side,x,lane))
            height=105+26*max(len(lane_ends[0]),len(lane_ends[1]));center=height/2;d=Drawing(495,height)
            d.add(Line(25,center,470,center,strokeColor=grid))
            for minute in (lo,lo+span/2,hi):
                x=25+445*(minute-lo)/span
                d.add(String(x-6,center-3,number(round(minute,1)),fontSize=6,fillColor=muted))
            for i,side,x,lane in placed:
                y=center+(16+lane*13)*(1 if side==0 else -1);color=colors.HexColor(palette[side])
                d.add(Line(x,center,x,y,strokeColor=color));d.add(Circle(x,y,2.4,fillColor=color,strokeColor=color));d.add(String(x+5,y-2,str(i),fontSize=7,fillColor=color))
            story.append(d)
            table(['序号','时间','事件','球员'],[[i,time(e),e.get('kind') or kind.get(e.get('event_type'),'记录事件'),e.get('player_name') or names.get(e.get('player_id'),'未关联')] for i,e in enumerate(events,1)],[45,100,100,250])
        text('编号按阶段独立排序；时间轴事件可与本报告的分段表、机会链及同版 JSON 对照。','small')

    contests=evidence.get('contests',[])
    if contests:
        new('球员对抗关系 · 已配对去重')
        groups=defaultdict(lambda:{'n':0,'won':0,'lost':0,'unknown':0})
        for c in contests:
            a,b=c['a'],c['b']
            if a.get('team_id')!=r['match']['home_id']:a,b=b,a
            key=(c['kind'],a.get('player_name') or names.get(a.get('player_id'),'未关联'),b.get('player_name') or names.get(b.get('player_id'),'未关联'))
            g=groups[key];g['n']+=1
            if c['kind']=='foul':g['unknown']+=1
            elif c['kind']=='take_on' and a.get('outcome') in (0,1):
                g['won' if a['outcome']==1 else 'lost']+=1
            elif a.get('outcome')==1 and b.get('outcome')==0:g['won']+=1
            elif a.get('outcome')==0 and b.get('outcome')==1:g['lost']+=1
            else:g['unknown']+=1
        table(['类型','主队球员','客队球员','次数','主成功','主失败','未定'],[[labels.get(k[0],k[0]),k[1],k[2],v['n'],v['won'],v['lost'],v['unknown']] for k,v in sorted(groups.items(),key=lambda kv:-kv[1]['n'])],[85,125,125,40,40,40,40])
        text(f'共{len(contests)}次已配对且去重的对抗；未配对事件不进入矩阵，覆盖见证据 JSON。犯规关系不判为对抗胜负；争顶结果冲突保留未定。过人/防守动作分别记录，表列主队动作成功与失败，不以1减本方结果推算对手结果。','small')

    keeper=evidence.get('goalkeeper_shots',[])
    if keeper:
        new('门将射后机会质量与出球')
        for side,t in enumerate(teams):
            candidates=[e for e in keeper if e.get('team_id')!=t['id']]
            rows,unclassified=legacy_on_target_shots(candidates,teams)
            known=[e for e in rows if e.get('xgot') is not None]
            text(t['name'],'heading')
            text(f"对手确认射正 {len(rows)}；射正xGOT 有值 {len(known)}；累计 {number(sum(e['xgot'] for e in known)) if known else '未提供'}；同覆盖记录进球 {sum(bool(e.get('goal')) for e in known)}。")
            text('先筛事件15/16，再用同快照逐事件结果排除封堵。事件15同时包含扑救和封堵，不能依据数字outcome或xGOT=0判断；未能确认的事件不计入射正覆盖，完整射门仍在同版JSON。','small')
            if unclassified:text(f'事件15中{len(unclassified)}条缺少可关联结果，暂不计入确认射正。','small')
            table(['射正球员','时间','xGOT','进球'],[[e.get('player_name') or names.get(e.get('player_id'),'未关联'),time(e),e.get('xgot'),'是' if e.get('goal') else '否'] for e in rows],[245,100,75,75])
            keepers=[p for p in t['players'] if p.get('position') in ('Goalkeeper','GK')]
            for p in keepers:
                text(p['name']+' · 官方出球／扑救统计','heading')
                keys=[('totalPass','传球'),('accuratePass','传球成功'),('totalLongBalls','长传'),('accurateLongBalls','长传成功'),('goalKicks','门球'),('accurateGoalKicks','门球成功'),('saves','扑救'),('totalSaves','扑救'),('goodHighClaim','高球接稳')]
                values=[]
                for key,label in keys:
                    vs=[m['stats'][key] for m in p.get('official_by_match',[]) if m.get('stats',{}).get(key) is not None]
                    if vs:values.append([label,sum(vs),len(vs)])
                table(['指标','已记录合计','场次覆盖'],values,[295,100,100])


def register_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    paths = [os.environ.get('STUDIO_REPORT_CJK_FONT', ''),
             '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
             '/usr/share/fonts/truetype/arphic/uming.ttc']
    font = next((p for p in paths if p and Path(p).is_file()), None)
    if not font:
        raise ValueError('PDF 需要可嵌入中文字体，请配置 STUDIO_REPORT_CJK_FONT')
    if 'AnalystCJK' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('AnalystCJK', font, subfontIndex=0))
    return 'AnalystCJK'


def render(value, path, *, compact=True, profile="reader"):
    if profile not in ("reader","evidence"):raise ValueError("invalid_pdf_profile")
    if profile=="evidence":
        from .analyst_pdf_evidence import render_evidence
        return render_evidence(value,path)
    from reportlab.pdfbase import pdfmetrics
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
    from reportlab.graphics.shapes import Drawing, Rect, Line, Circle, String, Path
    font = register_font()
    styles={k:ParagraphStyle(k,fontName=font,fontSize=size,leading=size*1.55,spaceAfter=10,textColor=colors.HexColor('#263445'),wordWrap='CJK') for k,size in [('title',23),('heading',16),('body',10),('small',8)]}
    styles['heading'].keepWithNext=True
    r=value['report']; story=[]; palette=['#b42338','#7037a7']
    def p(text,style='body',verbatim=False):return Paragraph(escape(str(text) if verbatim else reader_text(text)).replace('\n','<br/>'),styles[style])
    general_notes=set()
    def text(text,style='body',verbatim=False):
        if not verbatim and str(text).startswith('比较限于本快照的比赛、阶段、人数及比分口径'):
            if 'scope' in general_notes:return
            general_notes.add('scope')
        story.append(p(text,style,verbatim))
    def number(n):return '未提供' if n is None else f'{n:.2f}' if isinstance(n,float) else str(n)
    def table(headers,rows,widths=None,keep=False):
        data=[[p(x,'small') for x in headers]]+[[p(number(x),'small') for x in row] for row in rows]
        t=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#f0f3f7')),('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#dce2e9')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        t.spaceAfter=12
        story.append(KeepTogether([t]) if keep else t)
    chapter_count=[0]
    def new(title):
        if not compact or title.startswith('证据附录 ·'):
            story.append(PageBreak())
        else:story.append(Spacer(1,12))
        chapter_count[0]+=1
        heading=p(title,'heading');heading._bookmark=(f'chapter-{chapter_count[0]}',reader_text(title));story.append(heading)
    def pitch(shots,sequence=None):
        width,height=495,335;d=Drawing(width,height);sx=4.5;sy=2.88;ox=22;oy=25
        def xy(x,y):return ox+x*sx,oy+y*sy
        line=colors.HexColor('#81948b')
        d.add(Rect(ox,oy,450,288,fillColor=colors.HexColor('#f7faf8'),strokeColor=line,strokeWidth=.7))
        d.add(Line(*xy(50,0),*xy(50,100),strokeColor=line,strokeWidth=.7))
        radius=100/11.475*sx
        d.add(Circle(*xy(50,50),radius,strokeColor=line,fillColor=None,strokeWidth=.7))
        d.add(Circle(*xy(50,50),1.5,strokeColor=line,fillColor=line))
        from math import cos,sin
        for cx,cy,start in ((0,0,0),(100,0,pi/2),(100,100,pi),(0,100,3*pi/2)):
            radius_corner=450/105
            points=[(xy(cx,cy)[0]+radius_corner*cos(start+j*pi/40),xy(cx,cy)[1]+radius_corner*sin(start+j*pi/40)) for j in range(21)]
            for a,b in zip(points,points[1:]):d.add(Line(*a,*b,strokeColor=line,strokeWidth=.7))
        for away in (False,True):
            x=83 if away else 0
            d.add(Rect(*xy(x,21.1),17*sx,57.8*sy,strokeColor=line,fillColor=None,strokeWidth=.7))
            d.add(Rect(*xy(94.2 if away else 0,36.8),5.8*sx,26.4*sy,strokeColor=line,fillColor=None,strokeWidth=.7))
            spot=88.5 if away else 11.5
            d.add(Circle(*xy(spot,50),1.7,strokeColor=line,fillColor=line))
            d.add(Rect(*xy(100 if away else -1.5,44.62),1.5*sx,10.76*sy,strokeColor=line,fillColor=None,strokeWidth=.7))
            # Only the arc outside the penalty area is drawn.
            from math import cos,sin,acos
            theta=acos((17-11.5)/(100/11.475))
            points=[]
            for j in range(41):
                a=-theta+2*theta*j/40
                xx=(spot*sx+radius*cos(a)) if not away else (spot*sx-radius*cos(a))
                points.append((ox+xx,oy+50*sy+radius*sin(a)))
            for a,b in zip(points,points[1:]):d.add(Line(*a,*b,strokeColor=line,strokeWidth=.7))
        for side,c in shots:
            if c.get('x') is None or c.get('y') is None:continue
            x,y=(c['x'],c['y']) if side==0 else (100-c['x'],100-c['y'])
            if c.get('xg') is None or c['xg']<=0:continue
            color=colors.HexColor(palette[side]);radius=35*sqrt(c['xg']/pi)*.65
            d.add(Circle(*xy(x,y),radius,fillColor=color if c.get('goal') else None,strokeColor=color,strokeWidth=1.2))
        for index,e in enumerate(sequence or []):
            if e.get('x') is None or e.get('y') is None:continue
            side=e.get('_display_side',0 if e.get('team_id')==r['match']['home_id'] else 1)
            if side is None:continue
            x,y=(e['x'],e['y']) if side==0 else (100-e['x'],100-e['y'])
            color=colors.HexColor(palette[side]);a=xy(x,y)
            if e.get('event_type') in (1,2) and e.get('end_x') is not None and e.get('end_y') is not None:
                ex,ey=(e['end_x'],e['end_y']) if side==0 else (100-e['end_x'],100-e['end_y'])
                d.add(Line(*a,*xy(ex,ey),strokeColor=color,strokeWidth=1))
            d.add(Circle(*a,3,fillColor=color,strokeColor=color));d.add(String(a[0]+3,a[1]+3,str(index+1),fontSize=7,fillColor=color))
        d.add(String(ox,8,'Away attacks left / Home attacks right',fontSize=8,fillColor=colors.HexColor('#64748b')))
        return d
    text(value['title'],'title')
    text('阅读顺序：关键判断 → 阵容变化 → 射门与区域对位 → 关键机会链 → 判断核验。后附专项图表与证据摘要；PDF 书签可直接跳转各章。','small')
    text(('已审核' if value['status']=='reviewed' else '待审核初稿')+f" · 编辑版本 {value['revision']} · {r['analysis_mode']}")
    text('资料截止时间：'+reader_time(r['cutoff_at']),'small')
    if r.get('metric_coverage'):
        text('比赛统计：CC Opta；预计首发与伤停：VIP 快照。','small')
        text('xA采用CC保存的Opta官方数据，按整场统计。整场值不拆分到某个区域或时段；详细来源见附录。','small')
    text('重点关注哪些优势与风险','heading')
    for h in r['hypotheses']:
        edit=value.get('judgments',{}).get(h['id'],{})
        text(h['title'],'heading');text('已撤回：'+edit['reason'] if edit.get('withdrawn') else edit.get('text') or h['text'],verbatim=bool(edit.get('withdrawn') or edit.get('text')));text(h['condition']);text(h['limitation'],'small')
        if h.get('personnel'):text(h['personnel'],'small')
        if h.get('basis'):text(h['basis'],'small')
        text(f"相关记录：支持 {len(h.get('events',[]))} 条；需要同时考虑 {len(h.get('counter_events',[]))} 条；其他进攻方式 {len(h.get('alternative_events',[]))} 条。具体事件和编号见附录。",'small')
        if edit.get('video_note'):text('录像观察：'+edit['video_note'],verbatim=True)
    if value['editorial_notes']:
        text('分析师修订与录像观察','heading');text(value['editorial_notes'],verbatim=True)
    new('指标怎么读')
    for title,description in GLOSSARY:
        text(title,'heading');text(description)
    render_lineup_layout(r,story,text,table,new,pitch,font,palette)
    new('阵容与人员变化')
    for team,side in zip(r['teams'],('home','away')):
        if side=='away':new('阵容与人员变化 · 客队')
        text(team['name'],'heading')
        roster=next((t for t in r.get('lineup',{}).get('teams',[]) if t['side']==side),{})
        change=next((t for t in r['lineup_changes']['teams'] if t['side']==side),{})
        text('新增：'+('、'.join(p['name'] for p in change.get('added',[])) if change.get('comparable') else '没有可比较的此前名单'))
        text('退出：'+('、'.join(p['name'] for p in change.get('removed',[])) if change.get('comparable') else '无法比较'))
        before=next((t for t in (r.get('lineup_baseline') or {}).get('teams',[]) if t.get('side')==side),{})
        text('此前名单：'+'、'.join(p['name'] for p in before.get('players',[])),'small')
        text('当前名单：'+'、'.join(p['name'] for p in roster.get('players',[])),'small')
        impact=next((x for x in r.get('lineup_impacts',[]) if x['team_id']==team['id']),None)
        if impact:
            for note in impact['role_observations']:text(note,'small')
            table(['变化','球员','历史分钟','推进/90','直接机会'],[[label,p['name'],p['minutes'],p.get('per90',{}).get('progressive'),p.get('direct_chances')] for label,ps in [('进入',impact['added']),('退出',impact['removed'])] for p in ps],[45,165,90,90,105])
        table(['球员','角色','样本分钟','xA','xT'],[[x['name'],(x.get('profile') or {}).get('position','未关联'),(x.get('profile') or {}).get('minutes'),(x.get('profile') or {}).get('xa'),(x.get('profile') or {}).get('model_xt')] for x in roster.get('players',[])],[155,90,90,75,85],keep=True)
    new('射门位置与机会质量')
    shots=[(i,c) for i,t in enumerate(r['teams']) for c in t['chains'] if c['direction']=='attack']
    story.append(pitch(shots));text('未进球空心，进球实心；圆面积与已记录 xG 成正比。缺失／零 xG 不绘制虚构面积。')
    table(['球队','射门','xG','已记录 xG','被创造 xG'],[[t['name'],t['attack']['shots'],t['attack']['xg'],t['attack']['known_xg'],t['defence']['xg']] for t in r['teams']],[155,70,90,90,90])
    if r['phase']=='postmatch' and all(c.get('elapsed_ms') is not None for _,c in shots):
        new('累计 xG · 本场过程')
        d=Drawing(495,265);max_x=max([c['elapsed_ms']/60000 for _,c in shots]+[90]);max_y=max([t['attack']['xg'] or 0 for t in r['teams']]+[1])
        def pos(x,y):return 38+430*x/max_x,35+195*y/max_y
        for tick in range(5):
            y=max_y*tick/4;d.add(Line(*pos(0,y),*pos(max_x,y),strokeColor=colors.HexColor('#dce2e9')))
            d.add(String(4,pos(0,y)[1]-3,f'{y:.2f}',fontSize=8))
        for side,t in enumerate(r['teams']):
            total=0;last=(0,0);color=colors.HexColor(palette[side]);missing=False
            for _,c in sorted([v for v in shots if v[0]==side],key=lambda v:v[1]['elapsed_ms']):
                x=c['elapsed_ms']/60000
                if c['xg'] is None:missing=True;last=(x,total);continue
                if not missing:d.add(Line(*pos(*last),*pos(x,total),strokeColor=color,strokeWidth=1.6))
                before=total;total+=c['xg'];d.add(Line(*pos(x,before),*pos(x,total),strokeColor=color,strokeWidth=1.6));last=(x,total);missing=False
            d.add(String(38+side*235,8,t['name'],fontName=font,fontSize=9,fillColor=color))
        for tick in range(5):
            x=max_x*tick/4;d.add(String(pos(x,0)[0]-8,22,f'{x:.0f}',fontSize=8))
        story.append(d);text('横轴：累计比赛分钟（含补时）；纵轴：已记录 xG。含点球，不含点球大战。缺失数据断开。','small')
    new('区域对位 · 最后传球的来源')
    for side,t in enumerate(r['teams']):
        if side:new('区域对位 · 客队进攻视角')
        other=r['teams'][1-side]
        baseline=next((x for x in r.get('regional_baseline') or [] if x['id']==t['id']),None)
        current=next((x for x in r.get('regional_current') or [] if x['id']==t['id']),None)
        if r['phase']=='postmatch' and (not baseline or not baseline.get('samples') or not current or (r.get('regional_baseline_info') or {}).get('status')!='available'):
            text('历史基准缺失或口径不可比较，暂不绘制零值图。');continue
        a=[c for c in (current if r['phase']=='postmatch' else t)['chains'] if c['direction']=='attack']
        b=[c for c in (baseline if r['phase']=='postmatch' else other)['chains'] if c['direction']==('attack' if r['phase']=='postmatch' else 'defence')]
        if r['phase']=='postmatch':text((r.get('regional_baseline_info') or {}).get('note','')+'；常规时间、11对11、非点球口径。','small')
        labels=[t['name']+('本场' if r['phase']=='postmatch' else '进攻'),t['name']+'赛前样本' if r['phase']=='postmatch' else other['name']+'防守']
        text(' / '.join(labels),'heading')
        maximum=max([sum(c.get('creator_zone')==z for c in g) for g in (a,b) for z in range(15)]+[1])
        d=Drawing(495,150)
        for i,g in enumerate((a,b)):
            for z in range(15):
                count=sum(c.get('creator_zone')==z for c in g);x=15+i*250+(z//5)*72;y=10+(z%5)*24
                base=colors.HexColor(palette[side]);alpha=count/maximum
                fill=colors.Color(1-(1-base.red)*alpha,1-(1-base.green)*alpha,1-(1-base.blue)*alpha)
                d.add(Rect(x,y,72,24,fillColor=fill,strokeColor=colors.white,strokeWidth=1))
                d.add(String(x+30,y+8,str(count),fontSize=9,fillColor=colors.white if alpha>.6 else colors.HexColor('#263445')))
            for j,label in enumerate(('后场','中场','前场')):d.add(String(28+i*250+j*72,138,label,fontName=font,fontSize=8))
        story.append(d);text(f'最后传球关联覆盖 {sum(c.get("creator_zone") is not None for c in a)}/{len(a)} 与 {sum(c.get("creator_zone") is not None for c in b)}/{len(b)}。五通道按进攻方视角自下而上：右边路、右肋部、中路、左肋部、左边路；同尺度，颜色代表次数。','small')
        zone_rows=[]
        for z in range(15):
            gs=[[c for c in g if c.get('creator_zone')==z] for g in (a,b)]
            if not any(gs):continue
            values=[]
            for g in gs:
                known=[c['xg'] for c in g if c.get('xg') is not None]
                values.append(str(len(g))+' / '+(number(sum(known)) if known else '未提供' if g else '0')+f' / {len(known)}/{len(g)}')
            zone_rows.append([['后场','中场','前场'][z//5]+' '+['右边路','右肋部','中路','左肋部','左边路'][z%5],*values])
        table(['区域','本方次数 / xG / 覆盖','对照次数 / xG / 覆盖'],zone_rows,[135,180,180])
    important=sorted(shots,key=lambda v:(not v[1].get('goal'),-(v[1].get('xg') or 0)))[:4]
    for i,c in important:
        new(('进球' if c['goal'] else '主要机会')+f" · {r['teams'][i]['name']} · 阶段 {c['period']} {c['minute']}:{c['second']:02}")
        from .analyst_pdf_layout import sequence_for_pitch
        text(c['method']+' · xG '+number(c['xg']));story.append(pitch([],sequence_for_pitch(c['sequence'],r['teams'][i]['id'],i)))
        text('按已记录动作编号；线段只表示有终点的传球，断点未补画。')
        names={p['id']:p['name'] for t in r['teams'] for p in t['players']}
        kinds={1:'传球',2:'越位传球',3:'过人',4:'犯规',7:'抢断',8:'拦截',12:'解围',13:'射偏',14:'门框',15:'射门被扑／封堵',16:'进球',17:'牌',44:'争顶',49:'夺回球权'}
        sequence=c['sequence'][:8]
        table(['步骤','时间','球员','动作'],[[j+1,f"{e['minute']}:{e['second']:02}",names.get(e.get('player_id'),'未关联'),kinds.get(e.get('event_type'),'其他记录动作')] for j,e in enumerate(sequence)],[45,65,235,150])
        if len(c['sequence'])>8:text(f"显示前 8 / {len(c['sequence'])} 步；完整序列见同版本证据导出。",'small')
        text('证据：'+c['match_id']+' / '+c['event_id'],'small')
    if r['checks']:new('赛前判断核验')
    else:text('赛前判断核验','heading')
    if not r['checks']:text('尚无可核验的已保存赛前判断。')
    for check in r['checks']:
        text(check['status']+' · '+check['title'],'heading');text(check['reason']);text(check['criterion'],'small')
        for label,key in [('支持过程','support_events'),('反驳过程','counter_events'),('其他路线（非反驳）','alternative_events')]:
            es=check.get(key,[])
            text(label+f'：{len(es)} 条；列出前 5 条，完整证据见 JSON。','small')
            for e in es[:5]:text(f"阶段 {e['period']} · {e['minute']}:{e['second']:02} · {e['match_id']} / {e['event_id']}",'small')
    new('证据附录 · 专项图表与完整球员统计')
    text('主报告到此完成；以下按专项组织详细图表和样本。使用 PDF 书签直接跳转，完整事件与来源回执在同版 JSON。')
    for h in r['hypotheses']:
        edit=value.get('judgments',{}).get(h['id'],{})
        text(h['title'],'heading')
        text('已撤回：'+edit['reason'] if edit.get('withdrawn') else edit.get('text') or h['text'],verbatim=bool(edit.get('withdrawn') or edit.get('text')))
        text(h.get('condition',''));text(h.get('limitation',''),'small')
        finding_evidence(h,text)
    text('推进、进攻方式与比分状态','heading')
    for side,t in enumerate(r['teams']):
        if side:new('推进、进攻方式与比分状态 · 客队')
        text(t['name'],'heading')
        table(['成功推进','进入前场','进入禁区','成功传球'],[[t['progression']['completed'],t['progression']['third_entries'],t['progression']['box_entries'],t['completed_passes']]],[125,125,125,120])
        table(['方式','射门','xG'],[[m['name'],m['shots'],m['xg']] for m in t['methods']],[275,110,110])
        table(['比分状态','分钟','射门','xG','xGA'],[[{'drawing':'平局','leading':'领先','trailing':'落后'}.get(m['name'],m['name']),m['minutes'],m['shots'],m['xg'],m['defence']['xg']] for m in t['states']],[115,100,80,100,100])
    new('球员贡献与定位球')
    for t in r['teams']:
        text(t['name'],'heading')
        table(['球员','分钟','xG','xA','xA覆盖场','xT','PV'],[[p['name'],p['minutes'],p['xg'],p['xa'],p.get('xa_match_count'),p.get('model_xt'),p.get('pv')] for p in t['players']],[145,55,55,55,55,65,65])
        for kind in ('角球','任意球','界外球','门球'):
            rs=[x for x in t['restarts'] if x['kind']==kind]
            text(f"{kind}：进攻 {sum(x['direction']=='attack' for x in rs)} / 防守 {sum(x['direction']=='defence' for x in rs)}；首次争顶识别 {sum(x['first_aerial_won'] is not None for x in rs)} 次。",'small')
    _append_analysis_sections(r, story, text, table, new, pitch, font, palette, number)
    append_detail_sections(r, story, text, table, new, pitch, font, palette, number)
    for note in r['notes']:text(note,'small')
    for reference in r.get('references',[]):text(reference['title']+'\n'+reference['url'],'small')
    text('xA 来源回执：同版证据 JSON 的逐球员 xa_sources 保存逐场比赛、来源、供给源、定义版本、值及可用的归档内容哈希；xa_match_count 为有值比赛覆盖。旧快照未记录覆盖时显示未提供，不把缺失当作0。','small')
    text('固定数据版本：'+r['revision'],'small')
    def footer(canvas,doc):
        canvas.setFont(font,8);canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawString(38,23,'CC Studio | '+('待审核' if value['status']!='reviewed' else '已审核')+f" | 编辑 v{value['revision']}")
        canvas.drawRightString(557,23,str(doc.page))
    class BookmarkedDoc(SimpleDocTemplate):
        def afterFlowable(self,flowable):
            if hasattr(flowable,'_bookmark'):
                key,title=flowable._bookmark
                self.canv.bookmarkPage(key);self.canv.addOutlineEntry(title,key,level=0,closed=False)
    if compact:
        from .analyst_pdf_layout import compact_report
        appendix=next(i for i,item in enumerate(story) if getattr(item,'_bookmark',(None,''))[1].startswith('证据附录 ·'))
        story=compact_report(value,font,pitch,palette)
    BookmarkedDoc(str(path),pagesize=(595,842),rightMargin=50,leftMargin=50,topMargin=38,bottomMargin=42,title=value['title']).build(story,onFirstPage=footer,onLaterPages=footer)
