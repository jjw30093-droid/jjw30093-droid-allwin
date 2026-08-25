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
 * 球场图(2026-08-25 纵向化,站长验收返工):此前是"半场 + 主/客 tab 切换、
 * 按阵型字符串分行"的旧结构——FotMob 原生 APP 的阵容图不论预计还是确认首发
 * 都是**纵向双队同屏**(APK 反编译核实:Compose 自定义 Layout 按服务端
 * verticalLayout 绝对定位,res/layout-land/ 无横屏变体)。现改为与赛后
 * 「阵容」tab 共用 VerticalPitchFormation(两队上下各半、面对面),主/客
 * tab 移除,教练/替补改两列并排(与赛后名单版式统一)。pos_x/pos_y 本来就
 * 取自上游 verticalLayout(backend/providers/fotmob_snapshots.py),坐标
 * 语义与赛后侧完全同源。该字段上线前写入的旧快照没有坐标,此时如实退化成
 * 纯名单列表(不猜站位),不会按数组顺序画错位置。
 */

"use client";

import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import {
  VerticalPitchFormation,
  type VerticalPitchSide,
} from "./VerticalPitchFormation";
import styles from "./ProjectedLineupSection.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import { formatBeijingDateTime } from "./zh";
import type { components } from "@/lib/api-types";

type LineupSide = components["schemas"]["MatchPreviewLineupSideDTO"];
type SidelinedPlayer = components["schemas"]["MatchPreviewSidelinedPlayerDTO"];

const REASON_ZH: Record<string, string> = {
  injury: "伤病",
  suspension: "停赛",
  international: "国家队",
};

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
  "这类预测名单只有首发 11 人，本来就不带替补。开赛前会再拿一次。";

/** 其余类型(含 lastStarting11,它平时是带替补的,真实探测与仓内 fixture
 * 都是 9 人)→ 这次观测确实没带,不是结构性缺失。 */
const BENCH_EMPTY_GENERIC = "这次只拿到首发，没有替补。开赛前会再拿一次。";

/** 三个未确认分支(predicted/lastStarting11/类型未知)notice 共同的收尾句,
 * 抽到板块底部只渲染一次,避免同一句"请以官方公布为准"重复三遍。 */
const OFFICIAL_NOTE = "官方名单一般在开赛前一小时出，到时以官方为准。";

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
      notice: "这份名单是第三方预测的，不是官方名单，也不是上一场首发。",
      pitchCaption: "预测阵容(第三方预测,非上一场首发)",
      benchEmpty,
    };
  }
  if (lineupType === "lastStarting11") {
    return {
      confirmed: false,
      tag: "预计首发 · 基于上一场",
      notice: "这是两队上一场的首发，本场不一定这么排。",
      pitchCaption: "预计首发(基于上一场)",
      benchEmpty,
    };
  }
  return {
    confirmed: false,
    tag: "预计首发 · 来源类型未知",
    notice: "数据源没说这份名单怎么来的，可能是上一场首发，也可能是预测。",
    pitchCaption: "预计首发(来源类型未知)",
    benchEmpty,
  };
}

/** MatchPreviewLineupSideDTO → 纵向双队球场投影。pos_x/pos_y 即上游
 * verticalLayout(与赛后侧同源);赛前 DTO 不带行格宽,共享组件会按同行
 * 人数推导。side 为 null(该队无快照)时给空 players,球场组件只画另一队。 */
function toPitchSide(side: LineupSide | null, name: string): VerticalPitchSide {
  return {
    name,
    formation: side?.formation ?? null,
    players: (side?.starters ?? []).map((p) => ({
      key: String(p.id),
      avatarId: p.id,
      name: p.name,
      shirtNumber: p.shirt_number,
      x: p.pos_x,
      y: p.pos_y,
      w: null,
    })),
  };
}

function plottableCount(side: LineupSide | null): number {
  return (side?.starters ?? []).filter((p) => p.pos_x != null && p.pos_y != null).length;
}

/** 单队列(教练 + 无坐标时的首发纯名单 + 替补)——与赛后「阵容」tab 的
 * TeamColumn 同一"两列并排"版式,阵容部分不再有主/客 tab。 */
