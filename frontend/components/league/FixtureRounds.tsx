/**
 * 赛程/结果按轮分组。消费 /api/v1/leagues/{id}/fixtures 的
 * MatchSummary(team: TeamRef 已含中文名),SSR 与客户端会员加载共用。
 * 赛程/结果是免费层展示形态:对阵/比分/日期/轮次/状态,不含任何概率字段。
 *
 * 2026-08-06(审计 B5):行由纯展示改为整行链接进比赛详情——此前联赛赛程页
 * 12,726 行展示位全是死路,与"任意展示出的比赛都可点进详情"的产品要求相悖。
 * returnTo 由调用方传入当前联赛页路径(含 ?season=),详情页返回链接文案
 * 按来源渲染为「返回赛程」(见 lib/match-links.ts)。
 */

import Link from "next/link";
import type { MatchSummary } from "@/lib/api-v1";
import { buildMatchHref } from "@/lib/match-links";
import { formatBeijingZh } from "@/components/matches/zh";
import styles from "./FixtureRounds.module.css";

const STATUS_ZH: Record<string, string> = {
  NotStarted: "未开赛",
  InPlay: "进行中",
  Cancelled: "已取消",
};

/** 排序/分组用的统一时间键:有精确 kickoff 用它,没有就退回自然日
 * (ISO 字符串本身可字典序比较;北京时间是固定 UTC+8,按 UTC 排序
 * 等价于按北京时间排序,不需要额外转换,见 zh.ts 顶部说明)。 */
function timeKey(m: MatchSummary): string {
  return m.kickoff_at_utc ?? m.date_utc;
}

/** 紧凑月日("8月21日"),不含年份——kickoffDate 列宽有限,精确 kickoff 存在时
 * 走 formatBeijingZh;date_only 时没有具体钟点可显示,只显示日期本身即是
 * 诚实的"时间待定"信号,不编造一个钟点。 */
function shortDateZh(dateUtc: string): string {
  const [, m, d] = dateUtc.split("-");
  return `${parseInt(m, 10)}月${parseInt(d, 10)}日`;
}

function MatchRowView({ m, returnTo }: { m: MatchSummary; returnTo?: string }) {
  const isFinished = m.status === "Finish";
  const beijingKickoff = m.kickoff_at_utc ? formatBeijingZh(m.kickoff_at_utc) : null;
  return (
    <Link href={buildMatchHref(m.match_id, returnTo)} className={styles.matchRow}>
      <span className={styles.homeTeam}>{m.home.name}</span>
      {/* 全 span:整行在 <a> 内,沿用 MatchRow 的结构约定 */}
      <span className={styles.center}>
        {isFinished ? (
          <span className={styles.score}>
            {m.home_score} - {m.away_score}
          </span>
        ) : (
          <>
            <span className={styles.kickoffDate}>
              {beijingKickoff ?? shortDateZh(m.date_utc)}
            </span>
            <span className={styles.statusBadge}>
              {STATUS_ZH[m.status] ?? m.status}
            </span>
          </>
        )}
      </span>
      <span className={styles.awayTeam}>{m.away.name}</span>
    </Link>
  );
}

export function FixtureRounds({
  matches,
  returnTo,
}: {
  matches: MatchSummary[];
  /** 当前联赛页路径(含 ?season=),作为详情页「返回赛程」的目标。 */
  returnTo?: string;
}) {
  const roundKey = (m: MatchSummary) => m.round ?? "";

  const groups = new Map<string, MatchSummary[]>();
  for (const m of matches) {
    const key = roundKey(m);
    const list = groups.get(key);
    if (list) list.push(m);
    else groups.set(key, [m]);
  }

  // 轮次组按组内最早比赛时间排序,不按轮次号——真实改期轮次(如某轮临时
  // 挪到赛季中段开踢)必须出现在它实际发生的时间位置,不是数字顺位。这也
  // 顺带修掉了旧实现"无轮次比赛"(roundKey()==="")因 Number("")===0 被
  // 排到第 1 轮之前的问题:现在它们按自己的真实时间参与排序,不再有特殊位置。
  const rounds = Array.from(groups.keys()).sort((a, b) => {
    const earliestA = groups.get(a)!.map(timeKey).sort()[0];
    const earliestB = groups.get(b)!.map(timeKey).sort()[0];
    return earliestA.localeCompare(earliestB);
  });

  return (
    <>
      {rounds.map((round) => (
        <div key={round || "unknown"}>
          <div className={styles.roundHeading}>
            {round ? `第 ${round} 轮` : "其他场次"}
          </div>
          <div className={styles.card}>
            {groups
              .get(round)!
              .slice()
              .sort((a, b) => timeKey(a).localeCompare(timeKey(b)))
              .map((m) => (
                <MatchRowView key={m.match_id} m={m} returnTo={returnTo} />
              ))}
          </div>
        </div>
      ))}
    </>
  );
}
