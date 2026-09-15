"use client";

/**
 * 赛季切换器。2026-09-15 改下拉(站长反馈:chips 排一行手机上仍然拥挤,
 * 年份切换器也要改成同一种下拉,并且要能跟别的筛选控件塞进一行)——
 * 这是继 RecencyVenueSwitcher 之后本代码库第二个用客户端 JS 写 URL 查询
 * 参数的组件,之前"纯 GET 链接、零 JS"的惯例到此不再是硬约束,详见
 * RecencyVenueSwitcher.tsx 顶部注释。
 *
 * "自动"选项指向不带 season 的裸路径——这是有意义的第三态(每个 tab 各自
 * 挑最合适的默认赛季),不是"最新赛季"的别名。
 *
 * `inline`(默认 false):默认自带外层一行(`.chipRow`),standings/matches/
 * players/overview 四个现有调用页不用改一行代码。team-stats 页要把这个下拉
 * 塞进 RecencyVenueSwitcher 同一行,传 `inline` 跳过自带的外层行,由调用方
 * 自己包一层 flex 行。
 */

import { useRouter } from "next/navigation";
import { buildLeagueSeasonHref } from "@/lib/league-links";
import styles from "./SeasonSwitcher.module.css";

export function SeasonSwitcher({
  leagueId,
  section,
  seasons,
  selected,
  resolved,
  tableType,
  recency,
  venue,
  inline = false,
}: {
  leagueId: string;
  section: string;
  /** 后端升序返回(ORDER BY Season);这里倒序渲染,最新赛季在前。 */
  seasons: string[];
  /** 用户在 URL 里显式选择的赛季(undefined = 未选择,当前是"自动"态)。 */
  selected?: string;
  /** 后端实际解析并返回的赛季,直接作为默认选项的显示文本(不写"自动"二字)。 */
  resolved?: string;
  /** 排名页当前榜别(all/home/away/form/xg);切赛季时必须带着走,否则会掉回总榜。 */
  tableType?: string;
  /** 球队数据页当前筛选(最近 N 场/主客场);切赛季时必须带着走,同 tableType
   *  既有先例——否则切一次赛季就把用户刚选好的筛选悄悄清空了。 */
  recency?: number;
  venue?: string;
  /** true = 只渲染控件本身,不带外层行,由调用方把它跟别的筛选控件排进
   *  同一行(目前只有 team-stats 页这么用)。 */
  inline?: boolean;
}) {
  const router = useRouter();

  if (seasons.length === 0) return null;

  // 只有一个赛季:没有可切换的对象,渲染一个不可点击的静态徽章即可
  // (取代原来独立的 seasonChip span,不用同时维护两套"当前赛季"展示)。
  if (seasons.length === 1) {
    const badge = <span className={styles.chipStatic}>{seasons[0]}</span>;
    return inline ? (
      badge
    ) : (
      <div className={styles.chipRow} aria-label="当前赛季">
        {badge}
      </div>
    );
  }

  const descending = [...seasons].reverse();
  const isAuto = !selected;
  // 用户显式选的赛季不在本页数据源里(如从赛程 tab 带着 2026/2027 跳到排名 tab,
  // 而排名没有该赛季):后端会静默回退,此前 UI 零高亮、用户不知道自己在看什么。
  const selectedUnknown = !!selected && !seasons.includes(selected);

  const control = (
    <span className={styles.selectWrap}>
      <select
        className={styles.select}
        aria-label="选择赛季"
        value={isAuto ? "" : selected}
        onChange={(e) => {
          const next = e.target.value || undefined;
          router.push(
            buildLeagueSeasonHref(leagueId, section, { season: next, tableType, recency, venue }),
          );
        }}
      >
        {/* "自动"这个词不上屏(2026-09-15 站长要求)——直接显示这个态实际
            解析出的赛季,用户不需要知道"这是自动选出来的"这层内部概念。
            value 仍是空字符串(不带 season 参数),与下面显式的赛季选项在
            URL 语义上仍然不同(每个 tab 各自挑默认赛季 vs 强制固定赛季),
            只是不上屏那个词了;resolved 缺失(数据没取到)时退化到最新赛季,
            不留空选项。 */}
        <option value="">{resolved ?? descending[0]}</option>
        {descending.map((season) => (
          <option key={season} value={season}>
            {season}
          </option>
        ))}
      </select>
    </span>
  );

  const warning = selectedUnknown ? (
    <span className={styles.autoHint}>
      该赛季本页无数据{resolved ? `,已展示 ${resolved}` : ""}
    </span>
  ) : null;

  if (inline) {
    return (
      <>
        {control}
        {warning}
      </>
    );
  }

  return (
    <div className={styles.chipRow}>
      {warning}
      {control}
    </div>
  );
}
