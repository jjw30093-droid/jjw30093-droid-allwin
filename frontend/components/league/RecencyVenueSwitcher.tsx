"use client";

/**
 * 球队数据页"最近 N 场 / 主客场"筛选。
 *
 * 2026-09-15 改下拉(站长反馈:chips 排一整行手机上仍然拥挤)。这是本代码库
 * 第一个用客户端 JS 写 URL 查询参数的组件——此前 SeasonSwitcher/
 * TableTypeSwitcher 刻意用纯 GET 链接,图的是不需要客户端 JS 就能筛选;
 * 这里换成 <select> + router.push() 是站长在看过 chips 方案后明确要的
 * 权衡:两个下拉比 7 个常驻 chip 省空间得多,值得为此打破"零 JS 筛选"的
 * 惯例(随后 SeasonSwitcher 也改了同款下拉)。href 仍然复用
 * buildLeagueSeasonHref()——URL 才是真正的筛选状态,只是触发方式从
 * "点链接"变成"选完自动跳转",分享/收藏/浏览器后退都不受影响。
 *
 * 只渲染两个 <select> 控件本身,不带外层行——本组件只有 team-stats 页
 * 一处调用,由该页把它跟 SeasonSwitcher(inline 模式)一起排进同一行,
 * 样式复用 SeasonSwitcher.module.css 的 .selectWrap/.select,不重复定义。
 */

import { useRouter } from "next/navigation";
import { buildLeagueSeasonHref } from "@/lib/league-links";
import styles from "./SeasonSwitcher.module.css";

export function RecencyVenueSwitcher({
  leagueId,
  season,
  recency,
  venue,
}: {
  leagueId: string;
  /** 用户显式选择的赛季;切筛选维度时必须带着走,否则会跳回"自动"赛季。 */
  season?: string;
  recency?: number;
  venue?: string;
}) {
  const router = useRouter();

  return (
    <>
      <span className={styles.selectWrap}>
        <select
          className={styles.select}
          aria-label="选择场次范围"
          value={recency != null ? String(recency) : ""}
          onChange={(e) => {
            const next = e.target.value ? Number(e.target.value) : undefined;
            router.push(buildLeagueSeasonHref(leagueId, "team-stats", { season, recency: next, venue }));
          }}
        >
          <option value="">全部场次</option>
          <option value="3">最近3场</option>
          <option value="5">最近5场</option>
          <option value="10">最近10场</option>
        </select>
      </span>
      <span className={styles.selectWrap}>
        <select
          className={styles.select}
          aria-label="选择主客场"
          value={venue ?? "all"}
          onChange={(e) => {
            const next = e.target.value;
            router.push(
              buildLeagueSeasonHref(leagueId, "team-stats", { season, recency, venue: next }),
            );
          }}
        >
          <option value="all">全部主客场</option>
          <option value="home">主场</option>
          <option value="away">客场</option>
        </select>
      </span>
    </>
  );
}
