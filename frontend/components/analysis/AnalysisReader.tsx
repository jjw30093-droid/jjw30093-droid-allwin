"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, clientFetch, type GetJson } from "@/lib/api-v1";
import { fmtUtc } from "@/components/studio/api";
import styles from "@/app/analysis/analysis.module.css";

type Listing=GetJson<"/api/v1/analysis/articles">;
type Article=GetJson<"/api/v1/analysis/articles/{slug}">;
const names:Record<string,string>={prematch:"赛前研究",postmatch:"赛后复盘",round:"整轮观察"};

export function AnalysisIndex(){
 const [items,setItems]=useState<Listing["articles"]>([]);const [loading,setLoading]=useState(true);const [error,setError]=useState("");const [filter,setFilter]=useState("all");
 useEffect(()=>{let live=true;clientFetch<Listing>("/api/v1/analysis/articles").then(r=>{if(live)setItems(r.articles)}).catch(()=>{if(live)setError("内容暂时无法加载，请稍后重试。")}).finally(()=>{if(live)setLoading(false)});return()=>{live=false}},[]);
 const visible=items.filter(a=>filter==="all"||a.kind===filter);
 return <main className={styles.page}><AnalysisNav/><header className={styles.hero}><p className={styles.eyebrow}>喵弟 · 足球研究</p><h1>比赛结束了，<br/>讨论才刚开始。</h1><p className={styles.intro}>从数字里找到问题，再回到比赛寻找答案。赛前研究、赛后复盘与每一轮值得讨论的细节。</p><div className={styles.tags}><span>英超</span><span>J 联赛</span><span>澳超</span><span>欧冠</span></div></header><section aria-label="分析文章"><div className={styles.sectionHead}><h2>最新研究</h2><span>以证据为起点</span></div><div className={styles.filters}>{Object.entries({all:"全部",...names}).map(([key,label])=><button key={key} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{label}</button>)}</div>{loading&&<p role="status">正在加载研究…</p>}{error&&<p role="alert">{error}</p>}{!loading&&!error&&visible.length===0&&<div className={styles.empty}><h3>这一栏还在准备中</h3><p>文章完成审核后会出现在这里。你可以先浏览基础数据，或关注公众号接收更新。</p><a href="https://miaomiaodi.vip">前往数据站 →</a></div>}<div className={styles.grid}>{visible.map(a=><article key={a.slug} className={styles.card}><p className={styles.eyebrow}>{names[a.kind]} · {a.visibility==="public"?"公开样例":"会员阅读"}</p><h3><Link href={`/analysis/${encodeURIComponent(a.slug)}`}>{a.title}</Link></h3><p>{a.excerpt}</p><div className={styles.cardFoot}><time>{fmtUtc(a.published_at)}</time><span>阅读全文 ↗</span></div></article>)}</div></section><aside className={styles.note}><h2>看数据，也理解它的边界</h2><p>每篇分析保留发布时的数据版本和口径。单场数据描述的是这场比赛，不代表下一场一定如此。</p><Link href="/analysis/account">试用与会员说明 →</Link></aside></main>
}

export function AnalysisNav(){return <nav className={styles.nav} aria-label="分析站导航"><Link href="/analysis">分析首页</Link><a href="https://miaomiaodi.vip">基础数据 ↗</a><Link href="/analysis/account">我的权益</Link></nav>}

export function AnalysisArticle({slug}:{slug:string}){
 const [article,setArticle]=useState<Article|null>(null);const [error,setError]=useState("");const [status,setStatus]=useState(0);
 useEffect(()=>{let live=true;clientFetch<Article>(`/api/v1/analysis/articles/${encodeURIComponent(slug)}`).then(r=>{if(live)setArticle(r)}).catch(e=>{if(live){setStatus(e instanceof ApiError?e.status:503);setError(e instanceof Error?e.message:"内容暂不可用")}});return()=>{live=false}},[slug]);
 return <main className={styles.page}><AnalysisNav/>{error?<section className={styles.empty} role="alert"><h1>{status===401?"登录后继续阅读":status===403?"查看你的分析权益":status===404?"文章不存在或已撤下":"暂时无法打开文章"}</h1><p>{error}</p>{status===401?<Link href={`/login?next=${encodeURIComponent(`/analysis/${slug}`)}`}>登录 / 注册 →</Link>:status===403?<Link href="/analysis/account">通过公众号开通 →</Link>:<Link href="/analysis">返回分析首页</Link>}</section>:!article?<p role="status">正在加载文章…</p>:<article className={styles.article}><p className={styles.eyebrow}>{names[article.kind]} · 第 {article.publication_version} 个发布版本</p><h1>{article.title}</h1><p className={styles.intro}>{article.excerpt}</p><p className={styles.meta}>发布于 {fmtUtc(article.published_at)} · 数据截止 {fmtUtc(article.content.data_cutoff_at)}（北京时间）</p>{article.content.coverage&&<p role="note">证据覆盖 {String(article.content.coverage.available)}/{String(article.content.coverage.expected)} 场；{article.content.coverage.complete ? "整轮已覆盖" : "样本不完整，不代表全轮"}。</p>}<div className={styles.body}>{article.content.body}</div><section><h2>本篇数据依据</h2><p className={styles.meta}>{article.content.analysis_mode==="reconstructed"?"回溯重建资料":"固定事实快照"} · 缺失数据不补零</p><div className={styles.metrics}>{article.content.metrics.map((m,i)=><div key={i}><span>{article.content.teams[m.side??""]??m.side} · {m.label??"指标"}</span><strong>{m.value??"暂无"}<small> {m.unit}</small></strong><small>{m.definition_version}</small></div>)}</div></section></article>}</main>
}

export function AnalysisAccount(){
 const [access,setAccess]=useState<GetJson<"/api/v1/me/analysis-access">|null>(null);const [error,setError]=useState("");const [status,setStatus]=useState(0);
 useEffect(()=>{let live=true;clientFetch<GetJson<"/api/v1/me/analysis-access">>("/api/v1/me/analysis-access").then(r=>{if(live)setAccess(r)}).catch(e=>{if(live){setStatus(e instanceof ApiError?e.status:503);setError(e instanceof Error?e.message:"权益暂不可用")}});return()=>{live=false}},[]);
 return <main className={styles.page}><AnalysisNav/><section className={styles.hero}><p className={styles.eyebrow}>我的分析权益</p><h1>一起把比赛看深一点。</h1><p className={styles.intro}>VIP 数据站与 CC 分析站共用账号和分析会员资格。试用开放后，首次成功登录可体验 72 小时，每个账号一次。</p></section><section className={styles.note}><h2>{access?access.allowed?"当前可阅读分析":"暂无有效分析权益":"权益状态"}</h2>{access?<><p>试用到期：{access.trial_ends_at?fmtUtc(access.trial_ends_at):"尚未开始"}</p><p>会员到期：{access.membership_ends_at?fmtUtc(access.membership_ends_at):"尚未开通"}（北京时间）</p></>:error?<p role="status">{error}</p>:<p role="status">正在查询…</p>}{status===401&&<Link href="/login?next=%2Fanalysis%2Faccount">登录 / 注册 →</Link>}<h3>如何开通</h3><p>通过页面下方的公众号入口联系站长，发送你的用户编号与“分析会员”，获取使用方式。用户编号可在账户中心查看。</p><Link href="/account">查看账户编号 →</Link></section></main>
}
