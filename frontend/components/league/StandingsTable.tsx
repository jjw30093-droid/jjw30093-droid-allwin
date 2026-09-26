/**
 * 积分榜表格(纯展示,无数据获取)。
 *
 * 消费 /api/v1/leagues/{id}/standings 的行结构(team: TeamRef 已由服务端完成
 * 中文名解析与队徽地址),SSR 页面与客户端会员加载器(MemberLeagueSection)
 * 渲染同一份 JSX——深浅模式同一组件,不复制组件树(CLAUDE.md §11.2)。
 */

import { TeamBadge } from "@/components/teams/TeamBadge";
import type { StandingRow } from "@/lib/api-v1";
import { qualLabel } from "./qualLegend";
import styles from "./StandingsTable.module.css";

const CHAMPION_COLOR = "var(--brand-gold)";

function GoalDiff({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className={styles.num}>—</span>;
  const sign = value > 0 ? "+" : "";
  const cls = value > 0 ? styles.gdPos : value < 0 ? styles.gdNeg : "";
  return (
    <span className={`${styles.num} ${cls}`}>
      {sign}
      {value}
    </span>
  );
}

/** 手机(<768px)首屏要同时看到 名次/球队/积分/场次/净胜:积分与净胜各渲染两份,
 *  一份给桌面(原顺序:…净胜、积分在最后),一份给手机(积分紧跟球队、净胜紧跟场次),
 *  由 CSS 按断点各藏一份(display:none,读屏器与搜索引擎只看到可见的那份)。 */
function Row({ row, isChampion }: { row: StandingRow; isChampion: boolean }) {
  const name = row.team.name;
  const stripe = isChampion ? CHAMPION_COLOR : row.qual_color;
  return (
    <tr className={isChampion ? styles.championRow : undefined}>
      <td className={`${styles.rank} ${styles.cRank}`}>
        {stripe && <span className={styles.qualStripe} style={{ background: stripe }} />}
        <span className={styles.num}>{row.position}</span>
      </td>
      <td className={`${styles.team} ${styles.cTeam}`}>
        <span className={styles.teamIdentity}>
          <TeamBadge teamName={name} crestUrl={row.team.crest_url ?? null} size={24} />
          <span className={styles.teamNameZh} title={name}>
            {name}
          </span>
        </span>
        {isChampion && <span className={styles.championBadge}>冠军</span>}
      </td>
      <td className={`${styles.num} ${styles.points} ${styles.mOnly}`}>{row.points}</td>
      <td className={`${styles.num} ${styles.cPlayed}`}>{row.played}</td>
      <td className={styles.mOnly}>
        <GoalDiff value={row.goal_diff} />
      </td>
      <td className={`${styles.num} ${styles.wdl}`}>{row.wins}</td>
      <td className={`${styles.num} ${styles.wdl}`}>{row.draws}</td>
      <td className={`${styles.num} ${styles.wdl}`}>{row.losses}</td>
      <td className={`${styles.num} ${styles.gf}`}>{row.goals_for}</td>
      <td className={`${styles.num} ${styles.ga}`}>{row.goals_against}</td>
      <td className={styles.dOnly}>
        <GoalDiff value={row.goal_diff} />
      </td>
      <td className={`${styles.num} ${styles.points} ${styles.dOnly}`}>{row.points}</td>
    </tr>
  );
}

/** seasonFinished:该赛季所有比赛都已完赛(后端 season_finished)。只有此时才显示
 *  "冠军"——赛季进行中榜首只是"目前领先"。分组赛制(group_name 非空,如 J1 东/西区)
 *  各组第一不是联赛冠军,同样不标。 */
export function StandingsTable({
  rows,
  seasonFinished = false,
  leagueId,
}: {
  rows: StandingRow[];
  seasonFinished?: boolean;
  /** 图例文字按联赛配置(qualLegend.ts);缺省或未配置回落"晋级区" */
  leagueId?: number | string;
}) {
  const isChampion = (r: StandingRow) => seasonFinished && r.position === 1 && !r.group_name;
  const hasChampion = rows.some(isChampion);
  const usedQualColors = Array.from(
    new Set(rows.map((r) => r.qual_color).filter(Boolean))
  ) as string[];

  return (
    <>
      <div className={styles.card}>
        <p className={styles.scrollHint}>手机可左右滑动查看完整数据</p>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.cRank}>名次</th>
                <th className={styles.cTeam}>球队</th>
                <th className={styles.mOnly}>积分</th>
                <th className={styles.cPlayed}>场次</th>
                <th className={styles.mOnly}>净胜</th>
                <th className={styles.wdl}>胜</th>
                <th className={styles.wdl}>平</th>
                <th className={styles.wdl}>负</th>
                <th className={styles.gf}>进球</th>
                <th className={styles.ga}>失球</th>
                <th className={styles.dOnly}>净胜</th>
                <th className={styles.dOnly}>积分</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <Row
                  key={row.team.team_id ?? row.position}
                  row={row}
                  isChampion={isChampion(row)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {(usedQualColors.length > 0 || hasChampion) && (
        <div className={styles.legend}>
          {hasChampion && (
            <span>
              <span className={styles.legendDot} style={{ background: CHAMPION_COLOR }} />
              冠军
            </span>
          )}
          {usedQualColors.map((color) => (
            <span key={color}>
              <span className={styles.legendDot} style={{ background: color }} />
              {qualLabel(leagueId, color)}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
