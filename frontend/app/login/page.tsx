"use client";

/**
 * /login — 登录页(全部客户端渲染,不含任何会员数据)。
 *
 * 三种环境三种入口(UA 在浏览器端检测,避免 SSR 水合不一致):
 * - 微信内:用户点击后跳转公众号 OAuth(不自动跳转,后端 routes_auth.py 也要求如此);
 * - 电脑端:Device Login 扫码流(POST device → 轮询 claim,secret 只留在本页内存);
 * - 非微信手机浏览器:引导到微信中打开。
 *
 * useSearchParams 必须包在 Suspense 里(Next 16 生产构建约束,
 * 见 node_modules/next/dist/docs/01-app/03-api-reference/04-functions/use-search-params.md)。
 */

import {
  Suspense,
  useCallback,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";
import { useSearchParams } from "next/navigation";
import {
  ApiError,
  claimDeviceLogin,
  clientFetch,
  createDeviceLogin,
  getMe,
  wechatLoginUrl,
  type MeResponse,
} from "@/lib/api-v1";
import styles from "./login.module.css";

/** 与后端 service.is_safe_next_path 同规则:仅本站相对路径。 */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/")) return "/";
  if (raw.startsWith("//") || raw.includes("\\") || raw.includes("://")) return "/";
  return raw;
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

type Env = "wechat" | "mobile" | "desktop";

/** UA 环境检测:客户端一次性快照(服务端渲染为 null,水合后得到真实值)。 */
const emptySubscribe = () => () => {};
function detectEnv(): Env {
  const ua = navigator.userAgent;
  if (/MicroMessenger/i.test(ua)) return "wechat";
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return "mobile";
  return "desktop";
}
function useEnv(): Env | null {
  return useSyncExternalStore(emptySubscribe, detectEnv, () => null);
}

type DeviceState =
  | { phase: "idle" }
  | { phase: "creating" }
  | {
      phase: "waiting";
      requestId: string;
      secret: string;
      qrUrl: string;
      expiresAt: string;
    }
  | { phase: "claimed" }
  | { phase: "expired" }
  | { phase: "error"; message: string };

const IS_DEV = process.env.NODE_ENV === "development";

/* ── 复制按钮(clipboard 不可用时降级为提示手动复制) ───── */

function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("ok");
    } catch {
      setState("fail");
    }
    setTimeout(() => setState("idle"), 2000);
  };
  return (
    <button type="button" className={styles.btnGhost} onClick={onCopy}>
      {state === "ok" ? "已复制" : state === "fail" ? "复制失败,请手动复制" : label}
    </button>
  );
}

/* ── 电脑端:Device Login 扫码卡片 ─────────────────────── */

function DeviceLoginCard({ nextPath }: { nextPath: string }) {
  const [device, setDevice] = useState<DeviceState>({ phase: "idle" });
  const [nowTs, setNowTs] = useState(() => Date.now());

  const createQr = useCallback(async () => {
    setDevice({ phase: "creating" });
    try {
      const r = await createDeviceLogin();
      setDevice({
        phase: "waiting",
        requestId: r.request_id,
        secret: r.secret,
        qrUrl: r.qr_url,
        expiresAt: r.expires_at,
      });
    } catch (e) {
      setDevice({
        phase: "error",
        message: apiErrMsg(e, "无法创建扫码登录请求,请确认后端服务已启动后重试"),
      });
    }
  }, []);

  // 进入卡片即生成一次(创建 request 无副作用;跳转 OAuth 仍需用户在手机上确认)。
  // 经微任务回调触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)。
  useEffect(() => {
    if (device.phase !== "idle") return;
    void Promise.resolve().then(() => createQr());
  }, [device.phase, createQr]);

  // 每秒刷新倒计时;归零 → 过期态
  useEffect(() => {
    if (device.phase !== "waiting") return;
    const expiresMs = Date.parse(device.expiresAt);
    const t = setInterval(() => {
      setNowTs(Date.now());
      if (expiresMs - Date.now() <= 0) setDevice({ phase: "expired" });
    }, 1000);
    return () => clearInterval(t);
  }, [device]);

  // 轮询 claim(携带仅存于本页内存的 secret)
  useEffect(() => {
    if (device.phase !== "waiting") return;
    let stopped = false;
    const tick = async () => {
      try {
        const r = await claimDeviceLogin(device.requestId, device.secret);
        if (stopped) return;
        if (r.status === "claimed") {
          setDevice({ phase: "claimed" });
          window.location.assign(nextPath);
        }
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError) {
          if (e.status === 410) setDevice({ phase: "expired" });
          else if (e.status === 403)
            setDevice({ phase: "error", message: "扫码请求校验失败,请重新生成二维码" });
          // 429 / 网络抖动:跳过本轮,下一轮继续
        }
      }
    };
    const t = setInterval(tick, 2500);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [device, nextPath]);

  const secondsLeft =
    device.phase === "waiting"
      ? Math.max(0, Math.floor((Date.parse(device.expiresAt) - nowTs) / 1000))
      : 0;

  return (
    <section className={styles.card}>
      <h2 className={styles.cardTitle}>微信扫码登录(电脑端)</h2>
      {device.phase === "creating" || device.phase === "idle" ? (
        <div className={styles.qrBox}>
          <span className={styles.dim}>正在生成扫码请求…</span>
        </div>
      ) : device.phase === "waiting" ? (
        <>
          <div className={styles.qrBox}>
            <span className={styles.qrHint}>扫码授权链接</span>
            <code className={styles.qrUrl}>{device.qrUrl}</code>
          </div>
          <p className={styles.note}>
            本页暂未内置二维码图片生成:请在手机微信中打开上面的链接,完成公众号授权后,
            本页会自动完成登录。
          </p>
          <div className={styles.row}>
            <CopyButton text={device.qrUrl} label="复制授权链接" />
            <span className={`${styles.countdown} num`}>
              {secondsLeft} 秒后过期
            </span>
          </div>
          {IS_DEV && (
            <p className={styles.devNote}>
              开发环境:后端默认使用 Mock 微信 Provider,可直接在新标签页打开授权链接模拟扫码。
            </p>
          )}
        </>
      ) : device.phase === "claimed" ? (
        <div className={styles.qrBox}>
          <span>登录成功,正在跳转…</span>
        </div>
      ) : device.phase === "expired" ? (
        <div className={styles.qrBox}>
          <span className={styles.dim}>扫码请求已过期</span>
          <button type="button" className={styles.btnPrimary} onClick={createQr}>
            重新生成
          </button>
        </div>
      ) : (
        <div className={styles.qrBox}>
          <span className={styles.errText}>{device.message}</span>
          <button type="button" className={styles.btnPrimary} onClick={createQr}>
            重试
          </button>
        </div>
      )}
    </section>
  );
}

