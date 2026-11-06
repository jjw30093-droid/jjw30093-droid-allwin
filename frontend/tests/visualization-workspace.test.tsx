import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {VisualizationGate,VisualizationWorkspace} from '@/components/studio/visualization-workspace';
import {getMe} from '@/lib/api-v1';
import {getVisualization,loadBundle,type Bundle,type Event,type Match} from '@/components/studio/visualization-data';

vi.mock('@/lib/api-v1',()=>({getMe:vi.fn()}));
vi.mock('@/components/studio/visualization-charts',()=>({Pitch:({events}:{events:Event[]})=> <div>球场图 · {events.length}</div>,MomentumChart:({time,onRange}:{time:{phases:{offset:number}[]};onRange:(range:[number,number])=>void})=> <button onClick={()=>onRange([time.phases[1].offset+15*60,time.phases[1].offset+27*60])}>模拟拖选 60–72</button>,XgRace:()=> <div>xG 曲线图</div>,Matrix:()=> <div>传球矩阵图</div>}));
vi.mock('@/components/studio/visualization-data',async importOriginal=>({...await importOriginal<object>(),getVisualization:vi.fn(),loadBundle:vi.fn()}));
const match=(id:string):Match=>({id,competition:'EPL',date:'2026-11-03',kickoff_at:null,home_id:'h',away_id:'a',home_name:id+'主队',away_name:id+'客队',home_score:0,away_score:0});
const bundle=(id:string):Bundle=>({schema_version:'match-visualization.v1',match:match(id),version:'0'.repeat(64),updated_at:'2026-11-04',players:[],events:[],offset:0,limit:500,event_total:0,has_more:false,excluded_events:0,markers:[],pass_edges:[],pass_nodes:[],player_stats:[],momentum:[],pv_teams:[],pv_players:[],pv_observed_at:null,capabilities:{events:false,shots:false,passes:false,player_stats:false,momentum:false,pv:false},notes:[]});
beforeEach(()=>{vi.resetAllMocks();vi.mocked(getVisualization).mockResolvedValue({schema_version:'match-visualization.v1',matches:[match('甲'),match('乙')],offset:0,limit:12,has_more:false});});
afterEach(cleanup);

it('does not fetch research data for a non-admin',async()=>{
 vi.mocked(getMe).mockResolvedValue({authenticated:true,user:{role:'analyst'}} as Awaited<ReturnType<typeof getMe>>);
 render(<VisualizationGate/>);await screen.findByText('此工作台仅限管理员使用');
 expect(getVisualization).not.toHaveBeenCalled();expect(loadBundle).not.toHaveBeenCalled();
});

it('ignores an old completed request after switching matches',async()=>{
 let finishA!:(b:Bundle)=>void,finishB!:(b:Bundle)=>void;
 vi.mocked(loadBundle).mockImplementation(id=>new Promise(resolve=>{if(id==='甲')finishA=resolve;else finishB=resolve;}));
 render(<VisualizationWorkspace/>);await waitFor(()=>expect(loadBundle).toHaveBeenCalledTimes(1));
 fireEvent.click(screen.getByText('选择比赛',{exact:true}));
 fireEvent.click(await screen.findByRole('button',{name:/乙主队 0–0 乙客队/}));
 await waitFor(()=>expect(loadBundle).toHaveBeenCalledTimes(2));
 await act(async()=>{finishA(bundle('甲'));});
 expect(screen.queryByRole('heading',{name:'甲主队 0–0 甲客队'})).toBeNull();
 await act(async()=>{finishB(bundle('乙'));});
 await screen.findByRole('heading',{name:'乙主队 0–0 乙客队'});
 fireEvent.click(screen.getByRole('tab',{name:/传球矩阵\s*Pass Matrix/}));
 expect(screen.getByRole('slider',{name:'区间起点'})).toBeTruthy();
 expect(screen.getByRole('button',{name:'全场'}).getAttribute('aria-pressed')).toBe('true');
});

it('omits an empty date and distinguishes missing PV from a failed request',async()=>{
 vi.mocked(loadBundle).mockResolvedValue(bundle('甲'));render(<VisualizationWorkspace/>);
 await screen.findByRole('tab',{name:/控球价值\s*Possession Value/});
 expect(vi.mocked(getVisualization).mock.calls[0][0]).not.toContain('date=');
 fireEvent.click(screen.getByRole('tab',{name:/控球价值\s*Possession Value/}));
 await screen.findByText('本场尚无控球价值数据。');expect(screen.queryByRole('alert')).toBeNull();
});


const matchEvent=(id:string,period:number,minute:number,player:string,kind='pass'):Event=>({id,period,minute,second:0,clock:`${minute}:00`,elapsed:minute*60,team_id:'h',player_id:player,player_name:player,kind,label:kind==='shot'?'射门':'传球',success:true,x:50,y:50,end_x:65,end_y:50,key_pass:false,assist:false,xg:kind==='shot'?0.23:null,xgot:null,goal:false,own_goal:false,penalty:false,pv_value:null,pv_added:null});

