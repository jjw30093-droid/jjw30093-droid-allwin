"use client";

/**
 * 战绩单据的展示层(2026-09-16 从 app/reco/page.tsx 抽出)。
 *
 * 抽出的理由:战绩此前只存在于 /reco 的第三个 tab,没有独立路由——不能
 * 分享、不能收藏、不在 sitemap、桌面导航里也没有入口。站长收到真实用户
 * 反馈"看不到战绩,推荐入口不明确"后,新增了独立页面 /track-record,
 * 两个入口共用这一份渲染逻辑,避免两处各写一遍再慢慢漂走。
 *
 * 本文件是 "use client":SlipCard 里有「修正记录」的展开状态。新页面
 * (app/track-record/page.tsx)是服务端组件,负责取数,把数据当 props 传进来
 * ——服务端组件渲染客户端组件是标准用法,不踩 CLAUDE.md §11.4 那条
 * "服务端组件不能 import 客户端文件里的普通函数"的坑(这里 import 的是
 * 组件本身,不是要在服务端调用的函数)。
 */

import { useState } from "react";
import Link from "next/link";
import type { GetJson } from "@/lib/api-v1";
import { MARKET_ZH } from "@/components/matches/zh";
import { LoadMoreList } from "@/components/reco/LoadMoreList";
import styles from "@/app/reco/reco.module.css";

type TrackResp = GetJson<"/api/v1/reco/track-record">;
export type Slip = TrackResp["slips"][number];
export type TrackSummary = NonNullable<TrackResp["summary"]>;

// half_win/half_loss(2026-08-16 四分之一盘口扩展):半仓赢半仓走水 / 半仓
// 本金退回半仓告负。措辞遵守 CLAUDE.md §1(不用"红单""连红"等收益承诺式表述)。
const RESULT_ZH: Record<string, string> = {
  win: "命中", lose: "未中", push: "走水", half_win: "半赢", half_loss: "半输",
};


function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

/**
 * 推荐单卡片(结果优先层级,2026-08-15 定稿)。
 *
 * 三层视觉权重:
 *   1. 结果 —— 左列 124px 色块,21px/900,块底色即结果语义色(命中/未中/走水/已作废/未结算);
 *   2. 押的是什么 —— 右列每腿两行(比赛名 / 玩法·选项 + 赔率右对齐),串关规格在左列底部;
 *   3. 元数据 —— 日期 + 标题降为 11.5px 顶部小字,修正记录折叠成「修正记录 ›」。
 *
 * 赛前(status=published)单没有结果,左列换成浅底 + brand-teal 文字,
 * 主位是「未结算 / 最早开球 / 时间」,整卡虚线边,与已结算卡一眼分开。
 *
 * 可点:单关整卡跳 /matches/[matchId];串关每腿行各自跳自己那场
 * (不做整卡链接,避免 <a> 嵌套 <a>)。缺 match_id 的站外赛事不可点。
 */

// half_win/half_loss(2026-08-16):settle_slip() 的整单判定目前只会产生
// win/lose/push/half_loss 四种 slip 级结果(half_win 只出现在腿级——一张单
// 净赚但含 half_win 腿时,整单仍按连乘积判 win),但 reco_slips.result 的
// CHECK 约束同时允许 half_win,聚合与展示都按完整取值域防御式处理,不因
// "目前走不到"就当作不存在。
type SlipTone = "win" | "half_win" | "lose" | "half_loss" | "push" | "void" | "pending";

const TONE_TEXT: Record<SlipTone, string> = {
  win: "命中",
  half_win: "半赢",
  lose: "未中",
  half_loss: "半输",
  push: "走水",
  void: "已作废",
  pending: "未结算",
};

export function slipTone(slip: Slip): SlipTone {
  if (slip.status === "voided") return "void";
  if (slip.status !== "settled") return "pending";
  if (slip.result === "win") return "win";
  if (slip.result === "half_win") return "half_win";
  if (slip.result === "lose") return "lose";
  if (slip.result === "half_loss") return "half_loss";
  return "push";
}

/** 串关规格:DTO 只有 combo_type(single/parlay),几串几由腿数推导。 */
function comboLabel(legCount: number): string {
  return legCount === 1 ? "单关" : `${legCount}串1`;
}

