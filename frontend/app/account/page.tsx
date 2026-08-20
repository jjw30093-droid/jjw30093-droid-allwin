"use client";

/**
 * /account — 账户中心。全部数据在浏览器端经 clientFetch 拉取
 * (会话 cookie Path=/api/v1,匿名 HTML/RSC payload 不含任何会员数据,宪法 §10.2)。
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, apiErrorMessage, clientFetch, getMe, logout, type GetJson } from "@/lib/api-v1";
import styles from "./account.module.css";

/* ── 类型:从 OpenAPI 生成类型派生(Pydantic 单一真源,宪法 §10.3) ── */

type AccountResponse = GetJson<"/api/v1/account">;
type FavoritesResponse = GetJson<"/api/v1/favorites">;
type ProductsResponse = GetJson<"/api/v1/products">;
type MyAccessResponse = GetJson<"/api/v1/reco/my-access">;

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
  const [recoAccess, setRecoAccess] = useState<MyAccessResponse | null>(null);
  const [recoAccessError, setRecoAccessError] = useState<string | null>(null);
  const [planNames, setPlanNames] = useState<Record<string, string>>({});
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // 二次确认改用站内面板,不用 window.confirm——原生弹窗在部分浏览器/内嵌
  // webview(微信内置浏览器等)里会被静默忽略或自动划掉,点击后请求根本
  // 不会发出且页面没有任何反馈,和"没反应"没有区别(参照 admin 页
  // publishConfirmFor 同一次真实用户报告后的修复)。
  const [revokeConfirmFor, setRevokeConfirmFor] = useState<string | null>(null);
  const [logoutConfirm, setLogoutConfirm] = useState(false);

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
        message: apiErrorMessage(e, "账户数据加载失败,请确认后端服务已启动后重试"),
      });
    }
  }, []);

  useEffect(() => {
    // 经微任务回调触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)
    void Promise.resolve().then(() => load());
  }, [load]);

  // 收藏、每日精选按场授权记录、套餐名称(套餐中文名来自 /api/v1/products,
  // 不在组件写死)
  useEffect(() => {
    if (state.phase !== "ready") return;
    clientFetch<FavoritesResponse>("/api/v1/favorites")
      .then(setFavorites)
      .catch((e) => setFavError(apiErrorMessage(e, "收藏列表加载失败")));
    clientFetch<MyAccessResponse>("/api/v1/reco/my-access")
      .then(setRecoAccess)
      .catch((e) => setRecoAccessError(apiErrorMessage(e, "每日精选授权记录加载失败")));
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
    setRevokeConfirmFor(null);
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
      setActionMsg(apiErrorMessage(e, "撤销失败"));
    } finally {
      setBusyId(null);
    }
  };

  const onLogout = async () => {
    setLogoutConfirm(false);
    setBusyId("logout");
    try {
      await logout();
      window.location.assign("/");
    } catch (e) {
      setActionMsg(apiErrorMessage(e, "退出失败,请重试"));
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
          <p className={styles.note}>
            尚未登录。登录后可使用收藏、每日精选历史战绩查看、精选授权状态查询等
            账户功能;首次微信扫码会自动创建账号。
          </p>
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
  // 每日精选按"用户 + 单条 slip"授权(2026-08-16),不再有任何全局布尔权益——
  // "已授权 N 场"必须数当前 active 的按场授权记录,不能再用 entitlements 或
  // plan_id="daily_picks" 的订阅到期时间代替。
  const activeRecoGrants = recoAccess?.grants.filter((g) => g.status === "active") ?? [];

  return (
    <main className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>账户中心</h1>
        <button
          type="button"
          className={styles.btnGhost}
          onClick={() => setLogoutConfirm((v) => !v)}
          disabled={busyId === "logout"}
        >
          {busyId === "logout" ? "退出中…" : "退出登录"}
        </button>
      </div>

      {logoutConfirm && (
        <div className={styles.confirmPanel}>
          <p>确认退出登录?</p>
          <button type="button" className={styles.btnPrimary} onClick={onLogout}>
            确认退出
          </button>
          <button type="button" className={styles.btnGhost} onClick={() => setLogoutConfirm(false)}>
            取消
          </button>
        </div>
      )}

      {actionMsg && <p className={styles.actionMsg}>{actionMsg}</p>}

      {/* 权限状态:登录的价值一眼可见,技术细节折叠在下方 */}
      <div className={styles.accessGrid}>
        <div className={styles.accessCard}>
          <span className={styles.accessName}>完整足球数据</span>
          <span className={`${styles.accessState} ${styles.accessOn}`}>已开放</span>
          <span className={styles.accessHint}>
            全部联赛资料、模型完整概率与完整赔率时间线
          </span>
        </div>
        <div className={styles.accessCard}>
          <span className={styles.accessName}>历史推荐战绩</span>
          <span className={`${styles.accessState} ${styles.accessOn}`}>已开放</span>
          <span className={styles.accessHint}>
            <Link href="/reco?tab=record">查看每日精选历史战绩 →</Link>
          </span>
        </div>
        <div className={styles.accessCard}>
          <span className={styles.accessName}>每日精选</span>
          {recoAccessError ? (
            <span className={styles.accessHint}>{recoAccessError}</span>
          ) : recoAccess === null ? (
            <span className={styles.accessHint}>加载中…</span>
          ) : activeRecoGrants.length > 0 ? (
            <>
              <span className={`${styles.accessState} ${styles.accessOn}`}>
                已授权 {activeRecoGrants.length} 场
              </span>
              <span className={styles.accessHint}>
                <Link href="/reco?tab=daily">查看已授权场次 →</Link>
              </span>
            </>
          ) : (
            <>
              <span className={`${styles.accessState} ${styles.accessOff}`}>暂无授权场次</span>
              <span className={styles.accessHint}>
                由站长按场为账号开通,<Link href="/pricing">查看权限说明</Link>
              </span>
            </>
          )}
        </div>
      </div>

      {/* 基本信息 */}
      <section className={styles.card}>
        <h2 className={styles.cardTitle}>基本信息</h2>
        <dl className={styles.dl}>
          <div className={styles.dlRow}>
            <dt>昵称</dt>
            <dd>{account.user.display_name ?? "未设置"}</dd>
          </div>
          <div className={styles.dlRow}>
            <dt>当前身份</dt>
            <dd>
              <span className={account.plan === "free" ? styles.planDefault : styles.planHighlight}>
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
        <details className={styles.detailsBox}>
          <summary>账户详情(技术信息)</summary>
          <dl className={styles.dl}>
            <div className={styles.dlRow}>
              <dt>账号 ID</dt>
              <dd className="num">{account.user.id}</dd>
            </div>
            <div className={styles.dlRow}>
              <dt>角色</dt>
              <dd>{account.user.role}</dd>
            </div>
          </dl>
        </details>
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

      {/* 每日精选授权记录(2026-08-16 按"用户 + 单条 slip"授权;含历史撤销,
          "每日精选权限查询"——CLAUDE.md §8.1 允许要求登录的账户类个人功能) */}
      <section className={styles.card}>
        <h2 className={styles.cardTitle}>每日精选授权记录</h2>
        {recoAccessError ? (
          <p className={styles.errText}>{recoAccessError}</p>
        ) : recoAccess === null ? (
          <SectionSkeleton />
        ) : recoAccess.grants.length === 0 ? (
          <p className={styles.empty}>暂无任何场次的每日精选授权记录</p>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>场次</th>
                  <th>状态</th>
                  <th>授权时间</th>
                  <th>撤销时间</th>
                </tr>
              </thead>
              <tbody>
                {recoAccess.grants.map((g) => (
                  <tr key={g.id}>
                    <td>
                      <span className="num">{g.slip_date}</span> {g.slip_title}
                    </td>
                    <td>
                      <span className={g.status === "active" ? styles.stateOk : styles.stateDim}>
                        {g.status === "active" ? "生效中" : "已撤销"}
                      </span>
                    </td>
                    <td className="num">{fmtLocal(g.granted_at)}</td>
                    <td className="num">{fmtLocal(g.revoked_at)}</td>
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
                    onClick={() => setRevokeConfirmFor(revokeConfirmFor === s.id ? null : s.id)}
                  >
                    {busyId === s.id ? "撤销中…" : "撤销"}
                  </button>
                )}
                {revokeConfirmFor === s.id && (
                  <div className={styles.confirmPanel}>
                    <p>确认撤销该会话?对应设备将立即退出登录。</p>
                    <button
                      type="button"
                      className={styles.btnDanger}
                      onClick={() => onRevokeSession(s.id)}
                    >
                      确认撤销
                    </button>
                    <button
                      type="button"
                      className={styles.btnGhost}
                      onClick={() => setRevokeConfirmFor(null)}
                    >
                      取消
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
