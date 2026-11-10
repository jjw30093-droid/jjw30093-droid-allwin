"use client";

import Link from "next/link";
import { MarketComparison } from "@/components/studio/market-comparison";
import { useEffect, useState } from "react";
import { fmtUtc } from "@/components/studio/api";
import { clientFetch, type GetJson, type PostJson } from "@/lib/api-v1";
import styles from "@/app/studio/predictions/predictions.module.css";

type Prediction = GetJson<"/api/v1/studio/predictions/{source_match_id}">;
type Fixtures = GetJson<"/api/v1/studio/prediction-fixtures">;
const leagues: Record<string, string> = { EPL: "英超", J1L: "J1 联赛", ALM: "澳超", UCL: "欧冠" };
const missing: Record<string, string> = {
  no_capture_database: "尚未产生正式预测快照，请在每日采集完成后再查看。",
  no_proven_prematch_capture_at_cutoff: "这场比赛暂时没有可用的赛前预测快照。",
  match_has_started_at_cutoff: "比赛已经开始，本页不再展示赛前预测。",
};
const number = (value: number | null | undefined) => value == null ? "暂无数据" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);

export function PredictionWorkspace() {
  const [matchId, setMatchId] = useState("");
  const [imported, setImported] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [data, setData] = useState<Prediction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fixtures, setFixtures] = useState<Fixtures | null>(null);
  const [fixturesError, setFixturesError] = useState("");
  const [fixturesBusy, setFixturesBusy] = useState(true);
  useEffect(() => {
    let active = true;
    clientFetch<Fixtures>("/api/v1/studio/prediction-fixtures")
      .then((value) => { if (active) setFixtures(value); })
      .catch(() => { if (active) setFixturesError("赛程暂不可用，请重试或通过研究编号查询。"); })
      .finally(() => { if (active) setFixturesBusy(false); });
    return () => { active = false; };
  }, []);
  async function reloadFixtures() {
    setFixturesBusy(true); setFixturesError("");
    try { setFixtures(await clientFetch<Fixtures>("/api/v1/studio/prediction-fixtures")); }
    catch { setFixturesError("赛程暂不可用，请重试或通过研究编号查询。"); }
    finally { setFixturesBusy(false); }
  }
  async function load(selected?: string) {
    const id = (selected ?? matchId).trim();
    if (!/^[a-zA-Z0-9_-]{1,128}$/.test(id)) { setError("请输入有效的研究比赛编号。"); return; }
    setBusy(true); setError(""); setData(null); setImported(false);
    try { setData(await clientFetch<Prediction>(`/api/v1/studio/predictions/${encodeURIComponent(id)}`)); }
    catch (err) { setError(err instanceof Error ? err.message : "读取失败，请稍后重试。"); }
    finally { setBusy(false); }
  }
  const teams = data?.teams ?? [];
  const metrics = data?.team_predictions ?? [];
  const home = teams.find((t) => t.side === "home");
  const away = teams.find((t) => t.side === "away");
  const goal = (id?: string) => metrics.find((m) => m.team_id === id && m.metric_code === "goals")?.target;
  const homeGoals = goal(home?.team_id), awayGoals = goal(away?.team_id);
  const codes = [...new Set(metrics.map((m) => m.metric_code))];
  return <main className={styles.page}>
    <header className={styles.header}><div><p className={styles.kicker}>喵弟 · 研究工作台</p><h1>赛前预测</h1><p>查看两队预计表现，为赛前选题寻找依据。</p></div><Link href="/studio">返回 Studio</Link></header>
    <section className={styles.panel} aria-label="近期比赛"><h2>未来 72 小时</h2>
      <button disabled={fixturesBusy || busy} onClick={() => void reloadFixtures()}>刷新赛程</button>
      {fixturesBusy && <p role="status">正在读取赛程…</p>}
      {fixturesError && <p role="alert">{fixturesError}</p>}
      {!fixturesBusy && !fixturesError && fixtures?.matches.length === 0 && <p>当前覆盖赛事在此时间范围内暂无未开赛比赛。</p>}
      <div className={styles.fixtureList}>{fixtures?.matches.map((match) => <button key={match.match_id} disabled={busy || fixturesBusy} onClick={() => { setMatchId(match.match_id); void load(match.match_id); }}>
        <strong>{match.home_team_name ?? "主队名称待补齐"} vs {match.away_team_name ?? "客队名称待补齐"}</strong>
        <span>{leagues[match.competition_code]} · {fmtUtc(match.kickoff_utc)}（北京时间）</span>
        <span>{match.capture_status === "capture_recorded" ? "已有采集记录 · 查看核验结果" : "尚未采集"}</span>
      </button>)}</div>
      {fixtures?.truncated && <p>当前显示最早的 64 场比赛。</p>}
    </section>
    <section className={styles.panel}>
      <form onSubmit={(e) => { e.preventDefault(); if (!busy) void load(); }}>
        <label htmlFor="prediction-match">研究比赛编号</label>
        <div className={styles.formRow}><input id="prediction-match" value={matchId} onChange={(e) => { setMatchId(e.target.value); setData(null); setError(""); }} maxLength={128} disabled={busy} autoComplete="off" placeholder="输入采集清单中的比赛编号" /><button disabled={busy || !matchId.trim()}>{busy ? "读取中…" : "查看预测"}</button></div>
      </form>
      <p className={styles.muted}>也可通过采集清单中的研究编号直接查询。</p>
    </section>
    {busy && <p role="status">正在读取最新可用快照…</p>}
    {error && <section className={styles.panel} role="alert"><h2>暂时无法读取</h2><p>{error}</p><button onClick={() => void load()} disabled={busy}>重试</button></section>}
    {!busy && !error && !data && <section className={styles.panel}><h2>选择一场未开赛比赛</h2><p>这里展示统计预测，不代表确定赛果。</p></section>}
    {data?.status === "unavailable" && <section className={styles.panel} role="status"><h2>暂无可展示的预测</h2><p>{missing[data.reason ?? ""] ?? "预测暂不可用，请稍后再试。"}</p></section>}
    {data?.status === "available" && <>
      <section className={styles.panel}><button disabled={busy||importBusy} onClick={async()=>{setImportBusy(true);setError("");try{await clientFetch<PostJson<"/api/v1/studio/research/import-prematch">>("/api/v1/studio/research/import-prematch",{method:"POST",body:{match_id:data.match_id}});setImported(true)}catch(e){setError(e instanceof Error?e.message:"导入失败")}finally{setImportBusy(false)}}}>保存为赛前研究草稿</button>{imported&&<p role="status">已保存固定赛前证据。<Link href="/studio/research">打开研究列表进行审核与发布 →</Link></p>}</section>
      <section className={styles.panel}><p className={styles.kicker}>{data.display_name}</p><h2>{home?.name ?? "主队"} <span className={styles.muted}>vs</span> {away?.name ?? "客队"}</h2>
        <p className={styles.muted}>开球：{data.kickoff_utc ? fmtUtc(data.kickoff_utc) : "待确认"}（北京时间）</p>
        <div className={styles.goals}>{[["主队预计进球", homeGoals], ["客队预计进球", awayGoals], ["预计总进球", homeGoals != null && awayGoals != null ? homeGoals + awayGoals : null]].map(([label, value]) => <div key={String(label)}><span>{label}</span><strong>{number(value as number | null | undefined)}</strong></div>)}</div>
        <p>预计进球为统计均值，不是预测比分或胜平负概率。</p>
      </section>
      <section className={styles.panel}><h2>两队技术统计预测</h2><p className={styles.muted}>显示预测值与赛季均值；缺失项目保留空态。</p>
        <div className={styles.metrics}>{codes.map((code) => {
          const samples = metrics.filter((m) => m.metric_code === code);
          return <article className={styles.metric} key={code}><h3>{samples[0]?.label}</h3><div className={styles.sides}>{[home, away].map((team, index) => {
            const metric = samples.find((m) => m.team_id === team?.team_id);
            return <div key={index}><span>{team?.name ?? (index ? "客队" : "主队")}</span><strong>{number(metric?.target)}</strong><small>赛季均值 {number(metric?.season_average)}</small>{metric?.registered === false && <small>指标口径待核对</small>}</div>;
          })}</div></article>;
        })}</div>{codes.length === 0 && <p>当前快照没有球队统计指标。</p>}
      </section>
      {data.match_id && <MarketComparison key={data.match_id} matchId={data.match_id} />}
      <details className={styles.panel}><summary>快照与采集时间</summary><p>采集于 {data.observed_at ? fmtUtc(data.observed_at) : "未知"}（北京时间）</p><p>距查询时间 {number(data.age_hours)} 小时。每天采集一次，采集时间不等同模型生成时间。</p><p className={styles.hash}>快照：{data.snapshot_id}</p></details>
    </>}
  </main>;
}