/**
 * 赛前单主位的开球时间。reco_legs 没有开球时间列,match_desc 按约定
 * 以 "MM-DD HH:MM" 结尾(见 0010_reco_board.sql 注释),先从描述里取最早的一场;
 * 取不到就退化为「待开赛」,不编造时间。
 * 后端若补 earliest_kickoff_at 字段,这里换成直接读字段即可。
 */
function earliestKickoff(slip: Slip): string {
  const stamps = slip.legs
    .map((l) => l.match_desc.match(/(\d{2}-\d{2})\s+(\d{1,2}:\d{2})$/))
    .filter((m): m is RegExpMatchArray => m != null)
    .map((m) => ({ key: `${m[1]} ${m[2].padStart(5, "0")}`, time: m[2] }))
    .sort((a, b) => a.key.localeCompare(b.key));
  return stamps.length > 0 ? stamps[0].time : "待开赛";
}

/** 左列色块里的「标签 + 数值」两行。 */
function blockMetrics(slip: Slip, tone: SlipTone): { kicker: string; value: string } {
  if (tone === "pending") return { kicker: "最早开球", value: earliestKickoff(slip) };
  if (tone === "void") return { kicker: "", value: "不计分母" };
  if (slip.return_units == null) return { kicker: "净单位", value: "—" };
  const net = slip.return_units - 1;
  return { kicker: "净单位", value: `${net >= 0 ? "+" : ""}${net.toFixed(2)}` };
}

function LegRow({ leg }: { leg: Slip["legs"][number] }) {
  const inner = (
    <>
      <span className={styles.legDesc}>{leg.match_desc}</span>
      {leg.result ? (
        <span className={styles.legStamp} data-result={leg.result}>
          {RESULT_ZH[leg.result]}
        </span>
      ) : (
        <span />
      )}
      <span className={styles.legChevron} aria-hidden>
        ›
      </span>
      <span className={styles.legPick}>
        {MARKET_ZH[leg.market] ?? leg.market} · {leg.selection}
      </span>
      <span className={`${styles.legOdds} num`}>@{leg.odds.toFixed(2)}</span>
    </>
  );

  if (leg.match_id == null) {
    return <li className={styles.leg}>{inner}</li>;
  }
  return (
    <li>
      <Link className={styles.legLink} href={`/matches/${leg.match_id}`}>
        {inner}
      </Link>
    </li>
  );
}

