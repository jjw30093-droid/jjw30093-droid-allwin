'use client';
import Link from 'next/link';
import {useEffect,useMemo,useRef,useState,type KeyboardEvent} from 'react';
import {getMe} from '@/lib/api-v1';
import {TeamBadge} from '@/components/teams/TeamBadge';
import {Pitch,MomentumChart,Matrix,XgRace,PlayerValueChart} from './visualization-charts';
import {MatchTimeline} from './visualization-timeline';
import {axisPosition,inTimeRange,matchTime,type TimeRange} from './visualization-time';
import {eventLabel,filterEvents,formatValue,getVisualization,initialFilters,kinds,loadBundle,shotOutcomes,shotResult,views,type ShotOutcome,type Bundle,type Catalog,type Event,type Filters,type View} from './visualization-data';
import s from '@/app/studio/visualization/visualization.module.css';

export function VisualizationGate(){
  const [status,setStatus]=useState('loading');
  useEffect(()=>{let live=true;getMe().then(me=>{if(live)setStatus(!me.authenticated?'anonymous':me.user?.role==='admin'?'ready':'forbidden');}).catch(()=>{if(live)setStatus('error');});return()=>{live=false;};},[]);
  if(status==='ready')return <VisualizationWorkspace/>;
  return <main className={s.page}><h1>比赛可视化</h1><p>{status==='loading'?'正在验证身份…':status==='anonymous'?'请登录管理员账号':status==='forbidden'?'此工作台仅限管理员使用':'身份校验失败，请刷新重试'}</p>{status==='anonymous'&&<Link href="/login?next=/studio/visualization">前往登录</Link>}</main>;
}

function EventDetails({event}:{event?:Event}){
  if(!event)return <p className={s.empty}>点击时间轴或球场上的标记，查看对应事件。</p>;
  return <><h3>{eventLabel(event)} · {event.clock}</h3><dl>
    <dt>球员</dt><dd>{event.player_name??'未提供'}</dd><dt>时期</dt><dd>{['','上半场','下半场','加时上半场','加时下半场','点球大战'][event.period]??event.period}</dd>
    <dt>结果</dt><dd>{event.kind==='shot' ? (shotResult(event)===null ? '未区分射正与封堵' : eventLabel(event)) : event.success===null?'未提供':event.success?'成功':'未成功'}</dd>
    <dt>起点</dt><dd>{event.x===null||event.y===null?'未提供':`${formatValue(event.x,1)}, ${formatValue(event.y,1)}`}</dd>
    <dt>终点</dt><dd>{event.end_x===null||event.end_y===null?'未提供':`${formatValue(event.end_x,1)}, ${formatValue(event.end_y,1)}`}</dd>
    {event.kind==='shot'&&<><dt>xG</dt><dd>{formatValue(event.xg,3)}</dd>{event.xgot!==null&&<><dt>xGOT</dt><dd>{formatValue(event.xgot,3)}</dd></>}</>}
    {event.pv_added!==null&&<><dt>威胁变化</dt><dd>{signedValue(event.pv_added * 100)} 个百分点</dd></>}
    {event.pv_value!==null&&<><dt>动作后 PV</dt><dd>{formatValue(event.pv_value * 100, 2)}%</dd></>}
    {event.kind==='pass'&&<><dt>关键传球</dt><dd>{event.assist?'助攻':event.key_pass?'是':'否'}</dd></>}
  </dl><p className={s.definition}>详情坐标范围 0–100，按所属球队的进攻方向记录。</p></>;
}

function side(bundle: Bundle, id: string | null) {
  return id === bundle.match.home_id ? 'home' : id === bundle.match.away_id ? 'away' : undefined;
}

const playerGroups = [
  ['射门与进球', ['goals', 'total_scoring_att', 'ontarget_scoring_att', 'shot_off_target', 'blocked_scoring_att', 'hit_woodwork', 'big_chance_missed']],
  ['传球与创造', ['total_pass', 'accurate_pass', 'total_cross', 'accurate_cross', 'total_through_ball', 'accurate_through_ball', 'goal_assist', 'big_chance_created', 'corner_taken']],
  ['持球与对抗', ['touches', 'unsuccessful_touch', 'carries', 'progressive_carries', 'total_contest', 'won_contest', 'duel_won', 'duel_lost', 'aerial_won', 'aerial_lost']],
  ['防守与门将', ['total_tackle', 'won_tackle', 'interception', 'blocked_pass', 'total_clearance', 'ball_recovery', 'error_lead_to_goal', 'error_lead_to_shot', 'saves', 'goals_conceded']],
  ['其他', []],
] as const;
const categoryFor = (code: string) => playerGroups.find(([, codes]) => (codes as readonly string[]).includes(code))?.[0] ?? '其他';

