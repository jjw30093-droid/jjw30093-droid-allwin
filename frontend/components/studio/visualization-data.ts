import type { components } from './visualization-api-types';

export type Bundle = components['schemas']['Bundle'];
export type Match = components['schemas']['Match'];
export type Catalog = components['schemas']['Catalog'];
export type Event = components['schemas']['Event'];
export type Filters = {
  team: string; players: string[]; kind: string; result: string;
  special: string; pv: string; penalty: string;
  region: number[] | null;
};
export const initialFilters: Filters = {team:'',players:[],kind:'',result:'',special:'',pv:'',penalty:'',region:null};
export const views = [
  ['chalkboard','事件战术板','Chalkboard'],['pro','高级战术板','Pro Chalkboard'],
  ['heatmap','事件热图','Heatmap'],['passes','传球矩阵','Pass Matrix'],
  ['xg','预期进球','Expected Goals'],['players','球员表现','Player Match Performance'],
  ['momentum','比赛势头','Momentum'],['pv','控球价值','Possession Value'],
] as const;
export type View = typeof views[number][0];
export const kinds: Record<string,string> = {pass:'传球',shot:'射门',take_on:'突破',tackle:'抢断',interception:'拦截',clearance:'解围',recovery:'夺回球权',save:'扑救',duel:'对抗',touch:'触球',dispossessed:'被断球',foul:'犯规',unknown:'未解析动作'};
export const shotOutcomes = [
  ['goal', '进球'], ['on_target', '射正（未进）'], ['off_target', '射偏／中框'], ['blocked', '被封堵'],
] as const;
export type ShotOutcome = (typeof shotOutcomes)[number][0];

export function shotResult(event: Event): ShotOutcome | null {
  if (event.shot_outcome) return event.shot_outcome;
  if (event.goal || event.own_goal) return 'goal';
  if (event.label === '射门偏出' || event.label === '射门中框') return 'off_target';
  return null;
}

export const chalkboardGroups = [
  ['射门', [['shot:goal','进球'],['shot:on_target','射正（未进）'],['shot:off_target','射偏／中框'],['shot:blocked','射门被封堵']]],
  ['传球', [['pass:won','成功传球'],['pass:lost','失败传球'],['cross:open_play:won','成功的运动战传中'],['cross:open_play:lost','失败的运动战传中'],['cross:restart:won','成功的定位球传中'],['cross:restart:lost','失败的定位球传中'],['pass:key','关键传球'],['pass:assist','助攻']]],
  ['防守', [['tackle:won','抢断成功'],['tackle:lost','抢断后未取得球权'],['defensive_block','防守封堵'],['interception','拦截'],['clearance','解围'],['recovery','夺回球权']]],
  ['控球', [['take_on:won','突破成功'],['take_on:lost','突破失败']]],
  ['纪律', [['foul:won','赢得犯规'],['foul:lost','犯规'],['pass:offside','越位']]],
  ['门将', [['save','扑救'],['keeper_catch','接高球'],['keeper_punch','击球解围'],['keeper_unclaimed','漏接传中（Drops）']]],
] as const;

export const chalkboardExtra = ['其他比赛记录', [
  ['pass','全部传球记录'],['blocked_pass','封堵传球'],['duel','争顶／对抗'],['touch','触球'],
  ['dispossessed','被断球'],['offside_provoked','造成对手越位'],['keeper_pickup','拾球'],['keeper_sweeper','出击'],
  ['out','出界'],['corner_awarded','角球判罚'],['shield_out','护球出界'],['error','导致射门或失球的失误'],
  ['chance_missed','错失机会（未射门）'],['type_83','尝试抢断'],['unknown','未解析动作'],
]] as const;

function matchesChalkboardAction(e: Event, action: string) {
  const [kind, filter, crossResult] = action.split(':');
  if (kind === 'cross') return e.kind === 'pass' && e.cross_phase === filter && e.success === (crossResult === 'won');
  if (kind === 'recovery') return e.kind === 'recovery' || e.kind === 'keeper_pickup' || e.kind === 'keeper_catch' && e.success === true;
  if (kind !== e.kind) return false;
  if (!filter) return true;
  if (kind === 'shot') return shotResult(e) === filter;
  if (kind === 'foul') return filter === 'won' ? e.label === '赢得犯规' : e.event_type_id === 4 && e.label === '犯规';
  if (filter === 'offside') return e.event_type_id === 2 || e.label === '越位传球';
  if (filter === 'key') return e.key_pass && !e.assist;
  if (filter === 'assist') return e.assist;
  if (kind === 'pass' && !(e.ordinary_pass ?? !e.cross_phase)) return false;
  return e.success === (filter === 'won');
}