export function SlipCard({ slip }: { slip: Slip }) {
  const [editOpen, setEditOpen] = useState(false);
  const tone = slipTone(slip);
  const { kicker, value } = blockMetrics(slip, tone);

  return (
    <article className={styles.slipCard} data-tone={tone}>
      <div className={styles.resultBlock}>
        <strong className={styles.resultText}>{TONE_TEXT[tone]}</strong>
        {kicker && <span className={styles.blockKicker}>{kicker}</span>}
        <span className={`${styles.blockValue} num`}>{value}</span>
        <span className={styles.blockRule} aria-hidden />
        <span className={`${styles.comboLabel} num`}>{comboLabel(slip.legs.length)}</span>
      </div>

      <div className={styles.slipBody}>
        {tone === "void" && (
          <p className={styles.voidNote}>这场后来作废了，不计入战绩。</p>
        )}

        <div className={styles.metaLine}>
          <span className="num">{slip.slip_date}</span>
          <span>{slip.title}</span>
        </div>

        {slip.note && <p className={styles.note}>{slip.note}</p>}

        <ul className={styles.legs}>
          {slip.legs.map((l) => (
            <LegRow key={l.id} leg={l} />
          ))}
        </ul>

        {slip.edit_count > 0 && (
          <div className={styles.editFoldRow}>
            <button
              type="button"
              className={styles.editFold}
              aria-expanded={editOpen}
              onClick={() => setEditOpen((v) => !v)}
            >
              修正记录 {editOpen ? "⌄" : "›"}
            </button>
            {editOpen && (
              <p className={styles.editDetail}>
                改过 {slip.edit_count} 次，最后一次 {fmtDate(slip.last_edited_at)}。
              </p>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

/** 未授权 slip 的中性卡片:只展示后端下发的存在性 + 状态(slip_date/status),
 * 标题/腿/赔率/理由这些字段在网络响应里physically 不存在,这里自然也就


/** "1胜 2半赢 3负 1半输 2走"——四分之一盘口半赢/半输只在实际出现时才显示,
 * 避免绝大多数场次(没有 half_win/half_loss)时汇总条挤满恒为 0 的分类。 */
function resultBreakdownText(summary: TrackSummary): string {
  const parts = [`${summary.win_count}胜`];
  if (summary.half_win_count > 0) parts.push(`${summary.half_win_count}半赢`);
  parts.push(`${summary.lose_count}负`);
  if (summary.half_loss_count > 0) parts.push(`${summary.half_loss_count}半输`);
  parts.push(`${summary.push_count}走`);
  return parts.join(" ");
}

export function SummaryRow({ summary }: { summary: TrackSummary }) {
  return (
    <>
      <section className={styles.summaryRow} aria-label="战绩汇总">
        <div className={styles.summaryItem}>
          <span className={`${styles.summaryNum} num`}>{summary.settled_count}</span>
          <span className={styles.summaryLabel}>已结算</span>
        </div>
        <div className={styles.summaryItem}>
          <span className={`${styles.summaryNum} num`}>{resultBreakdownText(summary)}</span>
          <span className={styles.summaryLabel}>命中/未中/走水{(summary.half_win_count > 0 || summary.half_loss_count > 0) ? "（含四分之一盘半赢半输）" : ""}</span>
        </div>
        <div className={styles.summaryItem}>
          <span className={`${styles.summaryNum} num`}>
            {summary.hit_rate == null ? "—" : `${(summary.hit_rate * 100).toFixed(1)}%`}
          </span>
          <span className={styles.summaryLabel}>命中率</span>
        </div>
        <div className={styles.summaryItem}>
          <span className={`${styles.summaryNum} num`}>
            {summary.net_units >= 0 ? "+" : ""}
            {summary.net_units.toFixed(2)}
          </span>
          <span className={styles.summaryLabel}>净单位</span>
        </div>
        {summary.voided_count > 0 && (
          <div className={styles.summaryItem}>
            <span className={`${styles.summaryNum} num`}>{summary.voided_count}</span>
            <span className={styles.summaryLabel}>作废</span>
          </div>
        )}
      </section>
      <p className={styles.summaryNote}>
        走水不算进去，半赢半输各算半场；每单按 1 单位算，作废的不计入命中率。
      </p>
    </>
  );
}

/**
 * 战绩面板:汇总条 + 归档列表。/reco?tab=record 与 /track-record 共用。
 *
 * 取数不在这里做——调用方决定是服务端取(/track-record,可被搜索引擎收录)
 * 还是浏览器端取(/reco,与该页其它标签页同一节奏)。
 */
export function TrackRecordPanel({
  summary,
  slips,
  total,
  loading = false,
  error = null,
}: {
  summary: TrackSummary | null;
  slips: Slip[];
  total: number;
  loading?: boolean;
  error?: string | null;
}) {
  return (
    <section>
      {error && <p className={styles.errText}>{error}</p>}
      {summary && <SummaryRow summary={summary} />}
      <h2 className={styles.sectionTitle}>战绩归档（{total}）</h2>
      <p className={styles.archiveNote}>
        结算完的单子都在这儿，中没中都留着。改过的地方会在那张单子上标出来。
      </p>
      {loading ? (
        <div className={styles.card} aria-busy="true">
          <div className={styles.skeleton} />
        </div>
      ) : slips.length === 0 ? (
        // 取数失败时上面已经给了错误提示,这里不能再说"还没有结算完的单子"
        // ——那是在把"取不到"说成"没有",两件事不一样。
        error ? null : <p className={styles.empty}>还没有结算完的单子。</p>
      ) : (
        // 默认显示最近 10 条,底部「加载更多」每次再多 10 条(2026-09-26)
        <LoadMoreList pageSize={10}>
          {slips.map((s) => (
            <SlipCard key={s.id} slip={s} />
          ))}
        </LoadMoreList>
      )}
    </section>
  );
}