function PlayerStats({bundle, ids}: {bundle: Bundle; ids: string[]}) {
  const [group, setGroup] = useState('');
  const players = ids.flatMap(id => bundle.players.filter(player => player.id === id));
  if (!players.length) return <p className={s.empty}>请在阵容中选择一至两名球员。</p>;
  if (players.length > 2) return <p className={s.empty}>请在阵容中保留一至两名球员进行比较。</p>;
  const stats = bundle.player_stats.filter(stat => ids.includes(stat.player_id));
  const metrics = [...new Map(stats.map(stat => [stat.code, stat])).values()];
  const categories = playerGroups.map(([label, codes]) => {
    const rows = metrics.filter(metric => categoryFor(metric.code) === label);
    return { label, rows: codes.length ? (codes as readonly string[]).flatMap(code => rows.filter(metric => metric.code === code)) : rows };
  }).filter(category => category.rows.length > 0);
  const activeGroup = categories.some(category => category.label === group) ? group : '';
  return <>
    {categories.length > 0 && <div className={s.toolbar}><label>整场指标分类<select value={activeGroup} onChange={event => setGroup(event.target.value)}>
      <option value="">全部分类</option>{categories.map(category => <option key={category.label}>{category.label}</option>)}
    </select></label></div>}
    <div className={s.tableWrap}><table className={s.table}><thead><tr><th>整场指标</th>{players.map(player => <th key={player.id} data-side={side(bundle, player.team_id)}>{player.name}</th>)}</tr></thead>
    <tbody><tr><th scope="row">出场时间（分钟）</th>{players.map(player => <td key={player.id}>{formatValue(player.minutes, 0)}</td>)}</tr></tbody>
    {categories.filter(category => !activeGroup || category.label === activeGroup).map(({label, rows}) =>
      <tbody key={label}><tr className={s.statGroup}><th scope="rowgroup" colSpan={players.length + 1}>{label}</th></tr>{rows.map(metric => <tr key={metric.code}>
        <th scope="row">{metric.label} <small>({metric.unit === 'count' ? '次' : metric.unit})</small></th>{players.map(player => <td key={player.id}>{formatValue(stats.find(stat => stat.player_id === player.id && stat.code === metric.code)?.value)}</td>)}
      </tr>)}</tbody>
    )}
  </table></div>{!stats.length && <p className={s.empty}>所选球员暂无整场技术统计。</p>}</>;
}

function RangeStats({ bundle, events, scoped }: { bundle: Bundle; events: Event[]; scoped: boolean }) {
  const { home_id, away_id, home_name, away_name } = bundle.match;
  const rows = [[home_id, home_name], [away_id, away_name]].map(([id, name]) => {
    const teamEvents = events.filter(event => event.team_id === id);
    const shots = teamEvents.filter(event => event.kind === 'shot' && !event.own_goal);
    const known = shots.filter(shot => shot.xg !== null);
    const passes = teamEvents.filter(event => event.kind === 'pass');
    return { id, name, shots: shots.length, known: known.length, xg: known.length ? known.reduce((sum, shot) => sum + (shot.xg ?? 0), 0) : null, passes: passes.length, completed: passes.filter(pass => pass.success === true).length };
  });
  return <section className={s.rangeStats} aria-label={scoped ? '所选时段统计' : '全场事件统计'}>
    <h3>{scoped ? '所选时段统计' : '全场事件统计'}</h3>
    <div className={s.tableWrap}><table className={s.table}>
      <thead><tr><th scope="col">球队</th><th scope="col">射门</th><th scope="col">已记录 xG</th><th scope="col">传球</th><th scope="col">成功传球</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.id}><th scope="row" data-side={side(bundle, row.id)}>{row.name}</th><td>{row.shots}</td><td>{formatValue(row.xg, 3)} <small>{row.known}/{row.shots} 次</small></td><td>{row.passes}</td><td>{row.completed}</td></tr>)}</tbody>
    </table></div>
  </section>;
}

const allShotOutcomes = shotOutcomes.map(([value]) => value);
const eventPageSize = 12;
const fixtureCompetitions = [['EPL','英超'], ['J1L','日职'], ['ALM','澳超'], ['UCL','欧冠']] as const;
const kickoffFormat = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short', hourCycle: 'h23' });

const pvTotals = [['net', '净贡献'], ['positive', '增加威胁'], ['negative', '减少威胁']] as const;
const metricValue = (row: Bundle['pv_teams'][number], code: string) => row.metrics.find(metric => metric.code === code)?.value ?? null;
const signedValue = (value: number | null) => value === null ? '未提供' : `${value > 0 ? '+' : ''}${formatValue(value, 3)}`;

function ValueActions({ row }: { row: Bundle['pv_teams'][number] }) {
  const metrics = row.metrics.filter(metric => !pvTotals.some(([code]) => code === metric.code) && metric.value !== null && metric.value !== 0);
  if (!metrics.length) return <span className={s.muted}>—</span>;
  return <details><summary>查看分类</summary><dl className={s.valueActions}>{metrics.map(metric =>
    <div key={metric.code}><dt>{metric.label}</dt><dd>{signedValue(metric.value)}</dd></div>)}</dl></details>;
}

