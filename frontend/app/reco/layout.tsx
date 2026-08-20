import type { Metadata } from "next";

/**
 * /reco 页面本身是 "use client"(全部数据必须在浏览器端按登录/授权状态拉取),
 * Next 的 metadata/generateMetadata 只支持 Server Component。用一个纯透传的
 * server layout 承载静态 metadata,页面文件一行不动。
 */
export const metadata: Metadata = {
  title: "每日精选",
  description: "每场比赛按用户单独授权查看的精选内容与历史战绩。",
};

export default function RecoLayout({ children }: { children: React.ReactNode }) {
  return children;
}
