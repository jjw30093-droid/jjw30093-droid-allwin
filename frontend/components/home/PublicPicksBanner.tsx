/**
 * 首页「每日公推」banner 的服务端取数层(2026-09)。
 *
 * 这里也要过滤一次(不是只取数):SSR 出去的 HTML 若含已到点的公推,无 JS
 * 的访客、以及水合前那一帧都会看到它。服务端这次过滤的结果会被 ISR 冻住
 * (最坏陈旧 ~2.5 分钟),客户端挂载后按自己的当前时间再精确重算一次——
 * 两层各司其职:服务端保证首帧不离谱,客户端保证最终永远正确。
 *
 * 本文件是 Server Component(无 "use client"),纯函数从 lib/reco-banner.ts
 * 取——那个文件同样没有 "use client",服务端与客户端都能安全 import(§11.4)。
 */

import { cache } from "react";
import { serverGetOptional, type GetJson } from "@/lib/api-v1";
import { visiblePublicPicks } from "@/lib/reco-banner";
import { PublicPicksBannerLive } from "./PublicPicksBannerLive";

type CurrentResp = GetJson<"/api/v1/reco/public/current">;

type BannerData = {
  visible: CurrentResp["slips"];
  hideAfterHours: number;
};

/**
 * 取数 + 服务端首轮过滤。
 *
 * `Date.now()` 刻意放在这个 cache() 包住的普通异步函数里,而不是组件 render
 * 体内——组件 render 必须是纯的(react-hooks/purity:同一次渲染里调不纯函数
 * 会产生不稳定结果)。这里是一次性的数据获取,每次 ISR 重新生成时重算一次
 * 正是我们要的语义。
 */
const getBannerData = cache(async (): Promise<BannerData | null> => {
  // 优雅降级:接口挂了就当今天没有公推,绝不能拖垮整个首页
  // (同 app/page.tsx::getRecoOverview 的既有写法)。
  const data = await serverGetOptional<CurrentResp>(
    "/api/v1/reco/public/current",
    { revalidate: 60 },
  ).catch(() => null);
  if (!data) return null;
  return {
    visible: visiblePublicPicks(
      data.slips,
      Date.now(),
      data.hide_after_kickoff_hours,
    ),
    hideAfterHours: data.hide_after_kickoff_hours,
  };
});

export async function PublicPicksBanner() {
  const data = await getBannerData();
  if (!data || data.visible.length === 0) return null;

  return (
    <PublicPicksBannerLive
      slips={data.visible}
      hideAfterHours={data.hideAfterHours}
    />
  );
}
