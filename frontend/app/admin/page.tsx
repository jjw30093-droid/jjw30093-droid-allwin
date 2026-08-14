"use client";

/**
 * /admin — 最小可用管理后台(权限真源在服务端:非 admin 调任何 admin API 均 403)。
 *
 * 全部数据浏览器端 clientFetch(cookie Path=/api/v1 + CSRF),不进 RSC payload。
 * 响应类型全部从 OpenAPI 生成类型派生(Pydantic 单一真源,宪法 §10.3)。
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  apiErrorMessage,
  clientFetch,
  getMe,
  type GetJson,
  type PostJson,
} from "@/lib/api-v1";
import styles from "./admin.module.css";

/* ── 类型:从生成类型派生 ──────────────────────────────── */

type UsersResp = GetJson<"/api/v1/admin/users">;

type ProductsResp = GetJson<"/api/v1/products">;
/** 开通/生成表单只用到套餐的这三个字段(含本地回退项),故 Pick 收窄。 */
type PlanInfo = Pick<ProductsResp["plans"][number], "id" | "name_zh" | "rank">;

type GrantResp = PostJson<"/api/v1/admin/users/{user_id}/grant">;

type CodesListResp = GetJson<"/api/v1/admin/redeem-codes">;
type CodesCreateResp = PostJson<"/api/v1/admin/redeem-codes">;

type PredictionsResp = GetJson<"/api/v1/admin/predictions">;
type PublishUpcomingResp = PostJson<"/api/v1/admin/predictions/publish-upcoming">;
type EditPredictionResp = PostJson<"/api/v1/admin/predictions/{snapshot_id}/edit">;

type XrefResp = GetJson<"/api/v1/admin/xref">;

type AuditResp = GetJson<"/api/v1/admin/audit-logs">;

/* ── 工具 ──────────────────────────────────────────────── */

