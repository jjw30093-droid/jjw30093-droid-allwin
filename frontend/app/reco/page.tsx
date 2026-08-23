"use client";

/**
 * /reco — 每日精选(人工推荐板块),「今日精选|历史战绩」双标签页。
 *
 * 权限判定(2026-08-16 产品权限口径修正,经用户批准;后端是权限真源,本页
 * 只做体验——CLAUDE.md §8):
 * - 历史战绩:全站公开,匿名可见(不再要求登录)。
 * - 今日精选(近 30 天推荐):要求登录,但登录本身不解锁内容——每日精选是
 *   全站唯一需要 admin 按"用户 + 单条 slip"显式授权的内容。已登录用户请求
 *   GET /api/v1/reco/daily 得到的是"按 slip 分别授权"的投影:每一条要么是
 *   完整内容(access_required=false),要么是只有存在性 + 状态的中性投影
 *   (access_required=true,标题/腿/赔率/理由物理不下发,不是置 null)。
 *   没有任何"全局已解锁"的布尔状态,一场的权限不代表另一场也能看到。
 *
 * 本页是纯客户端组件("use client"),全部数据经浏览器端 clientFetch 在挂载后
 * 拉取——服务端渲染的初始 HTML/RSC payload 不包含任何 slip 数据(无论是否
 * 已授权),满足"未授权内容不能先发送再隐藏"的硬性要求:数据本身在服务端
 * 渲染阶段完全不存在,不是发送后被样式或条件渲染盖住。
 *
 * 人工内容可修正:修正次数与最近编辑时间公开展示,不使用"锁定不可改"表述
 * (那是模型登记簿的资格,见 /track-record)。
 *
 * useSearchParams 必须包在 Suspense 里(Next 16 生产构建约束,同 /login)。
 */

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ApiError,
  apiErrorMessage,
  clientFetch,
  getMe,
  type GetJson,
  type MeResponse,
} from "@/lib/api-v1";
import { MARKET_ZH } from "@/components/matches/zh";
import styles from "./reco.module.css";

type DailyResp = GetJson<"/api/v1/reco/daily">;
type DailyItem = DailyResp["slips"][number];
type TrackResp = GetJson<"/api/v1/reco/track-record">;
export type Slip = TrackResp["slips"][number];

// half_win/half_loss(2026-08-16 四分之一盘口扩展):半仓赢半仓走水 / 半仓
// 本金退回半仓告负。措辞遵守 CLAUDE.md §1(不用"红单""连红"等收益承诺式表述)。
const RESULT_ZH: Record<string, string> = {
  win: "命中", lose: "未中", push: "走水", half_win: "半赢", half_loss: "半输",
};

// 每日精选未授权状态固定文案:未登录时是列表级别的说明,不针对某一场,
// 不用"本场";已登录时改成针对具体这一场的措辞。
const LOCKED_NOTICE_ANON = "每日精选按场开通，登录后能看到你有哪几场。";
const LOCKED_NOTICE_AUTHED = "本场每日精选需要单独授权，当前账号暂无查看权限。";

