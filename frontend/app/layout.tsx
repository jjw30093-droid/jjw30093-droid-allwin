import type { Metadata, Viewport } from "next";
import { Oswald, Noto_Sans_SC } from "next/font/google";
import { SiteNav } from "@/components/SiteNav";
import { SiteFooter } from "@/components/SiteFooter";
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
  title: {
    default: "欧赢 ALLWIN｜看数据，也看门道",
    // 子路由段只需给出自己的短标题,后缀由这里统一追加——之前每个页面
    // 各自手写 "— 欧赢 ALLWIN" 后缀,新页面很容易漏写(2026-08 QA 抽查
    // 发现 6 个联赛子页 + /reco 浏览器标签页标题完全没设置)。
    template: "%s — 欧赢 ALLWIN",
  },
  description:
    "足球数据与比赛分析：赛程、状态、xG、赔率变化和公开记录，一场一场看。",
};

export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f6f6" },
    { media: "(prefers-color-scheme: dark)", color: "#07161e" },
  ],
};

const themeBootScript = `(function(){try{var k="allwin-theme";var s=localStorage.getItem(k);var t=s==="light"||s==="dark"?s:(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=t;document.documentElement.style.colorScheme=t}catch(e){document.documentElement.dataset.theme="light"}})()`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      data-theme="light"
      suppressHydrationWarning
      className={`${oswald.variable} ${notoSansSC.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body>
        <SiteNav />
        {children}
        <SiteFooter />
      </body>
    </html>
  );
}
