'use client';
import {useEffect,useId,useRef,useState} from 'react';
import dynamic from 'next/dynamic';
import type {EChartsOption} from 'echarts';
import {ballKinds,eventLabel,formatValue,matchPitchPoint,shotMapRadius,shotResult,type Bundle,type Event} from './visualization-data';
import {axisPosition, axisTicks, formatMatchTime, type MatchTime, type TimeRange} from './visualization-time';
import {MomentumBrush, momentumGrid} from './visualization-timeline';
import s from '@/app/studio/visualization/visualization.module.css';

const Chart=dynamic(()=>import('@/components/EChart').then(m=>m.EChart),{ssr:false});
const HOME = '#b42338';
const AWAY = '#7037a7';
const INK = '#45515e';
const GRID = '#e5e9ee';
const NEUTRAL = '#596675';
const PITCH_VIEW_WIDTH = 110;
const FIELD_HEIGHT = 64;
const PENALTY_DEPTH = 17;
const PENALTY_SPOT = 11.5;
const ARC_RADIUS = 100 / 11.475;
const CORNER_RADIUS = 100 / 85;
const pitchY = (y: number) => (100 - y) * FIELD_HEIGHT / 100;

function PitchMarkings({ endId, neutral }: { endId: string; neutral: boolean }) {
  const centreY = FIELD_HEIGHT / 2;
  const arcOffset = Math.sqrt(ARC_RADIUS ** 2 - (PENALTY_DEPTH - PENALTY_SPOT) ** 2);
  const line = neutral ? '#858c95' : '#81948b';
  return <g fill="none" stroke={line} strokeWidth=".3" aria-hidden="true" pointerEvents="none">
    <rect width="100" height={FIELD_HEIGHT}/>
    <path d={`M50 0V${FIELD_HEIGHT}`}/>
    <circle cx="50" cy={centreY} r={ARC_RADIUS}/>
    <circle cx="50" cy={centreY} r=".42" fill={line}/>
    <g id={endId}>
      <path d={`M0 ${pitchY(78.9)}H${PENALTY_DEPTH}V${pitchY(21.1)}H0`}/>
      <path d={`M0 ${pitchY(63.2)}H5.8V${pitchY(36.8)}H0`}/>
      <path d={`M0 ${pitchY(54.8)}H-1.5V${pitchY(45.2)}H0`}/>
      <circle cx={PENALTY_SPOT} cy={centreY} r=".48" fill={line}/>
      <path d={`M${PENALTY_DEPTH} ${centreY - arcOffset} A${ARC_RADIUS} ${ARC_RADIUS} 0 0 1 ${PENALTY_DEPTH} ${centreY + arcOffset}`}/>
      <path d={`M0 ${CORNER_RADIUS} A${CORNER_RADIUS} ${CORNER_RADIUS} 0 0 0 ${CORNER_RADIUS} 0 M0 ${FIELD_HEIGHT - CORNER_RADIUS} A${CORNER_RADIUS} ${CORNER_RADIUS} 0 0 1 ${CORNER_RADIUS} ${FIELD_HEIGHT}`}/>
    </g>
    <use href={`#${endId}`} transform={`rotate(180 50 ${centreY})`}/>
  </g>;
}

