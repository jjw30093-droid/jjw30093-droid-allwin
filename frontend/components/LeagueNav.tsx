import { Tabs } from "@/components/ui/Tabs";

const TABS = [
  // 速览排第一:四张图(进球时段/常见比分/大小球/主平客)全部来自银层表,
  // 是本页面组里唯一图形化、也是最不需要背景知识就能看懂的一档。
  { key: "overview", label: "速览", path: "overview" },
  { key: "standings", label: "排名", path: "standings" },
  { key: "matches", label: "赛程", path: "matches" },
  { key: "team-stats", label: "球队数据", path: "team-stats" },
  { key: "players", label: "球员榜", path: "players" },
] as const;

/** 联赛二级导航:全站统一的 Tabs(链接形态,横向可滚动,选中态底部粗线 + 主色文字)。 */
export function LeagueNav({
  leagueId,
  active,
  season,
}: {
  leagueId: string;
  active: (typeof TABS)[number]["key"];
  season?: string;
}) {
  return (
    <Tabs
      ariaLabel="联赛导航"
      activeKey={active}
      items={TABS.map((t) => ({
        key: t.key,
        label: t.label,
        href: season
          ? `/league/${leagueId}/${t.path}?season=${encodeURIComponent(season)}`
          : `/league/${leagueId}/${t.path}`,
      }))}
    />
  );
}
