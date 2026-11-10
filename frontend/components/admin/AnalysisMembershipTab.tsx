"use client";

import { useRef, useState } from "react";
import { clientFetch, type GetJson } from "@/lib/api-v1";
import { fmtUtc } from "@/components/studio/api";
import styles from "@/app/admin/admin.module.css";

type Access = GetJson<"/api/v1/admin/users/{user_id}/analysis-access">;
type History = GetJson<"/api/v1/admin/users/{user_id}/analysis-access/history">;
type Users = GetJson<"/api/v1/admin/users">;
const basis: Record<string, string> = { trial: "试用有效", membership: "会员有效", none: "暂无有效权益", account_disabled: "账号已停用" };

export function AnalysisMembershipTab() {
  const [query, setQuery] = useState("");
  const [users, setUsers] = useState<Users["users"]>([]);
  const [selected, setSelected] = useState<Users["users"][number] | null>(null);
  const [access, setAccess] = useState<Access | null>(null);
  const [history, setHistory] = useState<History["events"]>([]);
  const [expiry, setExpiry] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const request = useRef<{ fingerprint: string; key: string } | null>(null);
  const endpoint = (id: string) => `/api/v1/admin/users/${encodeURIComponent(id)}/analysis-access`;
  async function read(id: string) {
    const [next, events] = await Promise.all([
      clientFetch<Access>(endpoint(id)), clientFetch<History>(endpoint(id) + "/history"),
    ]);
    setAccess(next); setHistory(events.events);
  }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (err) { setError(err instanceof Error ? err.message : "操作失败，请刷新后核对。"); }
    finally { setBusy(false); }
  }
  async function change(action: "grant" | "revoke") {
    if (!selected || !access) return;
    const endsAt = action === "grant" ? new Date(expiry + "+08:00").toISOString() : null;
    const payload = { action, ends_at: endsAt, expected_revision: access.membership_revision, reason: reason.trim() };
    const fingerprint = JSON.stringify({ user_id: selected.id, ...payload });
    if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, key: crypto.randomUUID() };
    try {
      await clientFetch(endpoint(selected.id), { method: "POST", body: { ...payload, request_key: request.current.key } });
      await read(selected.id);
      request.current = null; setReason("");
      setNotice(action === "grant" ? "会员期限已更新，审计记录已保存。" : "会员已撤销；仍有效的试用会保留。");
    } catch (err) {
      setAccess(null); // uncertain outcome/version: require a fresh read before another write
      throw err;
    }
  }
  return <div>
    <h2>分析会员</h2><p>通过公众号确认后开通。此处管理分析产品，现有 VIP 免费数据和按场精选授权保持独立。</p>
    <form onSubmit={(e) => { e.preventDefault(); void run(async () => { const result = await clientFetch<Users>(`/api/v1/admin/users?query=${encodeURIComponent(query)}&limit=20`); setUsers(result.users); }); }}>
      <label htmlFor="analysis-user-query">用户编号或昵称</label>{" "}
      <input id="analysis-user-query" value={query} onChange={(e) => setQuery(e.target.value)} disabled={busy} />{" "}
      <button className={styles.btnPrimary} disabled={busy}>查找用户</button>
    </form>
    {error && <p role="alert" className={styles.msgErr}>{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <ul>{users.map((user) => <li key={user.id}><button className={styles.btnGhost} disabled={busy} onClick={() => void run(async () => {
      setSelected(user); setAccess(null); setHistory([]); setReason(""); setExpiry(""); request.current = null; await read(user.id);
    })}>{user.display_name} · {user.short_code ?? user.id}</button></li>)}</ul>
    {selected && <section>
      <h3>{selected.display_name}</h3><button className={styles.btnGhost} disabled={busy} onClick={() => void run(() => read(selected.id))}>刷新权益</button>
      {access && <>
        <p>{basis[access.basis]} · 第 {access.membership_revision} 版</p>
        <p>试用到期：{access.trial_ends_at ? fmtUtc(access.trial_ends_at) : "尚未开始"}（北京时间）</p>
        <p>会员记录：{access.membership_status === "revoked" ? "已撤销" : access.membership_status === "active" ? "已开通" : "未开通"}；到期 {access.membership_ends_at ? fmtUtc(access.membership_ends_at) : "—"}（北京时间）</p>
        <form onSubmit={(e) => { e.preventDefault(); void run(() => change("grant")); }}>
          <p><label htmlFor="analysis-expiry">会员到期时间（北京时间）</label><br /><input id="analysis-expiry" type="datetime-local" required value={expiry} onChange={(e) => setExpiry(e.target.value)} disabled={busy} /></p>
          <p><label htmlFor="analysis-reason">变更原因</label><br /><textarea id="analysis-reason" required maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} disabled={busy} /></p>
          <button className={styles.btnPrimary} disabled={busy || !expiry || !reason.trim()}>设置会员期限</button>{" "}
          <button type="button" className={styles.btnGhost} disabled={busy || !reason.trim() || access.membership_status !== "active"} onClick={() => void run(() => change("revoke"))}>撤销会员</button>
        </form>
      </>}
      <h3>最近变更记录</h3><ol>{history.map((event) => <li key={event.id}>第 {event.revision} 版 · {event.action === "grant" ? "设置期限" : "撤销"} · {event.reason} · {fmtUtc(event.created_at)}</li>)}</ol>
    </section>}
  </div>;
}