export function Pitch({events,match,onSelect,selected,heat,network=false,shotMap=false,region,onRegion,onPair,onHeatRegion,players=[],directionLabels=true}: {
  events:Event[];match:Bundle;onSelect:(e:Event)=>void;selected?:string;heat?:'density'|'touches';network?:boolean;shotMap?:boolean;
  region?:number[]|null;onRegion?:(r:number[])=>void;players?:string[];
  onPair?:(a:string,b:string,n:number)=>void;onHeatRegion?:(region:number[])=>void;directionLabels?:boolean;
}) {
  const uid=useId().replace(/:/g,'');const ref=useRef<SVGSVGElement>(null);
  const [start,setStart]=useState<number[]|null>(null);const [end,setEnd]=useState<number[]|null>(null);
  const [pitchWidth, setPitchWidth] = useState(700);
  useEffect(() => {
    const svg = ref.current;
    if (!shotMap || !svg) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setPitchWidth(entry.contentRect.width);
    });
    observer.observe(svg);
    return () => observer.disconnect();
  }, [shotMap]);
  const plotted = shotMap
    ? events.filter(event => shotMapRadius(event) > 0).sort((a, b) => shotMapRadius(b) - shotMapRadius(a))
    : events;
  const color = (teamId: string | null) => teamId === match.match.home_id ? HOME : teamId === match.match.away_id ? AWAY : NEUTRAL;
  const sameTeamComparison = players.length === 2 && match.players.find(p => p.id === players[0])?.team_id === match.players.find(p => p.id === players[1])?.team_id;
  const bins = new Map<string, { x: number; y: number; home: number; away: number }>();
  if (heat === 'density') for (const event of events) {
    if (!ballKinds.has(event.kind) || event.x === null || event.y === null) continue;
    const [x, y] = matchPitchPoint(event.x, event.y, event.team_id === match.match.away_id);
    const col = Math.min(19, Math.floor(x / 5)), row = Math.min(12, Math.floor(y / 8));
    const key = `${col},${row}`;
    const bin = bins.get(key) ?? { x: col, y: row, home: 0, away: 0 };
    if (event.team_id === match.match.home_id) bin.home++;
    if (event.team_id === match.match.away_id) bin.away++;
    bins.set(key, bin);
  }
  const max = Math.max(1, ...Array.from(bins.values(), bin => Math.max(bin.home, bin.away)));
  const xy=(event:React.PointerEvent<SVGSVGElement>)=>{
    const svg=ref.current,ctm=svg?.getScreenCTM();if(!svg||!ctm)return [0,0];
    const p=new DOMPoint(event.clientX,event.clientY).matrixTransform(ctm.inverse());
    return [Math.max(0,Math.min(100,p.x)),Math.max(0,Math.min(100,100-p.y*100/FIELD_HEIGHT))];
  };
  const active=start&&end?[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.max(start[0],end[0]),Math.max(start[1],end[1])]:region;
  const names=new Map(match.players.map(p=>[p.id,p]));
  return <svg ref={ref} viewBox="-5 -5 110 74" role="group" aria-label={!directionLabels?'球场底图':heat ? heat === 'density' ? '事件分布热图' : '球上事件点图' : network ? '整场传球网络' : `${shotMap ? '射门图' : '事件战术板'}；主队向右进攻，客队向左进攻`} style={{width:'100%',display:'block',touchAction:onRegion?'none':'auto'}}
    onPointerDown={onRegion?e=>{ref.current?.setPointerCapture(e.pointerId);const p=xy(e);setStart(p);setEnd(p);}:undefined}
    onPointerMove={onRegion?e=>{if(start)setEnd(xy(e));}:undefined}
    onPointerCancel={onRegion?()=>{setStart(null);setEnd(null);}:undefined}
    onPointerUp={onRegion?e=>{if(start){const p=xy(e);onRegion([Math.min(start[0],p[0]),Math.min(start[1],p[1]),Math.max(start[0],p[0]),Math.max(start[1],p[1])]);}setStart(null);setEnd(null);}:undefined}>
    <defs><marker id={`${uid}-home`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill={HOME}/></marker><marker id={`${uid}-away`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill={AWAY}/></marker><marker id={`${uid}-neutral`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill={NEUTRAL}/></marker></defs>
    <rect x="-5" y="-5" width="110" height="74" rx="2" fill={shotMap ? '#f7f8fa' : '#f5f8f6'}/>
    {!shotMap && !heat && [0,20,40,60,80].map(x=><rect key={x} x={x} y="0" width="10" height="64" fill="#e9f0ec"/>)}
    {heat === 'density' && <>
      <defs>
        <clipPath id={`${uid}-field`}><rect width="100" height={FIELD_HEIGHT}/></clipPath>
        {(['home','away'] as const).map(side => <radialGradient key={side} id={`${uid}-heat-${side}`}>
          <stop stopColor={side === 'home' ? HOME : AWAY}/><stop offset="100%" stopColor={side === 'home' ? HOME : AWAY} stopOpacity="0"/>
        </radialGradient>)}
      </defs>
      <g clipPath={`url(#${uid}-field)`} pointerEvents="none">
        {[...bins].map(([key, bin]) => <g key={key}>
          {(['home','away'] as const).map(side => bin[side] > 0 && <circle key={side} cx={bin.x * 5 + 2.5} cy={pitchY(Math.min(98, bin.y * 8 + 4))} r="7" fill={`url(#${uid}-heat-${side})`} opacity={.15 + .7 * Math.sqrt(bin[side] / max)}/>)}
        </g>)}
      </g>
      {[...bins].map(([key, bin]) => {
        const {x, y} = bin, top = Math.min(100, (y + 1) * 8);
        const bounds = [x * 5, y * 8, (x + 1) * 5, top];
        const choose = () => onHeatRegion?.(bounds);
        return <rect key={key} role="button" tabIndex={0} aria-pressed={region?.every((value, index) => value === bounds[index]) ?? false} aria-label={`热图区 ${x + 1}-${y + 1}，${bin.home + bin.away} 条事件`} onClick={choose}
          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(); } }}
          x={x * 5} y={pitchY(top)} width="5" height={pitchY(y * 8) - pitchY(top)} fill="transparent"><title>主队 {bin.home} 条，客队 {bin.away} 条</title></rect>;
      })}
    </>}
    <PitchMarkings endId={`${uid}-pitch-end`} neutral={shotMap}/>
    {directionLabels&&(network ? <text x="50" y="68" textAnchor="middle" fill="#455b51" fontSize="1.8">本队进攻方向 →</text> : <g textAnchor="middle" fontSize="1.8">
      <text x="25" y="68" fill={AWAY}>← 客队进攻方向</text>
      <text x="75" y="68" fill={HOME}>主队进攻方向 →</text>
    </g>)}
    {network&&match.pass_edges.map((edge,i)=>{
      const a=match.pass_nodes.find(p=>p.id===edge.from_id&&p.team_id===edge.team_id),b=match.pass_nodes.find(p=>p.id===edge.to_id&&p.team_id===edge.team_id);
      if(!a||!b||a.x===null||a.y===null||b.x===null||b.y===null||!names.has(a.id)||!names.has(b.id))return null;
      return <line key={i} role="button" tabIndex={0} aria-label={`${names.get(a.id)?.name} → ${names.get(b.id)?.name}：${edge.count} 次`} onClick={()=>onPair?.(a.id,b.id,edge.count)} onKeyDown={e=>{if(e.key==='Enter')onPair?.(a.id,b.id,edge.count);}} x1={a.x} y1={(100-a.y)*.64} x2={b.x} y2={(100-b.y)*.64} stroke={color(edge.team_id)} strokeWidth={Math.max(.2,Math.min(1.8,edge.count/12))} opacity=".5"><title>{names.get(a.id)?.name} → {names.get(b.id)?.name}：{edge.count} 次</title></line>;
    })}
    {network&&match.pass_nodes.map(p=>p.x!==null&&p.y!==null&&names.has(p.id)?<g key={`${p.team_id}-${p.id}`}><circle cx={p.x} cy={(100-p.y)*.64} r="1.7" fill={color(p.team_id)}/><text x={p.x} y={(100-p.y)*.64+.65} textAnchor="middle" fontSize="1.8" fill="#fff">{names.get(p.id)?.number}</text><title>{names.get(p.id)?.name} · 来源平均位置</title></g>:null)}
    {heat !== 'density' && !network && plotted.map(e=>{
      if(e.x===null||e.y===null)return null;
      const c=color(e.team_id),active=e.id===selected;
      const away = e.team_id === match.match.away_id;
      const [x, y] = matchPitchPoint(e.x, e.y, away);
      const end = e.end_x === null || e.end_y === null ? null : matchPitchPoint(e.end_x, e.end_y, away);
      const square = !shotMap && sameTeamComparison && e.player_id === players[1];
      const radius = shotMap ? shotMapRadius(e) * PITCH_VIEW_WIDTH / pitchWidth : active ? 1.2 : .65;
      const goal = e.goal || e.own_goal || e.shot_outcome === 'goal';
      return <g key={e.id} onClick={onRegion?undefined:()=>onSelect(e)} role="button" tabIndex={onRegion?-1:0} aria-pressed={active} aria-label={`${e.clock} ${e.player_name??''} ${eventLabel(e)}`} onKeyDown={a=>{if(!onRegion && (a.key==='Enter' || a.key===' ')){a.preventDefault();onSelect(e);}}} style={{cursor:'pointer'}} opacity={active ? 1 : selected ? .28 : shotMap ? 1 : events.length > 160 ? .55 : .92}>
        <title>{e.clock} {e.player_name} · {eventLabel(e)}{shotMap && !e.own_goal ? ` · xG ${formatValue(e.xg, 3)}` : ''}{e.pv_added===null?'':` · PV增量 ${formatValue(e.pv_added,4)}`}</title>
        {!heat&&e.kind==='pass'&&end&&<polyline points={`${x},${pitchY(y)} ${end[0]},${pitchY(end[1])}`} fill="none" stroke={c} strokeWidth={active?.7:.25} opacity={active?1:.55} strokeDasharray={e.success===false?'1 .8':undefined} markerEnd={`url(#${uid}-${c===HOME?'home':c===AWAY?'away':'neutral'})`}/>}
        {shotMap ? <circle data-outcome={goal ? 'goal' : 'shot'} cx={x} cy={pitchY(y)} r={radius} vectorEffect="non-scaling-stroke" fill={goal ? c : 'none'} stroke={c} strokeWidth={active ? 3 : goal ? 1 : 1.6} pointerEvents="all"/> : square ? <rect x={x-radius} y={pitchY(y)-radius} width={radius*2} height={radius*2} fill={c} stroke={active?'#182333':c} strokeWidth={active?.5:.3}/> : <circle cx={x} cy={pitchY(y)} r={radius} fill={e.kind === 'shot' && !goal ? 'none' : c} stroke={active?'#182333':c} strokeWidth={active?.5:.3}/>}
        {shotMap && e.own_goal && <path d={`M${x - radius / 2},${pitchY(y)}h${radius}m${-radius / 2},${-radius / 2}v${radius}`} stroke="#fff" strokeWidth="1" vectorEffect="non-scaling-stroke" pointerEvents="none"/>}
      </g>;
    })}
    {active&&<rect x={active[0]} y={(100-active[3])*.64} width={active[2]-active[0]} height={(active[3]-active[1])*.64} fill="#52796f" fillOpacity=".15" stroke="#52796f" strokeWidth=".4" pointerEvents="none"/>}
  </svg>;
}

