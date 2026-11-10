export type OfficialRow={match_id:string;stats:Record<string,number>;stat_status?:Record<string,string>;availability?:string;participation?:string};
type Person={minutes:number;official_by_match?:OfficialRow[]};
export function noAppearance(player:Person){const rows=player.official_by_match??[];return rows.length>0&&rows.every(r=>r.participation==='not_played');}
const reasons:Record<string,string>={not_played:'未出场',source_stat_list_missing:'原始统计列表缺失',source_stat_list_empty:'原始统计列表为空，无法确认',source_stat_list_invalid:'原始统计列表异常',source_value_invalid:'原始数值无效',source_value_conflict:'原始与标准值冲突',source_field_absent:'原始记录未列此项'};
export function metricText(player:{minutes:number;[key:string]:unknown},key:string,d=2){
 const value=player[key];
 if(typeof value==='number'&&Number.isFinite(value))return value.toLocaleString('zh-CN',{maximumFractionDigits:d});
 if(player.minutes===0){const rows=player.official_by_match as OfficialRow[]|undefined;return rows?.length&&rows.every(r=>r.participation==='not_played')?'未出场':'选区无样本';}
 return ({pv:'本快照未关联MA32评分',model_xt:'本快照未关联模型评分',xg:'射门xG未关联',xa:'官方xA未读取；需核对来源'} as Record<string,string>)[key]??'本快照未记录读取结果';
}
export function officialStat(player:Person,key:string){
 const allRows=player.official_by_match??[];
 const rows=allRows.filter(r=>r.participation!=='not_played');
 const known=rows.filter(r=>r.stats[key]!=null&&Number.isFinite(r.stats[key]));
 const missing=rows.filter(r=>!known.includes(r));
 const notes=Array.from(new Set(missing.map(r=>reasons[r.stat_status?.[key]??r.availability??'']??'旧快照未保存缺失原因')));
 if(!rows.length)notes.push(allRows.length?'未出场':player.minutes?'旧快照未保存统计读取凭据':'无出场样本');
 const value=known.length?known.reduce((n,r)=>n+r.stats[key],0):null;
 return {value,coverage:known.length,missing:missing.length,reasons:notes,
  text:value==null?notes.join('；'):`${value.toLocaleString('zh-CN',{maximumFractionDigits:2})}（${known.length}场${missing.length?`；另${missing.length}场未计入`:''}）`,
  detail:rows.map(r=>`${r.match_id}：${r.stat_status?.[key]==='zero_omitted'?'完整原始计数列表省略零值，已还原0':r.stat_status?.[key]==='raw_recovered'?'已从原始记录补回数值':r.stats[key]!=null?'来源有值':reasons[r.stat_status?.[key]??r.availability??'']??'旧快照未保存缺失原因'}`).join('；')};
}
