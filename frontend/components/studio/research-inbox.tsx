"use client";
import {useEffect,useState} from 'react';
import {clientFetch,type GetJson} from '@/lib/api-v1';
import {fmtUtc} from '@/components/studio/api';
type Inbox=GetJson<'/api/v1/studio/research/inbox'>;
export function ResearchInbox({disabled,onImport}:{disabled:boolean;onImport:(id:string)=>void}){
 const [data,setData]=useState<Inbox|null>(null);const [error,setError]=useState('');
 useEffect(()=>{let live=true;clientFetch<Inbox>('/api/v1/studio/research/inbox').then(r=>{if(live)setData(r)}).catch(()=>{if(live)setError('收件箱暂不可用，可通过下方编号导入。')});return()=>{live=false}},[]);
 return <section aria-label="研究收件箱"><h2>待研究比赛</h2>{error?<p role="status">{error}</p>:!data?<p role="status">正在校验证据…</p>:<><p>选择已收集的比赛即可导入；这些材料仍需人工审核。</p>{data.entries.length===0&&<p>尚无可导入证据。</p>}<ul>{data.entries.map(e=><li key={e.snapshot_id}><button type="button" disabled={disabled} onClick={()=>onImport(e.snapshot_id)}>{e.title} · {e.topic_count} 个选题</button><p>{fmtUtc(e.cutoff_at)}</p></li>)}</ul>{data.invalid_snapshot_ids.length>0&&<p role="alert">{data.invalid_snapshot_ids.length} 份证据校验失败，已阻止导入。</p>}{data.truncated&&<p>当前仅显示最多 20 份证据，其他材料可通过编号导入。</p>}</>}</section>
}
