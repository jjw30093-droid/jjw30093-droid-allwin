/**
 * 球队数据榜的分区渲染(纯展示)。把「我们自己聚合的指标」和「来源方赛季榜」
 * 两个来源合并成 重点数据 / 进攻 / 防守 / 纪律 四段——按足球语义分,不按数据
 * 出处分(改造理由见 teamStatSections.ts 头注释)。
 *
 * 两个来源不会撞车:后端 FREE_TEAM_BOARDS 已经把 TeamSeasonStatRow 里有同义
 * 字段的维度排除在 boards 之外,所以同一个指标只会来自其中一边。
 *
 * 老赛季只有我们自己的聚合、没采过来源榜,此时来源侧那几段自然为空——空的
 * 分区整段不渲染,不摆一排"暂无数据"。
 */

import { LeaderboardCard, type LeaderboardRow } from "@/components/LeaderboardCard";
import type { TeamSeasonStatRow, TeamSourceBoard } from "@/lib/api-v1";
import { boardTitle, formatBoardValue } from "./sourceBoardFormat";
import {
  OWN_METRICS,
  SECTION_LEAD,
  SECTION_ORDER,
  SECTION_TITLES,
  sectionOfSourceCategory,
  type OwnMetric,
  type SectionKey,
} from "./teamStatSections";
import styles from "./boards.module.css";

const TOP_N = 10;

type Card = { key: string; title: string; rows: LeaderboardRow[] };

function ownCard(rows: TeamSeasonStatRow[], metric: OwnMetric): Card | null {
  const usable = rows.filter((t) => t[metric.key] != null);
  if (usable.length === 0) return null;
  const sign = metric.order === "asc" ? 1 : -1;
  const ranked = [...usable]
    .sort((a, b) => sign * ((a[metric.key] as number) - (b[metric.key] as number)))
    .slice(0, TOP_N);
  return {
    key: `own:${metric.key}`,
    title: metric.title,
    rows: ranked.map((t, i) => ({
      rank: i + 1,
      name: t.team.name,
      value: metric.format(t[metric.key] as number),
      avatar: { kind: "team" as const, crestUrl: t.team.crest_url },
      teamColor: t.team_color,
    })),
  };
}

function sourceCard(board: TeamSourceBoard): Card | null {
  const rows: LeaderboardRow[] = [];
  board.entries.forEach((e, i) => {
    const value = formatBoardValue(e.value, board.stat_format, board.stat_decimals);
    // 没有数值的行不占位:排名榜里显示一个空值等于骗人说"这队排第 N"
    if (value == null) return;
    rows.push({
      rank: e.rank ?? i + 1,
      name: e.team.name,
      value,
      avatar: { kind: "team", crestUrl: e.team.crest_url },
      teamColor: e.team_color,
    });
  });
  if (rows.length === 0) return null;
  return { key: `src:${board.stat_name}`, title: boardTitle(board), rows };
}

export function TeamStatsSections({
  rows,
  boards,
}: {
  rows: TeamSeasonStatRow[];
  boards: TeamSourceBoard[];
}) {
  const bySection = new Map<SectionKey, Card[]>(SECTION_ORDER.map((k) => [k, []]));

  // 我方指标排在各区前面:它们对所有赛季/联赛都有(来源榜只有采过的赛季才有),
  // 顺序稳定,不会因为某个赛季没采到来源数据就整区改版。
  for (const metric of OWN_METRICS) {
    const card = ownCard(rows, metric);
    if (card) bySection.get(metric.section)!.push(card);
  }
  for (const board of boards) {
    const card = sourceCard(board);
    if (card) bySection.get(sectionOfSourceCategory(board.category))!.push(card);
  }

  // 分区内排序:SECTION_LEAD 里列过的按表走,没列过的保持插入顺序排在后面
  for (const [key, cards] of bySection) {
    const lead = SECTION_LEAD[key];
    const weight = (c: Card) => {
      const i = lead.indexOf(c.key);
      return i === -1 ? lead.length : i;
    };
    cards.sort((a, b) => weight(a) - weight(b));
  }

  return (
    <>
      {SECTION_ORDER.map((key) => {
        const cards = bySection.get(key)!;
        if (cards.length === 0) return null;
        return (
          <section key={key} className={styles.section}>
            <h2 className={styles.sectionHeader}>{SECTION_TITLES[key]}</h2>
            <div className={styles.grid}>
              {cards.map((c) => (
                <LeaderboardCard key={c.key} title={c.title} rows={c.rows} />
              ))}
            </div>
          </section>
        );
      })}
    </>
  );
}
