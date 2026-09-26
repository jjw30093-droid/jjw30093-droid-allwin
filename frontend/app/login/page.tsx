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
 * 2026-09-26 手机端体验修复:去掉标题旁大 logo(顶栏已有品牌区);副标题改成
 * 一句话;"扫码登录还在开通中"整张卡片与页尾"短信邮箱还没接"整段删除——扫码入口
 * 只在后端明确告知微信登录已开放(/auth/methods 的 wechat_enabled === true,即
 * WECHAT_AUTH_ENABLED 配置开关)时才渲染,默认关闭、拉不到也按关闭处理;登录页
 * 不再有页面中部的二维码(公众号二维码只在页脚,见 SiteFooter)。
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
import { ENV_TITLE, ScanLoginCard, useEnv } from "@/components/auth/ScanLoginCard";
import scanStyles from "@/components/auth/ScanLoginCard.module.css";
import styles from "./login.module.css";

/** 与后端 service.is_safe_next_path 同规则:仅本站相对路径。 */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/")) return "/";
  if (raw.startsWith("//") || raw.includes("\\") || raw.includes("://")) return "/";
  return raw;
}

/* ── 账号密码登录 ──────────────────────────────────────────
 *
 * 2026-09-16 真实用户反馈:站长手工开了账号,用户却"不知道从哪里登录"。
 * 根因是这块此前被折叠在页面最底部、summary 写「管理员密码登录」、脚注
 * 写「管理员账号专用」——普通用户即使翻到也会以为不是给自己用的,而
 * `POST /auth/password/login` 其实不受 WECHAT_AUTH_ENABLED 影响,一直能用。
 *
 * 所以按微信扫码开没开放分两种形态:
 * - 没开放(`standalone`):这是当前**唯一**能用的登录方式,渲染成常驻主卡片
 *   并排在"扫码还在开通中"那张卡上面,不折叠。
 * - 开放了:扫码是主路径,这里降级成折叠的次要入口。
 */

function PasswordLoginSection({
  nextPath,
  standalone,
}: {
  nextPath: string;
  standalone: boolean;
}) {
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

  const form = (
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
      <p className={styles.forgotHint}>忘记密码？通过公众号联系我们</p>
    </form>
  );

  if (standalone) {
    return (
      <section className={styles.card}>
        <h2 className={styles.cardTitle}>账号密码登录</h2>
        {form}
      </section>
    );
  }

  return (
    <details className={styles.pwDetails}>
      <summary className={styles.pwSummary}>账号密码登录</summary>
      {form}
      <p className={styles.note}>账号由我们开通,没有的话走上面的扫码登录。</p>
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
  // 配置开关(后端 WECHAT_AUTH_ENABLED):只有明确 true 才显示扫码入口;
  // null=还没拉到、false=未开放,两者都不显示(默认关闭,拉不到也不冒出入口)
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
        // 拉不到 methods:按"扫码未开放"处理,账号密码登录照常可用
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const scanOn = wechatEnabled === true;
  const pageTitle = scanOn && env ? ENV_TITLE[env] : "登录";

  return (
    <main className={styles.page}>
      <div className={styles.titleGroup}>
        <div className={styles.titleCopy}>
          <h1 className={styles.title}>{pageTitle}</h1>
          <p className={styles.note}>登录后可以收藏比赛、查看每日精选。比赛数据不用登录也能看。</p>
        </div>
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

      {/* 扫码没开放时,密码登录是唯一能用的方式,渲染成常驻主卡片 */}
      {!scanOn && <PasswordLoginSection nextPath={nextPath} standalone />}

      {scanOn && (env === null ? <ScanCardSkeleton /> : <ScanLoginCard nextPath={nextPath} env={env} />)}

      {scanOn && <PasswordLoginSection nextPath={nextPath} standalone={false} />}
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <main className={styles.page}>
          <div className={styles.titleGroup}>
            <div className={styles.titleCopy}>
              <h1 className={styles.title}>登录</h1>
            </div>
          </div>
        </main>
      }
    >
      <LoginBody />
    </Suspense>
  );
}
