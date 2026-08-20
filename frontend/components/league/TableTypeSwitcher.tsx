/**
 * 排名榜档位切换(服务端组件,纯 GET 链接,与 SeasonSwitcher 同范式)。
 *
 * fact_league_table 里 all / home / away / form / xg 五档共 3,639 行,此前
 * queries/matches.standings 硬编码 `table_type='all'`,**后四档 2,892 行
 * 连 API 都出不去**,更进不了页面。这个切换器是它们的唯一入口。
 */

import Link from "next/link";
import { buildLeagueSeasonHref } from "@/lib/league-links";
import styles from "./SeasonSwitcher.module.css";

export const TABLE_TYPES = [
  { key: "all", label: "总榜" },
  { key: "home", label: "主场" },
  { key: "away", label: "客场" },
  { key: "form", label: "近期" },
  { key: "xg", label: "xG 榜" },
] as const;

export type TableTypeKey = (typeof TABLE_TYPES)[number]["key"];

export function isTableType(value: string | undefined): value is TableTypeKey {
  return TABLE_TYPES.some((t) => t.key === value);
}

/** standings 页 h1 与浏览器标签标题共用同一个判定,两者不可能漂移
 * (page.tsx 的 h1 与 generateMetadata 都调这个函数)。 */
export function standingsHeading(tableType: TableTypeKey): string {
  return tableType === "xg" ? "xG 运气榜" : "排名榜";
}

export function TableTypeSwitcher({
  leagueId,
  season,
  active,
}: {
  leagueId: string;
  /** 用户显式选择的赛季;切换榜别时必须带着走,否则会跳回"自动"赛季。 */
  season?: string;
  active: TableTypeKey;
}) {
  return (
    <nav className={styles.chipRow} aria-label="选择榜别">
      {TABLE_TYPES.map((t) => {
        const isActive = t.key === active;
        return (
          <Link
            key={t.key}
            href={buildLeagueSeasonHref(leagueId, "standings", season, t.key)}
            className={isActive ? styles.chipActive : styles.chip}
            aria-current={isActive ? "page" : undefined}
            data-testid="table-type-chip"
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
