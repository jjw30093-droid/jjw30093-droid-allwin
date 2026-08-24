import type { NextConfig } from "next";
import { clientApiBase } from "./lib/api-base";

const nextConfig: NextConfig = {
  // 2026-08-24 经站长明确批准的一次性例外:球员头像(PlayerAvatar.tsx)
  // 直接热链 FotMob 图片 CDN,不像队徽那样走同源 rewrite 代理——不代表
  // CLAUDE.md §11.2"图片自托管、不依赖外链"规则废止,只用于这一个头像
  // 场景。next/image 即使传了 unoptimized,src 是绝对外部 URL 时仍然要求
  // remotePatterns 显式放行(不放行会在渲染时报错,不是可选的性能优化项)。
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "images.fotmob.com", pathname: "/image_resources/**" },
    ],
  },
  // 仅开发期生效:会话 cookie 是 host-only(127.0.0.1),本地要走通"浏览器带
  // cookie 调 127.0.0.1:8000"的会员链路,页面必须也从 127.0.0.1 打开(同站,
  // SameSite=Lax 才放行)。Next 16 默认拦截非 localhost origin 的 dev 资源,
  // 会让 127.0.0.1 打开的页面水合停摆——显式放行。生产构建忽略此配置。
  allowedDevOrigins: ["127.0.0.1"],
  // 队徽等媒体地址是后端返回的相对路径(/api/v1/media/...),浏览器按页面
  // 自身 origin 解析。生产环境靠 Nginx `location /api/` 做同源代理,天然可达;
  // 本地/E2E 场景数据请求走 NEXT_PUBLIC_API_BASE 拼绝对地址直连 FastAPI,但
  // 那份绝对地址只存在于已抓到的 JSON 里,不会回填进 crest_url 这类相对路径
  // 字符串本身——所以浏览器仍会把它们解析到 Next 自己的 origin 并 404。
  // 用同一个单一真源(lib/api-base.ts,宪法 §10.3)算出的浏览器基址做一条
  // rewrite 补上这一段,而不是在 TeamBadge 里给 crestUrl 手工拼前缀:
  // Studio 导出用 html-to-image 把页面转成 PNG,跨源 <img> 会污染 canvas
  // 直接弄坏已发布的 PNG 导出功能,同源 rewrite 没有这个问题。
  // 未设置 NEXT_PUBLIC_API_BASE(生产)时返回空数组,不生成任何 rewrite。
  async rewrites() {
    const base = clientApiBase();
    if (!base) return [];
    return [
      { source: "/api/v1/media/:path*", destination: `${base}/api/v1/media/:path*` },
    ];
  },
};

export default nextConfig;
