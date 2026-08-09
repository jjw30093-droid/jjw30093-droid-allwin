/**
 * 积分榜表格(纯展示,无数据获取)。
 *
 * 消费 /api/v1/leagues/{id}/standings 的行结构(team: TeamRef 已由服务端完成
 * 中文名解析与队徽地址),SSR 页面与客户端会员加载器(MemberLeagueSection)
 * 渲染同一份 JSX——深浅模式同一组件,不复制组件树(CLAUDE.md §11.2)。
 */

import { TeamBadge } from "@/components/teams/TeamBadge";
import type { StandingRow } from "@/lib/api-v1";
import styles from "./StandingsTable.module.css";

const QUAL_LABELS: Record<string, string> = {
  "#2AD572": "冠军 / 欧战资格",
  "#FFD908": "欧冠资格赛",
  "#0046A7": "欧联资格",
  "#02CCF0": "欧协联资格赛",
  "#FFA72F": "降级附加赛",
  "#FF4646": "降级区",
};

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

function Row({ row, isChampion }: { row: StandingRow; isChampion: boolean }) {
  const name = row.team.name;
  return (
    <tr className={isChampion ? styles.championRow : undefined}>
      <td className={styles.rank}>
        {row.qual_color && (
          <span
            className={styles.qualStripe}
            style={{ background: row.qual_color }}
          />
        )}
        <span className={styles.num}>{row.position}</span>
      </td>
      <td className={styles.team}>
        <span className={styles.teamIdentity}>
          <TeamBadge teamName={name} crestUrl={row.team.crest_url ?? null} size={24} />
          <span className={styles.teamNameZh}>{name}</span>
        </span>
        {isChampion && <span className={styles.championBadge}>冠军</span>}
      </td>
      <td className={styles.num}>{row.played}</td>
      <td className={`${styles.num} ${styles.wdl}`}>{row.wins}</td>
      <td className={`${styles.num} ${styles.wdl}`}>{row.draws}</td>
      <td className={`${styles.num} ${styles.wdl}`}>{row.losses}</td>
      <td className={`${styles.num} ${styles.gf}`}>{row.goals_for}</td>
      <td className={`${styles.num} ${styles.ga}`}>{row.goals_against}</td>
      <td>
        <GoalDiff value={row.goal_diff} />
      </td>
      <td className={`${styles.num} ${styles.points}`}>{row.points}</td>
    </tr>
  );
}

export function StandingsTable({ rows }: { rows: StandingRow[] }) {
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
                <th>名次</th>
                <th>球队</th>
                <th>场次</th>
                <th className={styles.wdl}>胜</th>
                <th className={styles.wdl}>平</th>
                <th className={styles.wdl}>负</th>
                <th>进球</th>
                <th>失球</th>
                <th>净胜</th>
                <th>积分</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <Row
                  key={row.team.team_id ?? row.position}
                  row={row}
                  isChampion={row.position === 1}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {usedQualColors.length > 0 && (
        <div className={styles.legend}>
          {usedQualColors.map((color) => (
            <span key={color}>
              <span className={styles.legendDot} style={{ background: color }} />
              {QUAL_LABELS[color] ?? "其他资格区域"}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
