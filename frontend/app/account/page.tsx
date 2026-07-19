"use client";

/**
 * /account — 账户中心。全部数据在浏览器端经 clientFetch 拉取
 * (会话 cookie Path=/api/v1,匿名 HTML/RSC payload 不含任何会员数据,宪法 §10.2)。
 *
 * 响应结构以 backend/api/routes_member.py 的 GET /api/v1/account 为准;
 * OpenAPI 对该端点未声明 schema,故在此按后端真实返回手写最小类型。
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, clientFetch, getMe, logout } from "@/lib/api-v1";
import styles from "./account.module.css";

/* ── 类型(对齐 routes_member.py 真实返回) ─────────────── */

interface Identity {
  provider: string;
  provider_app_id: string;
  created_at: string;
  last_used_at: string | null;
}

interface Subscription {
  id: string;
  plan_id: string;
  status: string;
  starts_at: string;
  ends_at: string;
  source: string;
  created_at: string;
}

interface SessionRow {
  id: string;
  created_at: string;
  last_seen_at: string | null;
  expires_at: string;
  user_agent: string | null;
  is_current: number;
}

interface AccountResponse {
  user: { id: string; display_name: string | null; role: string };
  plan: string;
  entitlements: string[];
  identities: Identity[];
  subscriptions: Subscription[];
  sessions: SessionRow[];
  recovery: { available: boolean; note: string };
}

interface FavoritesResponse {
  favorites: { match_id: number; created_at: string }[];
}

interface ProductsResponse {
  plans: { id: string; name_zh: string; description: string | null; rank: number }[];
}

/* ── 工具 ──────────────────────────────────────────────── */

const PROVIDER_ZH: Record<string, string> = {
  wechat_oa: "微信公众号",
  wechat_open: "微信开放平台",
  password: "密码账号",
  email: "邮箱",
  phone: "手机号",
};

/** 数据库统一 UTC,展示按用户本地时区。 */
function fmtLocal(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

function apiErrMsg(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    const d = e.detail;
    if (typeof d === "string" && d) return d;
    if (d && typeof d === "object" && "message" in d) {
      const m = (d as { message?: unknown }).message;
      if (typeof m === "string" && m) return m;
    }
    return `${fallback}(HTTP ${e.status})`;
  }
  return fallback;
}

function SectionSkeleton() {
  return (
    <div aria-busy="true">
      <div className={styles.skeleton} />
      <div className={styles.skeletonShort} />
    </div>
  );
}

/* ── 页面 ──────────────────────────────────────────────── */

type PageState =
  | { phase: "loading" }
  | { phase: "anonymous" }
  | { phase: "error"; message: string }
  | { phase: "ready"; account: AccountResponse };

