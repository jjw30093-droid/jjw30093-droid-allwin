/**
 * 模块一:预计阵容 + 伤停(数据 tab → 阵容子页)。
 *
 * 诚实红线(CLAUDE.md §2.2/§6.2,PIPELINE_REDESIGN_V2 P2 修正):
 * bronze_fm_lineup_snap.lineup_type 真实观测到四种值(2026-08-17 全量 228 行
 * 实测):payload 缺键(旧快照,类型未知)154 行、`lastStarting11`(上一场首发)
 * 57 行、`predicted`(source=`enetpulse`,第三方对本场的预测阵容)16 行、
 * `standard`(0 首发的近空快照)1 行——`"confirmed"` 从未出现过,这个分支
 * 保留只是为了 FotMob 未来真的开始下发该值时能正确渲染,不能靠它给用户许下
 * "稍后会自动变成已确认首发"这种产品目前兑现不了的承诺;`predicted` 是第三方
 * 对本场的预测,不是"两队上一场的首发",这两类数据不能共用同一句文案。
 *
 * lineup_type/source/observed_at 是整场共享的一条快照属性(bronze_fm_lineup_snap
 * 一行同时含两队),不是逐队各自的字段——这里按 API 契约(MatchPreviewLineupsDTO)
 * 把它们提到组件顶层,不比照原始设计稿把它们塞进每一侧,那样会诱使未来的改动
 * 误以为两队可能有不同的 lineup_type。
 *
 * 球场图(2026-08-19 真修复 H7/H8,不再是止损):真实用户报告马竞 vs 马拉加
 * 一场把奥布拉克画成中卫、卡多索画成门将——根因是后端 _sorted_players 为了
 * hash 稳定按 id 重排过首发数组,数组顺序早就不代表场上位置,组件此前却把
 * starters[0] 当门将、按数组切片当各线。现在后端 extract_lineup_snapshot 会
 * 额外保留每名球员的归一化球场坐标(pos_x/pos_y,来自上游 verticalLayout,
 * 2026-08-19 新增),rowsFor 按坐标重新排序、分行、分列,不再依赖数组顺序——
 * 该字段上线前写入的旧快照没有坐标,rowsFor 对这类快照如实返回 null,组件
 * 退化成不画球场图(纯名单列表),不会用旧顺序继续画错位置。
 * (components/matches/PitchFormation.tsx 是赛后阵型图,那里有 extra_json 的
 *  归一化坐标,两者不是同一份数据,不要复用。)
 */

"use client";

import { useState } from "react";
import styles from "./ProjectedLineupSection.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import { formatBeijingDateTime } from "./zh";
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

type LineupPresentation = {
  confirmed: boolean;
  tag: string;
  notice: string;
  pitchCaption: string;
  benchEmpty: string;
};

/** predicted 且 source==="enetpulse":2026-08-18 真实探测确认这类名单的键集
 * 里根本没有 subs 键(上游结构性不下发),不是"这次观测没带"。 */
const BENCH_EMPTY_ENETPULSE =
  "第三方(Enetpulse)预测阵容只给出首发 11 人,数据源不随这类名单下发替补名单——这不是本站漏采,也不代表两队没有替补。开赛前会再次采集。";

/** 其余类型(含 lastStarting11,它平时是带替补的,真实探测与仓内 fixture
 * 都是 9 人)→ 这次观测确实没带,不是结构性缺失。 */
const BENCH_EMPTY_GENERIC =
  "这条快照里没有替补名单:数据源本次观测只给了首发。开赛前会再次采集。";

/**
 * lineup_type → 展示文案。三个互斥分支,不能合并成一个"确认/未确认"布尔值——
 * `predicted`(第三方对本场的预测)和 `lastStarting11`(上一场真实首发)是两种
 * 完全不同的数据来源,共用一句"数据源给的是两队上一场的首发"会把预测说成事实。
 * `confirmed` 分支从未在真实数据里出现过,文案不承诺"稍后会自动变成这个状态"。
 *
 * benchEmpty 由 `source` 是否为 "enetpulse" 单独判定,**不能**用
 * `lineupType==="predicted"` 代替——vendor 名是 `source` 的属性,`lineup_type`
 * 只是名单的性质(预测/上一场首发),哪天换了预测供应商但 lineup_type 仍是
 * "predicted",用 lineup_type 判定就会对用户说假话(声称"Enetpulse"其实是
 * 别家给的)。
 */
