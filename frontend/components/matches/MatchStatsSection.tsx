"use client";

/**
 * 统计 tab:球队数据对比条 → 球员数据表。射门图只在「射门」tab 展示,不在这里
 * 重复(2026-08-24 站长反馈)。
 * 对比行按 TEAM_STAT_LABELS 顺序;两边都缺的统计项直接跳过(不渲染假 0)。
 * 球员表窄屏砍列不缩字(DESIGN.md 移动端规则)。
 * 2026-08-23:球队数据对比按 FotMob 分组(TEAM_STAT_GROUPS)+ 全场/上半场/
 * 下半场切换。半场数据已在 fact_team_match_stats 里,近三个赛季约 2/3
 * 场次有——该场没有半场行(teamStatsByHalf 为空)时不显示切换器,不是
 * 显示空的半场(诚实纪律,同 ThreatTimeline.tsx 的粒度切换写法)。
 */

import { Fragment, useState } from "react";
import type { MatchReportResponse } from "@/lib/api-v1";
import { RatingChip } from "@/components/matches/RatingChip";
import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import {
  buildPlayerHighlights,
  summarizePlayerShots,
} from "@/components/matches/playerHighlights";
import {
  TEAM_STAT_GROUPS,
  TEAM_STAT_LABELS,
  formatTeamStat,
  type TeamStatLabel,
} from "@/components/matches/zh";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import styles from "./MatchStatsSection.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type TeamStat = MatchReport["team_stats"][number];
type PlayerStat = MatchReport["player_stats"][number];
type Shot = MatchReport["shots"][number];
type LineupPlayer = MatchReport["lineups"][number]["starters"][number];

// 数值格式化收敛到 zh.ts::formatTeamStat(2026-08-25:此前这里和
// TopStatsCard 各写了一份相同的 fmt,新增 "km" 前先消掉重复)。

const LABEL_BY_KEY = new Map(TEAM_STAT_LABELS.map((l) => [l.key, l]));

type Row = { key: string; meta: TeamStatLabel; hv: number | null; av: number | null };

function buildRows(statKeys: string[], home: TeamStat, away: TeamStat): Row[] {
  return statKeys
    .map((key) => {
      const meta = LABEL_BY_KEY.get(key);
      if (!meta) return null;
      const hv = (home as Record<string, unknown>)[key] as number | null;
      const av = (away as Record<string, unknown>)[key] as number | null;
      return { key, meta, hv, av };
    })
    .filter((r): r is Row => r != null && (r.hv != null || r.av != null)); // 两边都缺 → 来源没有这项,跳过
}