export function chalkboardPlotEvent(event: Event, actions: string, officialOnly: boolean): Event | null {
  if (event.details_only) return null;
  if (officialOnly && !actions && !chalkboardGroups.some(([, options]) => options.some(([id]) => matchesChalkboardAction(event, id)))) return null;
  if (event.event_type_id !== 2 || actions && !actions.split(',').includes('pass:offside')) return event;
  return event.chalkboard_offside ? {
    ...event, ...event.chalkboard_offside, kind: 'offside', label: '越位', end_x: null, end_y: null,
  } : null;
}

export function eventLabel(event: Event) {
  return event.kind === 'shot' && event.label === '射门被扑' && !event.shot_outcome ? '射门（结果未细分）' : event.label;
}

export function shotMapRadius(event: Pick<Event, 'xg' | 'own_goal'>) {
  // Opta scales the area in CSS pixels and uses a fixed size for an own goal.
  return event.own_goal ? 3 : 35 * Math.sqrt(Math.max(0, event.xg ?? 0) / Math.PI);
}

export const ballKinds = new Set(['pass','shot','take_on','tackle','interception','clearance','recovery','save','duel','touch','dispossessed']);
// The source uses a separate left-to-right attacking frame for each team.
export function matchPitchPoint(x: number, y: number, away: boolean): [number, number] {
  return away ? [100 - x, 100 - y] : [x, y];
}

export function filterEvents(events: Event[], f: Filters, view: View, awayId?: string): Event[] {
  const tactical = view === 'chalkboard' || view === 'pro';
  const actions = tactical && f.kind ? f.kind.split(',') : [];
  return events.filter(e=>{
    const plotEvent = tactical ? chalkboardPlotEvent(e, f.kind, false) : e;
    if(f.team && e.team_id!==f.team)return false;
    if(f.players.length && !f.players.includes((plotEvent ?? e).player_id ?? ''))return false;
    if(f.result && e.success!==(f.result==='success'))return false;
    if(view==='heatmap' && (!ballKinds.has(e.kind)||e.x===null||e.y===null))return false;
    if(f.region && (view==='heatmap' || view==='pro')) {
      if (plotEvent?.x == null || plotEvent.y === null) return false;
      const [x,y] = matchPitchPoint(plotEvent.x,plotEvent.y,e.team_id===awayId);
      const [left,bottom,right,top] = f.region;
      if (x<left || x>right || y<bottom || y>top) return false;
      if (view==='heatmap' && (x===right&&right<100 || y===top&&top<100)) return false;
    }
    if(view==='xg') {
      if(e.kind!=='shot')return false;
      if(f.penalty==='exclude' && e.penalty!==false)return false;
      if(f.penalty==='only' && e.penalty!==true)return false;
    } else if(f.kind && (tactical ? !actions.some(action => matchesChalkboardAction(e, action)) : e.kind!==f.kind))return false;
    if(view==='pro') {
      if(f.special==='key' && !(e.key_pass||e.assist))return false;
      if(f.special==='third' && !(e.kind==='pass' && e.success===true && e.x!==null && e.x<200/3 && e.end_x!==null && e.end_x>=200/3))return false;
      if(f.special==='box' && !(e.kind==='pass' && e.success===true && e.end_x!==null && e.end_y!==null && e.end_x>=83 && e.end_y>=21.1 && e.end_y<=78.9 && !(e.x!==null && e.y!==null && e.x>=83 && e.y>=21.1 && e.y<=78.9)))return false;
      if(f.pv!=='' && (e.pv_added===null || e.pv_added<Number(f.pv)))return false;
    }
    return true;
  });
}
export function formatValue(value:number|null|undefined,digits=2) {return value==null?'未提供':value.toLocaleString('zh-CN',{maximumFractionDigits:digits});}
export async function getVisualization<T>(path: string, signal: AbortSignal): Promise<T> {
  const response=await fetch(`/api/v1/studio/visualization/${path}`,{credentials:'include',cache:'no-store',signal});
  if(!response.ok) {
    const messages:Record<number,string>={401:'请先登录',403:'需要管理员权限',404:'比赛尚未接入可视化',409:'比赛数据已更新，请重新加载整场数据'};
    throw new Error(messages[response.status]??'比赛数据暂不可用，请重试');
  }
  return response.json() as Promise<T>;
}
export async function loadBundle(id: string, signal: AbortSignal): Promise<Bundle> {
  const first=await getVisualization<Bundle>(`matches/${id}`,signal);
  if(first.match.id!==id||first.offset!==0)throw new Error('比赛数据校验失败');
  const events=[...first.events];let page=first;
  while(page.has_more) {
    if(page.events.length===0 || events.length>=10000)throw new Error('事件分页不完整，请重新加载');
    page=await getVisualization<Bundle>(`matches/${id}?offset=${events.length}&version=${first.version}`,signal);
    if(page.version!==first.version || page.match.id!==id || page.offset!==events.length)throw new Error('比赛数据已更新，请重新加载整场数据');
    events.push(...page.events);
  }
  if(events.length!==first.event_total||new Set(events.map(e=>e.id)).size!==events.length)throw new Error('事件分页不完整，请重新加载');
  return {...first,events,has_more:false};
}