export default function AccountPage() {
  const [state, setState] = useState<PageState>({ phase: "loading" });
  const [favorites, setFavorites] = useState<FavoritesResponse | null>(null);
  const [favError, setFavError] = useState<string | null>(null);
  const [planNames, setPlanNames] = useState<Record<string, string>>({});
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState({ phase: "loading" });
    try {
      const me = await getMe();
      if (!me.authenticated) {
        setState({ phase: "anonymous" });
        return;
      }
      const account = await clientFetch<AccountResponse>("/api/v1/account");
      setState({ phase: "ready", account });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setState({ phase: "anonymous" });
        return;
      }
      setState({
        phase: "error",
        message: apiErrMsg(e, "账户数据加载失败,请确认后端服务已启动后重试"),
      });
    }
  }, []);

  useEffect(() => {
    // 经微任务回调触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)
    void Promise.resolve().then(() => load());
  }, [load]);

  // 收藏与套餐名称(套餐中文名来自 /api/v1/products,不在组件写死)
  useEffect(() => {
    if (state.phase !== "ready") return;
    clientFetch<FavoritesResponse>("/api/v1/favorites")
      .then(setFavorites)
      .catch((e) => setFavError(apiErrMsg(e, "收藏列表加载失败")));
    clientFetch<ProductsResponse>("/api/v1/products")
      .then((r) => {
        const m: Record<string, string> = {};
        for (const p of r.plans) m[p.id] = p.name_zh;
        setPlanNames(m);
      })
      .catch(() => {
        // 拿不到套餐名时回退展示 plan id,不阻塞页面
      });
  }, [state.phase]);

  const onRevokeSession = async (sessionId: string) => {
    if (!window.confirm("确认撤销该会话?对应设备将立即退出登录。")) return;
    setBusyId(sessionId);
    setActionMsg(null);
    try {
      await clientFetch("/api/v1/account/sessions/revoke", {
        method: "POST",
        body: { session_id: sessionId },
      });
      setActionMsg("会话已撤销");
      await load();
    } catch (e) {
      setActionMsg(apiErrMsg(e, "撤销失败"));
    } finally {
      setBusyId(null);
    }
  };

  const onLogout = async () => {
    if (!window.confirm("确认退出登录?")) return;
    setBusyId("logout");
    try {
      await logout();
      window.location.assign("/");
    } catch (e) {
      setActionMsg(apiErrMsg(e, "退出失败,请重试"));
      setBusyId(null);
    }
  };

  if (state.phase === "loading") {
    return (
      <main className={styles.page}>
        <h1 className={styles.title}>账户中心</h1>
        <section className={styles.card}>
          <SectionSkeleton />
        </section>
        <section className={styles.card}>
          <SectionSkeleton />
        </section>
      </main>
    );
  }

  if (state.phase === "anonymous") {
    return (
      <main className={styles.page}>
        <h1 className={styles.title}>账户中心</h1>
        <section className={styles.card}>
          <p className={styles.note}>尚未登录。登录后可查看套餐、收藏与登录设备。</p>
          <Link className={styles.btnPrimary} href="/login?next=/account">
            前往登录
          </Link>
        </section>
      </main>
    );
  }

  if (state.phase === "error") {
    return (
      <main className={styles.page}>
        <h1 className={styles.title}>账户中心</h1>
        <section className={styles.card}>
          <p className={styles.errText}>{state.message}</p>
          <button type="button" className={styles.btnGhost} onClick={load}>
            重试
          </button>
        </section>
      </main>
    );
  }

  const { account } = state;
  const nowIso = new Date().toISOString();
  const activeSubs = account.subscriptions.filter(
    (s) => s.status === "active" && s.ends_at > nowIso,
  );
  const planLabel = (id: string) => planNames[id] ?? id;

  return (
    <main className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>账户中心</h1>
        <button
          type="button"
          className={styles.btnGhost}
          onClick={onLogout}
          disabled={busyId === "logout"}
        >
          {busyId === "logout" ? "退出中…" : "退出登录"}
        </button>
      </div>

      {actionMsg && <p className={styles.actionMsg}>{actionMsg}</p>}

      {/* 基本信息与套餐 */}
      <section className={styles.card}>
        <h2 className={styles.cardTitle}>基本信息</h2>
        <dl className={styles.dl}>
          <div className={styles.dlRow}>
            <dt>昵称</dt>
            <dd>{account.user.display_name ?? "未设置"}</dd>
          </div>
          <div className={styles.dlRow}>
            <dt>账号 ID</dt>
            <dd className="num">{account.user.id}</dd>
          </div>
          <div className={styles.dlRow}>
            <dt>角色</dt>
            <dd>{account.user.role}</dd>
          </div>
          <div className={styles.dlRow}>
            <dt>当前套餐</dt>
            <dd>
              <span className={account.plan === "free" ? styles.planFree : styles.planPaid}>
                {planLabel(account.plan)}
              </span>
              {activeSubs.length > 0 && (
                <span className={styles.dim}>
                  {" "}
                  · 有效期至 {fmtLocal(activeSubs[0].ends_at)}
                </span>
              )}
            </dd>
          </div>
        </dl>
        {account.plan === "free" && (
          <p className={styles.note}>
            免费套餐可查看每场最高一项概率。<Link href="/pricing">了解会员套餐</Link>
          </p>
        )}
      </section>

      {/* 订阅记录 */}
      <section className={styles.card}>
        <h2 className={styles.cardTitle}>订阅记录</h2>
        {account.subscriptions.length === 0 ? (
          <p className={styles.empty}>暂无订阅记录</p>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>套餐</th>
                  <th>状态</th>
                  <th>开始</th>
                  <th>到期</th>
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                {account.subscriptions.map((s) => (
                  <tr key={s.id}>
                    <td>{planLabel(s.plan_id)}</td>
                    <td>
                      <span
                        className={
                          s.status === "active" && s.ends_at > nowIso
                            ? styles.stateOk
                            : styles.stateDim
                        }
                      >
                        {s.status === "active"
                          ? s.ends_at > nowIso
                            ? "生效中"
                            : "已到期"
                          : s.status === "revoked"
                            ? "已撤销"
                            : s.status}
                      </span>
                    </td>
                    <td className="num">{fmtLocal(s.starts_at)}</td>
                    <td className="num">{fmtLocal(s.ends_at)}</td>
                    <td className={styles.dim}>
                      {s.source === "admin_grant"
                        ? "管理员开通"
                        : s.source === "redeem_code"
                          ? "兑换码"
                          : s.source}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* 绑定身份 */}
      <section className={styles.card}>
        <h2 className={styles.cardTitle}>绑定身份</h2>
        {account.identities.length === 0 ? (
          <p className={styles.empty}>暂无绑定身份</p>
        ) : (
          <ul className={styles.list}>
            {account.identities.map((it) => (
              <li key={`${it.provider}:${it.provider_app_id}:${it.created_at}`} className={styles.listItem}>
                <span className={styles.listMain}>
                  {PROVIDER_ZH[it.provider] ?? it.provider}
                </span>
                <span className={styles.dim}>
                  绑定于 {fmtLocal(it.created_at)}
                  {it.last_used_at ? ` · 最近使用 ${fmtLocal(it.last_used_at)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className={styles.note}>{account.recovery.note}</p>
      </section>

      {/* 收藏 */}
      <section className={styles.card}>
        <h2 className={styles.cardTitle}>收藏的比赛</h2>
        {favError ? (
          <p className={styles.errText}>{favError}</p>
        ) : favorites === null ? (
          <SectionSkeleton />
        ) : favorites.favorites.length === 0 ? (
          <p className={styles.empty}>暂无收藏。在比赛详情页可收藏关注的比赛。</p>
        ) : (
          <ul className={styles.list}>
            {favorites.favorites.map((f) => (
              <li key={f.match_id} className={styles.listItem}>
                <Link className={styles.listMain} href={`/matches/${f.match_id}`}>
                  比赛 #{f.match_id}
                </Link>
                <span className={styles.dim}>收藏于 {fmtLocal(f.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 活跃会话 */}
      <section className={styles.card}>
        <h2 className={styles.cardTitle}>登录设备(活跃会话)</h2>
        {account.sessions.length === 0 ? (
          <p className={styles.empty}>暂无活跃会话</p>
        ) : (
          <ul className={styles.list}>
            {account.sessions.map((s) => (
              <li key={s.id} className={styles.sessionItem}>
                <div className={styles.sessionMain}>
                  <span className={styles.listMain}>
                    {s.user_agent
                      ? s.user_agent.length > 72
                        ? `${s.user_agent.slice(0, 72)}…`
                        : s.user_agent
                      : "未知设备"}
                    {s.is_current === 1 && (
                      <span className={styles.currentBadge}>当前会话</span>
                    )}
                  </span>
                  <span className={styles.dim}>
                    登录 {fmtLocal(s.created_at)}
                    {s.last_seen_at ? ` · 最近活动 ${fmtLocal(s.last_seen_at)}` : ""}
                    {` · 到期 ${fmtLocal(s.expires_at)}`}
                  </span>
                </div>
                {s.is_current !== 1 && (
                  <button
                    type="button"
                    className={styles.btnDanger}
                    disabled={busyId === s.id}
                    onClick={() => onRevokeSession(s.id)}
                  >
                    {busyId === s.id ? "撤销中…" : "撤销"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
