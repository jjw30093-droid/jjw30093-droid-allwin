"use client";

/**
 * 扫码登录卡片(设备码 + webhook 事件),从 /login 抽取为可复用组件
 * (CLAUDE.md §7.3:电脑端一次性 Device Login;本组件同时服务 /login 与
 * 联赛门禁页 GateCard,行为不变,不允许出现第二份实现)。
 *
 * 三种环境走同一个扫码流,只是操作提示不同(UA 在浏览器端检测,避免 SSR
 * 水合不一致):
 * - 电脑端:手机微信「扫一扫」;
 * - 微信内:长按二维码 →「识别图中二维码」;
 * - 非微信手机浏览器:截图保存二维码,微信「扫一扫 → 相册」识别。
 * 扫码后微信服务器回调本站 webhook 完成批准,本组件轮询领取会话
 * (secret 只留在内存,绝不进二维码)。
 *
 * 2026-08-14 重设计(Claude Design 定稿,design_handoff_login_redesign,
 * 方向 2a):卡片结构固定为「状态带 → 进度条 → 定高扫码台 → 三步操作 →
 * 卡内脚注」五层,六个状态(idle/creating/waiting/waiting-即将过期/
 * claimed/expired/error)只换这五层里的内容,扫码台固定 min-height,
 * 切换状态时卡片不跳版。`title` prop 覆盖的是状态带默认词
 * (idle/creating/等待扫码三种"平静态"),不再是卡片自己的一个 <h2>——
 * claimed/expired/error/即将过期这几个真正需要传达信息的瞬态,始终显示
 * 自己的状态词,不被 title 顶掉。
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { toCanvas as qrToCanvas } from "qrcode";
import {
  ApiError,
  apiErrorMessage,
  claimDeviceLogin,
  createDeviceLogin,
} from "@/lib/api-v1";
import styles from "./ScanLoginCard.module.css";

export type Env = "wechat" | "mobile" | "desktop";

const emptySubscribe = () => () => {};
function detectEnv(): Env {
  const ua = navigator.userAgent;
  if (/MicroMessenger/i.test(ua)) return "wechat";
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return "mobile";
  return "desktop";
}

/** UA 环境检测:客户端一次性快照(服务端渲染为 null,水合后得到真实值)。 */
export function useEnv(): Env | null {
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
/** 即将过期预警阈值(秒)——不是后端字段,纯前端视觉判定。 */
const ENDING_SOON_SECONDS = 30;
/** 进度条百分比的分母。不新增 state 去追踪请求创建时刻,直接用后端默认
 * 有效期常量(DEVICE_REQUEST_TTL_SECONDS 默认 300 秒);纯前端派生计算。 */
const TTL_SECONDS = 300;

export const ENV_TITLE: Record<Env, string> = {
  desktop: "微信扫码登录",
  wechat: "微信内登录",
  mobile: "微信扫码登录(手机)",
};

const ENV_STEPS: Record<Env, [string, string, string]> = {
  desktop: ["打开手机微信「扫一扫」", "对准左侧二维码", "在微信内确认,本页自动登录"],
  wechat: ["长按左侧二维码", "选择「识别图中二维码」", "确认后自动登录,本页自己跳转"],
  mobile: ["截图保存二维码", "在微信「扫一扫 → 相册」中识别", "确认后自动登录,本页自己跳转"],
};

const PRIVACY_FOOTNOTE =
  "扫码后如提示关注公众号,关注即完成登录(仅获取最小身份标识,不读取昵称头像)。";
const MOBILE_FOOTNOTE =
  "手机上无法对着自己屏幕扫码,所以这一档走「截图 + 相册识别」,不是桌面端的「扫一扫」。";

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

function QrCanvas({ url, size, faded }: { url: string; size: number; faded?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let cancelled = false;
    // 二维码内容 = 微信带参二维码 URL(只关联公开 request id,绝不含浏览器 secret)
    qrToCanvas(canvas, url, {
      width: size,
      margin: 2,
      errorCorrectionLevel: "M",
    }).catch(() => {
      if (!cancelled) setFailed(true);
    });
    return () => {
      cancelled = true;
    };
  }, [url, size]);

  if (failed) {
    return (
      <code className={styles.qrUrl} data-qr-url={url}>
        {url}
      </code>
    );
  }
  return (
    <canvas
      ref={canvasRef}
      className={styles.qrCanvas}
      data-qr-url={url}
      data-faded={faded || undefined}
      role="img"
      aria-label="微信扫码登录二维码"
    />
  );
}

