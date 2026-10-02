"use client";

/**
 * 公众号发码登录卡片(2026-10 起取代带参数二维码扫码登录,经站长批准;CLAUDE.md §7.3)。
 *
 * 为什么换路线:带参数二维码接口只对「已认证服务号」开放,站长的公众号是未认证的个人
 * 公众号。发码登录只需要「消息推送」能力:
 * 1. 本卡片创建登录请求,拿到 4 位验证码(等待中的请求之间互不重复)和只留在内存的 secret;
 * 2. 用户关注公众号「足球喵喵第」,把验证码发给公众号;
 * 3. 微信服务器把这条消息推到本站 webhook,后端按发送者 openid 找到/创建账号并批准请求;
 * 4. 本卡片轮询领取会话(必须带 secret,验证码本身领不走会话)。
 *
 * 第一次登录的新账号(nickname_set=false)在跳转前多一步「给自己起个昵称」,可跳过
 * (未认证公众号拿不到微信昵称;后台靠用户编号认人,昵称只是方便站长和用户自己辨认)。
 *
 * 卡片结构沿用 2026-08-14 定稿的五层:状态带 → 进度条 → 定高操作台 → 三步操作 → 卡内脚注,
 * 各状态只换层内内容,切换时不跳版。`title` 只覆盖平静态的状态带文字。
 */

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import {
  ApiError,
  apiErrorMessage,
  claimDeviceLogin,
  createDeviceLogin,
  getMe,
  updateProfile,
} from "@/lib/api-v1";
import { WECHAT_MP_NAME, WECHAT_MP_QR_SRC } from "@/lib/wechat-mp";
import { copyText, saveQr } from "@/lib/wechat-mp-actions";
import styles from "./CodeLoginCard.module.css";

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

/** 公众号名称与二维码:与页脚关注入口同一个号,单一真源在 lib/wechat-mp.ts。 */
const OA_NAME = WECHAT_MP_NAME;

type CardState =
  | { phase: "idle" }
  | { phase: "creating" }
  | { phase: "waiting"; requestId: string; secret: string; code: string; expiresAt: string }
  | { phase: "claimed" }
  | { phase: "nickname" }
  | { phase: "expired" }
  | { phase: "error"; message: string };

const IS_DEV = process.env.NODE_ENV === "development";
/** 即将过期预警阈值(秒),纯前端视觉判定。 */
const ENDING_SOON_SECONDS = 30;
/** 进度条分母 = 后端默认有效期 DEVICE_REQUEST_TTL_SECONDS(300 秒)。 */
const TTL_SECONDS = 300;
const POLL_MS = 2500;

export const ENV_TITLE: Record<Env, string> = {
  desktop: "微信验证码登录",
  wechat: "微信验证码登录",
  mobile: "微信验证码登录",
};

/**
 * 第一步怎么关注,按环境给(三种环境都显示同一张公众号二维码,2026-10-01 站长要求手机也要有码):
 * - 电脑端:手机微信扫第 1 步下面的码;
 * - 微信内:长按同一张码识别;
 * - 其它手机浏览器:没法扫自己屏幕,给"保存二维码 → 微信扫一扫从相册选"与"复制名称去搜索"两条路。
 */
function stepsFor(env: Env, code: string): [string, string, string] {
  const follow =
    env === "desktop"
      ? `用手机微信扫下面的二维码,关注公众号「${OA_NAME}」`
      : env === "wechat"
        ? `长按下面的二维码识别,关注公众号「${OA_NAME}」`
        : `关注公众号「${OA_NAME}」:保存下面的二维码,在微信「扫一扫」右上角的相册里选它;或复制名称到微信搜索`;
  return [follow, `在公众号对话框里发送验证码 ${code}`, "回到本页,发送后自动登录"];
}

