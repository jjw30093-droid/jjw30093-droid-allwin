/**
 * 公众号相关的浏览器端小动作:复制公众号名称、保存公众号二维码。
 * 页脚手机关注面板(MobileFollowBar)与登录卡片(CodeLoginCard)共用,不写第二份。
 * 只在用户点击后调用(用到 navigator/document),本文件本身不带 "use client"。
 *
 * 保存二维码:优先 Web Share API(带文件)——iOS 分享面板里有"存储图像";
 * 不支持时退回 <a download>。能否真的进相册取决于浏览器/系统。
 */
import { WECHAT_MP_NAME, WECHAT_MP_QR_SRC } from "@/lib/wechat-mp";

export async function copyText(text: string): Promise<boolean> {
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

export async function saveQr(): Promise<"shared" | "downloaded" | "cancelled"> {
  const res = await fetch(WECHAT_MP_QR_SRC);
  const blob = await res.blob();
  const file = new File([blob], `${WECHAT_MP_NAME}-公众号二维码.jpg`, { type: blob.type || "image/jpeg" });
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

