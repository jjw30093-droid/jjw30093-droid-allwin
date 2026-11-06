import {describe,it,expect,vi} from 'vitest';
import {axisPosition, formatMatchTime, inTimeRange, matchTime} from '../components/studio/visualization-time';
import {filterEvents,initialFilters,loadBundle,shotMapRadius,shotResult,type Event,type Bundle} from '../components/studio/visualization-data';
const event=(overrides:Partial<Event>={}):Event=>({id:'one',team_id:'home',player_id:'p',player_name:'Player',period:1,minute:10,second:0,clock:'10:00',elapsed:600,kind:'pass',label:'传球',success:true,x:60,y:50,end_x:90,end_y:50,key_pass:true,assist:false,xg:null,xgot:null,goal:false,own_goal:false,penalty:null,pv_value:0,pv_added:0,...overrides});
describe('visualization filters',()=>{
 it('keeps key passes as passes and combines time, player and area',()=>{
  const f={...initialFilters,kind:'pass',players:['p'],region:[50,40,70,60],special:'box'};
  const events = [event(),event({id:'other',player_id:'q'}),event({id:'later',minute:20})];
  const time = matchTime({events, markers: [], momentum: []});
  expect(filterEvents(events.filter(point => inTimeRange(time, [300, 900], point)),f,'pro').map(point => point.id)).toEqual(['one']);
 });
 it('matches the displayed end and flank when selecting an away event on the pitch',()=>{
  const home = event({id:'home',team_id:'h',kind:'shot',x:89,y:68});
  const away = event({id:'away',team_id:'a',kind:'shot',x:89,y:68});
  const awayBox = {...initialFilters,region:[0,25,17,45]};
  expect(filterEvents([home,away],awayBox,'pro','a').map(point=>point.id)).toEqual(['away']);
  expect(filterEvents([home,away],awayBox,'heatmap','a').map(point=>point.id)).toEqual(['away']);
 });
 it('scales the marker area by xG and keeps unsupported shot results unknown',()=>{
  expect(shotMapRadius(event({xg:.8})) ** 2 / shotMapRadius(event({xg:.2})) ** 2).toBeCloseTo(4);
  expect(shotMapRadius(event({xg:null}))).toBe(0);
  expect(shotMapRadius(event({own_goal:true,xg:null}))).toBe(3);
  expect(shotResult(event({kind:'shot',label:'射门被扑'}))).toBeNull();
  expect(shotResult(event({kind:'shot',label:'射门中框'}))).toBe('off_target');
  expect(shotResult(event({kind:'shot',shot_outcome:'blocked'}))).toBe('blocked');
 });
 it('distinguishes missing PV from zero and missing penalty from confirmed non-penalty',()=>{
  expect(filterEvents([event(),event({pv_added:null})],{...initialFilters,pv:'0'},'pro')).toHaveLength(1);
  expect(filterEvents([event({kind:'shot',penalty:null}),event({kind:'shot',penalty:false})],{...initialFilters,penalty:'exclude'},'xg')).toHaveLength(1);
 });
 it('requires entering rather than remaining in the final third',()=>{
  expect(filterEvents([event(),event({x:80})],{...initialFilters,special:'third'},'pro')).toHaveLength(1);
 });
});
describe('versioned pagination',()=>{
 it('loads every event and refuses a mixed revision',async()=>{
  const first={match:{id:'match'},offset:0,version:'v1',events:[event()],has_more:true,event_total:2} as Bundle;
  const mock=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>first}).mockResolvedValueOnce({ok:true,json:async()=>({...first,offset:1,events:[event({id:'two'})],has_more:false})});
  vi.stubGlobal('fetch',mock);
  expect((await loadBundle('match',new AbortController().signal)).events).toHaveLength(2);
  expect(mock.mock.calls[1][0]).toContain('version=v1');
  mock.mockResolvedValueOnce({ok:true,json:async()=>first}).mockResolvedValueOnce({ok:true,json:async()=>({...first,offset:1,version:'v2',events:[],has_more:false})});
  await expect(loadBundle('match',new AbortController().signal)).rejects.toThrow('已更新');vi.unstubAllGlobals();
 });
 it('does not display a truncated or duplicated match',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({match:{id:'m'},offset:0,version:'v',events:[event(),event()],has_more:false,event_total:2})}));
  await expect(loadBundle('m',new AbortController().signal)).rejects.toThrow('分页不完整');vi.unstubAllGlobals();
 });
});


describe('Opta period clock', () => {
 it('keeps 45+2 separate from the same minute in the second half and includes cross-half endpoints', () => {
  const stoppage = event({ period: 1, minute: 47, second: 2 });
  const second = event({ period: 2, minute: 47, second: 2 });
  const time = matchTime({ events: [stoppage, second], markers: [], momentum: [], periods: [{id:1,duration_seconds:2890},{id:2,duration_seconds:2915}] });
  const firstEnd = time.phases[0].duration;
  const secondStart = time.phases[1].offset;
  expect(firstEnd).toBe(2890);
  expect(formatMatchTime(time, axisPosition(time, stoppage)!, true)).toBe('上半场 45+2:02');
  expect(formatMatchTime(time, time.end, true)).toBe('下半场 90+3:35');
  expect(inTimeRange(time, [0, firstEnd], stoppage)).toBe(true);
  expect(inTimeRange(time, [0, firstEnd], second)).toBe(false);
  expect(inTimeRange(time, [axisPosition(time, stoppage)!, axisPosition(time, second)!], second)).toBe(true);
  expect(inTimeRange(time, [secondStart, time.end], stoppage)).toBe(false);
 });
 it('uses event evidence for missing durations, adds extra time only when present and excludes shootouts', () => {
  const extra = event({period:4,minute:121,second:15});
  const penalty = event({period:5,minute:128});
  const regular = matchTime({events: [penalty], markers: [], momentum: []});
  expect(regular.phases.map(phase => phase.id)).toEqual([1,2]);
  const time = matchTime({events:[extra,penalty], markers:[event({period:3,minute:105})], momentum:[]});
  expect(time.phases.map(phase => phase.id)).toEqual([1,2,3,4]);
  expect(formatMatchTime(time,time.end,true)).toBe('加时下半场 120+1:15');
  expect(inTimeRange(time,null,penalty)).toBe(false);
 });
});