function fmtLocal(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

function shortId(id: string | null | undefined, n = 8): string {
  if (!id) return "—";
  return id.length > n ? `${id.slice(0, n)}…` : id;
}

function pct(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function isForbidden(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403;
}

type Msg = { kind: "ok" | "err"; text: string } | null;

function MsgBar({ msg }: { msg: Msg }) {
  if (!msg) return null;
  return (
    <p className={msg.kind === "ok" ? styles.msgOk : styles.msgErr}>{msg.text}</p>
  );
}

function Loading() {
  return (
    <div aria-busy="true">
      <div className={styles.skeleton} />
      <div className={styles.skeletonShort} />
    </div>
  );
}

/* ── Tab:用户 ─────────────────────────────────────────── */

function UsersTab({ plans }: { plans: PlanInfo[] }) {
  const [query, setQuery] = useState("");
  const [data, setData] = useState<UsersResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<Msg>(null);
  const [grantFor, setGrantFor] = useState<string | null>(null);
  const [grantPlan, setGrantPlan] = useState("pro");
  const [grantDays, setGrantDays] = useState(30);
  const [grantNotes, setGrantNotes] = useState("");
  const [revokeSubId, setRevokeSubId] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (q: string) => {
    setLoading(true);
    try {
      const r = await clientFetch<UsersResp>(
        `/api/v1/admin/users?query=${encodeURIComponent(q)}&limit=100`,
      );
      setData(r);
      setMsg(null);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "用户列表加载失败") });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // 经微任务回调触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)
    void Promise.resolve().then(() => load(""));
  }, [load]);

  const grantablePlans = plans.filter((p) => p.rank > 0);

  const onGrant = async (userId: string) => {
    if (
      !window.confirm(
        `确认为用户 ${shortId(userId)} 开通 ${grantPlan} × ${grantDays} 天?`,
      )
    )
      return;
    setBusy(true);
    try {
      const r = await clientFetch<GrantResp>(`/api/v1/admin/users/${userId}/grant`, {
        method: "POST",
        body: { plan_id: grantPlan, duration_days: grantDays, notes: grantNotes },
      });
      setMsg({
        kind: "ok",
        text: `已开通:订阅 ID ${r.subscription_id},${r.plan_id} 至 ${fmtLocal(r.ends_at)}(撤销时需要此订阅 ID)`,
      });
      setGrantFor(null);
      setGrantNotes("");
      await load(query);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "开通失败") });
    } finally {
      setBusy(false);
    }
  };

  const onRevokeSub = async () => {
    const sid = revokeSubId.trim();
    if (!sid) return;
    if (!window.confirm(`确认撤销订阅 ${sid}?该用户对应权益立即失效。`)) return;
    setBusy(true);
    try {
      await clientFetch(`/api/v1/admin/subscriptions/${encodeURIComponent(sid)}/revoke`, {
        method: "POST",
        body: {},
      });
      setMsg({ kind: "ok", text: `订阅 ${sid} 已撤销` });
      setRevokeSubId("");
      await load(query);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "撤销失败") });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <MsgBar msg={msg} />
      <div className={styles.toolbar}>
        <input
          className={styles.input}
          placeholder="按昵称 / 用户 ID 搜索"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void load(query);
          }}
        />
        <button type="button" className={styles.btnGhost} onClick={() => load(query)}>
          搜索
        </button>
        {data && <span className={styles.dim}>共 {data.total} 个用户</span>}
      </div>

      <div className={styles.panel}>
        <span className={styles.panelLabel}>按订阅 ID 撤销</span>
        <input
          className={styles.input}
          placeholder="订阅 ID(见开通结果或审计日志)"
          value={revokeSubId}
          onChange={(e) => setRevokeSubId(e.target.value)}
        />
        <button
          type="button"
          className={styles.btnDanger}
          disabled={busy || !revokeSubId.trim()}
          onClick={onRevokeSub}
        >
          撤销订阅
        </button>
      </div>

      {loading ? (
        <Loading />
      ) : !data || data.users.length === 0 ? (
        <p className={styles.empty}>没有匹配的用户</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>用户</th>
                <th>角色</th>
                <th>状态</th>
                <th>套餐</th>
                <th>套餐到期</th>
                <th>注册</th>
                <th>最近登录</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {data.users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div>{u.display_name ?? "未设置"}</div>
                    <div className={`${styles.dim} num`}>{shortId(u.id, 12)}</div>
                  </td>
                  <td>{u.role}</td>
                  <td>{u.status}</td>
                  <td>{u.plan_id}</td>
                  <td className="num">{fmtLocal(u.plan_ends_at)}</td>
                  <td className="num">{fmtLocal(u.created_at)}</td>
                  <td className="num">{fmtLocal(u.last_login_at)}</td>
                  <td>
                    {grantFor === u.id ? (
                      <div className={styles.inlineForm}>
                        <select
                          className={styles.input}
                          value={grantPlan}
                          onChange={(e) => setGrantPlan(e.target.value)}
                        >
                          {(grantablePlans.length > 0
                            ? grantablePlans
                            : [{ id: grantPlan, name_zh: grantPlan, rank: 1 }]
                          ).map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.name_zh}({p.id})
                            </option>
                          ))}
                        </select>
                        <input
                          className={`${styles.input} ${styles.inputNarrow}`}
                          type="number"
                          min={1}
                          max={3650}
                          value={grantDays}
                          onChange={(e) => setGrantDays(Number(e.target.value))}
                        />
                        <span className={styles.dim}>天</span>
                        <input
                          className={styles.input}
                          placeholder="备注(可空)"
                          value={grantNotes}
                          onChange={(e) => setGrantNotes(e.target.value)}
                        />
                        <button
                          type="button"
                          className={styles.btnPrimary}
                          disabled={busy || grantDays <= 0}
                          onClick={() => onGrant(u.id)}
                        >
                          确认开通
                        </button>
                        <button
                          type="button"
                          className={styles.btnGhost}
                          onClick={() => setGrantFor(null)}
                        >
                          取消
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className={styles.btnGhost}
                        onClick={() => setGrantFor(u.id)}
                      >
                        开通订阅
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ── Tab:兑换码 ───────────────────────────────────────── */

function CodesTab({ plans }: { plans: PlanInfo[] }) {
  const [list, setList] = useState<CodesListResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<Msg>(null);
  const [batchFilter, setBatchFilter] = useState("");
  const [planId, setPlanId] = useState("pro");
  const [days, setDays] = useState(30);
  const [count, setCount] = useState(10);
  const [expiresLocal, setExpiresLocal] = useState("");
  const [created, setCreated] = useState<CodesCreateResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async (batch: string) => {
    setLoading(true);
    try {
      const r = await clientFetch<CodesListResp>(
        `/api/v1/admin/redeem-codes?batch_id=${encodeURIComponent(batch)}&limit=200`,
      );
      setList(r);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "兑换码列表加载失败") });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // 经微任务回调触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)
    void Promise.resolve().then(() => load(""));
  }, [load]);

  const grantablePlans = plans.filter((p) => p.rank > 0);

  const onCreate = async () => {
    if (!window.confirm(`确认生成 ${count} 个「${planId} × ${days} 天」兑换码?`)) return;
    setBusy(true);
    setMsg(null);
    try {
      // datetime-local 是用户本地时区,转成后端统一的 UTC Z 格式
      const expires_at = expiresLocal
        ? new Date(expiresLocal).toISOString().replace(/\.\d{3}Z$/, "Z")
        : null;
      const r = await clientFetch<CodesCreateResp>("/api/v1/admin/redeem-codes", {
        method: "POST",
        body: { plan_id: planId, duration_days: days, count, expires_at },
      });
      setCreated(r);
      setMsg({ kind: "ok", text: `已生成 ${r.codes.length} 个兑换码(明文仅本次显示,请立即保存)` });
      await load(batchFilter);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "生成失败") });
    } finally {
      setBusy(false);
    }
  };

  const copyAll = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.codes.map((c) => c.code).join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setMsg({ kind: "err", text: "复制失败,请手动选择复制" });
    }
  };

  return (
    <div>
      <MsgBar msg={msg} />
      <div className={styles.panel}>
        <span className={styles.panelLabel}>批量生成</span>
        <select
          className={styles.input}
          value={planId}
          onChange={(e) => setPlanId(e.target.value)}
        >
          {(grantablePlans.length > 0
            ? grantablePlans
            : [{ id: planId, name_zh: planId, rank: 1 }]
          ).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name_zh}({p.id})
            </option>
          ))}
        </select>
        <input
          className={`${styles.input} ${styles.inputNarrow}`}
          type="number"
          min={1}
          max={3650}
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
        />
        <span className={styles.dim}>天 ×</span>
        <input
          className={`${styles.input} ${styles.inputNarrow}`}
          type="number"
          min={1}
          max={500}
          value={count}
          onChange={(e) => setCount(Number(e.target.value))}
        />
        <span className={styles.dim}>个 · 兑换截止(可空)</span>
        <input
          className={styles.input}
          type="datetime-local"
          value={expiresLocal}
          onChange={(e) => setExpiresLocal(e.target.value)}
        />
        <button
          type="button"
          className={styles.btnPrimary}
          disabled={busy || days <= 0 || count <= 0}
          onClick={onCreate}
        >
          {busy ? "生成中…" : "生成"}
        </button>
      </div>

      {created && (
        <div className={styles.codesBox}>
          <div className={styles.codesBoxHead}>
            <span>本批明文兑换码(仅此一次显示,数据库只存哈希)</span>
            <button type="button" className={styles.btnGhost} onClick={copyAll}>
              {copied ? "已复制" : "复制全部"}
            </button>
          </div>
          <pre className={styles.codesPre}>
            {created.codes.map((c) => c.code).join("\n")}
          </pre>
        </div>
      )}

      <div className={styles.toolbar}>
        <input
          className={styles.input}
          placeholder="按批次 ID 过滤"
          value={batchFilter}
          onChange={(e) => setBatchFilter(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void load(batchFilter);
          }}
        />
        <button type="button" className={styles.btnGhost} onClick={() => load(batchFilter)}>
          过滤
        </button>
      </div>

      {loading ? (
        <Loading />
      ) : !list || list.codes.length === 0 ? (
        <p className={styles.empty}>暂无兑换码记录</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>批次</th>
                <th>套餐</th>
                <th>时长</th>
                <th>状态</th>
                <th>创建</th>
                <th>兑换截止</th>
                <th>使用者</th>
                <th>使用时间</th>
              </tr>
            </thead>
            <tbody>
              {list.codes.map((c) => (
                <tr key={c.id}>
                  <td className="num">{c.batch_id}</td>
                  <td>{c.plan_id}</td>
                  <td className="num">{c.duration_days} 天</td>
                  <td>
                    <span
                      className={
                        c.status === "active"
                          ? styles.stateOk
                          : c.status === "used"
                            ? styles.stateWarn
                            : styles.stateDim
                      }
                    >
                      {c.status}
                    </span>
                  </td>
                  <td className="num">{fmtLocal(c.created_at)}</td>
                  <td className="num">{fmtLocal(c.expires_at)}</td>
                  <td className="num">{shortId(c.used_by)}</td>
                  <td className="num">{fmtLocal(c.used_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ── Tab:预测 ─────────────────────────────────────────── */

function PredictionsTab() {
  const [data, setData] = useState<PredictionsResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<Msg>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [retractFor, setRetractFor] = useState<string | null>(null);
  const [retractReason, setRetractReason] = useState("");
  const [lockOnPublish, setLockOnPublish] = useState(true);
  const [editFor, setEditFor] = useState<string | null>(null);
  const [editHomeWin, setEditHomeWin] = useState("");
  const [editDraw, setEditDraw] = useState("");
  const [editAwayWin, setEditAwayWin] = useState("");
  const [editReason, setEditReason] = useState("");

  const load = useCallback(async (status: string) => {
    setLoading(true);
    try {
      const r = await clientFetch<PredictionsResp>(
        `/api/v1/admin/predictions?status=${encodeURIComponent(status)}&limit=200`,
      );
      setData(r);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "预测列表加载失败") });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => load(statusFilter));
  }, [load, statusFilter]);

  const act = async (
    id: string,
    action: "publish" | "lock",
    confirmText: string,
  ) => {
    if (!window.confirm(confirmText)) return;
    setBusyId(id);
    setMsg(null);
    try {
      await clientFetch(`/api/v1/admin/predictions/${id}/${action}`, {
        method: "POST",
        body: {},
      });
      setMsg({ kind: "ok", text: `操作成功:${action} ${shortId(id)}` });
      await load(statusFilter);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "操作失败") });
    } finally {
      setBusyId(null);
    }
  };

  const onRetract = async (id: string) => {
    const reason = retractReason.trim();
    if (reason.length < 2) {
      setMsg({ kind: "err", text: "撤回原因至少 2 个字符" });
      return;
    }
    if (
      !window.confirm(
        `确认撤回预测 ${shortId(id)}?撤回将保留在公开记录中,不可物理删除。`,
      )
    )
      return;
    setBusyId(id);
    setMsg(null);
    try {
      await clientFetch(`/api/v1/admin/predictions/${id}/retract`, {
        method: "POST",
        body: { reason },
      });
      setMsg({ kind: "ok", text: `已撤回 ${shortId(id)}` });
      setRetractFor(null);
      setRetractReason("");
      await load(statusFilter);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "撤回失败") });
    } finally {
      setBusyId(null);
    }
  };

  const openEdit = (p: PredictionsResp["predictions"][number]) => {
    setEditFor(p.id);
    setEditHomeWin(String(p.home_win));
    setEditDraw(String(p.draw));
    setEditAwayWin(String(p.away_win));
    setEditReason("");
  };

  const onEdit = async (id: string) => {
    const reason = editReason.trim();
    if (reason.length < 2) {
      setMsg({ kind: "err", text: "修正原因至少 2 个字符" });
      return;
    }
    const home_win = Number(editHomeWin);
    const draw = Number(editDraw);
    const away_win = Number(editAwayWin);
    if ([home_win, draw, away_win].some((v) => Number.isNaN(v))) {
      setMsg({ kind: "err", text: "概率必须是数字" });
      return;
    }
    setBusyId(id);
    setMsg(null);
    try {
      const r = await clientFetch<EditPredictionResp>(
        `/api/v1/admin/predictions/${id}/edit`,
        { method: "POST", body: { home_win, draw, away_win, reason } },
      );
      setMsg({
        kind: "ok",
        text:
          r.changed_fields.length > 0
            ? `已修正 ${shortId(id)}(${r.changed_fields.join("、")}),累计修正 ${r.edit_count} 次`
            : `${shortId(id)} 未产生实质变化,未记录修正`,
      });
      setEditFor(null);
      setEditReason("");
      await load(statusFilter);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "修正失败") });
    } finally {
      setBusyId(null);
    }
  };

  const onPublishUpcoming = async () => {
    if (
      !window.confirm(
        `确认批量发布所有未开球的 draft 预测?${lockOnPublish ? "(发布后立即锁定)" : ""}`,
      )
    )
      return;
    setBusyId("publish-upcoming");
    setMsg(null);
    try {
      const r = await clientFetch<PublishUpcomingResp>(
        "/api/v1/admin/predictions/publish-upcoming",
        { method: "POST", body: { lock: lockOnPublish } },
      );
      const failText =
        r.failed.length > 0
          ? `;失败 ${r.failed.length} 条:${r.failed
              .map((f) => `${shortId(f.id)}(${f.reason})`)
              .join("、")}`
          : "";
      setMsg({ kind: r.failed.length > 0 ? "err" : "ok", text: `已发布 ${r.published} 条${failText}` });
      await load(statusFilter);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "批量发布失败") });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <MsgBar msg={msg} />
      <div className={styles.toolbar}>
        <select
          className={styles.input}
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">全部状态</option>
          {Object.keys(data?.counts ?? {}).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {data &&
          Object.entries(data.counts).map(([s, n]) => (
            <span key={s} className={styles.chip}>
              {s}: <span className="num">{n}</span>
            </span>
          ))}
        <span className={styles.spacer} />
        <label className={styles.checkLabel}>
          <input
            type="checkbox"
            checked={lockOnPublish}
            onChange={(e) => setLockOnPublish(e.target.checked)}
          />
          发布后锁定
        </label>
        <button
          type="button"
          className={styles.btnPrimary}
          disabled={busyId === "publish-upcoming"}
          onClick={onPublishUpcoming}
        >
          {busyId === "publish-upcoming" ? "发布中…" : "批量发布未开球 draft"}
        </button>
      </div>

      {loading ? (
        <Loading />
      ) : !data || data.predictions.length === 0 ? (
        <p className={styles.empty}>没有符合条件的预测快照</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>比赛</th>
                <th>开球(本地时间)</th>
                <th>状态</th>
                <th>主/平/客</th>
                <th>模型版本</th>
                <th>发布 / 锁定</th>
                <th>修正</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {data.predictions.map((p) => (
                <tr key={p.id}>
                  <td className="num">#{p.match_id}</td>
                  <td className="num">{fmtLocal(p.kickoff_at_utc)}</td>
                  <td>
                    <span
                      className={
                        p.status === "locked"
                          ? styles.stateOk
                          : p.status === "published"
                            ? styles.stateWarn
                            : p.status === "retracted"
                              ? styles.stateBad
                              : styles.stateDim
                      }
                    >
                      {p.status}
                    </span>
                    {p.is_official === 1 && <span className={styles.dim}> · 正式</span>}
                  </td>
                  <td className="num">
                    {pct(p.home_win)} / {pct(p.draw)} / {pct(p.away_win)}
                  </td>
                  <td className="num">{shortId(p.model_version_id, 10)}</td>
                  <td className="num">
                    {fmtLocal(p.published_at)}
                    <br />
                    {fmtLocal(p.locked_at)}
                  </td>
                  <td className="num">
                    {p.edit_count > 0 ? (
                      <>
                        已修正 {p.edit_count} 次
                        <br />
                        <span className={styles.dim}>{fmtLocal(p.last_edited_at)}</span>
                      </>
                    ) : (
                      <span className={styles.dim}>未修正</span>
                    )}
                  </td>
                  <td>
                    <div className={styles.inlineForm}>
                      {p.status === "draft" && (
                        <button
                          type="button"
                          className={styles.btnGhost}
                          disabled={busyId === p.id}
                          onClick={() =>
                            act(p.id, "publish", `确认发布预测 ${shortId(p.id)}?`)
                          }
                        >
                          发布
                        </button>
                      )}
                      {p.status === "published" && (
                        <button
                          type="button"
                          className={styles.btnGhost}
                          disabled={busyId === p.id}
                          onClick={() =>
                            act(
                              p.id,
                              "lock",
                              `确认锁定预测 ${shortId(p.id)}?锁定后即计入公开正式战绩;仍可通过"编辑"修正,修正会留下公开可查的记录。`,
                            )
                          }
                        >
                          锁定
                        </button>
                      )}
                      {editFor === p.id ? (
                        <div className={styles.inlineForm}>
                          <input
                            className={`${styles.input} ${styles.inputNarrow}`}
                            type="number"
                            step="0.0001"
                            placeholder="主胜"
                            value={editHomeWin}
                            onChange={(e) => setEditHomeWin(e.target.value)}
                          />
                          <input
                            className={`${styles.input} ${styles.inputNarrow}`}
                            type="number"
                            step="0.0001"
                            placeholder="平局"
                            value={editDraw}
                            onChange={(e) => setEditDraw(e.target.value)}
                          />
                          <input
                            className={`${styles.input} ${styles.inputNarrow}`}
                            type="number"
                            step="0.0001"
                            placeholder="客胜"
                            value={editAwayWin}
                            onChange={(e) => setEditAwayWin(e.target.value)}
                          />
                          <input
                            className={styles.input}
                            placeholder="修正原因(必填)"
                            value={editReason}
                            onChange={(e) => setEditReason(e.target.value)}
                          />
                          <button
                            type="button"
                            className={styles.btnPrimary}
                            disabled={busyId === p.id}
                            onClick={() => onEdit(p.id)}
                          >
                            确认修正
                          </button>
                          <button
                            type="button"
                            className={styles.btnGhost}
                            onClick={() => setEditFor(null)}
                          >
                            取消
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className={styles.btnGhost}
                          disabled={busyId === p.id}
                          onClick={() => openEdit(p)}
                        >
                          编辑
                        </button>
                      )}
                      {p.status !== "retracted" &&
                        (retractFor === p.id ? (
                          <>
                            <input
                              className={styles.input}
                              placeholder="撤回原因(必填)"
                              value={retractReason}
                              onChange={(e) => setRetractReason(e.target.value)}
                            />
                            <button
                              type="button"
                              className={styles.btnDanger}
                              disabled={busyId === p.id}
                              onClick={() => onRetract(p.id)}
                            >
                              确认撤回
                            </button>
                            <button
                              type="button"
                              className={styles.btnGhost}
                              onClick={() => {
                                setRetractFor(null);
                                setRetractReason("");
                              }}
                            >
                              取消
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            className={styles.btnDanger}
                            disabled={busyId === p.id}
                            onClick={() => setRetractFor(p.id)}
                          >
                            撤回
                          </button>
                        ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ── Tab:xref 审核 ────────────────────────────────────── */

function XrefTab() {
  const [data, setData] = useState<XrefResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<Msg>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async (status: string) => {
    setLoading(true);
    try {
      const r = await clientFetch<XrefResp>(
        `/api/v1/admin/xref?status=${encodeURIComponent(status)}&limit=200`,
      );
      setData(r);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "映射列表加载失败") });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => load(statusFilter));
  }, [load, statusFilter]);

  const review = async (id: number, action: "confirm" | "reject") => {
    const label = action === "confirm" ? "确认" : "驳回";
    if (!window.confirm(`确认${label}映射 #${id}?操作会写入审计日志。`)) return;
    setBusyId(id);
    setMsg(null);
    try {
      await clientFetch(`/api/v1/admin/xref/${id}/${action}`, {
        method: "POST",
        body: {},
      });
      setMsg({ kind: "ok", text: `映射 #${id} 已${label}` });
      await load(statusFilter);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, `${label}失败`) });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <MsgBar msg={msg} />
      <div className={styles.toolbar}>
        <select
          className={styles.input}
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">待处理(needs_review + auto_ok)</option>
          {Object.keys(data?.counts ?? {}).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {data &&
          Object.entries(data.counts).map(([s, n]) => (
            <span key={s} className={styles.chip}>
              {s}: <span className="num">{n}</span>
            </span>
          ))}
      </div>

      {loading ? (
        <Loading />
      ) : !data || data.xrefs.length === 0 ? (
        <p className={styles.empty}>没有待审核的比赛映射</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>ID</th>
                <th>来源</th>
                <th>来源比赛</th>
                <th>FotMob 比赛</th>
                <th>开球差(秒)</th>
                <th>置信度</th>
                <th>主客反转</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {data.xrefs.map((x) => (
                <tr key={x.id}>
                  <td className="num">{x.id}</td>
                  <td>{x.provider}</td>
                  <td className="num">{x.provider_match_id}</td>
                  <td className="num">{x.fotmob_match_id}</td>
                  <td className="num">{x.kickoff_diff_seconds ?? "—"}</td>
                  <td className="num">
                    {x.confidence == null ? "—" : x.confidence.toFixed(2)}
                  </td>
                  <td>{x.home_away_inverted === 1 ? "是" : "否"}</td>
                  <td>
                    <span
                      className={
                        x.review_status === "confirmed"
                          ? styles.stateOk
                          : x.review_status === "rejected"
                            ? styles.stateBad
                            : styles.stateWarn
                      }
                    >
                      {x.review_status}
                    </span>
                  </td>
                  <td>
                    <div className={styles.inlineForm}>
                      <button
                        type="button"
                        className={styles.btnGhost}
                        disabled={busyId === x.id}
                        onClick={() => review(x.id, "confirm")}
                      >
                        确认
                      </button>
                      <button
                        type="button"
                        className={styles.btnDanger}
                        disabled={busyId === x.id}
                        onClick={() => review(x.id, "reject")}
                      >
                        驳回
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ── Tab:任务健康(job_runs) ─────────────────────────── */

/** 列名对齐 platform.db job_runs 表(backend/migrations/platform/0001_init.sql)。 */
interface JobRunRow {
  id?: string;
  job_name?: string;
  status?: string;
  attempt?: number;
  max_attempts?: number;
  started_at?: string | null;
  finished_at?: string | null;
  input_count?: number | null;
  output_count?: number | null;
  error_summary?: string | null;
  created_at?: string;
}

function JobsTab() {
  const [state, setState] = useState<
    | { phase: "loading" }
    | { phase: "unavailable" }
    | { phase: "error"; message: string }
    | { phase: "ready"; jobs: JobRunRow[] }
  >({ phase: "loading" });

  const load = useCallback(async () => {
    setState({ phase: "loading" });
    try {
      const r = await clientFetch<{ jobs?: JobRunRow[] }>("/api/v1/admin/jobs?limit=100");
      if (Array.isArray(r.jobs)) setState({ phase: "ready", jobs: r.jobs });
      else setState({ phase: "unavailable" });
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
        setState({ phase: "unavailable" });
      } else {
        setState({ phase: "error", message: apiErrorMessage(e, "任务健康数据加载失败") });
      }
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => load());
  }, [load]);

  if (state.phase === "loading") return <Loading />;
  if (state.phase === "unavailable") {
    return (
      <p className={styles.empty}>
        后端尚未提供任务健康接口(GET /api/v1/admin/jobs)。job_runs 表已存在于
        platform.db,待 Worker 阶段(P0.11)接口上线后此处自动可用。
      </p>
    );
  }
  if (state.phase === "error") {
    return (
      <div>
        <p className={styles.msgErr}>{state.message}</p>
        <button type="button" className={styles.btnGhost} onClick={load}>
          重试
        </button>
      </div>
    );
  }
  if (state.jobs.length === 0) {
    return <p className={styles.empty}>暂无任务运行记录</p>;
  }
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>任务</th>
            <th>状态</th>
            <th>尝试</th>
            <th>开始</th>
            <th>结束</th>
            <th>输入/输出</th>
            <th>错误摘要</th>
          </tr>
        </thead>
        <tbody>
          {state.jobs.map((j, i) => (
            <tr key={j.id ?? i}>
              <td>{j.job_name ?? "—"}</td>
              <td>
                <span
                  className={
                    j.status === "succeeded"
                      ? styles.stateOk
                      : j.status === "failed"
                        ? styles.stateBad
                        : styles.stateWarn
                  }
                >
                  {j.status ?? "—"}
                </span>
              </td>
              <td className="num">
                {j.attempt ?? "—"}/{j.max_attempts ?? "—"}
              </td>
              <td className="num">{fmtLocal(j.started_at)}</td>
              <td className="num">{fmtLocal(j.finished_at)}</td>
              <td className="num">
                {j.input_count ?? "—"} / {j.output_count ?? "—"}
              </td>
              <td className={styles.dim}>{j.error_summary ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Tab:审计日志 ─────────────────────────────────────── */

function AuditTab() {
  const [data, setData] = useState<AuditResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<Msg>(null);
  const [offset, setOffset] = useState(0);
  const LIMIT = 100;

  const load = useCallback(async (off: number) => {
    setLoading(true);
    try {
      const r = await clientFetch<AuditResp>(
        `/api/v1/admin/audit-logs?limit=${LIMIT}&offset=${off}`,
      );
      setData(r);
      setMsg(null);
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "审计日志加载失败") });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => load(offset));
  }, [load, offset]);

  return (
    <div>
      <MsgBar msg={msg} />
      {loading ? (
        <Loading />
      ) : !data || data.logs.length === 0 ? (
        <p className={styles.empty}>{offset > 0 ? "没有更多日志" : "暂无审计日志"}</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>ID</th>
                <th>时间</th>
                <th>操作者</th>
                <th>动作</th>
                <th>目标</th>
                <th>详情</th>
              </tr>
            </thead>
            <tbody>
              {data.logs.map((l) => (
                <tr key={l.id}>
                  <td className="num">{l.id}</td>
                  <td className="num">{fmtLocal(l.created_at)}</td>
                  <td>
                    <span className="num">{shortId(l.actor_user_id)}</span>
                    <span className={styles.dim}>({l.actor_type})</span>
                  </td>
                  <td>{l.action}</td>
                  <td className="num">
                    {l.target_type ?? "—"}
                    {l.target_id ? ` #${shortId(l.target_id, 12)}` : ""}
                  </td>
                  <td>
                    <details>
                      <summary className={styles.detailSummary}>
                        {l.detail_json.length > 48
                          ? `${l.detail_json.slice(0, 48)}…`
                          : l.detail_json}
                      </summary>
                      <pre className={styles.detailPre}>{l.detail_json}</pre>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className={styles.toolbar}>
        <button
          type="button"
          className={styles.btnGhost}
          disabled={offset === 0 || loading}
          onClick={() => setOffset(Math.max(0, offset - LIMIT))}
        >
          上一页
        </button>
        <button
          type="button"
          className={styles.btnGhost}
          disabled={loading || !data || data.logs.length < LIMIT}
          onClick={() => setOffset(offset + LIMIT)}
        >
          下一页
        </button>
      </div>
    </div>
  );
}

/* ── 页面外壳 ──────────────────────────────────────────── */

/* ── Tab:每日精选(人工推荐板块;内容可修正但全程留痕) ── */

type RecoSlipsResp = GetJson<"/api/v1/admin/reco/slips">;
type RecoSlip = RecoSlipsResp["slips"][number];
type RecoCreateResp = PostJson<"/api/v1/admin/reco/slips">;
type RecoSettleResp = PostJson<"/api/v1/admin/reco/slips/{slip_id}/settle">;

type RecoMatchCandidatesResp = GetJson<"/api/v1/admin/reco/match-candidates">;
type RecoMatchCandidate = RecoMatchCandidatesResp["matches"][number];
type RecoOddsOptionsResp = GetJson<"/api/v1/admin/reco/match-candidates/{match_id}/odds-options">;
type RecoOddsOption = RecoOddsOptionsResp["options"][number];

const RECO_RESULT_ZH: Record<string, string> = { win: "命中", lose: "未中", push: "走水" };
const RECO_STATUS_ZH: Record<string, string> = {
  draft: "草稿", published: "已发布", settled: "已结算", voided: "已作废",
};

type LegDraft = {
  match_id: number | null;
  match_desc: string;
  market: string;
  selection: string;
  odds: string;
};
const emptyLeg = (): LegDraft => ({ match_id: null, match_desc: "", market: "1x2", selection: "", odds: "" });

/** 300ms 防抖:比赛搜索框边打字边查询,不是每个按键都发请求。 */
function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

function matchCandidateLabel(m: RecoMatchCandidate): string {
  const kickoff = m.kickoff_at_utc
    ? new Date(m.kickoff_at_utc).toLocaleString("zh-CN", {
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
        hour12: false, timeZone: "Asia/Shanghai",
      })
    : "";
  return `${m.home_name} vs ${m.away_name}${kickoff ? ` ${kickoff}` : ""}`;
}

/**
 * 单条腿的比赛/赔率录入:比赛从真实比赛候选搜索选(替代自由文本描述);
 * 选定比赛后若抓到真实盘口(1x2/大小球/角球大小),赔率从真实选项里选,
 * 不用手打数字——历史上手打的赔率数字没有任何东西保证它和真实盘口对得上。
 * 没有真实数据(未抓到 / AH 等未覆盖市场)时优雅退回原有自由文本三格,
 * 不因为"选不出来"就让这条腿没法录。
 */
function LegRowEditor({
  leg, onChange, onRemove, removable,
}: {
  leg: LegDraft;
  onChange: (patch: Partial<LegDraft>) => void;
  onRemove: () => void;
  removable: boolean;
}) {
  const [matchQuery, setMatchQuery] = useState(leg.match_desc);
  const [candidates, setCandidates] = useState<RecoMatchCandidate[]>([]);
  const [showCandidates, setShowCandidates] = useState(false);
  const [oddsOptions, setOddsOptions] = useState<RecoOddsOption[]>([]);
  const [manualOdds, setManualOdds] = useState(false);
  const debouncedQuery = useDebounced(matchQuery);

  useEffect(() => {
    if (!showCandidates) return;
    let cancelled = false;
    const q = debouncedQuery.trim();
    clientFetch<RecoMatchCandidatesResp>(
      `/api/v1/admin/reco/match-candidates${q ? `?q=${encodeURIComponent(q)}` : ""}`,
    )
      .then((r) => { if (!cancelled) setCandidates(r.matches); })
      .catch(() => { if (!cancelled) setCandidates([]); });
    return () => { cancelled = true; };
  }, [debouncedQuery, showCandidates]);

  useEffect(() => {
    let cancelled = false;
    if (leg.match_id == null) {
      void Promise.resolve().then(() => { if (!cancelled) setOddsOptions([]); });
      return () => { cancelled = true; };
    }
    clientFetch<RecoOddsOptionsResp>(
      `/api/v1/admin/reco/match-candidates/${leg.match_id}/odds-options`,
    )
      .then((r) => { if (!cancelled) setOddsOptions(r.options); })
      .catch(() => { if (!cancelled) setOddsOptions([]); });
    return () => { cancelled = true; };
  }, [leg.match_id]);

  const pickMatch = (m: RecoMatchCandidate) => {
    const label = matchCandidateLabel(m);
    setMatchQuery(label);
    setShowCandidates(false);
    setManualOdds(false);
    onChange({ match_id: m.match_id, match_desc: label });
  };

  const hasOptions = oddsOptions.length > 0;
  const showManualOddsFields = manualOdds || !hasOptions;
  const selectedOptionIndex = oddsOptions.findIndex(
    (o) => o.market === leg.market && o.selection === leg.selection && String(o.odds) === leg.odds,
  );

  return (
    <div className={styles.formRow}>
      <div className={styles.matchPicker}>
        <input
          className={styles.input}
          placeholder="比赛(搜索队名,或手动填写描述)"
          value={matchQuery}
          onFocus={() => setShowCandidates(true)}
          onChange={(e) => {
            const value = e.target.value;
            setMatchQuery(value);
            setShowCandidates(true);
            onChange({ match_id: null, match_desc: value });
          }}
          onBlur={() => setTimeout(() => setShowCandidates(false), 150)}
        />
        {showCandidates && (
          <ul className={styles.matchDropdown}>
            {candidates.length === 0 ? (
              <li className={styles.matchDropdownEmpty}>没有匹配的未开赛比赛</li>
            ) : (
              candidates.map((m) => (
                <li key={m.match_id}>
                  <button
                    type="button"
                    className={styles.matchDropdownItem}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => pickMatch(m)}
                  >
                    {m.league_name} · {matchCandidateLabel(m)}
                  </button>
                </li>
              ))
            )}
          </ul>
        )}
      </div>

      {showManualOddsFields ? (
        <>
          <input className={styles.input} placeholder="玩法" value={leg.market} style={{ maxWidth: 90 }}
                 onChange={(e) => onChange({ market: e.target.value })} />
          <input className={styles.input} placeholder="选项(如 主胜)" value={leg.selection} style={{ maxWidth: 140 }}
                 onChange={(e) => onChange({ selection: e.target.value })} />
          <input className={styles.input} placeholder="赔率" value={leg.odds} style={{ maxWidth: 80 }}
                 onChange={(e) => onChange({ odds: e.target.value })} />
        </>
      ) : (
        <select
          className={styles.input}
          style={{ maxWidth: 260 }}
          value={selectedOptionIndex >= 0 ? String(selectedOptionIndex) : ""}
          onChange={(e) => {
            const opt = oddsOptions[Number(e.target.value)];
            if (opt) onChange({ market: opt.market, selection: opt.selection, odds: String(opt.odds) });
          }}
        >
          <option value="" disabled>真实盘口选项…</option>
          {oddsOptions.map((o, i) => (
            <option key={i} value={i}>
              {o.market_label} · {o.selection} @{o.odds}({o.company_name})
            </option>
          ))}
        </select>
      )}
      {hasOptions && (
        <button type="button" className={styles.btnGhost}
                onClick={() => setManualOdds((v) => !v)}>
          {manualOdds ? "改用真实盘口" : "手动填写"}
        </button>
      )}
      {removable && (
        <button type="button" className={styles.btnGhost} onClick={onRemove}>删</button>
      )}
    </div>
  );
}

function beijingToday(): string {
  return new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);
}

function RecoTab() {
  const [data, setData] = useState<RecoSlipsResp | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // 新建表单
  const [slipDate, setSlipDate] = useState(beijingToday());
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [legs, setLegs] = useState<LegDraft[]>([emptyLeg()]);

  // 结算面板:slipId → legId → result
  const [settleFor, setSettleFor] = useState<string | null>(null);
  const [legResults, setLegResults] = useState<Record<string, string>>({});
  const [voidFor, setVoidFor] = useState<string | null>(null);
  const [voidReason, setVoidReason] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await clientFetch<RecoSlipsResp>("/api/v1/admin/reco/slips"));
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "推荐单列表加载失败") });
    }
  }, []);
  // 经微任务回调触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)
  useEffect(() => {
    void Promise.resolve().then(() => load());
  }, [load]);

  const create = async () => {
    const parsed = legs.map((l) => ({
      match_id: l.match_id,
      match_desc: l.match_desc.trim(),
      market: l.market.trim(),
      selection: l.selection.trim(),
      odds: Number(l.odds),
    }));
    if (parsed.some((l) => !l.match_desc || !l.selection || !(l.odds > 1))) {
      setMsg({ kind: "err", text: "每条腿需要 比赛/选项/大于1的赔率" });
      return;
    }
    try {
      await clientFetch<RecoCreateResp>("/api/v1/admin/reco/slips", {
        method: "POST",
        body: { slip_date: slipDate, title: title.trim(), note: note.trim() || null, legs: parsed },
      });
      setMsg({ kind: "ok", text: "已创建草稿" });
      setTitle(""); setNote(""); setLegs([emptyLeg()]);
      void load();
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "创建失败") });
    }
  };

  const act = async (id: string, path: string, body?: object) => {
    setBusyId(id);
    try {
      await clientFetch(path, { method: "POST", body: body ?? {} });
      setMsg({ kind: "ok", text: "操作成功" });
      setSettleFor(null); setVoidFor(null); setVoidReason("");
      void load();
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "操作失败") });
    } finally {
      setBusyId(null);
    }
  };

  const submitSettle = async (s: RecoSlip) => {
    const missing = s.legs.filter((l) => !legResults[l.id]);
    if (missing.length) {
      setMsg({ kind: "err", text: "每条腿都要选结果" });
      return;
    }
    setBusyId(s.id);
    try {
      const r = await clientFetch<RecoSettleResp>(
        `/api/v1/admin/reco/slips/${s.id}/settle`,
        { method: "POST", body: { leg_results: legResults } },
      );
      setMsg({ kind: "ok", text: `已结算:${RECO_RESULT_ZH[r.result]},回报 ${r.return_units} 单位` });
      setSettleFor(null); setLegResults({});
      void load();
    } catch (e) {
      setMsg({ kind: "err", text: apiErrorMessage(e, "结算失败") });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <MsgBar msg={msg} />

      <section className={styles.recoPanel}>
        <h2 className={styles.panelTitle}>新建推荐单(草稿)</h2>
        <div className={styles.formRow}>
          <label className={styles.fieldInline}>
            <span>日期</span>
            <input type="date" className={styles.input} value={slipDate}
                   onChange={(e) => setSlipDate(e.target.value)} />
          </label>
          <label className={styles.fieldInline}>
            <span>标题</span>
            <input className={styles.input} value={title} placeholder="如:今日三串一"
                   onChange={(e) => setTitle(e.target.value)} />
          </label>
        </div>
        <label className={styles.fieldInline}>
          <span>思路(可空)</span>
          <input className={styles.input} value={note}
                 onChange={(e) => setNote(e.target.value)} />
        </label>
        {legs.map((leg, i) => (
          <LegRowEditor
            key={i}
            leg={leg}
            removable={legs.length > 1}
            onChange={(patch) => setLegs(ls => ls.map((x, j) => j === i ? { ...x, ...patch } : x))}
            onRemove={() => setLegs(ls => ls.filter((_, j) => j !== i))}
          />
        ))}
        <div className={styles.formRow}>
          <button type="button" className={styles.btnGhost}
                  onClick={() => setLegs(ls => [...ls, emptyLeg()])}>+ 加一腿</button>
          <button type="button" className={styles.btnPrimary} onClick={create}
                  disabled={!title.trim()}>创建草稿</button>
        </div>
      </section>

      {!data ? <Loading /> : (
        <section className={styles.recoPanel}>
          <h2 className={styles.panelTitle}>推荐单({data.total})</h2>
          {data.slips.length === 0 && <p className={styles.empty}>还没有推荐单</p>}
          {data.slips.map((s) => (
            <div key={s.id} className={styles.rowCard}>
              <div>
                <strong>{s.slip_date} · {s.title}</strong>
                {" "}
                <span className={styles.badge}>{RECO_STATUS_ZH[s.status]}</span>
                {s.result && <span className={styles.badge}>{RECO_RESULT_ZH[s.result]}</span>}
                {s.return_units != null && <span className={styles.dim}> 回报 {s.return_units} 单位</span>}
                {s.edit_count > 0 && <span className={styles.dim}>(修正 {s.edit_count} 次)</span>}
                <ul>
                  {s.legs.map((l) => (
                    <li key={l.id} className={styles.dim}>
                      {l.match_desc} · {l.market} · {l.selection} @{l.odds}
                      {l.result ? ` → ${RECO_RESULT_ZH[l.result]}` : ""}
                    </li>
                  ))}
                </ul>
              </div>
              <div className={styles.rowActions}>
                {s.status === "draft" && (
                  <button className={styles.btnPrimary} disabled={busyId === s.id}
                          onClick={() => act(s.id, `/api/v1/admin/reco/slips/${s.id}/publish`)}>发布</button>
                )}
                {(s.status === "published" || s.status === "settled") && (
                  <button className={styles.btnGhost} disabled={busyId === s.id}
                          onClick={() => { setSettleFor(settleFor === s.id ? null : s.id); setLegResults({}); }}>
                    {s.status === "settled" ? "重新结算(留痕)" : "结算"}
                  </button>
                )}
                {s.status !== "voided" && (
                  <button className={styles.btnGhost} disabled={busyId === s.id}
                          onClick={() => setVoidFor(voidFor === s.id ? null : s.id)}>作废</button>
                )}
              </div>
              {settleFor === s.id && (
                <div className={styles.formRow}>
                  {s.legs.map((l) => (
                    <label key={l.id} className={styles.fieldInline}>
                      <span>{l.selection}</span>
                      <select className={styles.input} value={legResults[l.id] ?? ""}
                              onChange={(e) => setLegResults(r => ({ ...r, [l.id]: e.target.value }))}>
                        <option value="">选结果</option>
                        <option value="win">命中</option>
                        <option value="lose">未中</option>
                        <option value="push">走水</option>
                      </select>
                    </label>
                  ))}
                  <button className={styles.btnPrimary} disabled={busyId === s.id}
                          onClick={() => void submitSettle(s)}>提交结算</button>
                </div>
              )}
              {voidFor === s.id && (
                <div className={styles.formRow}>
                  <input className={styles.input} placeholder="作废原因(必填,战绩页单列展示)"
                         value={voidReason} onChange={(e) => setVoidReason(e.target.value)} />
                  <button className={styles.btnPrimary} disabled={busyId === s.id || !voidReason.trim()}
                          onClick={() => act(s.id, `/api/v1/admin/reco/slips/${s.id}/void`, { reason: voidReason })}>
                    确认作废
                  </button>
                </div>
              )}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

const TABS = [  { key: "users", label: "用户" },
  { key: "codes", label: "兑换码" },
  { key: "reco", label: "每日精选" },
  { key: "predictions", label: "预测" },
  { key: "xref", label: "映射审核" },
  { key: "jobs", label: "任务健康" },
  { key: "audit", label: "审计日志" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

type GateState =
  | { phase: "loading" }
  | { phase: "anonymous" }
  | { phase: "forbidden" }
  | { phase: "error"; message: string }
  | { phase: "ok" };

export default function AdminPage() {
  const [gate, setGate] = useState<GateState>({ phase: "loading" });
  const [tab, setTab] = useState<TabKey>("users");
  const [plans, setPlans] = useState<PlanInfo[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await getMe();
        if (cancelled) return;
        if (!me.authenticated) {
          setGate({ phase: "anonymous" });
          return;
        }
        if (me.user?.role !== "admin") {
          setGate({ phase: "forbidden" });
          return;
        }
        setGate({ phase: "ok" });
      } catch (e) {
        if (!cancelled)
          setGate({
            phase: "error",
            message: isForbidden(e)
              ? "需要管理员权限"
              : apiErrorMessage(e, "无法确认登录状态,请确认后端服务已启动"),
          });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 套餐选项来自 /api/v1/products(数据库真源,不在组件写死)
  useEffect(() => {
    if (gate.phase !== "ok") return;
    clientFetch<ProductsResp>("/api/v1/products")
      .then((r) => setPlans(r.plans))
      .catch(() => {
        // 拿不到时开通/生成表单回退为当前默认值
      });
  }, [gate.phase]);

  if (gate.phase === "loading") {
    return (
      <main className={styles.page}>
        <h1 className={styles.title}>管理后台</h1>
        <Loading />
      </main>
    );
  }

  if (gate.phase === "anonymous") {
    return (
      <main className={styles.page}>
        <h1 className={styles.title}>管理后台</h1>
        <section className={styles.gateCard}>
          <p className={styles.note}>需要登录后访问。</p>
          <Link className={styles.btnPrimary} href="/login?next=/admin">
            前往登录
          </Link>
        </section>
      </main>
    );
  }

  if (gate.phase === "forbidden") {
    return (
      <main className={styles.page}>
        <h1 className={styles.title}>管理后台</h1>
        <section className={styles.gateCard}>
          <p className={styles.gateTitle}>无权限</p>
          <p className={styles.note}>
            当前账号不是管理员。服务端已拒绝访问(403),本页不展示任何后台数据。
          </p>
          <Link className={styles.btnGhost} href="/">
            返回首页
          </Link>
        </section>
      </main>
    );
  }

  if (gate.phase === "error") {
    return (
      <main className={styles.page}>
        <h1 className={styles.title}>管理后台</h1>
        <section className={styles.gateCard}>
          <p className={styles.msgErr}>{gate.message}</p>
        </section>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <h1 className={styles.title}>管理后台</h1>
      <nav className={styles.tabs} aria-label="后台功能">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={tab === t.key ? styles.tabActive : styles.tab}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <section className={styles.tabBody}>
        {tab === "users" && <UsersTab plans={plans} />}
        {tab === "codes" && <CodesTab plans={plans} />}
        {tab === "reco" && <RecoTab />}
        {tab === "predictions" && <PredictionsTab />}
        {tab === "xref" && <XrefTab />}
        {tab === "jobs" && <JobsTab />}
        {tab === "audit" && <AuditTab />}
      </section>
    </main>
  );
}
