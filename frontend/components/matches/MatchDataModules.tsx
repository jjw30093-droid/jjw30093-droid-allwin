/**
 * 模块三:进攻来源拆解(双轨成分条) + 模块四:关键球员(占比条) + 模块五:门将对位卡。
 * 三块都是普通 HTML 卡片,不走 ECharts,但同样给文字摘要。
 *
 * 模块四需要主客切换(设计稿 dc.html 的 playerTeamTabs):后端 key_players
 * 是 {home: Block[], away: Block[]} 两支球队各自独立的榜单,不能合并成一份
 * 列表展示(会分不清某条记录属于哪队)——切换态由本组件自己管理。
 */

"use client";

import { useState } from "react";
import styles from "./MatchDataModules.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import type { components } from "@/lib/api-types";

/* ── 模块三 ─────────────────────────────────────────────── */

type SourceRow = components["schemas"]["MatchPreviewAttackSourceDTO"];

/** 色阶压深到 ≥3:1(非文本元素下限):浅 teal 在白底上几乎看不见。
 * 运动战原用 --brand-navy,但该 token 深色模式下改写成 #061923,和深色
 * 页面背景(#07161e)几乎同色,色块会糊到看不见——改用 --brand-blue,
 * 深浅两色都是明确可辨识的中亮度蓝(同 TeamStyleQuadrant.tsx 客队色的
 * 修复依据一致)。其余条目是固定中间调字面量色,深浅两色对比度本身已够,
 * 不需要改。 */
const SOURCE_COLOR: Record<string, string> = {
  运动战: "var(--brand-blue)",
  反击: "var(--brand-teal)",
  角球: "#3f8781",
  定位球: "#6b9e99",
  个人突破: "#4f6f79",
  点球: "#2a5a63",
  其他: "#8fa3a3",
};

const colorOf = (label: string) => SOURCE_COLOR[label] ?? "#8fa3a3";

function Bar({
  rows,
  pick,
  incomplete,
}: {
  rows: SourceRow[];
  pick: (r: SourceRow) => number | null | undefined;
  /** 有来源缺该字段时为 true——此时不把"已知来源"重新归一化成 100% 宽度
   * (那会把"已知部分"画成"全部"),整条替换成文字说明。 */
  incomplete: boolean;
}) {
  if (incomplete) {
    return <span className={styles.barUnavailable}>部分来源缺失,占比条暂不展示</span>;
  }
  const known = rows.filter((r) => pick(r) != null);
  const total = known.reduce((s, r) => s + (pick(r) as number), 0) || 1;
  return (
    <span className={styles.bar}>
      {known.map((r) => (
        <span
          key={r.label}
          className={styles.barSeg}
          style={{ width: `${((pick(r) as number) / total) * 100}%`, background: colorOf(r.label) }}
          title={`${r.label} ${(((pick(r) as number) / total) * 100).toFixed(1)}%`}
        />
      ))}
    </span>
  );
}