function CompareRowList({ rows }: { rows: Row[] }) {
  return (
    <ul className={styles.compareList}>
      {rows.map((r) => {
        // 2026-08-23 修两处既有缺陷:
        // (1) 单边缺失时不画条——此前 (hv??0)+(av??0) 会让存在的一侧画成
        //     100% 满条,文字却显示"—",条说"对方是 0"、数字说"未知",自相矛盾。
        // (2) 两侧真为 0 时画空槽,不画视觉上跟"平局"分不清的 50/50。
        const bothMissing = r.hv == null || r.av == null;
        const total = (r.hv ?? 0) + (r.av ?? 0);
        const hPct = total > 0 ? ((r.hv ?? 0) / total) * 100 : 0;
        return (
          <li key={r.key} className={styles.compareRow}>
            <div className={styles.compareHeader}>
              <span className={`${styles.compareValue} num`}>{formatTeamStat(r.hv, r.meta)}</span>
              <span className={styles.compareLabel}>{r.meta.label}</span>
              <span className={`${styles.compareValue} num`}>{formatTeamStat(r.av, r.meta)}</span>
            </div>
            {!bothMissing && (
              <div className={styles.barTrack} aria-hidden>
                {total > 0 && (
                  <>
                    <span className={styles.barHome} style={{ width: `${hPct}%` }} />
                    <span className={styles.barAway} style={{ width: `${100 - hPct}%` }} />
                  </>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** 2026-08-23 对照 FotMob 官方安卓包分组(见 zh.ts TEAM_STAT_GROUPS 头部注释)。
 * "重点数据"组直接展开,其余组用原生 <details> 折叠——复用站内已有 5 处的
 * 折叠模式(MarketCard.tsx/OddsTimeline.tsx 等),不引入 JS accordion。
 * 某组两队都没有数据时整组不渲染,不产生空标题/空 <details>。 */
function CompareRows({ home, away }: { home: TeamStat; away: TeamStat }) {
  const groups = TEAM_STAT_GROUPS.map((g) => ({ ...g, rows: buildRows(g.statKeys, home, away) }))
    .filter((g) => g.rows.length > 0);

  if (groups.length === 0) {
    return <p className={pageStyles.emptyText}>该场比赛暂无球队统计数据。</p>;
  }

  const [top, ...rest] = groups;
  return (
    <div className={styles.groupStack}>
      {top && (
        <div>
          <h3 className={styles.groupTitle}>{top.label}</h3>
          <CompareRowList rows={top.rows} />
        </div>
      )}
      {rest.map((g) => (
        <details key={g.key} className={styles.groupDetails}>
          <summary className={styles.groupSummary}>{g.label}</summary>
          <CompareRowList rows={g.rows} />
        </details>
      ))}
    </div>
  );
}

const PLAYER_COLS: { key: keyof PlayerStat; label: string; wide?: boolean }[] = [
  { key: "minutes_played", label: "分钟" },
  { key: "goals", label: "进球" },
  { key: "assists", label: "助攻" },
  { key: "expected_goals", label: "xG", wide: true },
  { key: "shots_on_target", label: "射正", wide: true },
  { key: "chances_created", label: "创造机会", wide: true },
  { key: "tackles", label: "抢断", wide: true },
];

/** 2026-08-23 对照 FotMob 官方安卓包核实:单场球员统计在 FotMob 自己的
 * payload 里就是分组的(外场球员 进攻/防守/对抗,门将单独一组)。这里补的
 * 是"已采集、DTO 也下发、但表格 7 列从未展示"的字段(见 match_report.py
 * ::_player_stats 的口径注释)——点开一行球员展开,不新增表格列(手机上
 * 本来就只剩 5 格,再加列只会更挤)。
 * hideForGoalkeeper/onlyGoalkeeper 对齐 is_goalkeeper 分流,该字段此前
 * 完全没被用来控制球员表的任何展示逻辑。 */
type DetailField = {
  key: keyof PlayerStat;
  label: string;
  unit?: string;
  decimals?: number;
  /** 分数式字段(如成功传球 37/40)的分母字段名。 */
  totalKey?: keyof PlayerStat;
};
const PLAYER_DETAIL_GROUPS: {
  key: string;
  label: string;
  fields: DetailField[];
  onlyGoalkeeper?: boolean;
  hideForGoalkeeper?: boolean;
}[] = [
  /* 2026-09-10:对照 FotMob 安卓包 236.17398 的球员卡,补上他们的 "Top stats"
   * 段。这些列早就采集入库,直到这次才下发(见 backend/queries/match_report.py
   * ::_player_stats 第二批投影)。刻意不重复表格 7 列已有的(分钟/进球/助攻/
   * xG/射正/创造机会/抢断)——同一个数字在同一屏出现两次只会让人怀疑哪个是
   * 真的。事件型字段(击中门框/乌龙/送点…)不进这里,它们走亮点句。 */
  {
    key: "core", label: "核心",
    fields: [
      { key: "expected_goals_on_target", label: "xGOT", decimals: 2 },
      { key: "expected_assists", label: "xA", decimals: 2 },
      { key: "xg_and_xa", label: "xG+xA", decimals: 2 },
      { key: "expected_goals_non_penalty", label: "非点球 xG", decimals: 2 },
      { key: "shots_off_target", label: "射偏" },
      { key: "big_chance_created", label: "创造绝佳机会" },
      { key: "big_chance_missed", label: "错失绝佳机会" },
    ],
    hideForGoalkeeper: true,
  },
  {
    key: "attack", label: "进攻", hideForGoalkeeper: true,
    fields: [
      { key: "touches", label: "触球" },
      { key: "touches_opp_box", label: "对方禁区内触球" },
      { key: "accurate_passes", label: "成功传球", totalKey: "accurate_passes_total" },
      { key: "passes_into_final_third", label: "传向进攻三区" },
      { key: "long_balls_accurate", label: "成功长传" },
      { key: "dribbles_succeeded", label: "成功过人" },
      { key: "accurate_crosses", label: "成功传中" },
      { key: "dispossessed", label: "丢球" },
    ],
  },
  {
    key: "defence", label: "防守", hideForGoalkeeper: true,
    fields: [
      { key: "recoveries", label: "回追" },
      { key: "defensive_actions", label: "防守行动" },
      { key: "clearances", label: "解围" },
      { key: "headed_clearance", label: "头球解围" },
      { key: "interceptions", label: "拦截" },
      { key: "shot_blocks", label: "封堵射门" },
      { key: "dribbled_past", label: "被过人" },
    ],
  },
  {
    key: "duels", label: "对抗", hideForGoalkeeper: true,
    fields: [
      { key: "duel_won", label: "对抗成功" },
      { key: "duel_lost", label: "对抗失败" },
      { key: "ground_duels_won", label: "地面对抗成功" },
      { key: "aerials_won", label: "争顶成功" },
      { key: "fouls", label: "犯规" },
      { key: "was_fouled", label: "被侵犯" },
    ],
  },
  {
    key: "goalkeeping", label: "门将", onlyGoalkeeper: true,
    fields: [
      { key: "saves", label: "扑救" },
      { key: "goals_conceded", label: "失球" },
      { key: "expected_goals_on_target_faced", label: "面对 xGOT", decimals: 2 },
      { key: "goals_prevented", label: "阻止进球", decimals: 2 },
      { key: "keeper_diving_save", label: "鱼跃扑救" },
      { key: "saves_inside_box", label: "禁区内扑救" },
      { key: "keeper_sweeper", label: "出击解围" },
      { key: "punches", label: "击球出局" },
      { key: "keeper_high_claim", label: "高球摘取" },
      { key: "player_throws", label: "手抛球" },
      { key: "saved_penalties", label: "扑出点球" },
    ],
  },
  {
    // 2026-08-23:有则显示、无则不显示,与 FotMob 自身行为一致。覆盖率
    // 现实见 backend/fotmob_client.py 里 physical_metrics_* 旁的实测注释
    // ——2026-09-10 对生产库全量核对后是英超 7.5% / 欧冠 24.1% / 其余 0%,
    // 比当初的抽样估计(英超约 50%)低得多,所以这一组大多数比赛不出现。
    key: "physical", label: "体能",
    fields: [
      { key: "physical_metrics_distance_covered", label: "跑动距离", unit: "m" },
      { key: "physical_metrics_topspeed", label: "最高速度", unit: "km/h", decimals: 1 },
      { key: "physical_metrics_sprinting", label: "冲刺跑动", unit: "m" },
      { key: "physical_metrics_running", label: "中高速跑动", unit: "m" },
      { key: "physical_metrics_jogging", label: "慢跑", unit: "m" },
      { key: "physical_metrics_walking", label: "步行", unit: "m" },
      { key: "physical_metrics_number_of_sprints", label: "冲刺次数" },
    ],
  },
];

function fmtDetailField(p: PlayerStat, f: DetailField): string | null {
  const v = p[f.key];
  if (typeof v !== "number") return null;
  if (f.totalKey) {
    const total = p[f.totalKey];
    if (typeof total === "number") return `${Math.round(v)}/${Math.round(total)}`;
  }
  const text = f.decimals ? v.toFixed(f.decimals) : String(Math.round(v));
  return f.unit ? `${text}${f.unit}` : text;
}

/** 展开面板顶部的身份行:号码/位置/队长/最佳/上下场时间。
 * 这些不在 player_stats 里,来自 report.lineups —— 拿不到就不渲染这一行,
 * 不用"—"占位假装有数据。 */
function PlayerIdentityRow({
  player,
  lineup,
}: {
  player: PlayerStat;
  lineup?: LineupPlayer;
}) {
  const bits: string[] = [];
  if (lineup?.position_group) bits.push(POSITION_ZH[lineup.position_group] ?? lineup.position_group);
  if (lineup?.is_captain) bits.push("队长");
  if (typeof lineup?.sub_in_time === "number") bits.push(`${lineup.sub_in_time}' 替补登场`);
  if (typeof lineup?.sub_out_time === "number") bits.push(`${lineup.sub_out_time}' 被换下`);

  return (
    <div className={styles.detailIdentity}>
      <PlayerAvatar
        playerId={player.player_id}
        playerName={player.name}
        shirtNumber={lineup?.shirt_number}
        size={36}
      />
      <div className={styles.detailIdentityText}>
        <span className={styles.detailIdentityName}>
          {lineup?.shirt_number ? `${lineup.shirt_number}. ` : ""}
          {player.name}
          {lineup?.is_player_of_the_match && (
            <span className={styles.detailMotm}>全场最佳</span>
          )}
        </span>
        {bits.length > 0 && <span className={styles.detailIdentityMeta}>{bits.join(" · ")}</span>}
      </div>
    </div>
  );
}

const POSITION_ZH: Record<string, string> = {
  GK: "门将", DEF: "后卫", MID: "中场", FWD: "前锋",
};

function PlayerHighlightList({
  player,
  allPlayers,
  finished,
}: {
  player: PlayerStat;
  allPlayers: readonly PlayerStat[];
  finished: boolean;
}) {
  const items = buildPlayerHighlights(player, allPlayers, { finished });
  // 一条都算不出来就整块不渲染(不写"暂无亮点"占位)
  if (items.length === 0) return null;
  return (
    <ul className={styles.highlightList}>
      {items.map((h) => (
        <li
          key={h.key}
          className={h.tone === "negative" ? styles.highlightNegative : styles.highlightPositive}
        >
          {h.text}
        </li>
      ))}
    </ul>
  );
}

/** 本场射门摘要:与射门图同源(report.shots 按 player_id 过滤),不在这里嵌图
 * ——表格行内再放一张画布,手机上既慢又挤,真正的射门图在「射门」子 tab。 */
function PlayerShotSummary({ player, shots }: { player: PlayerStat; shots: readonly Shot[] }) {
  const summary = summarizePlayerShots(shots, player);
  if (!summary) return null;
  const parts = [`射门 ${summary.shots} 次`];
  // 官方射正数缺失就不写这一项(不用射门图逐脚推——那个口径在 Is_Blocked
  // 未回填的比赛里系统性高估,见 summarizePlayerShots 的实测注释)
  if (summary.onTarget != null) parts.push(`射正 ${summary.onTarget}`);
  if (summary.xgTotal != null) {
    parts.push(
      summary.xgCounted === summary.shots
        ? `xG 合计 ${summary.xgTotal.toFixed(2)}`
        // 部分射门没有 xG 时如实标注分母,不把缺失当 0 混进合计
        : `xG 合计 ${summary.xgTotal.toFixed(2)}（${summary.xgCounted}/${summary.shots} 脚有 xG）`
    );
  }
  return <p className={styles.detailShots}>{parts.join(" · ")}</p>;
}

function PlayerDetailPanel({
  player,
  lineup,
  allPlayers = [],
  shots = [],
  finished = true,
}: {
  player: PlayerStat;
  lineup?: LineupPlayer;
  allPlayers?: readonly PlayerStat[];
  shots?: readonly Shot[];
  finished?: boolean;
}) {
  const groups = PLAYER_DETAIL_GROUPS
    .filter((g) => !(g.onlyGoalkeeper && !player.is_goalkeeper))
    .filter((g) => !(g.hideForGoalkeeper && player.is_goalkeeper))
    .map((g) => ({
      ...g,
      items: g.fields
        .map((f) => ({ label: f.label, text: fmtDetailField(player, f) }))
        .filter((x): x is { label: string; text: string } => x.text != null),
    }))
    .filter((g) => g.items.length > 0);

  const head = (
    <>
      <PlayerIdentityRow player={player} lineup={lineup} />
      <PlayerHighlightList player={player} allPlayers={allPlayers} finished={finished} />
      <PlayerShotSummary player={player} shots={shots} />
    </>
  );

  if (groups.length === 0) {
    return (
      <div className={styles.detailPanel}>
        {head}
        <p className={styles.detailEmpty}>暂无更多分组数据。</p>
      </div>
    );
  }
  return (
    <div className={styles.detailPanel}>
      {head}
      {groups.map((g) => (
        <div key={g.key} className={styles.detailGroup}>
          <h4 className={styles.detailGroupTitle}>{g.label}</h4>
          <div className={styles.detailGrid}>
            {g.items.map((it) => (
              <div key={it.label} className={styles.detailItem}>
                <span className={`${styles.detailValue} num`}>{it.text}</span>
                <span className={styles.detailLabel}>{it.label}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function PlayerTable({
  players,
  teamName,
  lineupById,
  allPlayers,
  shots,
  finished,
}: {
  players: PlayerStat[];
  teamName: string;
  lineupById: Map<string, LineupPlayer>;
  allPlayers: readonly PlayerStat[];
  shots: readonly Shot[];
  finished: boolean;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (players.length === 0) return null;
  const toggle = (id: string) => setExpanded((cur) => (cur === id ? null : id));
  return (
    <div className={styles.tableWrap}>
      <h3 className={styles.tableTitle}>{teamName}</h3>
      <table className={styles.playerTable}>
        <thead>
          <tr>
            <th className={styles.nameCol}>球员</th>
            <th>评分</th>
            {PLAYER_COLS.map((c) => (
              <th key={String(c.key)} className={c.wide ? styles.wideCol : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {players.map((p) => (
            <Fragment key={p.player_id}>
              <tr
                className={styles.playerRow}
                role="button"
                tabIndex={0}
                aria-expanded={expanded === p.player_id}
                onClick={() => toggle(p.player_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggle(p.player_id);
                  }
                }}
              >
                <td className={styles.nameCol}>{p.name}</td>
                <td>
                  <RatingChip rating={p.rating} />
                </td>
                {PLAYER_COLS.map((c) => {
                  const v = p[c.key];
                  return (
                    <td key={String(c.key)} className={`num ${c.wide ? styles.wideCol : ""}`}>
                      {typeof v === "number"
                        ? c.key === "expected_goals"
                          ? v.toFixed(2)
                          : Math.round(v)
                        : "—"}
                    </td>
                  );
                })}
              </tr>
              {expanded === p.player_id && (
                <tr className={styles.detailRow}>
                  <td colSpan={2 + PLAYER_COLS.length}>
                    <PlayerDetailPanel
                      player={p}
                      lineup={lineupById.get(p.player_id)}
                      allPlayers={allPlayers}
                      shots={shots}
                      finished={finished}
                    />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const HALF_OPTIONS: { key: "All" | "FirstHalf" | "SecondHalf"; label: string }[] = [
  { key: "All", label: "全场" },
  { key: "FirstHalf", label: "上半场" },
  { key: "SecondHalf", label: "下半场" },
];

export function MatchStatsSection({
  teamStats,
  teamStatsByHalf = [],
  playerStats,
  lineups = [],
  shots = [],
  finished = true,
  homeName,
  awayName,
}: {
  teamStats: MatchReport["team_stats"];
  teamStatsByHalf?: MatchReport["team_stats_by_half"];
  playerStats: MatchReport["player_stats"];
  /** 身份信息(号码/位置/队长/最佳/换人时间)只在阵容里,统计表没有。 */
  lineups?: MatchReport["lineups"];
  /** 逐脚射门:展开面板的射门摘要与射门图同源,不另算一套。 */
  shots?: MatchReport["shots"];
  finished?: boolean;
  homeName: string;
  awayName: string;
}) {
  const [period, setPeriod] = useState<"All" | "FirstHalf" | "SecondHalf">("All");
  const hasHalves = teamStatsByHalf.length > 0;
  const activeTeamStats = period === "All" ? teamStats : teamStatsByHalf.filter((t) => t.period === period);
  const home = activeTeamStats.find((t) => t.is_home);
  const away = activeTeamStats.find((t) => !t.is_home);
  const homePlayers = playerStats.filter((p) => p.is_home);
  const awayPlayers = playerStats.filter((p) => !p.is_home);
  const lineupById = new Map<string, LineupPlayer>();
  for (const team of lineups) {
    for (const p of [...team.starters, ...team.bench]) lineupById.set(p.player_id, p);
  }
  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>球队数据对比</h2>
      {hasHalves && (
        <div className={styles.periodSegmented} role="tablist" aria-label="切换时段">
          {HALF_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              type="button"
              role="tab"
              aria-selected={period === opt.key}
              className={period === opt.key ? styles.periodOn : styles.periodOff}
              onClick={() => setPeriod(opt.key)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
      {home && away ? (
        <CompareRows home={home} away={away} />
      ) : (
        <p className={pageStyles.emptyText}>该场比赛暂无球队统计数据。</p>
      )}

      <h2 className={pageStyles.sectionTitle}>球员数据</h2>
      {playerStats.length === 0 ? (
        <p className={pageStyles.emptyText}>该场比赛暂无球员统计数据。</p>
      ) : (
        <>
          {/* 亮点句按**本场全部球员**排名,不是按本队 —— FotMob 也是全场口径 */}
          <PlayerTable
            players={homePlayers} teamName={homeName} lineupById={lineupById}
            allPlayers={playerStats} shots={shots} finished={finished}
          />
          <PlayerTable
            players={awayPlayers} teamName={awayName} lineupById={lineupById}
            allPlayers={playerStats} shots={shots} finished={finished}
          />
        </>
      )}
    </section>
  );
}