export function MomentumChart({ bundle, time, range, markers, onRange }: {
  bundle: Bundle; time: MatchTime; range: TimeRange | null; markers: Event[]; onRange: (range: TimeRange) => void;
}) {
  const points = bundle.momentum.flatMap(point => {
    const position = axisPosition(time, point);
    if (position === null || point.net === null) return [];
    return [{ value: [position, point.net * 100], home: point.home, away: point.away, itemStyle: { color: point.net >= 0 ? HOME : AWAY } }];
  });
  if (!points.length) return <p className={s.empty}>本场暂无 MA27 净势头采样。</p>;
  const bound = Math.max(0.001, ...points.map(point => Math.abs(point.value[1]))) * 1.12;
  const incidents = markers.filter(event => event.goal || event.own_goal).flatMap(event => {
    const xAxis = axisPosition(time, event);
    return xAxis === null ? [] : [{ xAxis, lineStyle: { color: colorForSide(event.team_id, bundle) } }];
  });
  const separators = time.phases.slice(1).map(phase => ({ xAxis: phase.offset, lineStyle: { color: INK, type: 'solid' as const, opacity: .65 } }));
  const option: EChartsOption = {
    animation: false, grid: momentumGrid,
    tooltip: { trigger: 'axis', renderMode: 'richText', confine: true, formatter: params => {
      const point = (Array.isArray(params) ? params[0] : params)?.data as typeof points[number] | undefined;
      if (!point) return '';
      const [position, net] = point.value;
      const team = net > 0 ? bundle.match.home_name : net < 0 ? bundle.match.away_name : null;
      const threat = (value: number) => `${formatValue(value * 100, 2)}%`;
      return [
        formatMatchTime(time, position, true),
        team ? `${team} 占优 ${formatValue(Math.abs(net), 2)} 个百分点` : '双方势头持平',
        point.home === null ? '' : `${bundle.match.home_name} ${threat(point.home)}`,
        point.away === null ? '' : `${bundle.match.away_name} ${threat(point.away)}`,
      ].filter(Boolean).join('\n');
    } },
    xAxis: { type: 'value', min: 0, max: time.end, axisLabel: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLine: { onZero: true, lineStyle: { color: INK } } },
    yAxis: { type: 'value', min: -bound, max: bound, axisLabel: { color: INK, formatter: value => formatValue(value, 2) }, splitLine: { lineStyle: { color: GRID } } },
    series: [{ type: 'bar', barMaxWidth: 6, data: points, markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { opacity: .32 }, data: [...separators, ...incidents] } }],
  };
  return <MomentumBrush time={time} range={range} onRange={onRange}>
    <Chart option={option} height={248} ariaSummary="MA27 净势头，主队占优在零轴上方，客队占优在下方。" showSummary={false}/>
    <div className={s.plotPhases} aria-hidden="true" style={{ left: momentumGrid.left, right: momentumGrid.right }}>{time.phases.map(phase => <span key={phase.id} style={{ left: `${100 * (phase.offset + phase.duration / 2) / time.end}%` }}>{phase.label}</span>)}</div>
    <div className={s.plotTicks} aria-hidden="true" style={{ left: momentumGrid.left, right: momentumGrid.right }}>{axisTicks(time).map(tick => <span key={tick.position} style={{ left: `${100 * tick.position / time.end}%` }}>{tick.label}</span>)}</div>
  </MomentumBrush>;
}