function PossessionValue({ bundle, filters }: { bundle: Bundle; filters: Filters }) {
  const { home_id, away_id, home_name, away_name } = bundle.match;
  const [chartTeam, setChartTeam] = useState(home_id);
  const teamId = filters.team || chartTeam;
  const teams = bundle.pv_teams.filter(team => !filters.team || team.team_id === filters.team);
  const players = bundle.pv_players.filter(row => row.team_id === teamId && (!filters.players.length || filters.players.includes(row.player_id ?? '')))
    .map(row => ({ row, name: bundle.players.find(player => player.id === row.player_id)?.name ?? '未知球员',
      net: metricValue(row, 'net'), positive: metricValue(row, 'positive'), negative: metricValue(row, 'negative') }))
    .sort((a, b) => (b.net ?? -Infinity) - (a.net ?? -Infinity));
  return <>
    <p className={s.definition}>控球价值衡量控球队在未来 10 秒内进球的估计概率。一次动作造成的变化称为 PV+。整场净贡献汇总了这些增减。</p>
    {!bundle.capabilities.pv ? <p className={s.empty}>本场尚无控球价值数据。</p> : <>
      <h3>球队整场贡献</h3>
      {teams.length ? <div className={s.valueGrid}>{teams.map(team => <div key={team.team_id} className={s.valueCard}>
        <b data-side={side(bundle, team.team_id)}>{team.team_id === home_id ? home_name : away_name}</b>
        <strong>{signedValue(metricValue(team, 'net'))}</strong>
        <span>净贡献（PV+）</span>
        <p className={s.muted}>增加 {signedValue(metricValue(team, 'positive'))} · 减少 {signedValue(metricValue(team, 'negative'))}</p>
        <ValueActions row={team}/>
      </div>)}</div> : <p className={s.empty}>暂无球队整场汇总。</p>}
      <div className={s.toolbar}><h3>谁增加了进球威胁？</h3>
        {!filters.team && <label>球员贡献图球队<select value={teamId} onChange={event => setChartTeam(event.target.value)}>
          <option value={home_id}>{home_name}</option><option value={away_id}>{away_name}</option>
        </select></label>}
      </div>
      {players.length ? <>
        <div className={s.valueLegend} aria-label="控球价值图例"><span>← 减少威胁</span><span>◆ 净贡献</span><span>增加威胁 →</span></div>
        <PlayerValueChart rows={players}/>
        <details><summary>查看球员数值与动作分类</summary><div className={s.tableWrap}><table className={s.table}>
          <thead><tr><th>球员</th>{pvTotals.map(([code, label]) => <th key={code}>{label}</th>)}<th>动作分类</th></tr></thead>
          <tbody>{players.map(player => <tr key={player.row.player_id}><th scope="row">{player.name}</th>
            {pvTotals.map(([code]) => <td key={code}>{signedValue(player[code])}</td>)}<td><ValueActions row={player.row}/></td>
          </tr>)}</tbody>
        </table></div><p className={s.definition}>定位球明细含子项。球队和球员整场汇总来自 MA30。</p></details>
      </> : <p className={s.empty}>所选球队或球员暂无整场汇总。</p>}
    </>}
  </>;
}

function ShotSummary({ bundle, events }: { bundle: Bundle; events: Event[] }) {
  const { home_id, away_id, home_name, away_name } = bundle.match;
  const values = [home_id, away_id].map(id => {
    const shots = events.filter(event => event.team_id === id && !event.own_goal);
    const xg = shots.flatMap(event => event.xg === null ? [] : [event.xg]);
    const outcomes = shots.map(shotResult);
    const confirmed = outcomes.filter(result => result !== null).length;
    return [
      outcomes.filter(result => result === 'goal').length,
      xg.length ? `${formatValue(xg.reduce((sum, value) => sum + value, 0), 3)} · ${xg.length}/${shots.length}` : '未提供',
      shots.length,
      confirmed === shots.length ? outcomes.filter(result => result === 'goal' || result === 'on_target').length :
        `${outcomes.filter(result => result === 'goal' || result === 'on_target').length}（已确认）`,
    ];
  });
  return <div className={s.tableWrap}><table className={s.table}>
    <caption>当前筛选的射门统计</caption>
    <thead><tr><th scope="col" data-side="home">{home_name}</th><th scope="col">指标</th><th scope="col" data-side="away">{away_name}</th></tr></thead>
    <tbody>{['进球（射门）', '已记录 xG · 覆盖', '射门', '射正'].map((label, index) =>
      <tr key={label}><td>{values[0][index]}</td><th scope="row">{label}</th><td>{values[1][index]}</td></tr>)}</tbody>
  </table></div>;
}

function ShotMapControls({ outcomes, onOutcomes, showOwnGoals, onOwnGoals, legacyOutcomes, shots }: {
  outcomes: ShotOutcome[];
  onOutcomes: (outcomes: ShotOutcome[]) => void;
  showOwnGoals: boolean;
  onOwnGoals: (show: boolean) => void;
  legacyOutcomes: boolean;
  shots: Event[];
}) {
  const missing = shots.filter(shot => !shot.own_goal && shot.xg === null).length;
  const known = shots.filter(shot => !shot.own_goal && shot.xg !== null).length;
  const unlocated = shots.filter(shot => shot.xg !== null && (shot.x === null || shot.y === null)).length;
  return <>
    <fieldset className={s.shotFilters}><legend>射门结果</legend>
      {shotOutcomes.map(([value, label]) => <label key={value}><input type="checkbox" checked={outcomes.includes(value)} onChange={event => onOutcomes(event.target.checked ? [...outcomes, value] : outcomes.filter(outcome => outcome !== value))}/>{label}</label>)}
      <label><input type="checkbox" checked={showOwnGoals} onChange={event => onOwnGoals(event.target.checked)}/>显示乌龙球</label>
    </fieldset>
    <div className={s.shotKey} aria-label="射门图图例">
      <span className={s.shotSizeKey} aria-hidden="true"><i/><i/><i/></span><span>圆面积表示 xG</span>
      <span className={s.shotMissKey} aria-hidden="true"/><span>未进球</span>
      <span className={s.shotGoalKey} aria-hidden="true"/><span>进球</span>
      {showOwnGoals&&<><span className={s.shotOwnGoalKey} aria-hidden="true"/><span>乌龙球</span></>}
    </div>
    <p className={s.definition}>当前筛选 {known + missing} 次射门，其中 {known} 次有 xG。{missing > 0 && `缺少 xG 的 ${missing} 次射门保留在明细中。`}{unlocated > 0 && `另有 ${unlocated} 次射门缺少球场坐标，可在明细中查看。`}{legacyOutcomes && ' 旧记录中的部分射门无法区分射正与封堵，选中全部结果时可查看。'}</p>
  </>;
}

