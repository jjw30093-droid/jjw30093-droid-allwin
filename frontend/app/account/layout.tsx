import type { Metadata } from "next";

/** 页面本身是 "use client"(浏览器端按会话拉取账户数据),metadata 走这个
 * 纯透传的 server layout 承载,页面文件一行不动。同 app/reco/layout.tsx。 */
export const metadata: Metadata = {
  title: "账户中心",
  description: "查看关注的比赛、每日精选授权记录与绑定身份。",
};

export default function AccountLayout({ children }: { children: React.ReactNode }) {
  return children;
}
