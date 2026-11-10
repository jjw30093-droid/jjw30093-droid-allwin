"use client";
import {useEffect,useState} from 'react';
import {clientFetch,type GetJson} from '@/lib/api-v1';
import {fmtUtc} from '@/components/studio/api';
import styles from '@/app/studio/predictions/predictions.module.css';
type View=GetJson<'/api/v1/studio/predictions/{source_match_id}/comparison'>;
export function MarketComparison({matchId}:{matchId:string}){
 const [data,setData]=useState<View|null>(null);const [error,setError]=useState('');
 useEffect(()=>{let live=true;clientFetch<View>(`/api/v1/studio/predictions/${encodeURIComponent(matchId)}/comparison`).then(r=>{if(live)setData(r)}).catch(()=>{if(live)setError('市场对照暂不可用。')});return()=>{live=false}},[matchId]);
 const max=Math.max(5,data?.model_total_goals??0,data?.market_line??0);
 return <section className={styles.panel}><h2>喵弟模型与 bet365 市场</h2><p>模型展示预计总进球均值，市场展示大小球结算线。两条数值含义不同，不将差值解释为优势或胜率。</p>{error?<p role="status">{error}</p>:!data?<p role="status">正在读取对照…</p>:data.status!=='available'?<p>暂缺经过比赛映射和时间核验的对照数据。</p>:<><div className={styles.comparison}><label>喵弟模型 · 预计总进球 <strong>{data.model_total_goals?.toFixed(2)??'暂无'}</strong>{data.model_total_goals!=null&&<meter aria-label="预计总进球均值" min={0} max={max} value={data.model_total_goals}/>}</label><label>bet365 · {data.market_stale?"历史大小球结算线":"大小球结算线"} <strong>{data.market_line??'暂无报价'}</strong>{data.market_line!=null&&!data.market_stale&&<meter aria-label="大小球结算线" min={0} max={max} value={data.market_line}/>}</label></div>{data.market_stale&&<p role="note">市场报价距查询已超过 24 小时，仅供历史参考，暂不作当前对照。</p>}{data.market_line!=null&&<p>十进制报价：大 {data.over_decimal} / 小 {data.under_decimal}</p>}<p className={styles.muted}>预测采集 {data.model_observed_at?fmtUtc(data.model_observed_at):'未知'}；市场观察 {data.market_observed_at?fmtUtc(data.market_observed_at):'未知'}（北京时间）。</p></>}</section>
}