const SLIP_STATUS_ZH: Record<string, string> = {
  published: "已发布",
  settled: "已结算",
  voided: "已作废",
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
 * 没有任何东西可渲染——不是"拿到了再隐藏"。 */
function LockedSlipCard({ slip }: { slip: Extract<DailyItem, { access_required: true }> }) {
  return (
    <article className={styles.lockedCard} data-testid="reco-locked-slip">
      <div className={styles.lockedMeta}>
        <span className="num">{slip.slip_date}</span>
        <span className={styles.lockedStatus}>{SLIP_STATUS_ZH[slip.status] ?? slip.status}</span>
      </div>
      <p className={styles.note}>{LOCKED_NOTICE_AUTHED}</p>
      <Link className={styles.inlineLink} href="/pricing">
        申请查看本场每日精选 →
      </Link>
    </article>
  );
}

/** "1胜 2半赢 3负 1半输 2走"——四分之一盘口半赢/半输只在实际出现时才显示,
 * 避免绝大多数场次(没有 half_win/half_loss)时汇总条挤满恒为 0 的分类。 */
function resultBreakdownText(summary: NonNullable<TrackResp["summary"]>): string {
  const parts = [`${summary.win_count}胜`];
  if (summary.half_win_count > 0) parts.push(`${summary.half_win_count}半赢`);
  parts.push(`${summary.lose_count}负`);
  if (summary.half_loss_count > 0) parts.push(`${summary.half_loss_count}半输`);
  parts.push(`${summary.push_count}走`);
  return parts.join(" ");
}

function SummaryRow({ summary }: { summary: NonNullable<TrackResp["summary"]> }) {
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

type Tab = "daily" | "record";

function RecoBody() {
  const searchParams = useSearchParams();
  const [me, setMe] = useState<MeResponse | null | "loading">("loading");
  const [daily, setDaily] = useState<DailyResp | null>(null);
  const [dailyErr, setDailyErr] = useState<string | null>(null);
  const [track, setTrack] = useState<TrackResp | null>(null);
  const [trackErr, setTrackErr] = useState<string | null>(null);

  // 历史战绩(2026-08-16 起匿名可见):不依赖登录态,挂载后直接拉取。
  useEffect(() => {
    let cancelled = false;
    clientFetch<TrackResp>("/api/v1/reco/track-record")
      .then((t) => {
        if (!cancelled) setTrack(t);
      })
      .catch((e) => {
        if (!cancelled) setTrackErr(apiErrorMessage(e, "战绩加载失败"));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 登录态水合(浏览器端私有请求,不进匿名 HTML/RSC payload)。
  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((meResp) => {
        if (!cancelled) setMe(meResp);
      })
      .catch(() => {
        if (!cancelled) setMe(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 今日精选列表:仅登录后才请求(未登录该端点 401)。每一条按 slip 分别
  // 授权投影,不存在任何"全局已解锁"判断。
  useEffect(() => {
    if (me === "loading" || !me?.authenticated) return;
    let cancelled = false;
    clientFetch<DailyResp>("/api/v1/reco/daily")
      .then((d) => {
        if (!cancelled) setDaily(d);
      })
      .catch((e) => {
        if (!cancelled && !(e instanceof ApiError && e.status === 401)) {
          setDailyErr(apiErrorMessage(e, "推荐单加载失败"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [me]);

  const authed = me !== "loading" && Boolean(me?.authenticated);
  const summary = track?.summary;

  // 显式 ?tab= 优先;否则默认落在人人可看的「历史战绩」——每日精选没有
  // 任何"整体已解锁"的全局状态可以据此决定默认标签,具体到某一场是否可看
  // 只在列表渲染时逐条判断。
  const explicit = searchParams.get("tab");
  const tab: Tab = explicit === "daily" || explicit === "record" ? explicit : "record";

  return (
    <main className={styles.page}>
      <h1 className={styles.title}>每日精选</h1>
      <p className={styles.subtitle}>
        每天人工出的推荐。中了没中都留在这儿，作废的也照样列出来。这是个人分析，判断会错。
      </p>

      <nav className={styles.tabs} aria-label="精选内容切换">
        <Link
          href="/reco?tab=daily"
          className={tab === "daily" ? styles.tabActive : styles.tab}
          aria-current={tab === "daily" ? "page" : undefined}
        >
          今日精选
        </Link>
        <Link
          href="/reco?tab=record"
          className={tab === "record" ? styles.tabActive : styles.tab}
          aria-current={tab === "record" ? "page" : undefined}
        >
          历史战绩
        </Link>
      </nav>

      {tab === "daily" ? (
        me === "loading" ? (
          <section className={styles.card} aria-busy="true">
            <div className={styles.skeleton} />
            <div className={styles.skeletonShort} />
          </section>
        ) : !authed ? (
          <section className={styles.card}>
            <h2 className={styles.cardTitle}>今日精选</h2>
            <p className={styles.note}>{LOCKED_NOTICE_ANON}</p>
            <div className={styles.btnRow}>
              <Link className={styles.btnPrimary} href="/login?next=/reco?tab=daily">
                前往登录
              </Link>
              <Link className={styles.btnGhost} href="/reco?tab=record">
                先看历史战绩
              </Link>
            </div>
          </section>
        ) : (
          <section>
            <h2 className={styles.sectionTitle}>近 {daily?.window_days ?? 30} 天推荐</h2>
            {dailyErr && <p className={styles.errText}>{dailyErr}</p>}
            {!daily && !dailyErr ? (
              <div className={styles.card} aria-busy="true">
                <div className={styles.skeleton} />
              </div>
            ) : daily && daily.slips.length === 0 ? (
              <p className={styles.empty}>这 {daily?.window_days ?? 30} 天还没发过推荐。</p>
            ) : (
              daily?.slips.map((s) =>
                s.access_required ? (
                  <LockedSlipCard key={s.id} slip={s} />
                ) : (
                  <SlipCard key={s.id} slip={s} />
                ),
              )
            )}
          </section>
        )
      ) : (
        <section>
          {trackErr && <p className={styles.errText}>{trackErr}</p>}
          {summary && <SummaryRow summary={summary} />}
          <h2 className={styles.sectionTitle}>战绩归档（{track?.total ?? 0}）</h2>
          <p className={styles.archiveNote}>
            结算完的单子都在这儿，中没中都留着。改过的地方会在那张单子上标出来。
            <Link href="/track-record" className={styles.inlineLink}>
              模型预测
            </Link>
            是另一套记录，两边不合并算。
          </p>
          {!track && !trackErr ? (
            <div className={styles.card} aria-busy="true">
              <div className={styles.skeleton} />
            </div>
          ) : track && track.slips.length === 0 ? (
            <p className={styles.empty}>还没有结算完的单子。</p>
          ) : (
            track?.slips.map((s) => <SlipCard key={s.id} slip={s} />)
          )}
        </section>
      )}
    </main>
  );
}

export default function RecoPage() {
  return (
    <Suspense
      fallback={
        <main className={styles.page}>
          <h1 className={styles.title}>每日精选</h1>
          <section className={styles.card} aria-busy="true">
            <div className={styles.skeleton} />
            <div className={styles.skeletonShort} />
          </section>
        </main>
      }
    >
      <RecoBody />
    </Suspense>
  );
}
