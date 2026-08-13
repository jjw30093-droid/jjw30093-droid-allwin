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

function QrCanvas({ url }: { url: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let cancelled = false;
    // 二维码内容 = 微信带参二维码 URL(只关联公开 request id,绝不含浏览器 secret)
    qrToCanvas(canvas, url, {
      width: 200,
      margin: 2,
      errorCorrectionLevel: "M",
    }).catch(() => {
      if (!cancelled) setFailed(true);
    });
    return () => {
      cancelled = true;
    };
  }, [url]);

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
      role="img"
      aria-label="微信扫码登录二维码"
    />
  );
}

const ENV_TITLE: Record<Env, string> = {
  desktop: "微信扫码登录",
  wechat: "微信内登录",
  mobile: "微信扫码登录(手机)",
};

const ENV_HINT: Record<Env, string> = {
  desktop: "用手机微信「扫一扫」,在微信内确认后自动登录",
  wechat: "长按二维码,选择「识别图中二维码」,确认后自动登录",
  mobile: "截图保存二维码,在微信「扫一扫 → 相册」中识别,确认后自动登录",
};

export function ScanLoginCard({
  nextPath,
  env,
  title,
}: {
  nextPath: string;
  env: Env;
  /** 覆盖默认标题(登录页用默认;门禁页可传更贴合场景的文案)。 */
  title?: string;
}) {
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

  const secondsLeft =
    device.phase === "waiting"
      ? Math.max(0, Math.floor((Date.parse(device.expiresAt) - nowTs) / 1000))
      : 0;

  return (
    <section className={styles.card}>
      <h2 className={styles.cardTitle}>{title ?? ENV_TITLE[env]}</h2>
      {device.phase === "creating" || device.phase === "idle" ? (
        <div className={styles.qrBox}>
          <span className={styles.dim}>正在生成扫码请求…</span>
        </div>
      ) : device.phase === "waiting" ? (
        <>
          <div className={styles.qrBox}>
            <QrCanvas url={device.qrUrl} />
            <span className={styles.qrHint}>{ENV_HINT[env]}</span>
          </div>
          <div className={styles.row}>
            <span className={`${styles.countdown} num`}>
              {secondsLeft} 秒后过期
            </span>
          </div>
          <p className={styles.note}>
            扫码后如提示关注公众号,关注即完成登录(仅获取最小身份标识,不读取昵称头像)。
          </p>
          {IS_DEV && (
            <details className={styles.qrFallback}>
              <summary className={styles.qrFallbackSummary}>
                开发环境:模拟扫码
              </summary>
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