/* ── 管理员密码登录(折叠) ─────────────────────────────── */

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
      await clientFetch<{ status: string }>("/api/v1/auth/password/login", {
        method: "POST",
        body: { username, password },
      });
      window.location.assign(nextPath);
    } catch (e2) {
      setErr(apiErrMsg(e2, "登录失败,请稍后重试"));
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
        <p className={styles.note}>仅供站长通过 CLI 创建的管理员账号使用。</p>
      </form>
    </details>
  );
}

/* ── 主体 ──────────────────────────────────────────────── */

function LoginBody() {
  const searchParams = useSearchParams();
  const nextPath = safeNext(searchParams.get("next"));

  const env = useEnv();
  const [me, setMe] = useState<MeResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((data) => {
        if (!cancelled) setMe(data);
      })
      .catch(() => {
        // /me 拉不到(后端未启动等)不阻塞登录入口本身
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className={styles.page}>
      <h1 className={styles.title}>登录</h1>

      {me?.authenticated && (
        <section className={styles.card}>
          <p className={styles.note}>
            当前已登录:{me.user?.display_name ?? me.user?.id}
          </p>
          <div className={styles.row}>
            <a className={styles.btnPrimary} href={nextPath}>
              继续前往
            </a>
            <a className={styles.btnGhost} href="/account">
              账户中心
            </a>
          </div>
        </section>
      )}

      {env === null ? (
        <section className={styles.card} aria-busy="true">
          <div className={styles.skeleton} />
          <div className={styles.skeletonShort} />
        </section>
      ) : env === "wechat" ? (
        <section className={styles.card}>
          <h2 className={styles.cardTitle}>微信登录</h2>
          <p className={styles.note}>
            点击下方按钮,通过微信公众号授权完成登录(仅获取最小身份标识,不读取昵称头像)。
          </p>
          <a className={styles.btnPrimary} href={wechatLoginUrl(nextPath)}>
            微信授权登录
          </a>
          {IS_DEV && (
            <p className={styles.devNote}>
              开发环境:后端默认使用 Mock 微信 Provider,点击即可用模拟身份完成登录。
            </p>
          )}
        </section>
      ) : env === "desktop" ? (
        <>
          <DeviceLoginCard nextPath={nextPath} />
          {IS_DEV && (
            <section className={styles.card}>
              <p className={styles.devNote}>
                开发环境亦可用下方按钮直接走 Mock 微信 OAuth(与微信内登录同一入口):
              </p>
              <a className={styles.btnGhost} href={wechatLoginUrl(nextPath)}>
                Mock 微信登录(仅开发环境)
              </a>
            </section>
          )}
        </>
      ) : (
        <section className={styles.card}>
          <h2 className={styles.cardTitle}>请在微信中打开</h2>
          <p className={styles.note}>
            当前浏览器无法完成微信授权。请复制本页链接,在微信中打开后点击登录。
          </p>
          <CopyButton
            text={typeof window !== "undefined" ? window.location.href : "/login"}
            label="复制本页链接"
          />
          {IS_DEV && (
            <p className={styles.devNote}>
              开发环境:也可直接点击
              <a href={wechatLoginUrl(nextPath)}> Mock 微信登录</a>。
            </p>
          )}
        </section>
      )}

      <PasswordLoginSection nextPath={nextPath} />

      <p className={styles.footNote}>
        说明:当前版本仅支持微信登录,尚未接入短信/邮箱验证,暂不支持绑定备用恢复方式;
        请妥善保管微信账号。登录即表示同意本站以内部账号 ID 关联你的微信身份标识。
      </p>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <main className={styles.page}>
          <h1 className={styles.title}>登录</h1>
          <section className={styles.card} aria-busy="true">
            <div className={styles.skeleton} />
            <div className={styles.skeletonShort} />
          </section>
        </main>
      }
    >
      <LoginBody />
    </Suspense>
  );
}