function colorForSide(teamId: string | null, bundle: Bundle) {
  return teamId === bundle.match.home_id ? HOME : teamId === bundle.match.away_id ? AWAY : NEUTRAL;
}

export function XgRace({ bundle, events, time, range, onSelect, teamIds }: {
  bundle: Bundle; events: Event[]; time: MatchTime; range: TimeRange | null; onSelect: (event: Event) => void; teamIds?: string[];
}) {
  const start = range?.[0] ?? 0;
  const end = range?.[1] ?? time.end;
  const shots = events.filter(event => event.xg !== null && !event.own_goal);
  const shotsById = new Map(shots.map(shot => [shot.id, shot]));
  if (!shots.length) return <p className={s.empty}>当前区间暂无已记录 xG 的射门。</p>;
  const option: EChartsOption = {
    animation: false, grid: { left: 44, right: 20, top: 25, bottom: 33 },
    tooltip: { trigger: 'item', renderMode: 'richText', confine: true, formatter: params => {
      const point = (Array.isArray(params) ? params[0] : params)?.data as { value: number[]; eventId?: string } | undefined;
      const shot = point?.eventId ? shotsById.get(point.eventId) : undefined;
      return point && shot ? [
        formatMatchTime(time, point.value[0], true),
        [shot.player_name, eventLabel(shot)].filter(Boolean).join(' · '),
        `本次 xG ${formatValue(shot.xg, 3)} · 累计 xG ${formatValue(point.value[1], 3)}`,
      ].join('\n') : '';
    } },
    xAxis: { type: 'value', min: start, max: Math.max(start + 1, end), splitNumber: 5, axisLabel: { color: INK, hideOverlap: true, formatter: position => formatMatchTime(time, Math.round(position)) } },
    yAxis: { type: 'value', min: 0, axisLabel: { color: INK }, splitLine: { lineStyle: { color: GRID } } },
    series: (teamIds ?? [bundle.match.home_id, bundle.match.away_id]).map(teamId => {
      const color = colorForSide(teamId, bundle);
      let sum = 0;
      const data: { value: number[]; eventId?: string; symbol: string; symbolSize?: number }[] = [{ value: [start, 0], symbol: 'none' }];
      for (const shot of shots) {
        const position = axisPosition(time, shot);
        if (shot.team_id !== teamId || position === null || shot.xg === null) continue;
        sum += shot.xg;
        const goal = shotResult(shot) === 'goal';
        data.push({ value: [position, sum], eventId: shot.id, symbol: goal ? 'circle' : 'emptyCircle', symbolSize: goal ? 10 : 6 });
      }
      data.push({ value: [end, sum], symbol: 'none' });
      return { type: 'line', step: 'end', symbolSize: 7, name: teamId === bundle.match.home_id ? bundle.match.home_name : bundle.match.away_name, lineStyle: { color }, itemStyle: { color }, data };
    }),
  };
  return <Chart option={option} height={228} ariaSummary="所选区间的累计 xG，点击射门点查看事件。" onEvents={{ click: (params: unknown) => {
    const id = (params as { data?: { eventId?: string } }).data?.eventId;
    const shot = id && shotsById.get(id);
    if (shot) onSelect(shot);
  } }}/>
}

