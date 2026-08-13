/**
 * 赛季切换器(服务端组件,无 JS 也可用——照搬 app/matches/page.tsx 的
 * "纯 GET 链接" 筛选范式,不用客户端 <select>:仓库里目前没有任何写 URL
 * 查询参数的客户端组件,没必要为这一处开先例)。
 *
 * "自动" 链接指向不带 season 的裸路径——这是有意义的第三态(每个 tab 各自
 * 挑最合适的默认赛季),不是"最新赛季"的别名。
 */

import Link from "next/link";
import { buildLeagueSeasonHref } from "@/lib/league-links";
import styles from "./SeasonSwitcher.module.css";

export function SeasonSwitcher({
  leagueId,
  section,
  seasons,
  selected,
  resolved,
  tableType,
}: {
  leagueId: string;
  section: string;
  /** 后端升序返回(ORDER BY Season);这里倒序渲染,最新赛季在前。 */
  seasons: string[];
  /** 用户在 URL 里显式选择的赛季(undefined = 未选择,当前是"自动"态)。 */
  selected?: string;
  /** 后端实际解析并返回的赛季,用于"自动"链接的可见提示与无障碍标签。 */
  resolved?: string;
  /** 排名页当前榜别(all/home/away/form/xg);切赛季时必须带着走,否则会掉回总榜。 */
  tableType?: string;
}) {
  if (seasons.length === 0) return null;

  // 只有一个赛季:没有可切换的对象,渲染一个不可点击的静态徽章即可
  // (取代原来独立的 seasonChip span,不用同时维护两套"当前赛季"展示)。
  if (seasons.length === 1) {
    return (
      <div className={styles.chipRow} aria-label="当前赛季">
        <span className={styles.chipStatic}>{seasons[0]}</span>
      </div>
    );
  }

  const descending = [...seasons].reverse();
  const isAuto = !selected;
  const autoLabel =
    isAuto && resolved ? `自动选择赛季(当前 ${resolved})` : "自动选择赛季";
  // 用户显式选的赛季不在本页数据源里(如从赛程 tab 带着 2026/2027 跳到排名 tab,
  // 而排名没有该赛季):后端会静默回退,此前 UI 零高亮、用户不知道自己在看什么。
  const selectedUnknown = !!selected && !seasons.includes(selected);

  return (
    <nav className={styles.chipRow} aria-label="选择赛季">
      {selectedUnknown && (
        <span className={styles.autoHint}>
          该赛季本页无数据{resolved ? `,已展示 ${resolved}` : ""}
        </span>
      )}
      <Link
        href={buildLeagueSeasonHref(leagueId, section, undefined, tableType)}
        className={isAuto ? styles.chipActive : styles.chip}
        aria-current={isAuto ? "page" : undefined}
        aria-label={autoLabel}
        data-testid="season-switcher-auto"
      >
        自动
        {isAuto && resolved ? (
          <span className={styles.autoHint}> · {resolved}</span>
        ) : null}
      </Link>
      {descending.map((season) => {
        const active = selected === season;
        return (
          <Link
            key={season}
            href={buildLeagueSeasonHref(leagueId, section, season, tableType)}
            className={active ? styles.chipActive : styles.chip}
            aria-current={active ? "page" : undefined}
            data-testid="season-switcher-chip"
          >
            {season}
          </Link>
        );
      })}
    </nav>
  );
}
