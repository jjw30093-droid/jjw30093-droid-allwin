/**
 * 首页「每日公推」banner 的纯判定逻辑(2026-09)。
 *
 * 本文件**刻意不带 "use client"**:服务端组件(PublicPicksBanner.tsx)与
 * 客户端组件(PublicPicksBannerLive.tsx)都要 import 这里的函数。CLAUDE.md
 * §11.4 记录过真实生产事故——服务端组件从 "use client" 文件 import 纯函数
 * 会在运行期整页白屏,而 next build 与 vitest 都抓不到。同一先例见
 * frontend/components/matches/attackingZones.ts。
 *
 * 为什么撤下判定在前端而不是后端:后端 /api/v1/reco/public/current 走
 * s-maxage=60 的共享缓存、首页又是 ISR(revalidate 60),服务端算出来的
 * "该不该显示"会被烘进共享 HTML 并随缓存变陈旧,且所有访客共用同一份陈旧
 * 判定。后端只下发 kickoff_at_utc 这个**事实**,由各客户端按自己的当前时间
 * 判定才永远正确。函数一律**接收 now 参数、不自己读时钟**,与
 * backend/ingest/physical_stats_poll.py::within_candidate_window 同一写法,
 * 也让边界值可测。
 */

import { toExactEpochMs } from "@/components/matches/zh";

/** 判定只依赖这一个字段——测试可以造最小对象,DTO 将来加字段也不会失配。
 *
 * kickoff_at_utc 写成可选(`?`)是为了对齐 OpenAPI 生成的类型:后端
 * `Optional[str] = None` 生成的是可省略键(`string | null | undefined`)。
 * 下面一律用 `== null` 判空,同时覆盖 null 与 undefined 两种缺失形态。 */
export type PickLegTiming = { kickoff_at_utc?: string | null };
export type PickTiming = { legs: readonly PickLegTiming[] };

/**
 * 该单应当从首页撤下的时刻(ms epoch);无法判定时返回 null。
 *
 * 串关按**最后一场**开球算(max,不是最早):整单的比赛都开球超过
 * hideAfterHours 才撤下。注意 /reco 页里现成的 earliestKickoff() 取的是
 * **最早**一场且靠正则解析 match_desc 文本,语义与数据来源都相反,不可复用。
 *
 * 以下三种情形一律 fail-closed 返回 null(调用方按"不展示"处理):
 * - legs 为空(否则 Math.max() 会返回 -Infinity,恒判"已过期"——碰巧与
 *   fail-closed 同向,所以不会被发现,直到有人改成 fail-open);
 * - 任意一条腿 kickoff_at_utc 为 null(§6.2.1:来源只给自然日时该列必须
 *   为 NULL,不得补零推断)——**不能用"有时间的那几条腿取 max"糊弄过去**,
 *   缺时间的腿恰好是最晚一场时会提前撤下,等于用不完整数据冒充完整判定;
 * - 任意一条腿的时间串是 date_only 或无法解析(由 toExactEpochMs 保障)。
 *
 * 算不出何时该撤下的单就不进 banner:首页顶部挂着一条永远撤不掉的推荐,
 * 比少显示一条严重得多。这条只是兜底——发布前置要求每条腿都有真实盘口
 * 溯源,而赔率采集本身只针对有精确开球时间的比赛,常规录入路径产不出
 * 缺 kickoff 的 published 公推腿。
 */
export function pickHideAtMs(slip: PickTiming, hideAfterHours: number): number | null {
  if (slip.legs.length === 0) return null;
  let latest = -Infinity;
  for (const leg of slip.legs) {
    if (leg.kickoff_at_utc == null) return null;
    const ms = toExactEpochMs(leg.kickoff_at_utc);
    if (ms == null) return null;
    if (ms > latest) latest = ms;
  }
  return latest + hideAfterHours * 3600_000;
}

/**
 * 过滤出当前还该显示在首页的公推单。
 *
 * 半开区间 `now < hideAt`:恰好到点即撤下(与 backend/queries/matches.py
 * 的窗口边界惯例一致)。保持输入顺序,不重排;返回的是**输入对象的同一
 * 引用**(不复制、不重建),调用方可以安全地继续用它做 React key。
 */
export function visiblePublicPicks<T extends PickTiming>(
  slips: readonly T[],
  nowMs: number,
  hideAfterHours: number,
): T[] {
  return slips.filter((slip) => {
    const hideAt = pickHideAtMs(slip, hideAfterHours);
    return hideAt !== null && nowMs < hideAt;
  });
}

/**
 * "单关" / "N串1"。与 app/reco/page.tsx::comboLabel 同一文案,但那边是
 * "use client" 路由文件里的模块私有函数,服务端组件不能 import(§11.4),
 * 所以在这里独立实现一份。
 */
export function comboLabel(legCount: number): string {
  return legCount === 1 ? "单关" : `${legCount}串1`;
}
