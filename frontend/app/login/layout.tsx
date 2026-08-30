import type { Metadata } from "next";

/** 页面本身是 "use client"(微信扫码轮询 + 表单交互),metadata 走这个纯
 * 透传的 server layout 承载,页面文件一行不动。同 app/reco/layout.tsx。 */
export const metadata: Metadata = {
  title: "登录",
  description: "微信扫码登录喵弟数据研究室。",
};

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return children;
}