function TeamColumn({
  name,
  side,
  benchEmpty,
  showStartersAsList,
}: {
  name: string;
  side: LineupSide | null;
  benchEmpty: string;
  /** 该队首发没有任何坐标(旧快照)时为 true:球场上画不了,退化到本列里
   * 按纯名单如实列出(不猜站位)。 */
  showStartersAsList: boolean;
}) {
  return (
    <div className={styles.teamCol}>
      <h3 className={styles.teamTitle}>
        {name}
        <span className={`${styles.formation} num`}>
          {side == null ? "无快照" : (side.formation ?? "阵型未知")}
        </span>
      </h3>
      {side == null ? (
        <p className={styles.emptyInline}>该队暂无阵容快照,开赛前会再次采集。</p>
      ) : (
        <>
          {/* coach.id 2026-08-18 之前的快照没有这个键(可空),没有 id 就没法
              拼头像 URL,退回不渲染头像(文字名称仍然照常显示)。 */}
          <p className={styles.coachRow}>
            <span className={styles.coachLabel}>主教练</span>
            {side.coach?.id != null && (
              <PlayerAvatar playerId={side.coach.id} playerName={side.coach.name} size={24} />
            )}
            <span className={styles.coachName} data-empty={side.coach == null}>
              {side.coach?.name ?? "本条快照未包含主教练信息"}
            </span>
          </p>

          {side.starters.length === 0 ? (
            <p className={styles.emptyNote}>
              这条快照里没有记录到{name}的首发球员,不代表该队没有阵容,开赛前会再次采集。
            </p>
          ) : showStartersAsList ? (
            <ul className={styles.plainList}>
              {side.starters.map((p) => (
                <li key={p.id} className={styles.plainRow}>
                  {p.shirt_number ? `${p.shirt_number} ` : ""}
                  {p.name}
                </li>
              ))}
            </ul>
          ) : null}

          {side.subs.length > 0 ? (
            <details className={styles.bench}>
              <summary className={styles.benchSummary}>替补席 {side.subs.length} 人</summary>
              <ul className={styles.benchList}>
                {side.subs.map((p) => (
                  <li key={p.id} className={styles.benchRow}>
                    <PlayerAvatar playerId={p.id} playerName={p.name} shirtNumber={p.shirt_number} size={24} />
                    <span className={`${styles.benchNo} num`}>{p.shirt_number ?? "—"}</span>
                    <span className={styles.benchName}>{p.name}</span>
                  </li>
                ))}
              </ul>
            </details>
          ) : (
            side.starters.length > 0 && (
              /* 空态绝不套 <details>:把空态折叠起来只是换个说法继续藏
                 (CLAUDE.md §2.2)。有替补时保持默认折叠——<summary> 上的
                 "替补席 N 人"本身已是可见披露。 */
              <div className={styles.bench}>
                <p className={styles.benchHead}>替补席 暂无名单</p>
                <p className={styles.emptyNote}>{benchEmpty}</p>
              </div>
            )
          )}
        </>
      )}
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
          {observedAt} 查的时候这队没有伤停。是真没有，不是没查到。
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
  // 名单谈类型,必须换成一句面向"已采集但暂无名单"这个状态的诚实文案。
  const bothStartersEmpty =
    (home?.starters.length ?? 0) === 0 && (away?.starters.length ?? 0) === 0;

  const homePlottable = plottableCount(home);
  const awayPlottable = plottableCount(away);
  const pitchVisible = homePlottable > 0 || awayPlottable > 0;

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

            {/* 2026-08-25:两队同屏纵向球场(主上客下,FotMob 恒纵向布局),
                主/客 tab 已移除。预计首发用石板灰场(variant="probable",
                FotMob 用球场底色本身区分预计/确认)。 */}
            {pitchVisible && (
              <>
                <VerticalPitchFormation
                  home={toPitchSide(home, homeName)}
                  away={toPitchSide(away, awayName)}
                  variant="probable"
                />
                <p className={styles.pitchNote}>
                  {pitchCaption}:上{homeName} {home?.formation ?? "阵型未知"},下{awayName}{" "}
                  {away?.formation ?? "阵型未知"}。站位按真实坐标画的,前后场顺序没错,但不是精确到米的位置。
                </p>
              </>
            )}
            {!pitchVisible && (
              <p className={styles.pitchNote}>
                {pitchCaption}:这份名单没带坐标,只能按顺序列,位置别当真。开赛前会再拿一次。
              </p>
            )}

            {/* 教练/替补两列并排(与赛后「阵容」tab 的两列名单版式统一);
                无坐标的旧快照在各自列里退化为首发纯名单。 */}
            <div className={styles.pairGrid}>
              <TeamColumn
                name={homeName}
                side={home}
                benchEmpty={benchEmpty}
                showStartersAsList={(home?.starters.length ?? 0) > 0 && homePlottable === 0}
              />
              <TeamColumn
                name={awayName}
                side={away}
                benchEmpty={benchEmpty}
                showStartersAsList={(away?.starters.length ?? 0) > 0 && awayPlottable === 0}
              />
            </div>

            {!confirmed && <p className={styles.footNote}>{OFFICIAL_NOTE}</p>}
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
          回归时间按原文写（A few days、Day to day 这种），不折算成日期——具体哪天我们也不知道。
        </p>
      </section>
    </>
  );
}
