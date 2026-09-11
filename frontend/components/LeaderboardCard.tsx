import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import styles from "./LeaderboardCard.module.css";

/**
 * 榜单行的头像来源(2026-09-11,对照 FotMob 官方安卓包联赛 Stats tab 的
 * 横向卡片轮播改造)。球员/球队两种头像来源不同(player_id 经 PlayerAvatar
 * 热链 FotMob CDN；crest_url 经 TeamBadge 走同源代理)，姓名不在这里重复
 * ——渲染时直接取 LeaderboardRow.name 传给对应组件，避免同一个名字在类型
 * 里出现两次。
 */
export type LeaderboardAvatar =
  | { kind: "player"; playerId: string | number; shirtNumber?: string | null }
  | { kind: "team"; crestUrl?: string | null };

export interface LeaderboardRow {
  rank: number;
  name: string;
  subtitle?: string | null;
  value: string;
  /** 不传则不渲染头像槽——保留纯文字兼容路径，不强制所有调用方都有头像来源。 */
  avatar?: LeaderboardAvatar;
}

function RowAvatar({ avatar, name }: { avatar: LeaderboardAvatar; name: string }) {
  if (avatar.kind === "player") {
    return (
      <PlayerAvatar
        playerId={avatar.playerId}
        playerName={name}
        shirtNumber={avatar.shirtNumber}
        size={40}
      />
    );
  }
  return <TeamBadge teamName={name} crestUrl={avatar.crestUrl} size={40} />;
}

export function LeaderboardCard({
  title,
  rows,
}: {
  title: string;
  rows: LeaderboardRow[];
}) {
  return (
    <div className={styles.card}>
      <div className={styles.title}>{title}</div>
      {rows.length === 0 ? (
        <div className={styles.empty}>暂无数据</div>
      ) : (
        <div className={styles.rail} role="list">
          {rows.map((r, i) => (
            // 并列名次会共享同一个 rank(如两名球员同为第 1),不能单独当 key
            // ——2026-09-11 本地验证时用真实数据复现过 React 的重复 key 警告,
            // 带上数组下标避免同名次多行互相踩掉。
            <div key={`${r.rank}-${i}`} role="listitem" className={styles.rowCard}>
              {r.avatar && (
                <div className={styles.avatarSlot}>
                  <RowAvatar avatar={r.avatar} name={r.name} />
                </div>
              )}
              <span className={r.rank === 1 ? `${styles.rank} ${styles.rankTop3}` : styles.rank}>
                {r.rank}
              </span>
              <span className={styles.name}>{r.name}</span>
              {r.subtitle && <span className={styles.subtitle}>{r.subtitle}</span>}
              <span className={styles.value}>{r.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
