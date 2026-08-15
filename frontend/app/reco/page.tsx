"use client";

/**
 * /reco — 每日精选(人工推荐板块),「今日精选|历史战绩」双标签页。
 *
 * 三段可见性(CLAUDE.md §8;后端是权限真源,本页只做体验):
 * - 匿名:引导登录(无任何数字与内容);
 * - 已登录(member 基线,reco:track_record):默认打开「历史战绩」;
 *   「今日精选」标签展示锁定说明(权限由站长开通,生效后自动显示);
 * - 精选授权(reco:daily):默认打开「今日精选」,顶部展示授权有效期;
 *   近 30 天推荐(含未结算)只出现在「今日精选」,归档只出现在「历史战绩」,
 *   两个标签不重复展示同一批已结算单。
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
import styles from "./reco.module.css";

type DailyResp = GetJson<"/api/v1/reco/daily">;
type TrackResp = GetJson<"/api/v1/reco/track-record">;
type AccountResp = GetJson<"/api/v1/account">;
type Slip = TrackResp["slips"][number];

const RESULT_ZH: Record<string, string> = { win: "命中", lose: "未中", push: "走水" };

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

function fmtDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" });
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

type SlipTone = "win" | "lose" | "push" | "void" | "pending";

const TONE_TEXT: Record<SlipTone, string> = {
  win: "命中",
  lose: "未中",
  push: "走水",
  void: "已作废",
  pending: "未结算",
};

function slipTone(slip: Slip): SlipTone {
  if (slip.status === "voided") return "void";
  if (slip.status !== "settled") return "pending";
  if (slip.result === "win") return "win";
  if (slip.result === "lose") return "lose";
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
        {leg.market} · {leg.selection}
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

function SlipCard({ slip }: { slip: Slip }) {
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
                本单修正 {slip.edit_count} 次,最近 {fmtDate(slip.last_edited_at)}。
                人工内容允许修正,修正次数与时间公开留痕。
              </p>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

function SummaryRow({ summary }: { summary: NonNullable<TrackResp["summary"]> }) {
  return (
    <section className={styles.summaryRow} aria-label="战绩汇总">
      <div className={styles.summaryItem}>
        <span className={`${styles.summaryNum} num`}>{summary.settled_count}</span>
        <span className={styles.summaryLabel}>已结算</span>
      </div>
      <div className={styles.summaryItem}>
        <span className={`${styles.summaryNum} num`}>
          {summary.win_count}胜 {summary.lose_count}负 {summary.push_count}走
        </span>
        <span className={styles.summaryLabel}>命中/未中/走水</span>
      </div>
      <div className={styles.summaryItem}>
        <span className={`${styles.summaryNum} num`}>
          {summary.hit_rate == null ? "—" : `${(summary.hit_rate * 100).toFixed(1)}%`}
        </span>
        <span className={styles.summaryLabel}>命中率(走水不计)</span>
      </div>
      <div className={styles.summaryItem}>
        <span className={`${styles.summaryNum} num`}>
          {summary.net_units >= 0 ? "+" : ""}
          {summary.net_units.toFixed(2)}
        </span>
        <span className={styles.summaryLabel}>净单位(1 单位/单)</span>
      </div>
      {summary.voided_count > 0 && (
        <div className={styles.summaryItem}>
          <span className={`${styles.summaryNum} num`}>{summary.voided_count}</span>
          <span className={styles.summaryLabel}>作废(单列,不计分母)</span>
        </div>
      )}
    </section>
  );
}

type Tab = "daily" | "record";

function RecoBody() {
  const searchParams = useSearchParams();
  const [me, setMe] = useState<MeResponse | null | "loading">("loading");
  const [daily, setDaily] = useState<DailyResp | null>(null);
  const [track, setTrack] = useState<TrackResp | null>(null);
  const [grantEndsAt, setGrantEndsAt] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let meResp: MeResponse | null = null;
      try {
        meResp = await getMe();
      } catch {
        // 后端不可用:按匿名渲染
      }
      if (cancelled) return;
      setMe(meResp);
      if (!meResp?.authenticated) return;
      try {
        setTrack(await clientFetch<TrackResp>("/api/v1/reco/track-record"));
      } catch (e) {
        if (!cancelled) setErr(apiErrorMessage(e, "战绩加载失败"));
      }
      if (meResp.entitlements.includes("reco:daily")) {
        try {
          const d = await clientFetch<DailyResp>("/api/v1/reco/daily");
          if (!cancelled) setDaily(d);
        } catch (e) {
          if (!cancelled && !(e instanceof ApiError && e.status === 403)) {
            setErr(apiErrorMessage(e, "推荐单加载失败"));
          }
        }
        // 授权有效期(自己的账户数据,私有请求;拿不到只是不显示日期,不报错)
        try {
          const account = await clientFetch<AccountResp>("/api/v1/account");
          if (cancelled) return;
          const nowIso = new Date().toISOString();
          const active = account.subscriptions
            .filter(
              (s) =>
                s.status === "active" &&
                s.ends_at > nowIso &&
                s.plan_id === "daily_picks",
            )
            .sort((a, b) => b.ends_at.localeCompare(a.ends_at));
          setGrantEndsAt(active[0]?.ends_at ?? null);
        } catch {
          // 静默:横幅退化为不带日期
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const authed = me !== "loading" && me?.authenticated;
  const hasDaily = authed && me!.entitlements.includes("reco:daily");
  const summary = track?.summary;

  // 显式 ?tab= 优先;否则授权用户默认「今日精选」,普通登录默认「历史战绩」
  const explicit = searchParams.get("tab");
  const tab: Tab =
    explicit === "daily" || explicit === "record"
      ? explicit
      : hasDaily
        ? "daily"
        : "record";

  return (
    <main className={styles.page}>
      <h1 className={styles.title}>每日精选</h1>
      <p className={styles.subtitle}>
        每日人工推荐,战绩全程公开:命中、未中、走水与作废全部保留,不挑选、不隐藏。
        推荐为个人分析观点,存在不确定性,不构成任何收益承诺。
      </p>

      {me === "loading" ? (
        <section className={styles.card} aria-busy="true">
          <div className={styles.skeleton} />
          <div className={styles.skeletonShort} />
        </section>
      ) : !authed ? (
        <section className={styles.card}>
          <h2 className={styles.cardTitle}>登录后可免费查看全部历史战绩</h2>
          <p className={styles.note}>
            赛前精选仅向已获得权限的账号开放。首次微信扫码会自动创建账户,
            无需填写手机号;登录完成后会返回本页。
          </p>
          <div className={styles.btnRow}>
            <Link className={styles.btnPrimary} href="/login?next=/reco">
              免费登录查看战绩
            </Link>
            <Link className={styles.btnGhost} href="/matches">
              先看公开比赛资料
            </Link>
          </div>
        </section>
      ) : (
        <>
          {hasDaily && (
            <div className={styles.grantBanner} data-testid="reco-grant-banner">
              <span className={styles.grantBannerTitle}>精选权限已开通</span>
              <span>当前账号可以查看赛前推荐和完整历史战绩。</span>
              {grantEndsAt && (
                <span className={styles.grantBannerDim}>
                  有效期至 {fmtDay(grantEndsAt)}
                </span>
              )}
            </div>
          )}

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

          {err && <p className={styles.errText}>{err}</p>}

          {tab === "daily" ? (
            hasDaily ? (
              <section>
                <h2 className={styles.sectionTitle}>
                  近 {daily?.window_days ?? 30} 天推荐
                </h2>
                {!daily ? (
                  <div className={styles.card} aria-busy="true">
                    <div className={styles.skeleton} />
                  </div>
                ) : daily.slips.length === 0 ? (
                  <p className={styles.empty}>近 30 天暂无推荐</p>
                ) : (
                  daily.slips.map((s) => <SlipCard key={s.id} slip={s} />)
                )}
              </section>
            ) : (
              <section className={styles.card}>
                <h2 className={styles.cardTitle}>今日精选</h2>
                <p className={styles.note}>
                  当前账号尚未获得查看权限。如需开通,请通过公众号联系站长;
                  权限生效后本页会自动显示,无需重新注册。
                  历史战绩已对你开放,可切换上方「历史战绩」查看。
                </p>
                <Link className={styles.inlineLink} href="/pricing">
                  查看访问权限说明 →
                </Link>
              </section>
            )
          ) : (
            <section>
              {summary && <SummaryRow summary={summary} />}
              <h2 className={styles.sectionTitle}>
                战绩归档({track?.total ?? 0})
              </h2>
              <p className={styles.archiveNote}>
                你可以查看全部已结算推荐,包括命中、未中、走水和作废记录。
                结算后归档,人工内容允许修正,修正历史逐单标注;与
                <Link href="/track-record" className={styles.inlineLink}>
                  模型公开记录
                </Link>
                相互独立,口径不混用。
              </p>
              {!track ? (
                <div className={styles.card} aria-busy="true">
                  <div className={styles.skeleton} />
                </div>
              ) : track.slips.length === 0 ? (
                <p className={styles.empty}>还没有已结算的推荐</p>
              ) : (
                track.slips.map((s) => <SlipCard key={s.id} slip={s} />)
              )}
            </section>
          )}
        </>
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