function CheckIcon() {
  return (
    <svg className={styles.checkIcon} viewBox="0 0 88 88" aria-hidden>
      <circle cx="44" cy="44" r="44" />
      <path d="M25 45 L38 58 L64 30" fill="none" strokeLinecap="square" />
    </svg>
  );
}

function ErrorIcon() {
  return (
    <svg className={styles.errorIcon} viewBox="0 0 88 88" aria-hidden>
      <circle className={styles.errorRing} cx="44" cy="44" r="42" fill="none" />
      <path className={styles.errorMark} d="M44 24 L44 52" fill="none" />
      <circle className={styles.errorDot} cx="44" cy="62" r="1.8" />
    </svg>
  );
}

export function ScanLoginCard({
  nextPath,
  env,
  title,
}: {
  nextPath: string;
  env: Env;
  /** 覆盖状态带默认词(idle/creating/等待扫码三种"平静态");门禁页用它传
   * "扫码登录后查看本场数据"这类场景化文案。claimed/expired/error/即将
   * 过期这几个瞬态始终显示自己的状态词,不被这个 prop 顶掉。 */
  title?: string;
}) {
  const [device, setDevice] = useState<DeviceState>({ phase: "idle" });
  const [nowTs, setNowTs] = useState(() => Date.now());
  /** 过期态展示"变暗的旧二维码"用,不参与登录逻辑,只是视觉记忆。 */
  const [lastQrUrl, setLastQrUrl] = useState<string | null>(null);

  const createQr = useCallback(async () => {
    setDevice({ phase: "creating" });
    try {
      const r = await createDeviceLogin();
      setLastQrUrl(r.qr_url);
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
        message: apiErrorMessage(e, "无法创建扫码登录请求,请确认后端服务已启动后重试"),
      });
    }
  }, []);

  // 进入卡片即生成一次(创建 request 无副作用;批准仍需用户在微信内扫码确认)。
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

  // 轮询 claim(携带仅存于内存的 secret)
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

  const expiresMs = device.phase === "waiting" ? Date.parse(device.expiresAt) : 0;
  const secondsLeft =
    device.phase === "waiting" ? Math.max(0, Math.floor((expiresMs - nowTs) / 1000)) : 0;
  const pct = device.phase === "waiting" ? Math.max(0, Math.min(100, (secondsLeft / TTL_SECONDS) * 100)) : 0;
  const isEnding = device.phase === "waiting" && secondsLeft > 0 && secondsLeft <= ENDING_SOON_SECONDS;

  // 状态带:圆点样式 + 状态词 + 进度条颜色,六态(含"即将过期"子态)各自一套。
  let bandState: "idle" | "waiting" | "ending" | "claimed" | "expired" | "error";
  let bandLabel: string;
  if (device.phase === "idle" || device.phase === "creating") {
    bandState = "idle";
    bandLabel = title ?? "正在生成扫码请求…";
  } else if (device.phase === "waiting" && isEnding) {
    bandState = "ending";
    bandLabel = "即将过期";
  } else if (device.phase === "waiting") {
    bandState = "waiting";
    bandLabel = title ?? "等待扫码";
  } else if (device.phase === "claimed") {
    bandState = "claimed";
    bandLabel = "已确认,正在跳转";
  } else if (device.phase === "expired") {
    bandState = "expired";
    bandLabel = "已过期";
  } else {
    bandState = "error";
    bandLabel = "请求失败";
  }

  const footnote =
    device.phase === "waiting" && isEnding
      ? "二维码 5 分钟内有效，过期点一下重新生成。"
      : device.phase === "waiting"
        ? env === "mobile"
          ? MOBILE_FOOTNOTE
          : PRIVACY_FOOTNOTE
        : device.phase === "claimed"
          ? "登录成功，正在回到刚才那页。"
          : device.phase === "expired"
            ? "扫码请求有效期 5 分钟，过期后需要重新生成。"
            : device.phase === "error"
              ? "一直失败的话，刷新页面重试一次。"
              : null;

  // 进度条:idle 整条脉冲；claimed/error 走满；expired 归零；waiting/ending 按秒数走。
  const progressWidth =
    bandState === "claimed" || bandState === "error"
      ? 100
      : bandState === "expired"
        ? 0
        : pct;

  const qrSize = isEnding ? 170 : env === "desktop" ? 200 : 220;

  return (
    <section className={styles.card} data-state={bandState}>
      <header className={styles.band}>
        <span className={styles.bandLeft}>
          <span className={styles.dot} aria-hidden />
          <span className={styles.bandLabel}>{bandLabel}</span>
        </span>
        {device.phase === "waiting" && (
          <span className={styles.bandRight}>
            <span className={`${styles.countdownNum} num`}>{secondsLeft}</span>
            <span className={styles.countdownUnit}>秒后过期</span>
          </span>
        )}
        {device.phase === "expired" && (
          <span className={`${styles.countdownNum} ${styles.countdownNumSmall} num`}>0</span>
        )}
      </header>

      <div className={styles.progressTrack} data-pulse={bandState === "idle" || undefined}>
        <span className={styles.progressFill} style={{ width: `${progressWidth}%` }} />
      </div>

      <div className={styles.stage}>
        {env === "desktop" && <span className={styles.envPill}>{ENV_TITLE[env]}</span>}
        <div className={styles.stageQr}>
          {(device.phase === "idle" || device.phase === "creating") && (
            <span className={styles.qrSkeleton} aria-hidden />
          )}

          {device.phase === "waiting" && <QrCanvas url={device.qrUrl} size={qrSize} />}

          {device.phase === "claimed" && (
            <div className={styles.claimedBox}>
              <CheckIcon />
              <p className={styles.claimedText}>登录成功,正在跳转…</p>
            </div>
          )}

          {device.phase === "expired" && (
            <div className={styles.expiredBox}>
              {lastQrUrl && <QrCanvas url={lastQrUrl} size={200} faded />}
              <div className={styles.expiredOverlay}>
                <p className={styles.expiredText}>二维码已过期</p>
                <button type="button" className={styles.btnPrimary} onClick={createQr}>
                  重新生成二维码
                </button>
              </div>
            </div>
          )}

          {device.phase === "error" && (
            <div className={styles.errorBox}>
              <ErrorIcon />
              <p className={styles.errorText}>{device.message}</p>
              <button type="button" className={styles.btnPrimary} onClick={createQr}>
                重试
              </button>
            </div>
          )}
        </div>

        {device.phase === "waiting" && !isEnding && (
          <ol className={styles.steps}>
            {ENV_STEPS[env].map((step, i) => (
              <li key={i}>
                <span className={styles.stepNum}>{i + 1}</span>
                <span className={styles.stepText}>{step}</span>
              </li>
            ))}
          </ol>
        )}

        {device.phase === "waiting" && isEnding && (
          <p className={styles.endingNote}>
            这张码还有 {secondsLeft} 秒有效。过期后点「重新生成二维码」即可,不需要刷新页面。
          </p>
        )}
      </div>

      {footnote ? (
        <p className={styles.footnote}>{footnote}</p>
      ) : (
        <span className={styles.footnoteSkeleton} aria-hidden />
      )}

      {IS_DEV && device.phase === "waiting" && (
        <details className={styles.qrFallback}>
          <summary className={styles.qrFallbackSummary}>开发环境:模拟扫码</summary>
          <code className={styles.qrUrl} data-testid="qr-request-id">
            {device.requestId}
          </code>
          <p className={styles.devNote}>
            后端为 Mock Provider,二维码不可真实扫描。终端运行
            python -m backend.cli.simulate_wechat_scan --request-id 上方ID
            模拟微信服务器回调。
          </p>
          <div className={styles.row}>
            <CopyButton text={device.requestId} label="复制 request id" />
          </div>
        </details>
      )}
    </section>
  );
}
