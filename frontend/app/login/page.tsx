"use client";

/**
 * /login — 登录页(全部客户端渲染,不含任何会员数据)。
 *
 * 登录路线(2026-08 起):带参数二维码 + webhook 事件。网页授权已废弃(网页授权
 * 域名要求 ICP 备案,本站部署海外不备案),因此三种环境走同一个扫码流,只是
 * 操作提示不同(UA 在浏览器端检测,避免 SSR 水合不一致):
 * - 电脑端:手机微信「扫一扫」;
 * - 微信内:长按二维码 →「识别图中二维码」;
 * - 非微信手机浏览器:截图保存二维码,微信「扫一扫 → 相册」识别。
 * 扫码后微信服务器回调本站 webhook 完成批准,本页轮询领取会话
 * (secret 只留在本页内存,绝不进二维码)。
 *
 * useSearchParams 必须包在 Suspense 里(Next 16 生产构建约束,
 * 见 node_modules/next/dist/docs/01-app/03-api-reference/04-functions/use-search-params.md)。
 *
 * 2026-08-14 重设计(Claude Design 定稿,design_handoff_login_redesign,方向 2a):
 * 标题组 h1 随环境变化;扫码卡是页面核心区域;密码登录从卡片降级为纯文字
 * <details>;整页改 flex+gap 纵向堆叠,不再用 margin 堆叠。SSR 水合前(env===null)
 * 的骨架直接复用 ScanLoginCard 自己的卡片外壳,不再是单独两条骨架横条。
 *
 * 2026-08-23 修复(P1,生产实测普通用户无法注册登录):删除"登录后可免费查看"
 * 三条假卖点(内容早已全站免费,"模型完整概率"功能不存在),与 pricing 页口径
 * 对齐;删除英文 eyebrow 装饰;微信登录未开放态补充公众号关注入口作为过渡。
 */

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  apiErrorMessage,
  clientFetch,
  getMe,
  type AuthMethodsResponse,
  type MeResponse,
  type PasswordLoginResponse,
} from "@/lib/api-v1";
import Image from "next/image";
import { ENV_TITLE, ScanLoginCard, useEnv } from "@/components/auth/ScanLoginCard";
import scanStyles from "@/components/auth/ScanLoginCard.module.css";
import followStyles from "@/components/trust/WechatFollowCard.module.css";
import styles from "./login.module.css";

/** 与后端 service.is_safe_next_path 同规则:仅本站相对路径。 */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/")) return "/";
  if (raw.startsWith("//") || raw.includes("\\") || raw.includes("://")) return "/";
  return raw;
}

/* ── 管理员密码登录(降级为纯文字折叠入口,不再是卡片) ────── */

function PasswordLoginSection({ nextPath }: { nextPath: string }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await clientFetch<PasswordLoginResponse>("/api/v1/auth/password/login", {
        method: "POST",
        body: { username, password },
      });
      window.location.assign(nextPath);
    } catch (e2) {
      setErr(apiErrorMessage(e2, "登录失败,请稍后重试"));
      setBusy(false);
    }
  };

  return (
    <details className={styles.pwDetails}>
      <summary className={styles.pwSummary}>管理员密码登录</summary>
      <form className={styles.pwForm} onSubmit={onSubmit}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>用户名</span>
          <input
            className={styles.input}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>密码</span>
          <input
            className={styles.input}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {err && <p className={styles.errText}>{err}</p>}
        <button type="submit" className={styles.btnPrimary} disabled={busy}>
          {busy ? "登录中…" : "登录"}
        </button>
        <p className={styles.note}>管理员账号专用。</p>
      </form>
    </details>
  );
}

/** SSR/水合前骨架:复用 ScanLoginCard 自己的卡片外壳(idle 态视觉),
 * 避免和真正挂载后的卡片切换时跳版。 */