it('shares a Momentum selection with event views, and opens an incident by its event ID',async()=>{
 const shot=matchEvent('shot',2,63,'区间射门球员','shot');
 const before=matchEvent('before',1,47,'补时球员');
 const after=matchEvent('after',2,73,'区间外球员');
 const marker={...matchEvent('card',1,46,'黄牌球员'),kind:'card',label:'黄牌'};
 const substitution={...matchEvent('substitution',2,66,'换人球员'),kind:'substitution',label:'换上'};
 const data=bundle('甲');
 vi.mocked(loadBundle).mockResolvedValue({...data,events:[before,shot,after],markers:[marker,substitution],capabilities:{...data.capabilities,momentum:true}});
 render(<VisualizationWorkspace/>);
 await screen.findByText('球场图 · 0');
 fireEvent.change(screen.getByLabelText('球场动作'),{target:{value:'shot'}});
 expect(screen.getByText('球场图 · 1')).toBeTruthy();
 fireEvent.change(screen.getByLabelText('球场动作'),{target:{value:''}});
 fireEvent.click(screen.getByRole('tab',{name:/比赛势头/}));
 fireEvent.click(screen.getByRole('button',{name:'模拟拖选 60–72'}));
 expect(screen.getByRole('region',{name:'所选时段统计'}).textContent).toContain('0.23');
 expect(screen.getByText('区间射门球员',{exact:false})).toBeTruthy();
 expect(screen.queryByText('区间外球员',{exact:false})).toBeNull();
 fireEvent.click(screen.getByRole('tab',{name:/事件战术板 Chalkboard/}));
 expect(screen.getByText('区间射门球员',{exact:false})).toBeTruthy();
 expect(screen.queryByText('补时球员',{exact:false})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:/^换人$/}));
 expect(screen.queryByRole('button',{name:'46:00 黄牌'})).toBeNull();
 expect(screen.getByRole('button',{name:'66:00 换上'}).getAttribute('data-in-range')).toBe('true');
 expect(screen.getByRole('slider',{name:'区间起点'}).getAttribute('aria-valuetext')).toContain('60:00');
 fireEvent.click(screen.getByRole('button',{name:/^牌$/}));
 fireEvent.click(screen.getByRole('button',{name:'46:00 黄牌'}));
 expect(screen.getByRole('complementary',{name:'事件详情'}).textContent).toContain('黄牌球员');
 fireEvent.click(screen.getByRole('button',{name:'上半场'}));
 expect(screen.getByText('补时球员',{exact:false})).toBeTruthy();
 expect(screen.queryByText('区间射门球员',{exact:false})).toBeNull();
});


it('keeps shootout kicks out of match-time totals and preserves their order',async()=>{
 const miss={...matchEvent('miss',5,0,'第一位球员','shot'),label:'射失'};
 const goal={...matchEvent('goal',5,0,'第二位球员','shot'),goal:true};
 const data=bundle('甲');
 vi.mocked(loadBundle).mockResolvedValue({...data,events:[miss,goal],markers:[goal]});
 render(<VisualizationWorkspace/>);
 fireEvent.click(await screen.findByText('点球大战',{exact:true}));
 expect(screen.getByRole('button',{name:'1. 第一位球员 · 射失'})).toBeTruthy();
 expect(screen.getByRole('button',{name:'2. 第二位球员 · 罚进'})).toBeTruthy();
 expect(screen.getByText('事件明细 · 0').closest('details')?.open).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'1. 第一位球员 · 射失'}));
 expect(screen.getByRole('complementary',{name:'事件详情'}).textContent).toContain('第一位球员');
});

it('opens a missed penalty on the timeline and reports filtered xG coverage and confirmed shots on target', async () => {
 const goal = {...matchEvent('goal', 1, 12, '进球球员', 'shot'), goal: true, shot_outcome: 'goal' as const};
 const miss = {...matchEvent('miss', 2, 62, '射失球员', 'shot'), penalty: true, xg: null, shot_outcome: 'off_target' as const};
 const legacy = {...matchEvent('legacy', 2, 65, '旧记录球员', 'shot'), team_id: 'a'};
 const data = bundle('甲');
 vi.mocked(loadBundle).mockResolvedValue({...data, events: [goal, miss, legacy], markers: [goal]});
 render(<VisualizationWorkspace/>);
 const chalkboard = await screen.findByRole('tab', {name:/^事件战术板/});
 fireEvent.keyDown(chalkboard, {key:'End'});
 expect(screen.getByRole('tab', {name:/^控球价值/}).getAttribute('aria-selected')).toBe('true');
 fireEvent.click(screen.getByRole('tab', {name:/^预期进球/}));
 const summary = screen.getByRole('table', {name:'当前筛选的射门统计'});
 expect(summary.textContent).toContain('0.23 · 1/2');
 expect(summary.textContent).toContain('0（已确认）');
 fireEvent.click(screen.getByRole('button', {name:'上半场'}));
 expect(summary.textContent).toContain('0.23 · 1/1');
 fireEvent.click(screen.getByRole('button', {name:'62:00 点球未进'}));
 expect(screen.getByRole('complementary', {name:'事件详情'}).textContent).toContain('射失球员');
});
