/**
 * 模块一:预计阵容 + 伤停(数据 tab → 阵容子页)。
 *
 * 诚实红线(CLAUDE.md §6.2/docs/design-brief-match-detail-viz.md,最关键的一条):
 * bronze_fm_lineup_snap 的 lineup_type 绝大多数是 `lastStarting11` ——「上一场的首发」,
 * 不是本场确认阵容。文案一律「预计首发(基于上一场)」+ 虚线卡型;
 * 只有 lineup_type 明确表示已确认时才切成实线卡 + 实心徽标。
 *
 * lineup_type/source/observed_at 是整场共享的一条快照属性(bronze_fm_lineup_snap
 * 一行同时含两队),不是逐队各自的字段——这里按 API 契约(MatchPreviewLineupsDTO)
 * 把它们提到组件顶层,不比照原始设计稿把它们塞进每一侧,那样会诱使未来的改动
 * 误以为两队可能有不同的 lineup_type。
 *
 * 球场图:数据源只给 formation + starters[],**没有站位坐标**,
 * 所以这里按 formation 分行排布,并在图下写明「位置为按阵型示意」。
 * (components/matches/PitchFormation.tsx 是赛后阵型图,那里有 extra_json 的
 *  归一化坐标,两者不是同一份数据,不要复用。)
 */

"use client";

import { useState } from "react";
import styles from "./ProjectedLineupSection.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import type { components } from "@/lib/api-types";

type LineupSide = components["schemas"]["MatchPreviewLineupSideDTO"];
type Player = components["schemas"]["MatchPreviewPlayerDTO"];
type SidelinedPlayer = components["schemas"]["MatchPreviewSidelinedPlayerDTO"];

const REASON_ZH: Record<string, string> = {
  injury: "伤病",
  suspension: "停赛",
  international: "国家队",
};

/** 拉丁名在球场图上取姓氏;中文名一律完整渲染,截断交给 CSS 省略号 —— slice 会砍掉姓。 */
function pitchLabel(name: string): string {
  if (/[A-Za-z]/.test(name)) {
    const parts = name.trim().split(/\s+/);
    return parts[parts.length - 1];
  }
  return name;
}

/** 按 formation 分行:[门将] + 各线。formation 缺失或首发不足 11 人时退化为不画球场。 */
function rowsFor(side: LineupSide): Player[][] | null {
  if (!side.formation || side.starters.length < 11) return null;
  const lines = side.formation.split("-").map(Number);
  if (lines.some((n) => !Number.isInteger(n) || n <= 0)) return null;
  const rows: Player[][] = [[side.starters[0]]];
  let i = 1;
  for (const n of lines) {
    rows.push(side.starters.slice(i, i + n));
    i += n;
  }
  return rows;
}

