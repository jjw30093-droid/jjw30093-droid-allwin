"use client";

import Link from "next/link";
import { WeeklyResearch } from "@/components/studio/weekly-research";
import { ResearchInbox } from "@/components/studio/research-inbox";
import { PublicationPanel } from "@/components/analysis/PublicationPanel";
import { useCallback, useEffect, useRef, useState } from "react";
import { downloadServerExport, fmtUtc } from "@/components/studio/api";
import { ApiError } from "@/lib/api-v1";
import { editResearch, exportResearch, importResearch, importRound, listResearch, readHistory, readResearch, reviewResearch, type ResearchDraft, type ResearchHistory } from "@/components/studio/research-api";
import styles from "@/app/studio/research/research.module.css";

const message = (error: unknown) => error instanceof Error ? error.message : "请求失败，请重试。";
const statusName = (status: string) => status === "reviewed" ? "已审核" : "待审核";
const actionName: Record<string, string> = { import: "导入证据", edit: "修改内容", review: "完成审核", reopen: "重新打开" };

export function ResearchWorkspace() {
  const [drafts, setDrafts] = useState<Awaited<ReturnType<typeof listResearch>>["drafts"]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [draft, setDraft] = useState<ResearchDraft | null>(null);
  const [history, setHistory] = useState<ResearchHistory["events"]>([]);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const sequence = useRef(0);
  const dirty = !!draft && (title !== draft.title || notes !== (draft.overrides.editorial_notes ?? ""));
  const loadList = useCallback(async () => {
    setListLoading(true); setListError("");
    try { setDrafts((await listResearch()).drafts); }
    catch (err) { setListError(message(err)); }
    finally { setListLoading(false); }
  }, []);
  useEffect(() => {
    let active = true;
    listResearch().then((result) => { if (active) setDrafts(result.drafts); })
      .catch((err) => { if (active) setListError(message(err)); })
      .finally(() => { if (active) setListLoading(false); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function load(id: string) {
    const request = ++sequence.current;
    const [next, audit] = await Promise.all([readResearch(id), readHistory(id)]);
    if (request !== sequence.current) return;
    setDraft(next); setHistory(audit.events); setTitle(next.title);
    setNotes(String(next.overrides.editorial_notes ?? "")); setChecked(false); setConflict(false);
  }
  async function act(work: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); }
    catch (err) {
      if (err instanceof ApiError && err.status === 409 && draft) {
        setConflict(true); setChecked(false);
        setError("操作未完成。版本可能已更新，请重新载入核对后再操作。本地输入已保留。" + message(err));
      } else setError(message(err));
    } finally { setBusy(false); }
  }
  const packet = draft?.bundle.research_packet;
  const sideName = (side: string) => side === "round" ? "本轮样本" : side === "home" ? packet?.display_names.home || packet?.match.home_team || "主队" : packet?.display_names.away || packet?.match.away_team || "客队";
  const locked = busy || conflict;

  return <main className={styles.page}>
    <header className={styles.header}><div><p className={styles.eyebrow}>喵弟 · 内容工作台</p><h1>研究与审核</h1><p>从固定比赛证据中选题，核对后再交给视频制作。</p></div><Link href="/studio" onClick={(e) => { if (dirty) { e.preventDefault(); setError("请先保存修改，或重新载入后离开。"); } }}>返回 Studio</Link></header>
    <WeeklyResearch onImportRound={(id) => void act(async () => { const result = await importRound(id); await load(result.draft_id); await loadList(); })} disabled={busy || dirty} onImport={(id) => void act(async () => { const result = await importResearch(id); await load(result.draft_id); await loadList(); })} />
    <div className={styles.layout}>
      <aside className={styles.panel}>
        <ResearchInbox disabled={busy || dirty} onImport={(id) => void act(async () => { const result = await importResearch(id); await load(result.draft_id); await loadList(); })} /><hr /><h2>研究草稿</h2>
        <form onSubmit={(e) => { e.preventDefault(); void act(async () => { const created = await importResearch(snapshot.trim()); await load(created.draft_id); await loadList(); setNotice("证据已载入；重复导入会打开已有草稿。"); }); }}>
          <label htmlFor="snapshot">证据包编号</label><input id="snapshot" value={snapshot} onChange={(e) => setSnapshot(e.target.value)} required pattern="[0-9a-f]{64}" placeholder="粘贴证据包的完整编号" disabled={busy || dirty} />
          <p className={styles.muted}>使用采集流程生成的编号。文件须已进入私有收件目录。</p><button disabled={busy || dirty}>导入证据</button>
        </form>
        <hr />
        <button type="button" onClick={() => void loadList()} disabled={listLoading || busy}>刷新列表</button>
        {listLoading && <p role="status">正在加载草稿…</p>}
        {listError && <p role="alert">{listError}</p>}
        {!listLoading && !listError && drafts.length === 0 && <p>暂无研究草稿。先导入一份证据包。</p>}
        <ul className={styles.list}>{drafts.map((item) => <li key={item.id}><button type="button" disabled={busy || dirty} aria-pressed={draft?.id === item.id} onClick={() => void act(() => load(item.id))}><strong>{item.title}</strong><span>{statusName(item.status)} · {fmtUtc(item.updated_at)}</span></button></li>)}</ul>
      </aside>
      <section className={styles.detail} aria-label="证据详情">
        {error && <p className={styles.error} role="alert">{error}</p>}
        {notice && <p className={styles.notice} role="status">{notice}</p>}
        {!draft && <div className={styles.panel}><h2>选一场比赛，开始核对</h2><p>这里会显示选题依据、指标口径和比赛事件线索。导出不会自动发布内容。</p></div>}
        {draft && packet && <>
          <section className={styles.panel}>
            <p className={styles.eyebrow}>{statusName(draft.status)} · 第 {draft.revision} 版</p>
            <h2>{packet.kind && packet.kind !== "postmatch" ? packet.title : <>{sideName("home")} {packet.match.home_score}:{packet.match.away_score} {sideName("away")}</>}</h2>
            <p>数据截止：{fmtUtc(packet.cutoff_at)}（北京时间） · {packet.analysis_mode === "current" ? "当前事实快照" : "回溯重建"}</p>
            <p className={styles.muted}>{packet.kind === "prematch" ? "赛前统计预测，不代表确定赛果。" : packet.kind === "round" ? "整轮观察以已覆盖比赛为样本，请核对覆盖范围。" : "本页为赛后研究，不作为赛前预测表现。"}缺失数据不补零。</p>
            <details><summary>来源与证据版本</summary><p className={styles.hash}>{packet.snapshot_id}</p>{Object.entries(packet.source_capture_sha256).map(([name, hash]) => <p className={styles.hash} key={name}>{name}：{hash}</p>)}</details>
            {!!packet.quality.warnings?.length && <div role="note"><h3>待核对的数据问题</h3>{packet.quality.warnings.map((warning) => <p key={warning}>{warning}</p>)}</div>}
          </section>
          <section className={styles.panel}><h2>可讲的点</h2>{packet.claims.length === 0 && <p>证据不足，尚未生成选题。</p>}{packet.claims.map((claim) => <article className={styles.claim} key={claim.topic_id}><h3>{claim.title}</h3><p>{claim.limitation}</p><ul>{claim.evidence_refs.map((ref) => { const metric = packet.metrics.find((m) => m.evidence_id === ref); return <li key={ref}>{metric ? `${sideName(metric.side)} · ${metric.label}：${metric.value} ${metric.unit}` : "引用证据缺失，请勿据此制作"}</li>; })}</ul></article>)}</section>
          <section className={styles.panel}><h2>指标与口径</h2><div className={styles.tableWrap}><table><caption>固定证据中的全部指标</caption><thead><tr><th>球队</th><th>指标</th><th>数值</th><th>口径版本</th></tr></thead><tbody>{packet.metrics.map((metric) => <tr key={metric.evidence_id}><td>{sideName(metric.side)}</td><th scope="row">{metric.label}</th><td>{metric.value} {metric.unit}</td><td>{metric.definition_version}</td></tr>)}</tbody></table></div></section>
          <details className={styles.panel}><summary>比赛片段线索 · {packet.event_timeline.length} 条射门事件</summary><p>以下为比赛时钟，不是转播文件播放位置；请结合实际画面定位。</p><ol className={styles.events}>{packet.event_timeline.map((event, index) => <li key={`${event.event_id}-${index}`}>节次 {event.period_id} · {event.minute}:{String(event.second).padStart(2, "0")} · xG {event.xg ?? "缺失"}</li>)}</ol></details>
          <section className={styles.panel}><h2>编辑与审核</h2>
            <label htmlFor="research-title">内容标题</label><input id="research-title" maxLength={300} value={title} disabled={locked} onChange={(e) => { setTitle(e.target.value); setChecked(false); }} />
            <label htmlFor="research-notes">编辑备注</label><textarea id="research-notes" maxLength={20000} rows={5} value={notes} disabled={locked} onChange={(e) => { setNotes(e.target.value); setChecked(false); }} placeholder="记录讲解角度、待找片段和核对结论。" />
            <p className={styles.muted}>修改后需要重新审核。备注与固定数据分开保存，不会替换指标。</p>
            <div className={styles.actions}><button disabled={locked || !dirty} onClick={() => void act(async () => { await editResearch(draft, title, notes); await load(draft.id); await loadList(); setNotice("修改已保存，当前版本待审核。"); })}>保存修改</button><button disabled={busy} onClick={() => void act(() => load(draft.id))}>{dirty || conflict ? "重新载入（放弃本地修改）" : "重新载入"}</button></div>
            {draft.status === "draft" && <label className={styles.check}><input type="checkbox" checked={checked} disabled={locked || dirty} onChange={(e) => setChecked(e.target.checked)} />我已核对本版选题、证据与编辑备注</label>}
            <div className={styles.actions}>
              {draft.status === "draft" ? <button disabled={locked || dirty || !checked} onClick={() => void act(async () => { await reviewResearch(draft, "reviewed"); await load(draft.id); await loadList(); setNotice("本版审核已记录。尚未发布。"); })}>确认本版审核通过</button> : <button disabled={locked || dirty} onClick={() => void act(async () => { await reviewResearch(draft, "draft"); await load(draft.id); await loadList(); })}>重新打开审核</button>}
              <button disabled={locked || dirty} onClick={() => void act(async () => { const result = await exportResearch(draft.id); if (result.side !== "server") throw new Error("未收到服务端导出文件"); await downloadServerExport(result.download_url, `miaodi-research-${packet.snapshot_id.slice(0, 12)}.json`); setNotice("导出完成。文件中的审核记录为导出时状态，请以文件内容为准。"); })}>{draft.status === "reviewed" ? "导出 JSON" : "导出待审核 JSON"}</button>
            </div>
          </section>
          <div className={styles.panel}><PublicationPanel kind={packet.kind ?? "postmatch"} key={`${draft.id}-${draft.revision}`} draftId={draft.id} revision={draft.revision} reviewed={draft.status === "reviewed"} disabled={locked || dirty} /></div>
          <details className={styles.panel}><summary>审核历史 · 最近 {history.length} 条</summary><ol>{history.map((event) => <li key={event.revision}><strong>第 {event.revision} 版 · {actionName[event.action] ?? event.action}</strong><p>{fmtUtc(event.created_at)}</p><details><summary>查看本版内容与记录</summary><p className={styles.hash}>操作人：{event.actor_id} · 内容校验：{event.content_hash}</p><pre className={styles.hash}>{event.content_json}</pre></details></li>)}</ol></details>
        </>}
      </section>
    </div>
  </main>;
}
