"use client";
import { useEffect, useState } from "react";
import { clientFetch, type GetJson } from "@/lib/api-v1";

type State=GetJson<"/api/v1/studio/research/drafts/{draft_id}/publication">;
export function PublicationPanel({draftId,revision,reviewed,disabled,kind="postmatch"}:{draftId:string;revision:number;reviewed:boolean;disabled:boolean;kind?:"prematch"|"postmatch"|"round"}){
 const [state,setState]=useState<State|null>(null);const [slug,setSlug]=useState("");const [excerpt,setExcerpt]=useState("");const [body,setBody]=useState("");const [visibility,setVisibility]=useState("member");const [reason,setReason]=useState("");const [confirmed,setConfirmed]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState("");const [notice,setNotice]=useState("");
 const endpoint=`/api/v1/studio/research/drafts/${encodeURIComponent(draftId)}/publication`;
 useEffect(()=>{let live=true;clientFetch<State>(endpoint).then(r=>{if(live){setState(r);setSlug(r.slug??"")}}).catch(()=>{if(live)setError("发布状态暂不可用，请刷新后核对。")});return()=>{live=false}},[endpoint]);
 async function refresh(){const r=await clientFetch<State>(endpoint);setState(r);setSlug(r.slug??slug)}
 async function run(fn:()=>Promise<void>){setBusy(true);setError("");setNotice("");try{await fn()}catch(e){setError(e instanceof Error?e.message:"操作失败");setState(null)}finally{setBusy(false)}}
 const locked=busy||disabled;
 return <section><h2>发布到分析站</h2><p>下方正文是公众会读到的内容。内部编辑备注不会自动发布；发布时固定本版已审核指标。</p>{error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}<p>{state?`发布版本 ${state.publication_version} · ${state.published?"当前已发布":"尚未发布或已撤下"}`:"正在核对发布状态"}</p><button disabled={locked} onClick={()=>void run(refresh)}>刷新发布状态</button><form onSubmit={e=>{e.preventDefault();if(!state)return;void run(async()=>{await clientFetch(endpoint,{method:"POST",body:{expected_revision:revision,expected_publication_version:state.publication_version,slug,excerpt,body,kind,visibility,reason,confirmed}});await refresh();setConfirmed(false);setNotice("本版已发布。后续修改需再次审核并发布。");})}}>
 <label htmlFor="publication-slug">文章地址（小写英文、数字、连字符）</label><input id="publication-slug" value={slug} pattern="[a-z0-9]+(-[a-z0-9]+)*" maxLength={100} required disabled={locked||!!state?.slug} onChange={e=>{setSlug(e.target.value);setConfirmed(false)}}/>
 <label htmlFor="publication-excerpt">公开摘要</label><textarea id="publication-excerpt" maxLength={300} required rows={2} value={excerpt} disabled={locked} onChange={e=>{setExcerpt(e.target.value);setConfirmed(false)}}/>
 <label htmlFor="publication-body">分析正文</label><textarea id="publication-body" maxLength={20000} required rows={8} value={body} disabled={locked} onChange={e=>{setBody(e.target.value);setConfirmed(false)}}/>
 <label htmlFor="publication-visibility">阅读范围</label><select id="publication-visibility" value={visibility} disabled={locked} onChange={e=>{setVisibility(e.target.value);setConfirmed(false)}}><option value="member">试用 / 会员</option><option value="public">公开样例</option></select>
 <label htmlFor="publication-reason">发布或撤下原因</label><input id="publication-reason" required maxLength={500} value={reason} disabled={locked} onChange={e=>setReason(e.target.value)}/>
 <label><input type="checkbox" checked={confirmed} disabled={locked||!reviewed} onChange={e=>setConfirmed(e.target.checked)}/>我已核对本版标题、公开摘要、正文、指标及阅读范围，确认发布</label>
 <button disabled={locked||!state||!reviewed||!confirmed||!body.trim()||!reason.trim()}>确认发布本版</button>{" "}<button type="button" disabled={locked||!state?.published||!reason.trim()} onClick={()=>void run(async()=>{await clientFetch(endpoint.replace(/publication$/,"retract"),{method:"POST",body:{expected_publication_version:state!.publication_version,reason}});await refresh();setNotice("文章已撤下，历史版本已保留。")})}>撤下文章</button></form>{state?.published&&<p><a href={`/analysis/${state.slug}`} target="_blank" rel="noreferrer">查看已发布页面 ↗</a></p>}</section>
}
