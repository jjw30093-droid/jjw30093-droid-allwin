import type { Metadata } from "next";
import { Oswald, Noto_Sans_SC } from "next/font/google";
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
  title: "欧赢 allwin",
  description: "英超真实数据 · 排名 / 赛程 / 数据榜",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className={`${oswald.variable} ${notoSansSC.variable}`}>
      <body>{children}</body>
    </html>
  );
}
