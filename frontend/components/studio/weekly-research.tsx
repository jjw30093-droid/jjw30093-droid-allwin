"use client";
import {useEffect,useState} from 'react';
import {clientFetch,type GetJson} from '@/lib/api-v1';
type Weekly=GetJson<'/api/v1/studio/research/weekly'>;
export function WeeklyResearch({disabled,onImport,onImportRound}:{disabled:boolean;onImport:(id:string)=>void;onImportRound?:(id:string)=>void}){
 const [data,setData]=useState<Weekly|null>(null);const [error,setError]=useState('');
 useEffect(()=>{let active=true;clientFetch<Weekly>('/api/v1/studio/research/weekly').then(r=>{if(active)setData(r)}).catch(()=>{if(active)setError('周度材料暂不可用，仍可从下方比赛收件箱开始研究。')});return()=>{active=false}},[]);
 return <section aria-label="周度复盘"><h2>本周复盘候选</h2>{error?<p role="status">{error}</p>:!data?<p role="status">正在读取周度材料…</p>:<><p>制作周：{data.week_start} 起 · 最多 20 个候选，按比赛分散排列，仍需人工选择与审核。</p>{data.stale&&<p role="alert">这是历史制作周的材料，本周编排尚未就绪。</p>}{data.status==='awaiting_explicit_scopes'&&<p>本周尚未提供经过核对的轮次清单。</p>}{data.rounds.map((r,i)=><details key={i}><summary>{r.competition} · {r.label} · 证据 {r.coverage.available}/{r.coverage.expected} 场 · {r.candidates.length} 个候选</summary>{!r.coverage.complete&&<p role="note">覆盖尚不完整，不能把当前样本解释为全轮表现。</p>}<p>比分覆盖 {r.score_matches}/{r.coverage.expected} 场；总进球 {r.total_goals??'缺失'}；场均 {r.goals_per_match==null?'缺失':r.goals_per_match.toFixed(2)}。</p>{onImportRound&&r.import_id&&<button type="button" disabled={disabled||r.coverage.available===0} onClick={()=>onImportRound(r.import_id)}>导入整轮研究</button>}<ul>{r.candidates.map((c,j)=><li key={j}><p><strong>{c.title}</strong></p><p>{c.limitation}</p><button type="button" disabled={disabled} onClick={()=>onImport(c.snapshot_id)}>导入该场证据</button></li>)}</ul></details>)}</>}</section>
}
