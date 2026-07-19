"use client";

/**
 * 兑换码输入(定价页)。会员/登录态只在浏览器端经 /api/v1/me 私有请求水合,
 * 不进匿名 HTML/RSC payload(宪法 §10.2);POST /api/v1/redeem 走 clientFetch,
 * 自带 credentials + CSRF。
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, getMe, redeemCode } from "@/lib/api-v1";
import { track } from "@/lib/analytics";
import { LocalTime } from "./LocalTime";
import styles from "./RedeemBox.module.css";

const PLAN_ZH: Record<string, string> = {
  free: "免费",
  pro: "Pro",
  premium: "Premium",
};

type AuthState = "loading" | "anonymous" | "authenticated" | "unknown";

function detailMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail) return detail;
  if (
    detail !== null &&
    typeof detail === "object" &&
    "message" in detail &&
    typeof (detail as { message: unknown }).message === "string"
  ) {
    return (detail as { message: string }).message;
  }
  return null;
}

export function RedeemBox() {
  const [auth, setAuth] = useState<AuthState>("loading");
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<{ planId: string; endsAt: string } | null>(null);

  useEffect(() => {
    track("pricing_viewed");
    let cancelled = false;
    getMe()
      .then((me) => {
        if (!cancelled) setAuth(me.authenticated ? "authenticated" : "anonymous");
      })
      .catch(() => {
        if (!cancelled) setAuth("unknown");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = code.trim();
    setError(null);
    if (trimmed.length < 6) {
      setError("兑换码至少 6 位,请检查后重试");
      return;
    }
    setSubmitting(true);
    try {
      const grant = await redeemCode(trimmed);
      setSuccess({ planId: grant.plan_id, endsAt: grant.ends_at });
      setCode("");
      track("redeem_completed", { plan_id: grant.plan_id });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setError("登录状态已失效,请先登录后再兑换");
          setAuth("anonymous");
        } else {
          setError(detailMessage(err.detail) ?? `兑换失败(HTTP ${err.status}),请稍后重试`);
        }
      } else {
        setError("网络异常,兑换请求未完成,请稍后重试");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.box}>
      <h2 className={styles.boxTitle}>使用兑换码开通</h2>

      {auth === "loading" && <div className={styles.skeleton} aria-hidden="true" />}

      {auth === "anonymous" && (
        <p className={styles.loginHint}>
          兑换码需要登录后使用。
          <Link href="/login" className={styles.loginLink}>
            前往登录
          </Link>
        </p>
      )}

      {(auth === "authenticated" || auth === "unknown") && (
        <>
          {auth === "unknown" && (
            <p className={styles.hint}>暂时无法确认登录状态,提交时将再次校验。</p>
          )}
          <form className={styles.form} onSubmit={onSubmit}>
            <input
              className={styles.input}
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="输入兑换码"
              autoComplete="off"
              spellCheck={false}
              maxLength={32}
              disabled={submitting}
              aria-label="兑换码"
            />
            <button className={styles.button} type="submit" disabled={submitting}>
              {submitting ? "兑换中…" : "兑换"}
            </button>
          </form>
        </>
      )}

      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}

      {success && (
        <p className={styles.success} role="status">
          兑换成功:{PLAN_ZH[success.planId] ?? success.planId} 已生效,有效期至{" "}
          <LocalTime utc={success.endsAt} />
          。刷新页面后可见完整会员内容。
        </p>
      )}

      <p className={styles.note}>
        当前未接入线上支付,会员通过兑换码或管理员开通;开通与撤销均有审计记录。
      </p>
    </div>
  );
}