function ScanCardSkeleton() {
  return (
    <section className={scanStyles.card} data-state="idle" aria-busy="true">
      <header className={scanStyles.band}>
        <span className={scanStyles.bandLeft}>
          <span className={scanStyles.dot} aria-hidden />
          <span className={scanStyles.bandLabel}>正在生成扫码请求…</span>
        </span>
      </header>
      <div className={scanStyles.progressTrack} data-pulse>
        <span className={scanStyles.progressFill} style={{ width: "0%" }} />
      </div>
      <div className={scanStyles.stage}>
        <div className={scanStyles.stageQr}>
          <span className={scanStyles.qrSkeleton} aria-hidden />
        </div>
      </div>
      <span className={scanStyles.footnoteSkeleton} aria-hidden />
    </section>
  );
}

/* ── 主体 ──────────────────────────────────────────────── */

function LoginBody() {
  const searchParams = useSearchParams();
  const nextPath = safeNext(searchParams.get("next"));

  const env = useEnv();
  const [me, setMe] = useState<MeResponse | null>(null);
  // null=未知(按可用渲染),false=后端明确告知微信登录未开放(AUTH_DISABLED 三态)
  const [wechatEnabled, setWechatEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((data) => {
        if (!cancelled) setMe(data);
      })
      .catch(() => {
        // /me 拉不到(后端未启动等)不阻塞登录入口本身
      });
    clientFetch<AuthMethodsResponse>("/api/v1/auth/methods")
      .then((r) => {
        if (!cancelled) setWechatEnabled(r.wechat_enabled);
      })
      .catch(() => {
        // 拉不到 methods 时不阻塞登录入口(按可用渲染,端点自身仍会 503)
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const pageTitle = wechatEnabled === false ? "登录" : env ? ENV_TITLE[env] : "登录";

  return (
    <main className={styles.page}>
      <div className={styles.titleGroup}>
        <h1 className={styles.title}>{pageTitle}</h1>
        <p className={styles.note}>
          第一次扫码会自动建号，不用填手机号。登完自动回到你刚才那页。
        </p>
      </div>

      {me?.authenticated && (
        <section className={styles.loggedInBar}>
          <p className={styles.loggedInText}>
            当前已登录:<b>{me.user?.display_name ?? me.user?.id}</b>
          </p>
          <div className={styles.row}>
            <a className={styles.btnPrimary} href={nextPath}>
              继续前往
            </a>
            <a className={styles.linkGhost} href="/account">
              账户中心 →
            </a>
          </div>
        </section>
      )}

      {wechatEnabled === false ? (
        <section className={styles.card}>
          <h2 className={styles.cardTitle}>扫码登录还在开通中</h2>
          <p className={styles.note}>
            公众号那边的配置还没走完，暂时登不了。这段时间比赛数据、赔率、历史战绩都不用登录，
            照常看。配好之后这页会直接出二维码。
          </p>
          <p className={styles.note}>想在扫码开放时第一时间收到通知，可以先关注公众号。</p>
          <div className={followStyles.qrBox}>
            <Image
              src="/brand/wechat-mp-qr.png"
              alt="欧赢 ALLWIN 公众号二维码"
              width={160}
              height={160}
              className={followStyles.qr}
              unoptimized
            />
            <span className={followStyles.qrHint}>微信扫码关注</span>
          </div>
        </section>
      ) : env === null ? (
        <ScanCardSkeleton />
      ) : (
        <ScanLoginCard nextPath={nextPath} env={env} />
      )}

      <section className={styles.perksBlock}>
        <p className={styles.perksTitle}>
          登录只用于收藏、关注和每日精选授权。比赛数据、赔率和赛果不登录也能看全。
        </p>
        <p className={styles.perksFoot}>比赛数据、赔率、概率都不用登录，直接看。</p>
      </section>

      <PasswordLoginSection nextPath={nextPath} />

      <p className={styles.footNote}>
        现在只能用微信登录，短信和邮箱还没接，也就没有备用的找回方式，微信号别丢。
        登录后我们只存一个内部账号 ID 跟你的微信对应，不读昵称和头像。
      </p>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <main className={styles.page}>
          <div className={styles.titleGroup}>
            <h1 className={styles.title}>登录</h1>
          </div>
          <ScanCardSkeleton />
        </main>
      }
    >
      <LoginBody />
    </Suspense>
  );
}
