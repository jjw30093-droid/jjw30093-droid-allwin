"""Independent evidence appendix from the same immutable report revision."""
import json
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate,Paragraph,Table,TableStyle,PageBreak,Spacer


def event_index(value):
    found={}
    def visit(obj):
        if isinstance(obj,dict):
            if obj.get('event_id') and obj.get('match_id'):
                key=(str(obj['match_id']),str(obj['event_id']))
                row=found.setdefault(key,{})
                for k in ('period','minute','second','player_id','player_name'):
                    if obj.get(k) is not None:row.setdefault(k,obj[k])
            for v in obj.values():visit(v)
        elif isinstance(obj,list):
            for v in obj:visit(v)
    visit(value['report'])
    return [(mid,eid,fields) for (mid,eid),fields in sorted(found.items())]


def render_evidence(value,path):
    from .analyst_pdf import register_font
    from .analyst_pdf_layout import reader_report
    font=register_font();r=reader_report(value['report']);story=[]
    styles={k:ParagraphStyle(k,fontName=font,fontSize=n,leading=n*1.4,spaceAfter=7,wordWrap='CJK') for k,n in [('title',18),('heading',13),('body',9)]}
    def p(s,style='body'):return Paragraph(escape(str(s)).replace('\n','<br/>'),styles[style])
    def table(headers,rows,widths):
        t=Table([[p(x) for x in headers]]+[[p(x) for x in row] for row in rows],colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#edf1f5')),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#dce2e9')),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]));story.append(t)
    story.extend([p('独立证据附录','title'),p(value['title']),p('本附录与阅读版使用相同的固定报告和编辑版本。内容用于追溯，不重复输出阅读版图表。')])
    meta={k:r.get(k) for k in ('phase','cutoff_at','revision','definition_version','workflow_version','filters','source_versions','quality','regional_baseline_info')}
    meta.update(draft_id=value.get('id'),editor_revision=value['revision'],review_status=value['status'])
    story.extend([p('报告范围、版本与质量','heading'),p(json.dumps(meta,ensure_ascii=False,indent=2))])
    for team in r['teams']:
        story.append(p(team['name']+' · 来源与模型','heading'))
        for entry in team.get('model_sources',[]):story.append(p(json.dumps(entry,ensure_ascii=False)))
        for player in team.get('players',[]):
            for entry in player.get('xa_sources',[]):story.append(p(player['name']+' · '+json.dumps(entry,ensure_ascii=False)))
        story.append(p('指标定义版本：'+json.dumps(team.get('definitions',[]),ensure_ascii=False)))
    for name in ('lineup','lineup_baseline'):
        ctx=r.get(name) or {};story.append(p('阵容来源 · '+name,'heading'))
        story.append(p(json.dumps({k:v for k,v in ctx.items() if k not in ('teams','players')},ensure_ascii=False,indent=2)))
        for team in ctx.get('teams',[]):
            story.append(p(str(team.get('side'))))
            for person in team.get('players',[]):story.append(p(person['name']+' · '+json.dumps(person.get('identity_link',{}),ensure_ascii=False)))
    story.extend([PageBreak(),p('判断依据与核验记录','title')])
    for h in r.get('hypotheses',[]):
        story.extend([p(h['title'],'heading'),p(h.get('text','')),p('条件：'+h.get('condition','')),p('限制：'+h.get('limitation',''))])
        for key in ('events','counter_events','alternative_events'):
            refs=h.get(key,[])
            if refs:table([key,'比赛编号','事件编号'],[[key,e.get('match_id',''),e.get('event_id','')] for e in refs],[85,205,205])
    for check in r.get('checks',[]):story.append(p(json.dumps(check,ensure_ascii=False)))
    for note in r.get('notes',[]):story.append(p(note))
    for ref in r.get('references',[]):story.append(p(json.dumps(ref,ensure_ascii=False)))
    names={x['id']:x['name'] for t in r['teams'] for x in t.get('players',[])}
    rows=event_index(value);story.extend([PageBreak(),p('完整事件编号索引','title'),p(f'按比赛编号与事件编号去重，共 {len(rows)} 条。所有动作字段及模型评分保留于同版本证据 JSON。')])
    from itertools import groupby
    for mid,items in groupby(rows,key=lambda x:x[0]):
        story.append(p('比赛编号：'+mid,'heading'))
        table(['完整事件编号','阶段／时间','球员'],[[eid,str(x.get('period',''))+' / '+str(x.get('minute',''))+':'+str(x.get('second','')),x.get('player_name') or names.get(x.get('player_id'),'未关联')] for _,eid,x in items],[220,65,210])
    def footer(canvas,doc):
        canvas.setFont(font,8);canvas.drawString(50,22,'CC Studio · 独立证据附录 · 编辑版本 '+str(value['revision']));canvas.drawRightString(545,22,str(doc.page))
    SimpleDocTemplate(str(path),pagesize=(595,842),leftMargin=50,rightMargin=50,topMargin=38,bottomMargin=42,title=value['title']+' · 证据附录').build(story,onFirstPage=footer,onLaterPages=footer)
