"""Approved six-page prematch reader, rendered from a frozen Studio report."""
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from xml.sax.saxutils import escape

from .analyst_threat import label as zone_label

W,H=595.28,841.89
M=36; GAP=22; CW=(W-2*M-GAP)/2
C=['#b42338','#7037a7']; INK='#172e3c'; MUTED='#607480'; RULE='#dce4e8'; LIGHT='#f3f6f8'
CHAPTERS=['进攻威胁','防守隐患','关键球员','失误风险','进攻来源','机会拆解']

def register_value_font():
    for candidate in (
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
    ):
        if Path(candidate).is_file():
            name = 'AnalystValueBold'
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, candidate))
            return name
    return 'Helvetica-Bold'

def sign(v,d=3): return '—' if v is None else f'{v:+.{d}f}'

def val(v,d=3): return '—' if v is None else f'{v:.{d}f}'

def pct(v):return '—' if v is None else f'{v*100:.1f}%'

def col(i):return M+i*(CW+GAP)

def blend(hexval,a):
    z=HexColor(hexval);base=HexColor('#f7f9fa')
    return Color(base.red+(z.red-base.red)*a,base.green+(z.green-base.green)*a,base.blue+(z.blue-base.blue)*a)

def luminance(color):
    rgb=[color.red,color.green,color.blue]
    linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
    return sum(v*weight for v,weight in zip(linear,[.2126,.7152,.0722]))

def heat_label_colors(background):
    lum=luminance(background)
    def contrast(hexval):
        other=luminance(HexColor(hexval))
        return (max(lum,other)+.05)/(min(lum,other)+.05)
    primary=max(['#111820','#ffffff'],key=contrast)
    secondary='#586b77' if contrast('#586b77')>=4.5 else primary
    return primary,secondary

def xy(x,y,w,h,p):return (x+p[0]*w/100,y+p[1]*h/100)