export function Matrix({bundle,onPair}:{bundle:Bundle;onPair:(a:string,b:string,n:number)=>void}){
  const ids=[...new Set(bundle.pass_edges.flatMap(e=>[e.from_id,e.to_id]))];
  const names=ids.map(id=>bundle.players.find(p=>p.id===id)?.name??'未知球员');
  const option:EChartsOption={animation:false,tooltip:{renderMode:'richText',formatter:(p:unknown)=>{const v=(p as {data?:number[]}).data;return v?`${names[v[1]]} → ${names[v[0]]}：${v[2]} 次`:'';}},
    grid:{left:105,right:15,top:34,bottom:115},xAxis:{type:'category',name:'接球人',nameLocation:'center',nameGap:94,data:names,axisLabel:{rotate:60,color:INK,fontSize:10}},yAxis:{type:'category',name:'传球人',inverse:true,data:names,axisLabel:{color:INK,fontSize:10,width:90,overflow:'truncate'}},
    visualMap:{min:0,max:Math.max(1,...bundle.pass_edges.map(e=>e.count)),show:false,inRange:{color:['#f5f1f7', colorForSide(bundle.pass_edges[0]?.team_id ?? null, bundle)]}},
    series:[{type:'heatmap',data:bundle.pass_edges.map(e=>[ids.indexOf(e.to_id),ids.indexOf(e.from_id),e.count]),label:{show:ids.length<18,color:'#182333',textBorderColor:'#fff',textBorderWidth:2}}]};
  return <Chart option={option} height={Math.max(400,ids.length*22)} ariaSummary="整场成功传球矩阵；行是传球人，列是接球人。点击格子查看次数。" onEvents={{click:(p:unknown)=>{const v=(p as {data?:number[]}).data;if(v)onPair(ids[v[1]],ids[v[0]],v[2]);}}}/>;
}