function Pitch({ side, isHome }: { side: LineupSide; isHome: boolean }) {
  const rows = rowsFor(side);
  if (!rows) {
    return (
      <ul className={styles.plainList}>
        {side.starters.map((p) => (
          <li key={p.id} className={styles.plainRow}>
            {p.name}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className={styles.pitch} data-side={isHome ? "home" : "away"}>
      <span className={styles.pitchLine} aria-hidden />
      <span className={styles.pitchHalf} aria-hidden />
      <span className={styles.pitchCircle} aria-hidden />
      <span className={styles.pitchBox} aria-hidden />
      <div className={styles.pitchRows}>
        {rows.map((row, ri) => (
          <div key={ri} className={styles.pitchRow}>
            {row.map((p) => (
              <span key={p.id} className={styles.dotWrap}>
                <span className={`${styles.dot} num`}>{p.shirt_number ?? ""}</span>
                <span className={styles.dotName}>{pitchLabel(p.name)}</span>
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function SidelinedCard({
  teamName,
  players,
  observedAt,
  hasSnapshot,
}: {
  teamName: string;
  players: SidelinedPlayer[];
  observedAt: string;
  /** 该场是否曾被采集过(阵容/伤停快照耦合写入)——为 false 时"0 人"是
   * "从未采集",不能显示成"确认无伤停",两者是完全不同的诚实结论。 */
  hasSnapshot: boolean;
}) {
  return (
    <div className={styles.card}>
      <div className={styles.cardHead}>
        <strong className={styles.cardTitle}>{teamName}</strong>
        <span
          className={`${styles.count} num`}
          data-empty={hasSnapshot && players.length === 0}
        >
          {hasSnapshot ? `${players.length} 人` : "暂无数据"}
        </span>
      </div>
      {!hasSnapshot ? (
        <p className={styles.emptyInline}>
          该场暂无伤停快照采集记录,开赛前会再次采集——这不是「确认无伤停」。
        </p>
      ) : players.length === 0 ? (
        <p className={styles.emptyInline}>
          数据源当前没有该队的伤停记录。这是「没有伤停」,不是「没有数据」—— 最近一次观测在 {observedAt}。
        </p>
      ) : (
        <ul className={styles.sidelinedList}>
          {players.map((p) => (
            <li key={p.id} className={styles.sidelinedRow}>
              <span className={styles.sidelinedName}>{p.name}</span>
              <span className={styles.reason} data-reason={p.reason ?? undefined}>
                {(p.reason && REASON_ZH[p.reason]) ?? p.reason ?? "—"}
              </span>
              <span className={styles.etaLabel}>预计回归</span>
              {/* 保留数据源英文原文口径,不换算成日期 —— 我们并不知道确切日期 */}
              <span className={`${styles.eta} num`}>{p.expected_return ?? "—"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ProjectedLineupSection({
  homeName,
  awayName,
  lineupType,
  observedAt,
  home,
  away,
  homeSidelined,
  awaySidelined,
}: {
  homeName: string;
  awayName: string;
  lineupType: string | null;
  observedAt: string | null;
  home: LineupSide | null;
  away: LineupSide | null;
  homeSidelined: SidelinedPlayer[];
  awaySidelined: SidelinedPlayer[];
}) {
  const [side, setSide] = useState<"home" | "away">(home ? "home" : "away");
  const active = side === "home" ? home : away;
  const confirmed = lineupType === "confirmed";
  const observedLabel = observedAt ?? "—";
  // 阵容/伤停快照耦合写入(见 backend/queries/lineup_preview.py)——两队都没
  // 阵容快照时,伤停的"0 人"也不是"确认无伤停",而是这场从未被采集过。
  const hasSnapshot = home != null || away != null;

  return (
    <>
      <section className={pageStyles.section}>
        <h2 className={pageStyles.sectionTitle}>
          <span className={pageStyles.sectionBar} aria-hidden />
          预计阵容
        </h2>

        {!home && !away ? (
          <p className={pageStyles.emptyText}>
            该场暂无阵容快照。数据源尚未提供两队的上一场首发,开赛前会再次采集。
          </p>
        ) : (
          <>
            <div className={styles.notice} data-confirmed={confirmed}>
              <div className={styles.noticeHead}>
                <span className={styles.noticeTag} data-confirmed={confirmed}>
                  {confirmed ? "已确认首发" : "预计首发 · 基于上一场"}
                </span>
                <span className={`${styles.observed} num`}>观测于 {observedLabel}</span>
              </div>
              <p className={styles.noticeText}>
                {confirmed
                  ? "数据源已更新为本场官方名单。"
                  : "这不是本场官方名单。数据源给的是两队上一场的首发,确认阵容通常在开赛前 1 小时才更新;更新后本区会换成「已确认首发」。"}
              </p>
            </div>

            <div className={styles.sideTabs}>
              {([
                { key: "home" as const, name: homeName, data: home },
                { key: "away" as const, name: awayName, data: away },
              ]).map((t) => (
                <button
                  key={t.key}
                  type="button"
                  disabled={!t.data}
                  title={t.data ? undefined : "该队暂无阵容快照"}
                  className={side === t.key ? styles.sideTabOn : styles.sideTab}
                  onClick={() => setSide(t.key)}
                >
                  {t.name}
                  <span className={`${styles.formation} num`}>
                    {t.data?.formation ?? "无快照"}
                  </span>
                </button>
              ))}
            </div>

            {active && (
              <>
                <Pitch side={active} isHome={side === "home"} />
                <p className={styles.pitchNote}>
                  {confirmed ? "已确认首发" : "预计首发(基于上一场)"}:
                  {side === "home" ? homeName : awayName} {active.formation ?? "阵型未知"},门将{" "}
                  {active.starters[0]?.name}。位置为按阵型示意 —— 数据源只给阵型与首发名单,不含站位坐标。
                </p>
                {active.subs.length > 0 && (
                  <details className={styles.bench}>
                    <summary className={styles.benchSummary}>
                      替补席 {active.subs.length} 人
                    </summary>
                    <ul className={styles.benchList}>
                      {active.subs.map((p) => (
                        <li key={p.id} className={styles.benchRow}>
                          <span className={`${styles.benchNo} num`}>
                            {p.shirt_number ?? "—"}
                          </span>
                          <span className={styles.benchName}>{p.name}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </>
            )}
          </>
        )}
      </section>

      <section className={pageStyles.section}>
        <h2 className={pageStyles.sectionTitle}>
          <span className={pageStyles.sectionBar} aria-hidden />
          伤停名单
        </h2>
        <div className={styles.sidelinedGrid}>
          <SidelinedCard
            teamName={homeName}
            players={homeSidelined}
            observedAt={observedLabel}
            hasSnapshot={hasSnapshot}
          />
          <SidelinedCard
            teamName={awayName}
            players={awaySidelined}
            observedAt={observedLabel}
            hasSnapshot={hasSnapshot}
          />
        </div>
        <p className={styles.footNote}>
          预计回归保留数据源原文口径(如 A few days / Day to day),不换算成具体日期 —— 我们并不知道确切日期。
        </p>
      </section>
    </>
  );
}