export function AttackSourceCard({
  teamName,
  rows,
  note,
}: {
  teamName: string;
  rows: SourceRow[];
  note: string;
}) {
  const shots = rows.reduce((s, r) => s + r.shots, 0);
  // 任一来源 xG 缺失时,不能把 null 当 0 求和——那会显示成一个看似精确、
  // 实则低估的假合计(CLAUDE.md §6.2 不伪装精确度)。只有全部来源都有 xG
  // 时才展示真实总数,否则如实说"部分来源缺失"。
  const xgKnownRows = rows.filter((r) => r.xg != null);
  const xgComplete = rows.length > 0 && xgKnownRows.length === rows.length;
  const xgTotal = xgKnownRows.reduce((s, r) => s + (r.xg as number), 0);
  const xgMeta = rows.length === 0
    ? "xG 暂无数据"
    : xgComplete
      ? `xG ${xgTotal.toFixed(2)}`
      : xgKnownRows.length === 0
        ? "xG 部分来源数据缺失"
        : "xG 部分来源数据缺失(合计不完整)";
  return (
    <div className={styles.card}>
      <div className={styles.cardHead}>
        <strong className={styles.cardTitle}>{teamName}</strong>
        <span className={`${styles.cardMeta} num`}>
          {shots} 脚射门 · {xgMeta}
        </span>
      </div>

      <div className={styles.bars}>
        <div className={styles.barRow}>
          <span className={styles.barLabel}>射门</span>
          <Bar rows={rows} pick={(r) => r.shots} incomplete={false} />
        </div>
        <div className={styles.barRow}>
          <span className={styles.barLabel}>xG</span>
          <Bar rows={rows} pick={(r) => r.xg} incomplete={!xgComplete && rows.length > 0} />
        </div>
      </div>

      <ul className={styles.sourceList}>
        {rows.map((r) => (
          <li key={r.label} className={styles.sourceRow}>
            <span className={styles.swatch} style={{ background: colorOf(r.label) }} />
            <span className={styles.sourceLabel}>{r.label}</span>
            <span className={`${styles.sourceShots} num`}>
              {r.shots} 脚 · {r.shot_pct.toFixed(1)}%
            </span>
            <span className={`${styles.sourceXg} num`}>
              {r.xg == null ? "xG —" : `xG ${r.xg.toFixed(2)}`}
            </span>
          </li>
        ))}
      </ul>
      <p className={styles.summary}>{note}</p>
    </div>
  );
}

export function AttackSourceSection({ children }: { children: React.ReactNode }) {
  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>
        <span className={pageStyles.sectionBar} aria-hidden />
        进攻来源拆解
      </h2>
      <p className={styles.windowNote}>
        上条是射门次数占比,下条是 xG 占比 —— 两条错位的地方就是「次数多但质量差」或反过来。
      </p>
      <div className={styles.stack}>{children}</div>
    </section>
  );
}

/* ── 模块四 ─────────────────────────────────────────────── */

type PlayerShareBlock = components["schemas"]["MatchPreviewPlayerShareBlockDTO"];

