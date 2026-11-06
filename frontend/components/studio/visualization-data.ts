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
  return events.filter(e=>{
    if(f.team && e.team_id!==f.team)return false;
    if(f.players.length && !f.players.includes(e.player_id ?? ''))return false;
    if(f.result && e.success!==(f.result==='success'))return false;
    if(view==='heatmap' && (!ballKinds.has(e.kind)||e.x===null||e.y===null))return false;
    if(f.region && (view==='heatmap' || view==='pro')) {
      if (e.x===null || e.y===null) return false;
      const [x,y] = matchPitchPoint(e.x,e.y,e.team_id===awayId);
      const [left,bottom,right,top] = f.region;
      if (x<left || x>right || y<bottom || y>top) return false;
      if (view==='heatmap' && (x===right&&right<100 || y===top&&top<100)) return false;
    }
    if(view==='xg') {
      if(e.kind!=='shot')return false;
      if(f.penalty==='exclude' && e.penalty!==false)return false;
      if(f.penalty==='only' && e.penalty!==true)return false;
    } else if(f.kind && e.kind!==f.kind)return false;
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