class ThreatReader:
    def __init__(self, data, pdf_canvas, font):
        self.teams = data['teams']
        names_zh = {
            'Manchester City': '曼城', 'Fulham': '富勒姆',
            'Liverpool': '利物浦', 'Manchester United': '曼联',
            'Chelsea': '切尔西', 'Leeds United': '利兹联',
            'Aston Villa': '阿斯顿维拉', 'Sunderland': '桑德兰',
        }
        self.team_names = [names_zh.get(team['name'], team['name']) for team in self.teams]
        self.match_name = ' vs '.join(self.team_names)
        self.kickoff = datetime.fromisoformat(data['match']['kickoff_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Shanghai'))
        self.font = font
        self.value_font = register_value_font()
        self.page = 0
        self.canvas = pdf_canvas

    def tx(self, x,y,s,size=9,color=INK,align='left',latin=False,bold=False):
        self.canvas.setFillColor(HexColor(color));self.canvas.setFont((self.value_font if bold else 'Helvetica') if latin else self.font,size)
        getattr(self.canvas,{'left':'drawString','right':'drawRightString','center':'drawCentredString'}[align])(x,y,str(s))

    def para(self, x,y,w,s,size=8.6,color=INK,leading=None):
        style=ParagraphStyle('p',fontName=self.font,fontSize=size,leading=leading or size*1.52,textColor=HexColor(color),wordWrap='CJK')
        p=Paragraph(escape(str(s)).replace('\n','<br/>'),style);_,h=p.wrap(w,H);p.drawOn(self.canvas,x,y-h);return y-h

    def line(self, x,y,w,color=RULE,lw=.6):
        self.canvas.setStrokeColor(HexColor(color));self.canvas.setLineWidth(lw);self.canvas.line(x,y,x+w,y)

    def rect(self, x,y,w,h,fill,stroke=None):
        self.canvas.setFillColor(HexColor(fill));self.canvas.setStrokeColor(HexColor(stroke or fill));self.canvas.rect(x,y,w,h,fill=1,stroke=bool(stroke))

    def panel(self, x,y,w,h,fill=LIGHT,r=0):
        self.canvas.setFillColor(HexColor(fill));self.canvas.roundRect(x,y,w,h,r,stroke=0,fill=1)

    def begin(self, title,subtitle):
        self.page+=1
        self.tx(M,808,'喵弟量化研究所',10,INK)
        self.canvas.setStrokeColor(HexColor(RULE));self.canvas.setLineWidth(.6);self.canvas.line(M+87,799,M+87,821)
        self.tx(M+96,814,'PREMATCH REPORT',7.0,MUTED,latin=True)
        chapter = CHAPTERS[self.page-1]
        self.tx(M+96,802,chapter,8.2,INK)
        self.tx(W-M,814,self.match_name,9,INK,'right')
        self.tx(W-M,802,self.kickoff.strftime('%Y.%m.%d  %H:%M')+' 北京时间',7.2,MUTED,'right')
        self.line(M,789,W-2*M,RULE,.65)
        self.tx(M,765,title,18,INK)
        self.para(M,749,W-2*M,subtitle,8.7,MUTED,12.2)
        self.line(M,45,W-2*M)
        self.tx(M,30,'近5场联赛 · 11人对11人 · 全部主客场 · 历史样本回看',7.2,MUTED)
        if any(team['matches'] < team['requested'] for team in self.teams):
            coverage = '，'.join(f"{name} {team['matches']}/{team['requested']}场"
                                 for name, team in zip(self.team_names, self.teams))
            self.tx(M,17,'PV覆盖：'+coverage+'。按各队有评分的样本分钟计算。',7.2,MUTED)
        page_label = f'{self.page:02d} / 06'
        self.tx(W-M,30,page_label,8,INK,'right',latin=True)
        self.canvas.bookmarkPage(f'threat-{self.page}');self.canvas.addOutlineEntry(title,f'threat-{self.page}',0)

    def team_head(self, i,y,subtitle=None):
        x=col(i);self.tx(x,y,self.team_names[i],12,C[i])
        if self.team_names[i] != self.teams[i]['name']:
            self.tx(x + pdfmetrics.stringWidth(self.team_names[i], self.font, 12) + 9,
                    y, self.teams[i]['name'], 7.3, MUTED, latin=True)
        self.line(x,y-10,CW,RULE,.7)
        if subtitle:self.tx(x,y-26,subtitle,7.8,MUTED)

    def section(self, x,y,title,w=CW,color=INK):
        self.tx(x,y,title,10,color);self.line(x,y-8,w,'#9aa7af',.65);return y-25

    def pitch(self, x,y,w,h,quiet=False):
        self.canvas.saveState();self.canvas.setStrokeColor(HexColor('#b6c1bd' if quiet else '#879a94'));self.canvas.setLineWidth(.35 if quiet else .55)
        self.canvas.setFillColor(HexColor('#879a94'))
        self.canvas.rect(x,y,w,h,fill=0,stroke=1);self.canvas.line(x+w/2,y,x+w/2,y+h)
        self.canvas.circle(x+w/2,y+h/2,w*9.15/105,stroke=1,fill=0)
        for side in (0,1):
            edge=x+side*w;sgn=1 if side==0 else -1
            self.canvas.rect(edge if side==0 else edge-w*16.5/105,y+h*(1-40.32/68)/2,w*16.5/105,h*40.32/68,stroke=1,fill=0)
            self.canvas.rect(edge if side==0 else edge-w*5.5/105,y+h*(1-18.32/68)/2,w*5.5/105,h*18.32/68,stroke=1,fill=0)
            if not quiet:self.canvas.circle(edge+sgn*w*11/105,y+h/2,.75,fill=1,stroke=0)
            self.canvas.line(edge,y+h*(1-7.32/68)/2,edge,y+h*(1+7.32/68)/2)
        self.canvas.restoreState()

    def arrow(self, a,b,color,width=1,dash=False):
        dx=b[0]-a[0];dy=b[1]-a[1];le=math.hypot(dx,dy)
        if le<1:return
        ux,uy=dx/le,dy/le
        self.canvas.saveState();self.canvas.setStrokeColor(HexColor(color));self.canvas.setFillColor(HexColor(color));self.canvas.setLineWidth(width)
        if dash:self.canvas.setDash(2.5,2)
        self.canvas.line(*a,*b);self.canvas.setDash()
        self.canvas.line(*b,b[0]-4.5*ux+2*uy,b[1]-4.5*uy-2*ux)
        self.canvas.line(*b,b[0]-4.5*ux-2*uy,b[1]-4.5*uy+2*ux)
        self.canvas.restoreState()

    def dot(self, x,y,text,color,r=6):
        self.canvas.setFillColor(HexColor(color));self.canvas.circle(x,y,r,fill=1,stroke=0);self.tx(x,y-2.2,text,6.3,'#ffffff','center')

    def heatmap(self, x,y,w,g,side,vmax,h=None,small=False,defending=False,counts=False):
        h=h or w*68/105;vs=g['values'];ns=g['counts']
        cell_colors=[]
        for z in range(15):
            a=z//5;b=z%5;v=vs[z];intensity=min(1,v/vmax) if vmax else 0
            cell_colors.append(blend(C[side],intensity*.86))
            self.canvas.setFillColor(cell_colors[-1])
            self.canvas.rect(x+w*a/3,y+h*b/5,w/3,h/5,stroke=0,fill=1)
        self.canvas.saveState();self.canvas.setLineWidth(.35);self.canvas.setStrokeColor(HexColor('#d2dbdd'))
        for a in range(1,3):self.canvas.line(x+w*a/3,y,x+w*a/3,y+h)
        for b in range(1,5):self.canvas.line(x,y+h*b/5,x+w,y+h*b/5)
        self.canvas.restoreState();self.pitch(x,y,w,h,quiet=True)
        if not small:
            for z in range(15):
                a=z//5;b=z%5;v=vs[z];cx=x+w*(a+.5)/3;cy=y+h*(b+.5)/5
                primary,secondary=heat_label_colors(cell_colors[z])
                # An opaque, cell-coloured exclusion area keeps every field line
                # outside the complete value/count label, including its padding.
                label=f'{v:.3f}';size=11.3 if counts else 11.8
                label_width=pdfmetrics.stringWidth(label,self.value_font,size)+10
                label_bottom=cy-11 if counts else cy-7
                label_height=24 if counts else 17
                assert label_width < w/3-4 and label_height < h/5-2
                self.canvas.setFillColor(cell_colors[z]);self.canvas.rect(cx-label_width/2,label_bottom,label_width,label_height,stroke=0,fill=1)
                self.tx(cx,cy+(1.5 if counts else -4),label,size,primary,'center',latin=True,bold=True)
                if counts:self.tx(cx,cy-8.5,f'{ns[z]}次',6.3,secondary,'center')
            for b,name in enumerate(['右路','右肋','中路','左肋','左路']):self.tx(x-4,y+h*(b+.5)/5-2,name,6.2,MUTED,'right')
            for a,name in enumerate(['后场','中场','前场']):self.tx(x+w*(a+.5)/3,y-13,name,6.6,MUTED,'center')
        self.tx(x+w,y+h+7,'对手进攻 ←' if defending else '本队进攻 →',6.8,MUTED,'right')
        return h

    def legend(self, x,y,w,side,vmax,unit='威胁值（正PV/90分钟）'):
        bw=10
        for j in range(6):
            self.canvas.setFillColor(blend(C[side],j/5*.86));self.canvas.rect(x+j*bw,y,bw,5,stroke=0,fill=1)
        self.tx(x,y-10,'0',6,MUTED);self.tx(x+60,y-10,f'{vmax:.2f}',6,MUTED,'right');self.tx(x+67,y-3,unit,6.8,MUTED)

    def insight(self, y,s):
        self.rect(M,y-44,W-2*M,44,LIGHT)
        self.rect(M,y-44,2,44,'#889aa4')
        self.para(M+12,y-10,W-2*M-24,s,9,INK,13.6)

    def finish(self):
        self.canvas.showPage()

    def page_attack(self):
        lead = max(range(2), key=lambda i: self.teams[i]['per90']['positive'])
        other = 1 - lead
        a, b = (self.teams[i]['per90'] for i in (lead, other))
        name = self.team_names[lead]
        subtitle = f"{name}制造威胁每90分钟{a['positive']:.3f}，对手{b['positive']:.3f}。"
        if a['positive'] == b['positive']:
            subtitle = f"两队每90分钟制造威胁均为{a['positive']:.3f}。"
        subtitle += f"扣除各自损耗后，威胁净值相差{abs(a['net'] - b['net']):.3f}。"
        self.begin('谁的进攻威胁更大，主要从哪边发起', subtitle)
        vmax=max(v for t in self.teams for v in t['attack_grid']['values'])
        for i,t in enumerate(self.teams):
            x=col(i);self.team_head(i,710)
            for j,(label,key) in enumerate([('制造威胁','positive'),('进攻损耗','negative'),('威胁净值','net')]):
                xx=x+j*CW/3;self.tx(xx,678,label,8,MUTED);self.tx(xx,657,sign(t['per90'][key]),18,C[i] if key!='negative' else MUTED,latin=True)
            self.tx(x,637,f"PV /90分钟 · 统计{t['minutes']:.1f}分钟 · {t['totals']['count']:,}次已评分动作",7.1,MUTED)
            self.section(x,616,'威胁主要来自哪里',CW)
            self.heatmap(x+15,433,CW-15,t['attack_grid'],i,vmax,counts=True)
            self.legend(x+15,406,CW-15,i,vmax,'大字：正PV/90 · 小字：次数')
            z=max(range(15),key=lambda z:t['attack_grid']['values'][z]);share=t['attack_grid']['values'][z]/t['per90']['positive'] if t['per90']['positive'] else 0
            self.para(x,383,CW,f"最有威胁的区域：{['后场','中场','前场'][z//5]}{['右路','右肋','中路','左肋','左路'][z%5]}，贡献全队正PV的{share:.1%}。",8.6)
            routes = t['routes']
            title = f'威胁最高的{len(routes)}条推进路线' if routes else '推进路线'
            self.section(x,342,title,CW)
            if not routes:
                self.para(x,305,CW,'所选样本没有可比较的 xT 路线。',8.6)
                continue
            px,py,pw=x+15,164,CW-15;ph=pw*68/105
            self.rect(px,py,pw,ph,'#f7f9f8');self.pitch(px,py,pw,ph)
            rs=max(r['per90'] for tt in self.teams for r in tt['routes'])
            for j,r in reversed(list(enumerate(routes))):
                a=xy(px,py,pw,ph,r['from_xy']);b=xy(px,py,pw,ph,r['to_xy'])
                self.arrow(a,b,C[i],.8+2*r['per90']/rs)
            label_positions=[]
            for j,r in enumerate(routes):
                a=xy(px,py,pw,ph,r['from_xy'])
                offsets = [(0, 0), (-13, 0), (13, 0), (0, -13), (0, 13)]
                offsets += [(dx, dy) for dx in (-26, -13, 0, 13, 26) for dy in (-26, -13, 0, 13, 26)]
                candidates=[(a[0]+dx,a[1]+dy) for dx,dy in offsets]
                candidates=[p for p in candidates if px+6<=p[0]<=px+pw-6 and py+6<=p[1]<=py+ph-6]
                p = next((p for p in candidates if all(math.dist(p, q) >= 13 for q in label_positions)), None)
                if p is None:
                    p = (px + 9, py + ph - 9 - j * 15)
                if math.dist(a,p)>1:
                    self.canvas.saveState();self.canvas.setStrokeColor(HexColor(C[i]));self.canvas.setLineWidth(.5);self.canvas.line(*a,*p);self.canvas.restoreState()
                self.dot(*p,str(j+1),C[i],5);label_positions.append(p)
            self.tx(px,py+ph+6,f"xT覆盖 {t['xt']['matches']}/{t['requested']}场",6.8,MUTED)
            self.tx(px+pw,py+ph+6,'进攻 →',6.8,MUTED,'right')
            for j,r in enumerate(routes):
                yy=152-j*12;self.tx(x,yy,f'{j+1}  '+r['label'],7.6);self.tx(x+CW,yy,f"{r['per90']:.3f}  ·  {r['count']}次",7.5,C[i],'right')
        self.panel(M,56,W-2*M,40)
        self.para(M+10,88,W-2*M-20,'读图：上图大字为正PV/90，小字为动作次数。下图按正xT/90展示前5条路线；箭头为正xT动作的平均起终点，次数包含该路线全部已评分动作，不代表连续配合。两种指标不能相加。',7.8,MUTED,11.5)
        self.finish()

    def page_defence(self):
        areas = []
        for i, team in enumerate(self.teams):
            values = team['against_grid']['values']
            idx = max(range(15), key=values.__getitem__)
            if values[idx]:
                areas.append(self.team_names[i] + '在' + zone_label(idx) + '承受的威胁最多')
        subtitle = '，'.join(areas) + '。' if areas else '所选样本的对手正PV为零。'
        self.begin('防守哪边容易出问题，本场能否被利用', subtitle)
        vmax=max(v for t in self.teams for v in t['against_grid']['values'])
        dmax=max(v for t in self.teams for v in t['against_delivery_grid']['values'])
        for i,t in enumerate(self.teams):
            x=col(i);self.team_head(i,710,f"承受威胁（正PV/90）：{t['against_per90']['positive']:.3f} · {t['against_count']:,}次动作")
            self.section(x,662,'对手在哪些位置制造威胁',CW)
            self.heatmap(x+15,482,CW-15,t['against_grid'],i,vmax,defending=True)
            self.legend(x+15,457,CW-15,i,vmax)
            self.section(x,420,'对手的威胁传球送到了哪里',CW)
            self.heatmap(x+15,247,CW-15,t['against_delivery_grid'],i,dmax,defending=True)
            self.legend(x+15,222,CW-15,i,dmax,'正PV/90 · 按传球落点统计')
            self.section(x,185,'进攻强项与对手防守弱点',CW)
            opp=self.teams[1-i];aa=t['attack_grid']['values'];bb=opp['against_attacking_grid']['values']
            high=max([.001]+[v for tt in self.teams for v in tt['attack_grid']['values'][10:]]+[v for tt in self.teams for v in tt['against_attacking_grid']['values'][10:]])
            self.tx(x+CW,169,'彩色：本队制造威胁  /  灰色：对手承受威胁',6.4,MUTED,'right')
            for j,z in enumerate([14,13,12,11,10]):
                yy=153-j*16;self.tx(x,yy,['右路','右肋','中路','左肋','左路'][z%5],7.5)
                bx=x+32;bw=CW-111
                self.rect(bx,yy+1,bw*aa[z]/high,3.8,C[i]);self.rect(bx,yy-4,bw*bb[z]/high,3.8,'#a5b1b8')
                self.tx(x+CW,yy-1,f'{aa[z]:.3f} / {bb[z]:.3f}',7.2,INK,'right',True)
        self.para(M,73,W-2*M,'怎么看：防守图本方球门在左，色深表示该区域承受的威胁多。下方从进攻方看：右肋对应对手左侧；灰条越长，对手承受威胁越多。对位是本场观察线索，尚未校正对手强弱。',7.5,MUTED,10.5)
        self.finish()

    def page_players(self):
        leaders = [team['players'][0]['name'] for team in self.teams if team['players']]
        subtitle = '、'.join(leaders) + '的威胁净值居队内首位。' if leaders else '球员榜要求至少180分钟。'
        self.begin('进攻重点看谁，威胁来自哪些位置', subtitle)
        ps=[p for t in self.teams for p in t['players'][:6]]
        scale=max([abs(p['per90'][key]) for p in ps for key in ('positive','negative')]+[.01])
        for i,t in enumerate(self.teams):
            x=col(i);self.team_head(i,710)
            self.panel(x,672,CW,20)
            self.tx(x+6,680,'球员',7.4,MUTED);self.tx(x+160,680,'损耗 ← PV/90 → 威胁',6.7,MUTED,'center');self.tx(x+CW-5,680,'威胁净值',7.3,MUTED,'right')
            for j,p in enumerate(t['players'][:6]):
                yy=654-j*48;zero=x+147
                self.tx(x,yy,p['name']+(' *' if p['starter'] else ''),8.7)
                self.line(zero,yy-3,0)  # exact zero anchor below
                self.canvas.setStrokeColor(HexColor('#9caab2'));self.canvas.setLineWidth(.5);self.canvas.line(zero,yy-5,zero,yy+8)
                pos=p['per90']['positive']/scale*57;neg=-p['per90']['negative']/scale*57
                self.rect(zero,yy-1,pos,5,C[i]);self.rect(zero-neg,yy-1,neg,5,'#8b9ba5')
                self.tx(x+CW,yy,sign(p['per90']['net']),8.2,INK,'right',True)
                self.tx(x,yy-15,f"{p['minutes']:.0f}分钟 · xG/90 {val(p['xg90'],2)} · 整场xA/90 {val(p['xa90'],2)}",7.2,MUTED)
                self.tx(x,yy-28,f"每百次动作净PV {sign(p['per100'])} · 动作/90分钟 {p['actions90']:.1f}",7.0,MUTED)
                self.line(x,yy-37,CW)
            self.section(x,343,'制造威胁前两名：主要贡献区域',CW)
            top=sorted(t['players'],key=lambda p:-p['per90']['positive'])[:2]
            gmax=max((v for tt in self.teams for p in sorted(tt['players'],key=lambda p:-p['per90']['positive'])[:2] for v in p['zones']['values']), default=0)
            for j,p in enumerate(top):
                xx=x+j*(CW+10)/2;ww=(CW-10)/2
                self.tx(xx,310,p['name'],8.2,C[i]);self.tx(xx,296,f"制造威胁/90  {p['per90']['positive']:.3f}",7.5,MUTED)
                self.heatmap(xx,208,ww,p['zones'],i,gmax,small=True)
                self.para(xx,195,ww,f"主要区域：{p['zone_label']}\n占个人正PV {p['zone_share']:.1%}",7.7,INK,12)
            if not t['players']:
                self.para(x,645,CW,'没有达到180分钟的球员。',8.5)
                continue
            p=t['players'][0]
            self.legend(x,160,CW,i,gmax,'正PV/90 · 四图共用刻度')
            self.para(x,136,CW,f"赛前关注：{p['name']}是否首发，以及常用搭档是否变化。威胁贡献高，不代表一定进球或助攻。",8.3)
        self.para(M,87,W-2*M,'仅展示统计时间不少于180分钟的球员，主表按净PV/90排序。*沿用原稿首发标记，不表示阵容变化。xG按筛选后的比赛片段统计；整场xA按所覆盖比赛的官方出场时间折算。',7.7,MUTED,11)
        self.finish()

    def marker(self, x,y,position,color,size=3):
        self.canvas.setFillColor(HexColor(color));self.canvas.setStrokeColor(HexColor(color))
        if position=='Defender':
            p=self.canvas.beginPath();p.moveTo(x,y+size);p.lineTo(x-size,y-size);p.lineTo(x+size,y-size);p.close();self.canvas.drawPath(p,fill=1,stroke=0)
        elif position=='Defensive Midfielder':self.canvas.rect(x-size,y-size,size*2,size*2,fill=1,stroke=0)
        else:self.canvas.circle(x,y,size,stroke=0,fill=1)

    def scatter(self, x,y,w,h,players,side,xmax,ymax):
        px=x+27;pw=w-37;py=y+21;ph=h-35
        for tick in [i * ymax / 4 for i in range(4)]:
            yy=py+ph*tick/ymax;self.line(px,yy,pw,RULE,.35);self.tx(px-5,yy-2.2,f'{tick:.2f}',6.5,MUTED,'right',True)
        for tick in [i * xmax / 5 for i in range(5)]:
            xx=px+pw*tick/xmax;self.tx(xx,py-12,f'{tick:.1f}',6.5,MUTED,'center',True)
        self.line(px,py,pw,'#84959f',.6)
        self.canvas.setStrokeColor(HexColor('#84959f'));self.canvas.line(px,py,px,py+ph)
        self.tx(px,py+ph+7,'传球失误代价（负PV绝对值/90）↑',7.1,MUTED)
        self.tx(px+pw,py-24,'制造威胁（正PV/90）→',7.3,MUTED,'right')
        named={p['id'] for p in sorted(players,key=lambda p:-p['failed_loss90'])[:2]+sorted(players,key=lambda p:-p['per90']['positive'])[:1]}
        boxes=[]
        for p in players:
            xx=px+pw*p['per90']['positive']/xmax;yy=py+ph*p['failed_loss90']/ymax
            self.marker(xx,yy,p['position'],C[side])
            if p['id'] not in named:continue
            text=p['name'];tw=pdfmetrics.stringWidth(text,self.font,6.6)
            choices=[(xx+5,yy+4),(xx-tw-5,yy+4),(xx+5,yy-12),(xx-tw-5,yy-12),(xx-tw/2,yy+13)]
            best=None
            for bx,by in choices:
                bx=min(max(bx,px+1),px+pw-tw-1);by=min(max(by,py+1),py+ph-9)
                box=(bx-2,by-2,bx+tw+2,by+8)
                if not any(box[0]<b[2] and box[2]>b[0] and box[1]<b[3] and box[3]>b[1] for b in boxes):best=(bx,by,box);break
            bx,by,box=best or (choices[0][0],choices[0][1],(0,0,0,0));boxes.append(box)
            self.rect(bx-1,by-1,tw+2,8,'#ffffff');self.tx(bx,by,text,6.6,INK)

    def page_risk(self):
        self.begin('哪些球员传球风险高，哪些前场威胁偏少','冒险传球可能带来机会，也可能增加失误；传丢次数、失败率与威胁损失要一起看。')
        players = [p for team in self.teams for p in team['players'] if p['position'] != 'Goalkeeper']
        xmax = max([.45] + [1.1 * p['per90']['positive'] for p in players])
        ymax = max([.17] + [1.1 * p['failed_loss90'] for p in players])
        for i,t in enumerate(self.teams):
            x=col(i);self.team_head(i,710)
            pp=[p for p in t['players'] if p['position']!='Goalkeeper']
            if not pp:
                self.para(x,670,CW,'没有达到180分钟的非门将球员。',8.5)
                continue
            self.scatter(x,478,CW,205,pp,i,xmax,ymax)
            self.tx(x,456,'○ 前腰/前锋   □ 后腰   △ 后卫   · 至少180分钟',7.2,MUTED)
            worst=max(pp,key=lambda p:p['failed_loss90'])
            self.panel(x,395,CW,48)
            self.para(x+8,436,CW-16,f"{worst['name']}：每90分钟制造威胁{worst['per90']['positive']:.3f}，传球失误损失{worst['failed_loss90']:.3f}。有创造，也有冒险代价。",8.1,INK,11.8)
            self.section(x,378,'后场传球失误风险',CW)
            defenders=[p for p in pp if p['position'] in ['Defender','Defensive Midfielder']]
            ds=sorted(defenders,key=lambda p:-p['half_loss90'])[:3]
            self.panel(x,349,CW,20)
            self.tx(x+4,357,'球员',7.1,MUTED);self.tx(x+CW-105,357,'失败/传球',7.1,MUTED,'right');self.tx(x+CW-55,357,'失败率',7.1,MUTED,'right');self.tx(x+CW-4,357,'损失/90',7.1,MUTED,'right')
            for j,p in enumerate(ds):
                yy=336-j*24;self.tx(x,yy,p['name'],8.0);self.tx(x+CW-105,yy,f"{p['half_failures']}/{p['half_attempts']}",7.5,INK,'right');self.tx(x+CW-55,yy,pct(p['half_failure_rate']),7.5,INK,'right');self.tx(x+CW,yy,val(p['half_loss90']),8,C[i],'right',True);self.line(x,yy-9,CW)
            self.tx(x,262,'仅统计本方半场已评分传球，按PV损失/90排序。',6.8,MUTED)
            self.section(x,238,'制造威胁较少的前场球员',CW)
            ap=sorted([p for p in pp if p['position'] in ['Attacking Midfielder','Striker']],key=lambda p:p['per90']['positive'])[:3]
            self.panel(x,209,CW,20)
            self.tx(x+4,217,'球员',7.1,MUTED);self.tx(x+CW-55,217,'正PV/90',7.1,MUTED,'right');self.tx(x+CW-4,217,'xG/90',7.1,MUTED,'right')
            for j,p in enumerate(ap):
                yy=196-j*25;self.tx(x,yy,p['name'],8.1);self.tx(x+CW-55,yy,val(p['per90']['positive']),8,INK,'right',True);self.tx(x+CW,yy,val(p['xg90'],2),8,INK,'right',True);self.line(x,yy-9,CW)
        self.para(M,112,W-2*M,'怎么看：失误代价高，不一定就是防守漏洞。这里仅计算失败传球自身的PV损失，还没有统计随后让对手打出多少次反击或射门。后场表只看本方半场起脚、有PV评分的传球。',7.8,MUTED,11.3)
        self.para(M,72,W-2*M,'前场名单只在同队前腰及前锋中比较。中锋可能更多负责终结，需结合xG、实际进球和首发位置；PV低不等于没有进球威胁。',7.8,MUTED,11.3)
        self.finish()

    def table(self, x,top,widths,headers,rows,side,rowh=22):
        hh=32;self.rect(x,top-hh,sum(widths),hh,LIGHT)
        xx=x
        for w,s in zip(widths,headers):self.para(xx+5,top-7,w-10,s,7.2,MUTED,10.0);xx+=w
        for j,row in enumerate(rows):
            yy=top-hh-(j+1)*rowh
            if row[0]=='未识别':self.rect(x,yy,sum(widths),rowh,'#e9eff2')
            elif j%2==0:self.rect(x,yy,sum(widths),rowh,'#fafbfc')
            xx=x
            for k,(w,s) in enumerate(zip(widths,row)):
                self.tx(xx+5 if k==0 else xx+w-6,yy+7,s,8.2,C[side] if k==2 else INK,'left' if k==0 else 'right')
                xx+=w
            self.line(x,yy,sum(widths))
        return top-hh-len(rows)*rowh

    def page_possessions(self):
        self.begin('进攻从哪里起步，哪类球权更有威胁',
                   '对照各类起点的回合数、每回合威胁净值和进入进攻三区的比例。')
        widths=[58,66,67,64,91,96,81]
        for i,t in enumerate(self.teams):
            yy=710-i*282
            self.tx(M,yy,self.team_names[i],12,C[i]);self.tx(W-M,yy,f"{t['chains_total']}个回合 · {t['unassigned_pv_actions']}次PV动作尚未归入回合",7.7,MUTED,'right');self.line(M,yy-10,W-2*M,C[i],.7)
            rows=[]
            order=['高位夺回','中位夺回','低位夺回','开球','角球','任意球','界外球','门球','未知']
            for p in sorted(t['possessions'],key=lambda p:order.index(p['kind'])):
                er=f"{p['entry_count']}/{p['entry_covered']}" if p['entry_covered'] else '—'
                rate=f"{100*p['entry_count']/p['entry_covered']:.0f}% ({er})" if p['entry_covered'] else '—'
                rows.append(['未识别' if p['kind']=='未知' else p['kind'],f"{p['pv_count']} / {p['count']}",sign(p['average_pv'],3),pct(p['positive_share']),rate,pct(p['weighted_entry']),f"{100*p['shots_count']/p['count']:.0f}% ({p['shots_count']}/{p['count']})"])
            self.table(M,yy-22,widths,['如何获得球权','回合数\n有评分 / 全部','每回合\n威胁净值','贡献了多少\n进攻威胁¹','推进到进攻三区\n成功率（次数）','成功推进回合\n威胁占比²','形成射门\n回合占比'],rows,i,rowh=20)
        coverage = []
        for i, team in enumerate(self.teams):
            rows = {row['kind']: row for row in team['possessions']}
            high = rows.get('高位夺回', {}).get('count', 0)
            unknown = rows.get('未知', {}).get('count', 0)
            share = f"{unknown / team['chains_total']:.0%}" if team['chains_total'] else '—'
            coverage.append(f'{self.team_names[i]}高位夺回{high}次，未识别起点占{share}')
        self.insight(157, '。'.join(coverage) + '。比较不同起点的效率时，需同时看回合数和识别覆盖。')
        self.para(M,102,W-2*M,'怎么读：每回合威胁净值为有评分回合的平均净PV；¹为该类回合占全部已归组回合正PV的比例。²只看符合条件的回合，计算推进成功回合占其中正PV的比例，不是另一种成功率。',7.3,MUTED,10.3)
        self.para(M,75,W-2*M,'进攻三区指靠近对方球门的三分之一场地；推进率排除已在该区域起步或路径不明的回合，²另要求有PV评分。—为不适用或无法计算。高位夺回靠近对方球门，低位夺回靠近本方球门。',7.3,MUTED,10.3)
        self.finish()

    def page_chains(self):
        self.begin('一次有威胁的进攻，是怎样形成的','看哪些传递把球送到了危险位置，以及最后形成了什么射门；这两次进攻仅作示例。')
        vmax=max([.001]+[abs(e['pv']) for t in self.teams for e in (t['chain'] or {}).get('events',[]) if e['pv'] is not None])
        for i,t in enumerate(self.teams):
            x=col(i);self.team_head(i,710)
            chain = t['chain']
            if not chain or not chain['events']:
                self.para(x,670,CW,'没有可展示的完整机会链。',8.5)
                continue
            shot = chain['shot']
            sample = next(s for s in t['samples'] if s['id'] == shot['match_id'])
            matchday=datetime.fromisoformat(sample['kickoff_at'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y.%m.%d')
            opponent={'Leeds United':'利兹联','Newcastle United':'纽卡斯尔联'}.get(sample['opponent'],sample['opponent'])
            self.tx(x,681,f"对阵{opponent} · {matchday}",8,MUTED)
            method=(chain.get('method') or '进攻').removesuffix('起始')
            self.tx(x,664,f"{method}发起 · {shot['minute']:02d}:{shot['second']:02d}射门 · 射门xG {shot['xg']:.3f}",8.1)
            px,py,pw=x+7,470,CW-7;ph=pw*68/105
            self.rect(px,py,pw,ph,'#f7f9f8');self.pitch(px,py,pw,ph)
            moves=[e for e in chain['events'] if all(isinstance(e.get(k),(int,float)) for k in ['x','y','end_x','end_y'])]
            for j,e in enumerate(moves):
                a=xy(px,py,pw,ph,(e['x'],e['y']));b=xy(px,py,pw,ph,(e['end_x'],e['end_y']))
                self.arrow(a,b,C[i],.7+2.4*abs(e['pv'] or 0)/vmax,e['pv'] is not None and e['pv']<0)
                if j==0:self.dot(a[0],a[1],'起',C[i],5)
            se = next((e for e in chain['events'] if e['shot']), None)
            if se and se.get('x') is not None and se.get('y') is not None:
                self.dot(*xy(px,py,pw,ph,(se['x'],se['y'])),'射',C[i],6)
            self.tx(px+pw,py+ph+6,'进攻 →',6.8,MUTED,'right')
            self.tx(x,451,'实线：非负贡献  虚线：负贡献  起/射：起点/射门',6.8,MUTED)
            self.section(x,421,'每次处理增加或损失了多少威胁',CW)
            events=chain['events'];bw=(CW-12)/len(events);baseline=359
            self.line(x,baseline,CW,'#8a9aa4',.6)
            self.tx(x-3,baseline+2,'0',6,MUTED,'right',True)
            for j,e in enumerate(events):
                v=e['pv'];xx=x+3+j*bw
                if v is None:
                    self.tx(xx+bw*.35,baseline-6,'×',6.5,MUTED,'center');continue
                bh=v/vmax*33
                self.rect(xx,baseline+min(0,bh),max(1,bw*.65),abs(bh),C[i] if v>=0 else '#8b9ba5')
                if v==max(e['pv'] for e in events if e['pv'] is not None):self.tx(xx+bw*.325,baseline+bh+5,sign(v),7,C[i],'center',True)
            self.tx(x,baseline-28,'动作按时间排列 →  ×：无评分；两队共用刻度',6.9,MUTED)
            ranked=sorted([e for e in events if e['pv'] is not None],key=lambda e:-e['pv'])[:4]
            names = t['player_names']
            self.panel(x,299,CW,21)
            self.tx(x+4,307,'最能增加威胁的4次处理',8.0);self.tx(x+CW-4,307,'PV变化 / xT变化',7.2,MUTED,'right')
            for j,e in enumerate(ranked):
                yy=286-j*22;self.tx(x,yy,f"{e['minute']:02d}:{e['second']:02d}  {names.get(e['player_id'],'未知球员')}",7.6)
                self.tx(x+CW,yy,sign(e['pv'])+' / '+sign(e['xt']),7.3,INK,'right',True);self.line(x,yy-9,CW)
        self.section(M,178,'这些数据能说明什么',W-2*M)
        minutes = '，'.join(f"{name}{team['minutes']:.1f}分钟" for name, team in zip(self.team_names, self.teams))
        scope = '样本范围：目标比赛前最近5场联赛，统计11人对11人片段。PV采用双方评分均通过校验的比赛，'+minutes+'。xT覆盖场次按路线图列示。'
        self.para(M,153,W-2*M,scope,8.0,MUTED,12)
        self.para(M,116,W-2*M,'指标：制造威胁为正PV之和，进攻损耗为负PV之和，两者相加得到威胁净值。xG看射门质量，xT看推进带来的位置价值；均不等于本场进球概率。未结合盘口和赔率，不能直接判断投注价值。',8.0,MUTED,12)
        self.para(M,79,W-2*M,'范围：PV为赛后补采，xT训练截止尚未核实，仅作历史样本分析。压力、无球跑动和独立带球数据不完整；判断比赛还需结合首发、对手强弱与赛程。',7.5,MUTED,11)
        self.finish()


def draw_threat_reader(book):
    book.page_attack()
    book.page_defence()
    book.page_players()
    book.page_risk()
    book.page_possessions()
    book.page_chains()
def render_threat_reader(data, path, font):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    pdf_canvas = canvas.Canvas(str(path), pagesize=(W, H), pageCompression=1)
    book = ThreatReader(data, pdf_canvas, font)
    pdf_canvas.setTitle(book.match_name + ' · PV/xT 威胁专题')
    pdf_canvas.setAuthor('喵弟量化研究所')
    draw_threat_reader(book)
    pdf_canvas.save()