export function KeyPlayersSection({
  homeName,
  awayName,
  homeBlocks,
  awayBlocks,
}: {
  homeName: string;
  awayName: string;
  homeBlocks: PlayerShareBlock[];
  awayBlocks: PlayerShareBlock[];
}) {
  const [side, setSide] = useState<"home" | "away">("home");
  const blocks = side === "home" ? homeBlocks : awayBlocks;
  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>
        <span className={pageStyles.sectionBar} aria-hidden />
        关键球员
      </h2>
      <p className={styles.windowNote}>
        占全队总量的比例,不做 per-90 外推 —— 5 场窗口下低分钟替补会被系统性抬高。
        入选门槛:近 5 场至少出场 3 次。这是近 5 场数据最突出的球员,不代表本场首发。
      </p>
      <div className={styles.viewTabs} role="tablist" aria-label="切换球队">
        {([
          { key: "home" as const, name: homeName },
          { key: "away" as const, name: awayName },
        ]).map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={side === t.key}
            className={side === t.key ? styles.viewTabOn : styles.viewTab}
            onClick={() => setSide(t.key)}
          >
            {t.name}
          </button>
        ))}
      </div>
      <div className={styles.stack}>
        {blocks.map((b) => (
          <div key={b.id} className={styles.card}>
            <div className={styles.cardHead}>
              <strong className={styles.cardTitle}>{b.title}</strong>
              <span className={styles.cardMeta}>{b.metric}</span>
            </div>
            {b.rows.length === 0 ? (
              <p className={styles.emptyInline}>
                近 5 场没有出场满 3 次的球员,该维度不出榜。这是样本不够,不是这队没有这类球员。
              </p>
            ) : (
              <ul className={styles.shareList}>
                {b.rows.map((p, i) => (
                  <li key={p.player_id} className={styles.shareRow}>
                    <span className={styles.shareName}>{p.name}</span>
                    <span className={`${styles.sharePct} num`}>{p.pct.toFixed(1)}%</span>
                    <span className={styles.shareBar}>
                      <span
                        className={styles.shareFill}
                        style={{
                          width: `${p.pct}%`,
                          background: i === 0 ? "var(--brand-teal)" : "#3f8781",
                        }}
                      />
                    </span>
                    {/* 出场次数与分钟必须同时给:116 分钟排第三是「少量高效」不是「稳定输出」 */}
                    <span className={styles.shareMeta}>
                      {p.appearances} 场 · {p.minutes} 分钟 · {p.count} 次
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

/* ── 模块五 ─────────────────────────────────────────────── */

type KeeperRow = components["schemas"]["MatchPreviewKeeperDTO"];

export function GoalkeeperSection({
  teams,
}: {
  teams: { teamName: string; keepers: KeeperRow[] }[];
}) {
  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>
        <span className={pageStyles.sectionBar} aria-hidden />
        门将对位
      </h2>
      <p className={styles.windowNote}>
        阻止进球 = 面对的 xGOT − 实际失球。正数表示比平均水平多扑出若干球。
      </p>
      <div className={styles.stack}>
        {teams.map((t) => {
          const rotated = t.keepers.length > 1;
          return (
            <div key={t.teamName} className={styles.card}>
              <div className={styles.cardHead}>
                <strong className={styles.cardTitle}>{t.teamName}</strong>
                <span className={styles.rotationTag} data-rotated={rotated}>
                  {rotated
                    ? `轮换 ${t.keepers.length} 人`
                    : `全勤 ${t.keepers[0]?.appearances ?? 0} 场`}
                </span>
              </div>
              {t.keepers.length === 0 ? (
                <p className={styles.emptyInline}>近 5 场没有门将出场数据。</p>
              ) : (
                <ul className={styles.keeperList}>
                  {t.keepers.map((k) => {
                    // 直接来源存在就用它;缺失时只有 xGOT 覆盖了该门将窗口内
                    // 全部出场场次(xgot_faced_complete)才允许现算估算——
                    // 用不完整分母算出的数字会看似精确、实则把缺数据的门将
                    // 显示成表现更差,宁可不给数字也不能给一个误导性的数字。
                    const canEstimate = k.xgot_faced_complete && k.xgot_faced != null;
                    const estimated = k.goals_prevented == null;
                    const prevented = k.goals_prevented ?? (canEstimate ? k.xgot_faced! - k.goals_conceded : null);
                    return (
                      <li key={k.player_id} className={styles.keeperRow}>
                        <div className={styles.keeperHead}>
                          <span className={styles.keeperName}>{k.name}</span>
                          {prevented == null ? (
                            <span className={styles.keeperPreventedTag}>数据不足,无法估算阻止进球</span>
                          ) : (
                            <span className={styles.keeperPrevented}>
                              <b className="num" data-neg={prevented < 0}>
                                {prevented >= 0 ? "+" : "−"}
                                {Math.abs(prevented).toFixed(2)}
                              </b>
                              <span className={styles.keeperPreventedTag}>
                                {estimated ? "阻止进球 · 估算" : "阻止进球"}
                              </span>
                            </span>
                          )}
                        </div>
                        <div className={styles.keeperCells}>
                          {[
                            [String(k.appearances), "出场"],
                            [String(k.saves), "扑救"],
                            [k.xgot_faced == null ? "—" : k.xgot_faced.toFixed(2), "面对 xGOT"],
                            [String(k.goals_conceded), "失球"],
                          ].map(([v, label]) => (
                            <span key={label} className={styles.keeperCell}>
                              <b className="num">{v}</b>
                              <span>{label}</span>
                            </span>
                          ))}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
              {rotated && (
                <p className={styles.summary}>
                  近 5 场由 {t.keepers.length} 名门将分担,不是同一人的分场次数据 ——
                  本场首发是谁,开赛前看阵容区。
                </p>
              )}
            </div>
          );
        })}
      </div>
      <p className={styles.footNote}>
        标「估算」的阻止进球来自 xGOT − 失球现算,仅在该门将窗口内每场都采到该字段
        (数据源覆盖率约 97%)时才现算;直接来源覆盖率约 39%,与估算口径不同,不混用。
      </p>
    </section>
  );
}