/** 手机浏览器(非微信):保存二维码 + 复制公众号名称。与页脚关注面板共用同一套动作。 */
function MobileQrActions() {
  const [msg, setMsg] = useState<string | null>(null);
  const say = (m: string) => {
    setMsg(m);
    setTimeout(() => setMsg(null), 2500);
  };
  return (
    <span className={styles.qrActions}>
      <span className={styles.row}>
        <button
          type="button"
          className={styles.btnGhost}
          onClick={async () => {
            try {
              const r = await saveQr();
              if (r === "shared") say("请在分享面板选择「存储图像」");
              else if (r === "downloaded") say("已开始下载,也可以直接截图保存");
            } catch {
              say("保存失败,请直接截图保存二维码");
            }
          }}
        >
          保存二维码
        </button>
        <button
          type="button"
          className={styles.btnGhost}
          onClick={async () => say((await copyText(OA_NAME)) ? "已复制,去微信搜索关注" : "复制失败,请手动输入名称")}
        >
          复制公众号名称
        </button>
      </span>
      {msg && (
        <span className={styles.qrActionMsg} role="status">
          {msg}
        </span>
      )}
    </span>
  );
}

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
      {state === "ok" ? "已复制" : state === "fail" ? "复制失败,请手动输入" : label}
    </button>
  );
}

