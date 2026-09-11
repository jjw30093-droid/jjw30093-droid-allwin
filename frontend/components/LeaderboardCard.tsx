import type { CSSProperties } from "react";

import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import {
  resolvePillPaint,
  type TeamBrandColor,
} from "@/components/leaderboardPillColor";
import styles from "./LeaderboardCard.module.css";

/**
 * 榜单行的头像来源(2026-09-11)。球员/球队两种头像来源不同(player_id 经
 * PlayerAvatar 热链 FotMob CDN；crest_url 经 TeamBadge 走同源代理),姓名不在
 * 这里重复——渲染时直接取 LeaderboardRow.name 传给对应组件。
 */
export type LeaderboardAvatar =
  | { kind: "player"; playerId: string | number; shirtNumber?: string | null }
  | { kind: "team"; crestUrl?: string | null };

export interface LeaderboardRow {
  rank: number;
  name: string;
  subtitle?: string | null;
  value: string;
  /** 不传则不渲染头像槽——保留纯文字兼容路径,不强制所有调用方都有头像来源。 */
  avatar?: LeaderboardAvatar;
  /**
   * 该行所属球队的代表色(后端 team_color 字段)。只有第 1 名会用到:榜首
   * 数值胶囊改用队色。缺失或两套主题里任一套过不了对比度门槛时整体回退
   * 品牌色,见 leaderboardPillColor.ts。
   */
  teamColor?: TeamBrandColor | null;
}

/** 默认露出的行数,其余折叠(对齐 FotMob 联赛球队/球员数据 tab 的 top-3 卡)。 */
const VISIBLE_ROWS = 3;

function RowAvatar({ avatar, name }: { avatar: LeaderboardAvatar; name: string }) {
  if (avatar.kind === "player") {
    return (
      <PlayerAvatar
        playerId={avatar.playerId}
        playerName={name}
        shirtNumber={avatar.shirtNumber}
        size={28}
      />
    );
  }
  return <TeamBadge teamName={name} crestUrl={avatar.crestUrl} size={28} />;
}

/** 榜首胶囊的内联队色变量;没有安全队色时返回 undefined,CSS 回退品牌色。 */
function pillStyle(color: TeamBrandColor | null | undefined): CSSProperties | undefined {
  const paint = resolvePillPaint(color);
  if (!paint) return undefined;
  return {
    "--pill-bg": paint.light.bg,
    "--pill-ink": paint.light.ink,
    "--pill-bg-dark": paint.dark.bg,
    "--pill-ink-dark": paint.dark.ink,
  } as CSSProperties;
}

function Rows({
  rows,
  start,
  folded = false,
}: {
  rows: LeaderboardRow[];
  start: number;
  /** 折叠区:收起时必须显式 display:none,见 CSS 里 .foldedList 的注释。 */
  folded?: boolean;
}) {
  return (
    <ol
      className={folded ? `${styles.list} ${styles.foldedList}` : styles.list}
      start={start}
    >
      {rows.map((r, i) => (
        // 并列名次会共享同一个 rank(如两队同为第 1),不能单独当 key
        <li key={`${r.rank}-${start + i}`} className={styles.row}>
          <span className={styles.rank}>{r.rank}</span>
          {r.avatar && (
            <span className={styles.avatarSlot}>
              <RowAvatar avatar={r.avatar} name={r.name} />
            </span>
          )}
          <span className={styles.name}>
            {r.name}
            {r.subtitle && <span className={styles.subtitle}> · {r.subtitle}</span>}
          </span>
          {/* 第 1 名用胶囊强调。默认配色沿用 RatingChip 那套"前景取 --bg"的
              做法(--gold-300 在深浅两套主题里明暗方向相反,--bg 作前景天然
              都对比);有安全队色时用内联 CSS 变量覆盖成队色。这里只下发两
              套主题各自的值,由谁生效交给 CSS 的 [data-theme] 选择器——本
              组件是服务端组件,渲染时根本不知道用户选的是哪套主题。 */}
          <span
            className={`${styles.value} ${r.rank === 1 ? styles.valueTop : ""} num`}
            style={r.rank === 1 ? pillStyle(r.teamColor) : undefined}
          >
            {r.value}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function LeaderboardCard({
  title,
  rows,
}: {
  title: string;
  rows: LeaderboardRow[];
}) {
  if (rows.length === 0) {
    return (
      <div className={styles.card}>
        <div className={styles.head}>
          <span className={styles.title}>{title}</span>
        </div>
        <div className={styles.empty}>暂无数据</div>
      </div>
    );
  }

  const head = rows.slice(0, VISIBLE_ROWS);
  const rest = rows.slice(VISIBLE_ROWS);

  // 没有更多行时不套 <details>,免得出现一个点了没反应的箭头
  if (rest.length === 0) {
    return (
      <div className={styles.card}>
        <div className={styles.head}>
          <span className={styles.title}>{title}</span>
        </div>
        <Rows rows={head} start={1} />
      </div>
    );
  }

  // 用原生 <details> 折叠,不引入 JS accordion——与站内既有折叠(MarketCard /
  // OddsTimeline / MatchStatsSection 等 5 处)同一范式,也让本组件保持服务端渲染。
  // top-3 放进 <summary> 里,箭头才能落在卡片右上角(与 FotMob 一致);summary
  // 的无障碍名默认会把里面所有文字连起来念,所以显式给一个短 aria-label。
  return (
    <details className={styles.card}>
      <summary className={styles.summary} aria-label={`${title},展开全部 ${rows.length} 名`}>
        <span className={styles.head}>
          <span className={styles.title}>{title}</span>
          <span className={styles.chevron} aria-hidden="true" />
        </span>
        <Rows rows={head} start={1} />
      </summary>
      <Rows rows={rest} start={VISIBLE_ROWS + 1} folded />
    </details>
  );
}
