"use client";

/**
 * 手机端(<768px)页脚的公众号入口(2026-09-26):原来那张常驻的大卡片折叠成
 * 一行"品牌名 + 关注公众号"按钮,点按钮从底部弹出面板——
 * - 微信内置浏览器(UA 含 MicroMessenger):二维码 + "长按识别二维码关注"
 *   (微信里长按图片才能识别,不能让用户去"扫"自己手机上的码);
 * - 其它浏览器:公众号名称 + "复制公众号名称" + "保存二维码到相册"
 *   (手机浏览器里没法自己扫自己屏幕上的码,给"搜名称关注"和"存图给微信扫一扫"两条路)。
 * 桌面端(≥768px)本组件被 CSS 藏起来,继续用 WechatFollowCard 大卡片。
 *
 * 保存二维码:优先 Web Share API(带文件)——iOS 分享面板里有"存储图像";
 * 不支持时退回 <a download>。这只是"发起保存",能否真的进相册取决于浏览器/系统,
 * 真机效果 UNVERIFIED(本环境没有手机)。
 */

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { WECHAT_MP_NAME, WECHAT_MP_QR_SRC } from "@/lib/wechat-mp";
import styles from "./MobileFollowBar.module.css";

/** 微信内置浏览器判定。只在客户端交互之后才调用(面板是点击后才挂载的),不会有 SSR 水合不一致。 */
export function isWeChatBrowser(ua: string): boolean {
  return /MicroMessenger/i.test(ua);
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 落到下面的兜底
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

async function saveQr(): Promise<"shared" | "downloaded" | "cancelled"> {
  const res = await fetch(WECHAT_MP_QR_SRC);
  const blob = await res.blob();
  const file = new File([blob], `${WECHAT_MP_NAME}-公众号二维码.png`, { type: blob.type || "image/png" });
  if (typeof navigator.canShare === "function" && navigator.canShare({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: WECHAT_MP_NAME });
      return "shared";
    } catch (e) {
      if ((e as { name?: string })?.name === "AbortError") return "cancelled";
      // 其它错误退回下载
    }
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = file.name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return "downloaded";
}

export function FollowSheet({ onClose }: { onClose: () => void }) {
  // 面板只在点击之后才挂载,直接读 UA 即可
  const [inWeChat] = useState(() => isWeChatBrowser(navigator.userAgent));
  const [toast, setToast] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const say = (msg: string) => {
    setToast(msg);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setToast(null), 2200);
  };

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [onClose]);

  const onCopy = async () => {
    say((await copyText(WECHAT_MP_NAME)) ? "已复制，去微信搜索关注" : "复制失败，请手动长按名称复制");
  };
  const onSave = async () => {
    try {
      const r = await saveQr();
      if (r === "shared") say("请在分享面板选择「存储图像」");
      else if (r === "downloaded") say("已开始下载二维码，请到相册/下载里查看");
    } catch {
      say("保存失败，请截图保存二维码");
    }
  };

  return (
    <div className={styles.overlay} onClick={onClose} data-testid="follow-sheet-overlay">
      <div
        className={styles.sheet}
        role="dialog"
        aria-modal="true"
        aria-label="关注公众号"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.sheetHead}>
          <strong className={styles.sheetTitle}>关注公众号</strong>
          <button ref={closeRef} type="button" className={styles.closeBtn} aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>

        {inWeChat ? (
          <div className={styles.qrWrap}>
            <Image
              src={WECHAT_MP_QR_SRC}
              alt={`${WECHAT_MP_NAME} 公众号二维码`}
              width={220}
              height={220}
              className={styles.qr}
              unoptimized
            />
            <p className={styles.hint}>长按识别二维码关注</p>
          </div>
        ) : (
          <div className={styles.namePanel}>
            <p className={styles.mpName}>{WECHAT_MP_NAME}</p>
            <div className={styles.actions}>
              <button type="button" className={styles.primary} onClick={onCopy}>
                复制公众号名称
              </button>
              <button type="button" className={styles.ghost} onClick={onSave}>
                保存二维码到相册
              </button>
            </div>
          </div>
        )}

        <p className={styles.toast} role="status" aria-live="polite">
          {toast}
        </p>
      </div>
    </div>
  );
}

export function MobileFollowBar() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className={styles.bar} data-testid="mobile-follow-bar">
        <span className={styles.brand}>
          <Image src="/brand/logo-badge-256.png" alt="" width={24} height={24} className={styles.logo} />
          <span className={styles.brandName}>{WECHAT_MP_NAME}</span>
        </span>
        <button type="button" className={styles.followBtn} onClick={() => setOpen(true)}>
          关注公众号
        </button>
      </div>
      {open && <FollowSheet onClose={() => setOpen(false)} />}
    </>
  );
}