function describeLineup(lineupType: string | null, source: string | null): LineupPresentation {
  const benchEmpty = source === "enetpulse" ? BENCH_EMPTY_ENETPULSE : BENCH_EMPTY_GENERIC;
  if (lineupType === "confirmed") {
    return {
      confirmed: true,
      tag: "已确认首发",
      notice: "数据源已更新为本场官方名单。",
      pitchCaption: "已确认首发",
      benchEmpty,
    };
  }
  if (lineupType === "predicted") {
    return {
      confirmed: false,
      tag: "预测阵容 · 第三方预测",
      notice:
        "这不是本场官方名单,也不是两队上一场的首发。数据源标注为第三方(Enetpulse)对本场比赛的预测阵容,可能与实际出场不同,请以官方公布为准。",
      pitchCaption: "预测阵容(第三方预测,非上一场首发)",
      benchEmpty,
    };
  }
  if (lineupType === "lastStarting11") {
    return {
      confirmed: false,
      tag: "预计首发 · 基于上一场",
      notice:
        "这不是本场官方名单。数据源给的是两队上一场的首发,不代表本场实际出场,请以官方公布为准。",
      pitchCaption: "预计首发(基于上一场)",
      benchEmpty,
    };
  }
  return {
    confirmed: false,
    tag: "预计首发 · 来源类型未知",
    notice:
      "这不是本场官方名单。数据源未标注这份名单的类型,无法确认它是上一场首发还是预测阵容,请以官方公布为准。",
    pitchCaption: "预计首发(来源类型未知)",
    benchEmpty,
  };
}

/**
 * 按 formation 分行:[门将] + 各线。formation 缺失或首发不足 11 人时退化为
 * 不画球场。
 *
 * 2026-08-19 修复(H8,真实用户报告:马竞 vs 马拉加球场图把奥布拉克画成中卫,
 * 卡多索画成门将)——此前按 side.starters 的数组顺序切片,但后端
 * _sorted_players 为了 hash 稳定按 id 重排过这个数组,数组顺序早就不代表
 * 场上位置,starters[0] 也不可靠是门将。现在改为按数据源真实给出的球场坐标
 * (pos_y 从小到大 = 从己方球门到对方球门,同一行内 pos_x 从小到大 = 从左到
 * 右)重新排序后再按阵型行数切片,不再依赖数组顺序。任一首发缺坐标(旧快照,
 * 该字段上线前写入)时如实返回 null——不能对一部分球员有真坐标、另一部分没有
 * 时还硬画,那样只是把"整体乱"换成"部分乱",一样是编造。
 */