export function VisualizationWorkspace(){
  const [catalog,setCatalog]=useState<Catalog|null>(null),[catalogError,setCatalogError]=useState(''),[searching,setSearching]=useState(true);
  const [competitions, setCompetitions] = useState(() => new Map<string, string>(fixtureCompetitions));
  const [query,setQuery]=useState({date:'',competition:'',q:''}),[request,setRequest]=useState({...query,offset:0});
  const [matchId,setMatchId]=useState(()=>typeof window==='undefined'?'':new URLSearchParams(window.location.search).get('match')??''),[bundle,setBundle]=useState<Bundle|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[reload,setReload]=useState(0);
  const [view,setView]=useState<View>('chalkboard'),[filters,setFilters]=useState<Filters>(initialFilters),[selected,setSelected]=useState('');
  const [outcomes, setOutcomes] = useState<ShotOutcome[]>(allShotOutcomes);
  const [showOwnGoals, setShowOwnGoals] = useState(false);
  const [range, setRange] = useState<TimeRange | null>(null);
  const [heatMode, setHeatMode] = useState<'density'|'touches'>('density');
  const [heatRegion, setHeatRegion] = useState<number[] | null>(null);
  const eventList = useRef<HTMLDetailsElement>(null);
  const [passer, setPasser] = useState(''), [receiver, setReceiver] = useState('');
  const [box,setBox]=useState(false),[passSide,setPassSide]=useState(''),[pair,setPair]=useState(''),[eventLimit,setEventLimit]=useState(eventPageSize),[pvOrder,setPvOrder]=useState('value');
  useEffect(()=>{
    const controller=new AbortController();queueMicrotask(()=>{if(!controller.signal.aborted){setSearching(true);setCatalogError('');}});
    const params=new URLSearchParams({competition:request.competition,q:request.q,offset:String(request.offset),limit:'12'});
    if(request.date)params.set('date',request.date);
    getVisualization<Catalog>('matches?'+params,controller.signal).then(result=>{
      if(controller.signal.aborted)return;
      setCatalog(result);setMatchId(old=>old||result.matches[0]?.id||'');
      setCompetitions(previous => {
        const next = new Map(previous);
        for (const match of result.matches) if (match.competition && !next.has(match.competition)) next.set(match.competition, match.competition);
        return next;
      });
    }).catch(e=>{if(!controller.signal.aborted)setCatalogError(e.message);}).finally(()=>{if(!controller.signal.aborted)setSearching(false);});
    return()=>controller.abort();
  },[request]);
  useEffect(()=>{
    if(!matchId)return;const controller=new AbortController();queueMicrotask(()=>{if(!controller.signal.aborted){setLoading(true);setBundle(null);setError('');setSelected('');setPair('');setPasser('');setReceiver('');setRange(null);setFilters(initialFilters);setOutcomes(allShotOutcomes);setShowOwnGoals(false);setHeatRegion(null);setBox(false);setEventLimit(eventPageSize);}});
    loadBundle(matchId,controller.signal).then(result=>{if(!controller.signal.aborted){setBundle(result);setPassSide(result.match.home_id);
      const params=new URLSearchParams(window.location.search);
      if(params.get('match')===result.match.id){const event=result.events.find(e=>e.id===params.get('event'));
        if(event){const position=axisPosition(matchTime(result),event);setSelected(event.id);setView('chalkboard');if(position!==null)setRange([Math.max(0,position-15),position+15]);}
        const player=params.get('player');if(player&&result.players.some(p=>p.id===player))setFilters({...initialFilters,players:[player]});
      }
    }}).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[matchId,reload]);
  const change=<K extends keyof Filters>(key:K,value:Filters[K])=>{setFilters(f=>({...f,[key]:value}));setEventLimit(eventPageSize);};
  const selectTeam = (team: string) => {
    if (team === filters.team) return;
    setFilters(previous => ({ ...previous, team, players: [] }));
    setHeatRegion(null);
    setSelected('');
    setEventLimit(eventPageSize);
  };
  const time = useMemo(() => bundle ? matchTime(bundle) : null, [bundle]);
  const timeEvents = useMemo(() => bundle && time ? bundle.events.filter(event => inTimeRange(time, range, event)) : [], [bundle, time, range]);
  const tactical = view === 'chalkboard' || view === 'pro';
  const shotMode = view === 'xg' || tactical && filters.kind === 'shot';
  const effective = view === 'passes' ? { ...initialFilters, kind: 'pass', team: passSide } :
    view === 'heatmap' ? { ...filters, region: heatRegion } : shotMode ? { ...filters, result: '' } : filters;
  let events = filterEvents(timeEvents, effective, view, bundle?.match.away_id);
  const legacyOutcomes = shotMode && events.some(event => shotResult(event) === null);
  if (shotMode) events = events.filter(event => {
    if (event.own_goal && !showOwnGoals) return false;
    const result = shotResult(event);
    return result === null ? outcomes.length === allShotOutcomes.length : outcomes.includes(result);
  });
  if (view === 'pv') events = events.filter(event => event.pv_added !== null).sort((a, b) => pvOrder === 'time' ? a.period - b.period || a.minute - b.minute || a.second - b.second : ((b.pv_added ?? 0) - (a.pv_added ?? 0)) * (pvOrder === 'loss' ? -1 : 1));
  if (view === 'players' && !effective.players.length) events = [];
  const active = events.find(event => event.id === selected) ?? bundle?.markers.find(event => event.id === selected) ??
    bundle?.events.find(event => (event.period === 5 || event.kind === 'shot' && event.penalty) && event.id === selected);
  const passEdges = bundle?.pass_edges.filter(edge => edge.team_id === passSide) ?? [];
  const passPlayers = (key: 'from_id' | 'to_id') => [...new Set(passEdges.map(edge => edge[key]))].map(id => ({ id, name: bundle?.players.find(player => player.id === id)?.name ?? '未知球员' }));
  const shown = bundle ? { ...bundle, pass_edges: passEdges.filter(edge => (!passer || edge.from_id === passer) && (!receiver || edge.to_id === receiver)), pass_nodes: bundle.pass_nodes.filter(node => node.team_id === passSide) } : null;
  const applyRange = (next: TimeRange | null) => { setRange(next); setSelected(''); setEventLimit(eventPageSize); };
  const heatEvents = view === 'heatmap' ? filterEvents(timeEvents, { ...effective, region: null }, view) : [];
  const keyShots = bundle?.events.filter(event => event.kind === 'shot' && (event.period === 5 || event.penalty && !event.goal)) ?? [];
  const markerEvents = [...new Map([...keyShots, ...(bundle?.markers ?? [])].map(event => [event.id, event])).values()];
  const hasPitchSelection = Boolean(range || filters.team || filters.players.length || filters.kind || filters.result ||
    view === 'pro' && (filters.special || filters.pv || filters.region));
  const pitchEvents = view === 'heatmap' ? heatMode === 'density' ? heatEvents : events :
    tactical && !hasPitchSelection ? events.filter(event => event.id === selected) : events;
  const compared = bundle ? filters.players.flatMap(id => bundle.players.filter(player => player.id === id)) : [];
  const sameSide = compared.length === 2 && compared[0].team_id === compared[1].team_id;
  const players=bundle?.players.filter(p=>!filters.team||p.team_id===filters.team)??[];
  const selectPlayer = (id: string, checked: boolean) => change('players', checked ? [...filters.players, id] : filters.players.filter(player => player !== id));
  const pairSelected=(a:string,b:string,n:number)=>setPair(`${bundle?.players.find(p=>p.id===a)?.name??'未知球员'} → ${bundle?.players.find(p=>p.id===b)?.name??'未知球员'}：整场 ${n} 次成功传球`);
  const selectView = (id: View) => { setView(id); setSelected(''); setEventLimit(eventPageSize); setPair(''); setBox(false); };
  const tabKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const next = { ArrowLeft: (index + views.length - 1) % views.length, ArrowRight: (index + 1) % views.length, Home: 0, End: views.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const [id] = views[next];
    selectView(id);
    document.getElementById(`studio-tab-${id}`)?.focus();
  };
  return <main className={s.page}>
    <header className={s.header}><div><p className={s.muted}>CC · 比赛研究</p><h1>比赛可视化</h1></div><Link href="/studio/prematch">赛前分析</Link><Link href="/studio">返回工作台</Link></header>
    <details className={s.catalog} open={!matchId||!!catalogError}><summary>选择比赛</summary><form className={s.search} onSubmit={e=>{e.preventDefault();setRequest({...query,offset:0});}}>
      <label>比赛日期<input type="date" value={query.date} onChange={e=>setQuery({...query,date:e.target.value})}/></label>
      <label>赛事<select value={query.competition} onChange={e=>setQuery({...query,competition:e.target.value})}><option value="">全部已入库赛事</option>{[...competitions].map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
      <label>球队搜索<input aria-label="球队搜索" placeholder="输入球队名称，例如 Manchester" value={query.q} onChange={e=>setQuery({...query,q:e.target.value})} maxLength={80}/></label><button disabled={searching}>查找比赛</button>
    </form>{catalogError&&<p className={s.error} role="alert">{catalogError}</p>}{searching?<p role="status">正在查找比赛…</p>:<>
      <div className={s.matchList}>{catalog?.matches.map(m=><button key={m.id} aria-pressed={matchId===m.id} onClick={()=>setMatchId(m.id)}><strong><span data-side="home">{m.home_name}</span>{' '}<span>{m.home_score??'—'}–{m.away_score??'—'}</span>{' '}<span data-side="away">{m.away_name}</span></strong><small className={s.muted}>{m.competition} · {m.kickoff_at?kickoffFormat.format(new Date(m.kickoff_at)):m.date}</small></button>)}</div>
      {catalog?.matches.length===0&&<p className={s.empty}>没有符合条件且已有事件数据的完赛比赛。</p>}
      <div className={s.pager}><button disabled={!catalog||catalog.offset===0} onClick={()=>setRequest({...request,offset:Math.max(0,request.offset-12)})}>上一页</button><span className={s.muted}>第 {Math.floor(request.offset/12)+1} 页 · 日期按北京时间筛选</span><button disabled={!catalog?.has_more} onClick={()=>setRequest({...request,offset:request.offset+12})}>下一页</button></div>
    </>}</details>
    {loading&&<p role="status" className={s.empty}>正在加载整场数据，完成版本核对后显示图表…</p>}
    {error&&<div className={s.error} role="alert">{error} <button onClick={()=>setReload(x=>x+1)}>重新加载整场</button></div>}
    {bundle&&time&&bundle.match.id===matchId&&<div className={s.matchBody}>
      <header className={s.scoreboard}>
        <div className={s.scoreMeta}><span>{bundle.match.competition ?? '比赛'}</span><span>完赛</span>{bundle.match.kickoff_at && <time dateTime={bundle.match.kickoff_at}>{kickoffFormat.format(new Date(bundle.match.kickoff_at))} 北京时间</time>}</div>
        <h2><span className={s.scoreTeam} data-side="home"><TeamBadge teamName={bundle.match.home_name} crestUrl={bundle.match.home_crest_url} size={48} eager/><span>{bundle.match.home_name}</span></span>{' '}
          <strong>{bundle.match.home_score ?? '—'}–{bundle.match.away_score ?? '—'}</strong>{' '}
          <span className={s.scoreTeam} data-side="away"><span>{bundle.match.away_name}</span><TeamBadge teamName={bundle.match.away_name} crestUrl={bundle.match.away_crest_url} size={48} eager/></span></h2>
        <div className={s.scoreMeta}><span>入库时间（UTC）{bundle.updated_at} · {bundle.event_total} 条比赛事件</span><button onClick={()=>setReload(x=>x+1)}>刷新数据</button></div>
      </header>
      <MatchTimeline key={bundle.match.id} time={time} range={range} markers={markerEvents} homeId={bundle.match.home_id} awayId={bundle.match.away_id} selected={active?.id} onRange={applyRange} onEvent={event => setSelected(event.id)}/>
      <nav className={s.tabs} aria-label="可视化视图" role="tablist">{views.map(([id,name,en],index)=><button key={id} id={`studio-tab-${id}`} role="tab" tabIndex={view===id?0:-1} aria-selected={view===id} aria-controls="studio-view" onClick={()=>selectView(id)} onKeyDown={event=>tabKey(event,index)}>{name}{' '}<small>{en}</small></button>)}</nav>
      <div className={s.layout}>
        <details className={s.filters}><summary>阵容与事件筛选</summary><div className={s.controls}>
          {view!=='passes'&&view!=='heatmap'&&<label>球队<select value={filters.team} onChange={e=>selectTeam(e.target.value)}><option value="">双方</option><option value={bundle.match.home_id}>{bundle.match.home_name}</option><option value={bundle.match.away_id}>{bundle.match.away_name}</option></select></label>}
          {view!=='passes'&&<div><p className={s.muted}>{view==='players'?'球员（选一至两名对比）':'球员（可多选）'}</p><div className={s.players}>{players.map(p=><label key={p.id} data-side={side(bundle,p.team_id)}><input type="checkbox" checked={filters.players.includes(p.id)} disabled={view==='players'&&filters.players.length>=2&&!filters.players.includes(p.id)} onChange={e=>selectPlayer(p.id,e.target.checked)}/><span>{p.number??'—'} {p.name}{p.minutes===0?'（未上场）':''}</span></label>)}</div></div>}
          {!tactical && view !== 'xg' && view !== 'passes' && <label>事件类型<select value={filters.kind} onChange={e=>change('kind',e.target.value)}><option value="">全部动作</option>{Object.entries(kinds).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>}
          {view !== 'passes' && !shotMode && <label>动作结果<select value={filters.result} onChange={e=>change('result',e.target.value)}><option value="">全部</option><option value="success">成功</option><option value="failure">未成功</option></select></label>}
          {view==='xg'&&<label>点球<select value={filters.penalty} onChange={e=>change('penalty',e.target.value)}><option value="">包含点球</option><option value="exclude">排除点球（须已确认）</option><option value="only">只看点球</option></select></label>}
          {view==='pro'&&<><label>进攻动作<select value={filters.special} onChange={e=>change('special',e.target.value)}><option value="">全部</option><option value="key">关键传球／助攻</option><option value="third">进入进攻三区</option><option value="box">进入禁区</option></select></label><label>最低 PV 增量<input type="number" step="0.01" placeholder="不限制" value={filters.pv} onChange={e=>change('pv',e.target.value)}/></label></>}
          <button onClick={()=>{setFilters(initialFilters);setOutcomes(allShotOutcomes);setShowOwnGoals(false);setHeatRegion(null);applyRange(null);}}>重置筛选</button>
        </div></details>
        <div className={s.content}>
          <section className={s.panel} id="studio-view" role="tabpanel" aria-labelledby={`studio-tab-${view}`}>
            <h2>{views.find(v=>v[0]===view)?.[1]}</h2>
            <div className={s.legend}><span data-side="home">● {bundle.match.home_name}</span><span data-side="away">● {bundle.match.away_name}</span>{sameSide&&tactical&&!shotMode&&<span data-side={side(bundle,compared[0].team_id)}>● {compared[0].name}　■ {compared[1].name}</span>}</div>
            {view==='momentum'?<>
              <p className={s.definition}>零轴上方主队占优，下方客队占优。纵轴是双方短时进球威胁的差，单位为百分点。竖虚线标记进球。拖选图中一段，或拖动时间轴端点查看时段统计。</p>
              {bundle.capabilities.momentum?<MomentumChart bundle={bundle} time={time} range={range} markers={markerEvents} onRange={applyRange}/>:<p className={s.empty}>本场 MA27 势头尚未采集。时间轴仍可筛选比赛事件。</p>}
              <RangeStats bundle={bundle} events={timeEvents} scoped={range !== null}/>
            </>:view==='passes'&&shown?<>
              <RangeStats bundle={bundle} events={timeEvents} scoped={range !== null}/>
              <h3>整场传球关系</h3>
              <p className={s.definition}>行是传球人，列是接球人。网络使用球员的整场平均位置，时段筛选应用于上方统计与下方传球明细。</p>
              <div className={s.toolbar}><label>显示球队<select value={passSide} onChange={e=>{setPassSide(e.target.value);setPasser('');setReceiver('');setPair('');}}><option value={bundle.match.home_id}>{bundle.match.home_name}</option><option value={bundle.match.away_id}>{bundle.match.away_name}</option></select></label></div>
              <div className={s.toolbar}>{([['from_id', '传球人', passer, setPasser], ['to_id', '接球人', receiver, setReceiver]] as const).map(([key, label, value, update]) => <label key={key}>{label}<select value={value} onChange={event => { update(event.target.value); setPair(''); }}><option value="">全部</option>{passPlayers(key).map(player => <option key={player.id} value={player.id}>{player.name}</option>)}</select></label>)}</div>
              {pair&&<p role="status">{pair}</p>}{!shown.pass_edges.length?<p className={s.empty}>当前筛选暂无整场传球关系。</p>:<><div className={s.chart}><Pitch events={[]} match={shown} network onPair={pairSelected} onSelect={()=>{}}/></div><Matrix bundle={shown} onPair={pairSelected}/></>}
            </>:<>
              {view==='players'&&<><p className={s.definition}>选择一至两名球员查看整场技术统计。时间轴筛选下方的逐事件明细。</p><PlayerStats bundle={bundle} ids={filters.players}/></>}
              {view==='pv'&&<PossessionValue key={bundle.match.id} bundle={bundle} filters={filters}/>}
              {view==='heatmap'&&<>
                <div className={s.toolbar} role="group" aria-label="热图球队">
                  <button aria-pressed={!filters.team} onClick={()=>selectTeam('')}>双方</button>
                  <button data-side="home" aria-pressed={filters.team===bundle.match.home_id} onClick={()=>selectTeam(bundle.match.home_id)}>{bundle.match.home_name}</button>
                  <button data-side="away" aria-pressed={filters.team===bundle.match.away_id} onClick={()=>selectTeam(bundle.match.away_id)}>{bundle.match.away_name}</button>
                </div>
                <div className={s.toolbar} role="group" aria-label="事件热图模式">
                  <button aria-pressed={heatMode==='density'} onClick={()=>setHeatMode('density')}>热区</button>
                  <button aria-pressed={heatMode==='touches'} onClick={()=>{setHeatMode('touches');setHeatRegion(null);setEventLimit(eventPageSize);}}>球上事件点</button>
                  {heatRegion&&<button onClick={()=>{setHeatRegion(null);setEventLimit(eventPageSize);}}>清除热图区域</button>}
                </div>
                <p className={s.definition}>{heatMode==='density'?'颜色深浅表示已记录球上动作的密度。点击热区查看该格的事件。':'每个点是一条带起点坐标的球上动作。点击查看事件。'}</p>
              </>}
              {tactical&&<><div className={s.toolbar}><label>球场动作<select value={filters.kind} onChange={event=>change('kind',event.target.value)}><option value="">选择动作</option>{Object.entries(kinds).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label></div><p className={s.definition}>{shotMode ? '主队向右进攻，客队向左进攻。点击标记查看射门明细。' : <>箭头为传球方向，虚线为未成功传球；圆点为动作起点。主队向右进攻，客队向左进攻。缺少终点只显示起点。{view==='pro'?'可框选起点区域、比较两名球员，使用进攻三区和禁区几何筛选。':''}</>}</p>{!hasPitchSelection&&!active&&<p className={s.definition}>选择动作、球队、球员或比赛时段，在球场查看对应事件。</p>}</>}
              {view==='pro'&&<div className={s.toolbar}>
                <button aria-pressed={box} onClick={()=>setBox(!box)}>{box?'取消框选':'框选起点区域'}</button>
                {filters.region&&<button onClick={()=>{change('region',null);setBox(false);}}>清除框选区域</button>}
                <span role="status" className={s.muted}>{box?'在球场上拖出矩形':filters.region?`框选内 ${events.length} 条事件`:''}</span>
              </div>}
              {shotMode&&<ShotMapControls outcomes={outcomes} onOutcomes={value=>{setOutcomes(value);setSelected('');setEventLimit(eventPageSize);}} showOwnGoals={showOwnGoals} onOwnGoals={value=>{setShowOwnGoals(value);setSelected('');setEventLimit(eventPageSize);}} legacyOutcomes={legacyOutcomes} shots={events}/>}
              {view!=='players'&&view!=='pv'&&<div className={s.chart}><Pitch events={pitchEvents} match={bundle} onSelect={e=>setSelected(e.id)} selected={pitchEvents.some(event=>event.id===active?.id)?active?.id:undefined} heat={view==='heatmap'?heatMode:undefined} shotMap={shotMode} region={view==='pro'?filters.region:view==='heatmap'?heatRegion:null} onHeatRegion={r=>{
                setHeatRegion(r);setSelected('');setEventLimit(eventPageSize);
                if (eventList.current) eventList.current.open = true;
              }} onRegion={view==='pro'&&box?r=>{change('region',r);setBox(false);}:undefined} players={filters.players}/></div>}
              {view==='heatmap'&&heatRegion&&<p className={s.definition} role="status">所选热区 {events.length} 条事件，已展开下方明细。</p>}
              {view==='xg'&&<><ShotSummary bundle={bundle} events={events}/><h3>{range?'所选时段累计 xG':'全场累计 xG'}</h3><XgRace bundle={bundle} time={time} range={range} events={events} teamIds={filters.team?[filters.team]:filters.players.length?[...new Set(bundle.players.filter(p=>filters.players.includes(p.id)).map(p=>p.team_id))]:undefined} onSelect={event=>setSelected(event.id)}/></>}
              {view==='pro'&&<div className={s.toolbar}><button disabled={!events.length||events.findIndex(e=>e.id===selected)<=0} onClick={()=>setSelected(events[Math.max(0,events.findIndex(e=>e.id===selected)-1)].id)}>上一个动作</button><button disabled={!events.length||events.findIndex(e=>e.id===selected)>=events.length-1} onClick={()=>setSelected(events[Math.min(events.length-1,events.findIndex(e=>e.id===selected)+1)].id)}>下一个动作</button><span className={s.muted}>按比赛时间逐事件查看</span></div>}
            </>}
          </section>
          <details className={s.panel} key={bundle.match.id+view} ref={eventList}><summary>{view==='pv'?'哪些动作改变了威胁？':view==='passes'?'传球明细':view==='heatmap'&&heatRegion?'所选热区事件':'事件明细'} · {events.length}</summary>
            {view==='pv'&&<><p className={s.definition}>{range?'仅显示时间轴所选时段。':'当前显示全场，可用时间轴缩小时段。'}数值为动作造成的概率变化，单位为百分点。</p>
              <div className={s.toolbar}><label>排序<select value={pvOrder} onChange={e=>{setPvOrder(e.target.value);setEventLimit(eventPageSize);}}><option value="value">威胁增加最多</option><option value="loss">威胁减少最多</option><option value="time">比赛时间</option></select></label></div></>}
            {!events.length&&<p className={s.empty}>当前筛选暂无事件。</p>}
            <ul className={s.rows}>{events.slice(0,eventLimit).map(e=><li key={e.id}><button aria-pressed={selected===e.id} onClick={()=>setSelected(e.id)}>
              <b data-side={side(bundle,e.team_id)}>{e.clock}</b><span>{e.player_name??'未知球员'}<small className={s.muted}>{eventLabel(e)}{e.key_pass?' · 关键传球':''}{e.assist?' · 助攻':''}</small></span>
              <span>{view==='pv'&&e.pv_added!==null?`${signedValue(e.pv_added * 100)} 个百分点`:e.kind==='shot'?`xG ${formatValue(e.xg,3)}`:e.success===null?'':e.success?'成功':'未成功'}</span>
            </button></li>)}</ul>
            {events.length>eventLimit&&<button onClick={()=>setEventLimit(n=>n+eventPageSize)}>显示更多事件</button>}
          </details>
          <details className={s.panel}><summary>数据说明</summary>{bundle.notes.map(n=><p key={n} className={s.definition}>{n}</p>)}<p className={s.definition}>未解析动作 {bundle.events.filter(e=>e.kind==='unknown').length} 条；未知类型不猜测足球含义。数据来自已保存比赛记录，刷新页面不会触发上游采集。</p></details>
        </div>
        <aside className={s.detail} data-active={!!active} aria-label="事件详情">{active&&<button onClick={()=>setSelected('')}>关闭详情</button>}<EventDetails event={active}/></aside>
      </div>
    </div>}
  </main>;
}
