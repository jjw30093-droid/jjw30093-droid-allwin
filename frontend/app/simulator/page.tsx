import { readFile } from "node:fs/promises";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { SimParams } from "@/features/simulator/types";
import { SimulatorClient } from "./SimulatorClient";

// 本地原型:只在开发环境、且显式设置 SIMULATOR_ENABLED=1 时可访问;生产构建一律 404。
export const dynamic = "force-dynamic";

function enabled(): boolean {
  return process.env.NODE_ENV !== "production" && process.env.SIMULATOR_ENABLED === "1";
}

const SHARE_TITLE = "比赛模拟器:自己排首发,模拟一场比赛";
const SHARE_DESCRIPTION = "选首发、阵型和战术侧重点,模拟一场五大联赛比赛:比分、进球、xG 赛跑和 1000 次模拟的胜平负。模拟结果,仅供娱乐。";

// 用 generateMetadata 而不是静态 metadata:静态导出即使页面 notFound() 也会把标题写进生产响应。
// 分享卡片:标题、描述、示意缩略图(静态图,不含真实比分)。微信聊天里的链接卡片读 og:title / og:description /
// og:image;示意图另备一张正方形(微信缩略图按正方形裁切),关键内容都放在画面中部。
export async function generateMetadata(): Promise<Metadata> {
  if (!enabled()) return { robots: { index: false, follow: false } };
  const images = [
    { url: "/brand/simulator-share-card.png", width: 1200, height: 630, alt: "比赛模拟器示意图(模拟比赛,非真实比分)" },
    { url: "/brand/simulator-share-square.png", width: 600, height: 600, alt: "比赛模拟器示意图(模拟比赛,非真实比分)" },
  ];
  return {
    title: "比赛模拟器(原型)",
    description: SHARE_DESCRIPTION,
    robots: { index: false, follow: false },
    openGraph: {
      type: "website",
      url: "/simulator",
      siteName: "喵弟数据研究室",
      locale: "zh_CN",
      title: SHARE_TITLE,
      description: SHARE_DESCRIPTION,
      images,
    },
    twitter: { card: "summary_large_image", title: SHARE_TITLE, description: SHARE_DESCRIPTION, images: [images[0].url] },
  };
}

export default async function SimulatorPage() {
  if (!enabled()) notFound();
  const path = process.env.SIMULATOR_PARAMS_PATH;
  if (!path) {
    return <p style={{ padding: 24 }}>未设置 SIMULATOR_PARAMS_PATH,无法读取模拟器参数。</p>;
  }
  let params: SimParams;
  try {
    params = JSON.parse(await readFile(path, "utf8")) as SimParams;
  } catch (e) {
    return <p style={{ padding: 24 }}>读取参数失败:{String(e)}</p>;
  }
  return <SimulatorClient params={params} />;
}