export function PlayerValueChart({ rows }: {
  rows: { name: string; positive: number | null; negative: number | null; net: number | null }[];
}) {
  const option: EChartsOption = {
    animation: false,
    grid: { left: 108, right: 24, top: 10, bottom: 35 },
    xAxis: { type: 'value', axisLabel: { color: INK }, splitLine: { lineStyle: { color: GRID } } },
    yAxis: { type: 'category', inverse: true, data: rows.map(row => row.name), axisLabel: { color: INK, width: 94, overflow: 'truncate' }, axisTick: { show: false } },
    tooltip: { trigger: 'item', renderMode: 'richText', confine: true, formatter: params => {
      const { dataIndex } = Array.isArray(params) ? params[0] : params;
      const row = rows[dataIndex];
      return row ? `${row.name}\n增加 ${formatValue(row.positive, 3)} · 减少 ${formatValue(row.negative, 3)}\n净贡献 ${formatValue(row.net, 3)}` : '';
    } },
    series: [
      { name: '增加威胁', type: 'bar', stack: 'pv', barMaxWidth: 16, itemStyle: { color: '#26729a' }, data: rows.map(row => row.positive) },
      { name: '减少威胁', type: 'bar', stack: 'pv', barMaxWidth: 16, itemStyle: { color: '#ba5464' }, data: rows.map(row => row.negative) },
      { name: '净贡献', type: 'scatter', symbol: 'diamond', symbolSize: 8, itemStyle: { color: '#182333' }, data: rows.map((row, index) => [row.net ?? '-', index]) },
    ],
  };
  return <Chart option={option} height={Math.max(145, rows.length * 27 + 45)} ariaSummary="球员整场控球价值贡献图，左侧为减少的威胁，右侧为增加的威胁，菱形为净贡献。精确数值可在下方展开查看。" showSummary={false}/>;
}
