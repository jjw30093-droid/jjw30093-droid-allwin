import type { Metadata } from "next";
import { Oswald, Noto_Sans_SC } from "next/font/google";
import { SiteNav } from "@/components/SiteNav";
import "./globals.css";

const oswald = Oswald({
  variable: "--font-latin",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
});

const notoSansSC = Noto_Sans_SC({
  variable: "--font-cn",
  subsets: ["latin"],
  weight: ["400", "500", "900"],
});

export const metadata: Metadata = {
  title: "欧赢 allwin — 开赛前,把一场比赛讲清楚",
  description:
    "中文足球赛前分析:真实数据、模型概率、公开可查的预测记录。排名 / 赛程 / 数据榜 / 概率卡。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className={`${oswald.variable} ${notoSansSC.variable}`}>
      <body>
        <SiteNav />
        {children}
      </body>
    </html>
  );
}
