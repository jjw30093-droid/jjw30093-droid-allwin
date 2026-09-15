/**
 * 球员头像 URL 拼法(2026-09-15 从 PlayerAvatar.tsx 抽出成独立纯函数文件)。
 *
 * 不放在 PlayerAvatar.tsx 里:那是 "use client" 文件,CLAUDE.md §11.4 记过
 * 一次真实生产事故——服务端组件 import 任何东西(哪怕是零依赖的纯函数)
 * 出自 "use client" 文件,都会在真实请求时(不是构建期)崩溃
 * "Attempted to call X() from the server but X is on the client"。这个
 * URL 拼法函数本身不需要任何客户端能力,但 playerQuadrantViews.ts(球员
 * 象限图的取数逻辑)可能被服务端组件间接触达,所以必须放进一个不带
 * "use client" 的独立文件,PlayerAvatar.tsx 与 playerQuadrantViews.ts
 * 都从这里 import,不重复定义这个 CDN 路径。
 *
 * 2026-08-24 经站长明确批准的一次性例外:球员头像直接热链 FotMob 图片
 * CDN,不新建自托管代理/缓存层。
 */

const PLAYER_AVATAR_BASE = "https://images.fotmob.com/image_resources/playerimages";

export function playerAvatarUrl(playerId: string | number): string {
  return `${PLAYER_AVATAR_BASE}/${playerId}.png`;
}