export function rowsFor(side: LineupSide): Player[][] | null {
  if (!side.formation || side.starters.length < 11) return null;
  const lines = side.formation.split("-").map(Number);
  if (lines.some((n) => !Number.isInteger(n) || n <= 0)) return null;
  if (side.starters.some((p) => p.pos_x == null || p.pos_y == null)) return null;
  const ordered = [...side.starters].sort(
    (a, b) => (a.pos_y as number) - (b.pos_y as number) || (a.pos_x as number) - (b.pos_x as number),
  );
  const rows: Player[][] = [[ordered[0]]];
  let i = 1;
  for (const n of lines) {
    rows.push(ordered.slice(i, i + n));
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
  source,
  observedAt,
  home,
  away,
  homeSidelined,
  awaySidelined,
}: {
  homeName: string;
  awayName: string;
  lineupType: string | null;
  /** bronze_fm_lineup_snap 的 provider 口径(如 "enetpulse"/"lastStartingLineups")
   * ——决定替补空态该说哪句话,详见 describeLineup 的 benchEmpty 注释。 */
  source: string | null;
  observedAt: string | null;
  home: LineupSide | null;
  away: LineupSide | null;
  homeSidelined: SidelinedPlayer[];
  awaySidelined: SidelinedPlayer[];
}) {
  const [side, setSide] = useState<"home" | "away">(home ? "home" : "away");
  const active = side === "home" ? home : away;
  const { confirmed, tag, notice, pitchCaption, benchEmpty } = describeLineup(lineupType, source);
  // 赛程相关时间戳按北京时间展示(CLAUDE.md §11.2)。formatBeijingDateTime 是
  // 纯算术(固定 +8,不依赖 Intl/ICU),SSR 与水合结果一致;date_only 或非法
  // 输入返回 null,此时退回原始字符串而不是伪造一个北京时间。刻意不做相对时间
  // ("N 分钟前")——那需要渲染期 now,会造成 SSR/水合不一致。
  const observedBeijing = observedAt ? formatBeijingDateTime(observedAt) : null;
  const observedLabel = observedBeijing ? `${observedBeijing}(北京时间)` : (observedAt ?? "—");
  // 阵容/伤停快照耦合写入(见 backend/queries/lineup_preview.py)——两队都没
  // 阵容快照时,伤停的"0 人"也不是"确认无伤停",而是这场从未被采集过。
  const hasSnapshot = home != null || away != null;
  // 窗口放宽到 72h 后(CLAUDE.md §6.3),远端比赛的第一枪常常拿到空阵容——
  // §6.3 明确"这一枪拿不到数据属正常,不是失败告警"。两侧首发都是 0 人时,
  // describeLineup(null) 的"数据源未标注这份名单的类型"是在对一份不存在的
  // 名单谈类型,必须换成一句面向"已采集但暂无名单"这个状态的诚实文案,并且
  // 把 notice/tabs/球场整块换掉,不能让两段互相矛盾的文案同屏。
  const bothStartersEmpty =
    (home?.starters.length ?? 0) === 0 && (away?.starters.length ?? 0) === 0;

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
        ) : bothStartersEmpty ? (
          <p className={styles.emptyNote}>
            已在 {observedLabel} 采集过这场比赛,但数据源当时还没有提供任何阵容名单。开赛前会再次采集。
          </p>
        ) : (
          <>
            <div className={styles.notice} data-confirmed={confirmed}>
              <div className={styles.noticeHead}>
                <span className={styles.noticeTag} data-confirmed={confirmed}>
                  {tag}
                </span>
                <span className={`${styles.observed} num`}>观测于 {observedLabel}</span>
              </div>
              <p className={styles.noticeText}>{notice}</p>
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
                    {t.data == null ? "无快照" : (t.data.formation ?? "阵型未知")}
                  </span>
                </button>
              ))}
            </div>

            {active && (
              <>
                {/* coach 是每侧属性,必须在 active 内才会跟着主/客 tab 切换,
                    且放在空首发守卫之外:教练与首发是两份独立数据,一侧没
                    首发不代表没教练。 */}
                <p className={styles.coachRow}>
                  <span className={styles.coachLabel}>主教练</span>
                  <span className={styles.coachName} data-empty={active.coach == null}>
                    {active.coach?.name ?? "本条快照未包含主教练信息"}
                  </span>
                </p>

                {active.starters.length === 0 ? (
                  <p className={styles.emptyNote}>
                    这条快照里没有记录到{side === "home" ? homeName : awayName}
                    的首发球员,不代表该队没有阵容,开赛前会再次采集。
                  </p>
                ) : (
                  <>
                    <Pitch side={active} isHome={side === "home"} />
                    <p className={styles.pitchNote}>
                      {pitchCaption}:{side === "home" ? homeName : awayName}{" "}
                      {active.formation ?? "阵型未知"}。
                      {rowsFor(active)
                        ? "图中站位按数据源给出的球场坐标摆放,坐标为归一化示意坐标,不是精确 GPS,门将/各线的先后顺序真实可信。"
                        : "本站保存的这条快照没有带球场坐标,球员只能按名单顺序列出,不代表真实站位(开赛前会再次采集,新采集的快照通常带坐标)。"}
                    </p>
                    {active.subs.length > 0 ? (
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
                    ) : (
                      /* 空态绝不套 <details>:把空态折叠起来只是换个说法继续
                         藏(CLAUDE.md §2.2)。有替补时保持默认折叠——
                         <summary> 上的"替补席 N 人"本身已是可见披露。 */
                      <div className={styles.bench}>
                        <p className={styles.benchHead}>替补席 暂无名单</p>
                        <p className={styles.emptyNote}>{benchEmpty}</p>
                      </div>
                    )}
                  </>
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
