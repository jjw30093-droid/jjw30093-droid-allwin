"use client";

// 根 layout 自身渲染失败时的最后一道兜底。此时 SiteNav、globals.css 里的
// 主题变量、乃至 layout.tsx 里那段写 data-theme 的启动脚本都可能没跑起来
// ——这个文件必须自带完整 <html><body> 和全部样式,不能依赖任何外部资源。
// 出于同样的原因,回首页用普通 <a href> 硬跳转而不是 next/link:路由此时
// 可能整个坏了,客户端导航未必好使。
export default function GlobalError() {
  return (
    <html lang="zh-CN">
      <head>
        <style>{`
          :root { color-scheme: light dark; }
          body {
            margin: 0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            background: #f4f6f6;
            color: #0d2c3d;
            font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
          }
          @media (prefers-color-scheme: dark) {
            body { background: #07161e; color: #e7eef0; }
            .btn { background: #45b9af !important; color: #07161e !important; }
          }
          .wrap { max-width: 420px; text-align: center; }
          h1 { margin: 0 0 12px; font-size: 20px; font-weight: 900; }
          p { margin: 0 0 24px; font-size: 14px; line-height: 1.6; }
          .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 44px;
            padding: 5px 22px;
            border-radius: 999px;
            font-size: 14px;
            font-weight: 700;
            text-decoration: none;
            background: #087e78;
            color: #ffffff;
          }
        `}</style>
      </head>
      <body>
        <div className="wrap">
          <h1>网站出错了</h1>
          <p>页面加载失败，请稍后再试。</p>
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- 根 layout 本身失败时,next/link 的客户端路由未必可用,这里必须硬跳转 */}
          <a href="/" className="btn">
            重新加载
          </a>
        </div>
      </body>
    </html>
  );
}