function CodeDigits({ code, faded }: { code: string; faded?: boolean }) {
  return (
    <div
      className={styles.codeDigits}
      data-faded={faded || undefined}
      data-testid={faded ? undefined : "login-code"}
      aria-label={`验证码 ${code.split("").join(" ")}`}
      role="img"
    >
      {code.split("").map((d, i) => (
        <span key={i} className={`${styles.codeDigit} num`} aria-hidden>
          {d}
        </span>
      ))}
    </div>
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

/** 首次登录:给自己起个昵称(2–16 个字,可跳过;跳过保留默认的「球迷 + 编号」)。 */
function NicknameStep({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const trimmed = name.trim();
  const tooShort = [...trimmed].length < 2;

  const save = async (value: string | null) => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await updateProfile(value);
      onDone();
    } catch (e) {
      if (value === null) {
        onDone(); // 跳过失败不拦人:下次登录会再问一次
        return;
      }
      setErr(apiErrorMessage(e, "保存失败,请稍后再试"));
      setBusy(false);
    }
  };

  return (
    <form
      className={styles.nickForm}
      onSubmit={(e) => {
        e.preventDefault();
        if (!tooShort) void save(trimmed);
      }}
    >
      <p className={styles.nickTitle}>登录成功!给自己起个昵称吧</p>
      <label className={styles.nickField}>
        <span className={styles.nickLabel}>昵称(2–16 个字,之后可在账户中心修改)</span>
        <input
          className={styles.nickInput}
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={16}
          autoFocus
          data-testid="nickname-input"
        />
      </label>
      {err && <p className={styles.errorText}>{err}</p>}
      <div className={styles.row}>
        <button type="submit" className={styles.btnPrimary} disabled={busy || tooShort} data-testid="nickname-save">
          {busy ? "保存中…" : "保存"}
        </button>
        <button type="button" className={styles.btnGhost} disabled={busy} onClick={() => void save(null)} data-testid="nickname-skip">
          跳过
        </button>
      </div>
    </form>
  );
}

export function CodeLoginCard({
  nextPath,
  env,
  title,
}: {
  nextPath: string;
  env: Env;
  /** 覆盖平静态(生成中/等待发送)的状态带文字;其余状态始终显示自己的状态词。 */
  title?: string;
}) {
  const [card, setCard] = useState<CardState>({ phase: "idle" });
  const [nowTs, setNowTs] = useState(() => Date.now());
  /** 过期态展示"变暗的旧验证码"用,只是视觉记忆。 */
  const [lastCode, setLastCode] = useState<string | null>(null);

  const createCode = useCallback(async () => {
    setCard({ phase: "creating" });
    try {
      const r = await createDeviceLogin();
      setLastCode(r.login_code);
      setNowTs(Date.now());
      setCard({
        phase: "waiting",
        requestId: r.request_id,
        secret: r.secret,
        code: r.login_code,
        expiresAt: r.expires_at,
      });
    } catch (e) {
      setCard({
        phase: "error",
        message: apiErrorMessage(e, "无法获取验证码,请稍后重试"),
      });
    }
  }, []);

  // 进入卡片即生成一次。经微任务触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)。
  useEffect(() => {
    if (card.phase !== "idle") return;
    void Promise.resolve().then(() => createCode());
  }, [card.phase, createCode]);

  // 每秒刷新倒计时;归零 → 过期态
  useEffect(() => {
    if (card.phase !== "waiting") return;
    const expiresMs = Date.parse(card.expiresAt);
    const t = setInterval(() => {
      setNowTs(Date.now());
      if (expiresMs - Date.now() <= 0) setCard({ phase: "expired" });
    }, 1000);
    return () => clearInterval(t);
  }, [card]);

  // 轮询领取(携带仅存于内存的 secret)。领取成功后看是否需要起昵称。
  useEffect(() => {
    if (card.phase !== "waiting") return;
    let stopped = false;
    const tick = async () => {
      try {
        const r = await claimDeviceLogin(card.requestId, card.secret);
        if (stopped || r.status !== "claimed") return;
        stopped = true;
        setCard({ phase: "claimed" });
        let needNickname = false;
        try {
          const me = await getMe();
          needNickname = me.authenticated === true && me.user?.nickname_set === false;
        } catch {
          // /me 拉不到不影响登录本身,直接跳转
        }
        if (needNickname) setCard({ phase: "nickname" });
        else window.location.assign(nextPath);
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError) {
          if (e.status === 410) setCard({ phase: "expired" });
          else if (e.status === 403 && e.code === "account_disabled")
            setCard({ phase: "error", message: "该账号已被停用" });
          else if (e.status === 403)
            setCard({ phase: "error", message: "登录请求校验失败,请重新获取验证码" });
          // 429 / 网络抖动:跳过本轮,下一轮继续
        }
      }
    };
    const t = setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [card, nextPath]);

  const expiresMs = card.phase === "waiting" ? Date.parse(card.expiresAt) : 0;
  const secondsLeft = card.phase === "waiting" ? Math.max(0, Math.floor((expiresMs - nowTs) / 1000)) : 0;
  const pct = card.phase === "waiting" ? Math.max(0, Math.min(100, (secondsLeft / TTL_SECONDS) * 100)) : 0;
  const isEnding = card.phase === "waiting" && secondsLeft > 0 && secondsLeft <= ENDING_SOON_SECONDS;

  let bandState: "idle" | "waiting" | "ending" | "claimed" | "expired" | "error";
  let bandLabel: string;
  if (card.phase === "idle" || card.phase === "creating") {
    bandState = "idle";
    bandLabel = title ?? "正在获取验证码…";
  } else if (card.phase === "waiting" && isEnding) {
    bandState = "ending";
    bandLabel = "验证码即将过期";
  } else if (card.phase === "waiting") {
    bandState = "waiting";
    bandLabel = title ?? "等待你把验证码发给公众号";
  } else if (card.phase === "claimed") {
    bandState = "claimed";
    bandLabel = "登录成功,正在跳转";
  } else if (card.phase === "nickname") {
    bandState = "claimed";
    bandLabel = "登录成功";
  } else if (card.phase === "expired") {
    bandState = "expired";
    bandLabel = "验证码已过期";
  } else {
    bandState = "error";
    bandLabel = "获取失败";
  }

  const footnote =
    card.phase === "waiting"
      ? isEnding
        ? "验证码 5 分钟内有效,过期点一下重新获取,不用刷新页面。"
        : "只发送你自己在本页看到的验证码;别人发给你的验证码不要发,否则对方会登录你的账号。已关注的直接发送即可。"
      : card.phase === "claimed"
        ? "登录成功,正在回到刚才那页。"
        : card.phase === "nickname"
          ? "昵称只是方便辨认,不是登录凭证。"
          : card.phase === "expired"
            ? "验证码有效期 5 分钟,过期后需要重新获取。"
            : card.phase === "error"
              ? "一直失败的话,刷新页面重试一次。"
              : null;

  const progressWidth =
    bandState === "claimed" || bandState === "error" ? 100 : bandState === "expired" ? 0 : pct;

  const showQr = card.phase === "waiting";

  return (
    <section className={styles.card} data-state={bandState} data-testid="code-login-card">
      <header className={styles.band}>
        <span className={styles.bandLeft}>
          <span className={styles.dot} aria-hidden />
          <span className={styles.bandLabel}>{bandLabel}</span>
        </span>
        {card.phase === "waiting" && (
          <span className={styles.bandRight}>
            <span className={`${styles.countdownNum} num`}>{secondsLeft}</span>
            <span className={styles.countdownUnit}>秒后过期</span>
          </span>
        )}
      </header>

      <div className={styles.progressTrack} data-pulse={bandState === "idle" || undefined}>
        <span className={styles.progressFill} style={{ width: `${progressWidth}%` }} />
      </div>

      {card.phase === "nickname" ? (
        <div className={styles.stageSingle}>
          <NicknameStep onDone={() => window.location.assign(nextPath)} />
        </div>
      ) : (
        <div className={styles.stage}>
          <div className={styles.stageCode}>
            {(card.phase === "idle" || card.phase === "creating") && (
              <span className={styles.codeSkeleton} aria-hidden />
            )}

            {card.phase === "waiting" && (
              <div className={styles.codeBox}>
                <span className={styles.codeLabel}>你的验证码</span>
                <CodeDigits code={card.code} />
                <CopyButton text={card.code} label="复制验证码" />
              </div>
            )}

            {card.phase === "claimed" && (
              <div className={styles.claimedBox}>
                <CheckIcon />
                <p className={styles.claimedText}>登录成功,正在跳转…</p>
              </div>
            )}

            {card.phase === "expired" && (
              <div className={styles.codeBox}>
                {lastCode && <CodeDigits code={lastCode} faded />}
                <p className={styles.expiredText}>验证码已过期</p>
                <button type="button" className={styles.btnPrimary} onClick={createCode}>
                  重新获取验证码
                </button>
              </div>
            )}

            {card.phase === "error" && (
              <div className={styles.errorBox}>
                <ErrorIcon />
                <p className={styles.errorText}>{card.message}</p>
                <button type="button" className={styles.btnPrimary} onClick={createCode}>
                  重试
                </button>
              </div>
            )}
          </div>

          {card.phase === "waiting" && (
            <div className={styles.stageSteps}>
              <ol className={styles.steps}>
                {stepsFor(env, card.code).map((step, i) => (
                  <li key={i}>
                    <span className={styles.stepNum}>{i + 1}</span>
                    <span className={styles.stepText}>
                      {step}
                      {/* 二维码放在第 1 步里、文字下方:放在步骤旁边会把步骤挤成窄条 */}
                      {i === 0 && showQr && (
                        // eslint-disable-next-line @next/next/no-img-element -- 自托管小图,无需 next/image 优化
                        <img
                          className={styles.oaQr}
                          data-env={env}
                          src={WECHAT_MP_QR_SRC}
                          alt={`${OA_NAME} 公众号二维码`}
                          width={112}
                          height={112}
                        />
                      )}
                      {i === 0 && showQr && env === "mobile" && <MobileQrActions />}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      {footnote ? <p className={styles.footnote}>{footnote}</p> : <span className={styles.footnoteSkeleton} aria-hidden />}

      {IS_DEV && card.phase === "waiting" && (
        <details className={styles.devPanel}>
          <summary className={styles.devSummary}>开发环境:模拟发送验证码</summary>
          <p className={styles.devNote}>
            本地没有真实的微信服务器。终端运行下面的命令,模拟「用户把验证码发给公众号」的消息推送:
          </p>
          <code className={styles.devCmd} data-testid="dev-simulate-cmd">
            python -m backend.cli.simulate_wechat_scan --code {card.code}
          </code>
        </details>
      )}
    </section>
  );
}
