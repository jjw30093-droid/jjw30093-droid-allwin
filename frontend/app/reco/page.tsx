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
 * (2026-08-25:曾经对照的模型预测登记簿/WDL 模型已整体废弃,/track-record
 * 页面已删除,不再有可比较的对象)。
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
// 战绩单据的展示层 2026-09-16 抽到 components/reco/TrackRecordPanel.tsx,
// 与新的独立页面 /track-record 共用(见该文件头注释)。这里保留 re-export,
// 因为「每日公推」标签页也在用 SlipCard,而外部(测试)按老路径 import。
import {
  SlipCard,
  TrackRecordPanel,
  slipTone,
  type Slip,
} from "@/components/reco/TrackRecordPanel";
import styles from "./reco.module.css";

export { SlipCard, slipTone };
export type { Slip };

type DailyResp = GetJson<"/api/v1/reco/daily">;
type DailyItem = DailyResp["slips"][number];
type TrackResp = GetJson<"/api/v1/reco/track-record">;
// 每日公推(2026-09 新增,board='daily_public'):完全公开、匿名可见,
// 响应形状同 RecoSlipDTO——直接复用既有 SlipCard,不新造投影/组件。
type PublicResp = GetJson<"/api/v1/reco/public">;
type MyAccessResponse = GetJson<"/api/v1/reco/my-access">;

// 每日精选未授权状态固定文案:未登录时是列表级别的说明,不针对某一场,
// 不用"本场";已登录时改成针对具体这一场的措辞。
const LOCKED_NOTICE_ANON = "每日精选按场开通，登录后能看到你有哪几场。";
const LOCKED_NOTICE_AUTHED = "本场每日精选需要单独授权，当前账号暂无查看权限。";

const SLIP_STATUS_ZH: Record<string, string> = {
  published: "已发布",
  settled: "已结算",
  voided: "已作废",
};

/** 未授权 slip 的中性卡片:只展示后端下发的存在性 + 状态(slip_date/status),
 * 标题/腿/赔率/理由这些字段在网络响应里 physically 不存在,这里自然也就
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

type Tab = "daily" | "record" | "public";

function RecoBody() {
  const searchParams = useSearchParams();
  const [me, setMe] = useState<MeResponse | null | "loading">("loading");
  const [daily, setDaily] = useState<DailyResp | null>(null);
  const [dailyErr, setDailyErr] = useState<string | null>(null);
  const [track, setTrack] = useState<TrackResp | null>(null);
  const [trackErr, setTrackErr] = useState<string | null>(null);
  const [pub, setPub] = useState<PublicResp | null>(null);
  const [pubErr, setPubErr] = useState<string | null>(null);
  // 当前账号是否有任何 active 授权;null = 还没查到(此时不放"怎么开通"按钮,避免闪一下)
  const [hasGrant, setHasGrant] = useState<boolean | null>(null);

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

  // 每日公推(2026-09 新增):完全公开、匿名可见,不依赖登录态,挂载后
  // 直接拉取——与历史战绩同一模式。
  useEffect(() => {
    let cancelled = false;
    clientFetch<PublicResp>("/api/v1/reco/public")
      .then((p) => {
        if (!cancelled) setPub(p);
      })
      .catch((e) => {
        if (!cancelled) setPubErr(apiErrorMessage(e, "每日公推加载失败"));
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

  // 授权状态(个人功能,要求登录):决定顶部放"怎么开通"还是什么都不放
  useEffect(() => {
    if (me === "loading" || !me?.authenticated) return;
    let cancelled = false;
    clientFetch<MyAccessResponse>("/api/v1/reco/my-access")
      .then((r) => {
        if (!cancelled) setHasGrant(r.grants.some((g) => g.status === "active"));
      })
      .catch(() => {
        // 查不到就当作"未知",不放按钮(不能因为接口失败就告诉用户"你没开通")
      });
    return () => {
      cancelled = true;
    };
  }, [me]);

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
  const noAccess = hasGrant === false;

  // 显式 ?tab= 优先;否则默认落在完全公开的「每日公推」(2026-09 起,站长
  // 明确要求:导航栏"每日精选"入口点进来直接看到公推,不需要登录门槛——
  // 每日公推排最左,是这一路由现在的默认落点)。
  const explicit = searchParams.get("tab");
  const tab: Tab =
    explicit === "daily" || explicit === "record" || explicit === "public"
      ? explicit
      : "public";

  return (
    <main className={styles.page}>
      <h1 className={styles.title}>每日精选</h1>
      <p className={styles.subtitle}>每天人工精选，开通后在这里查看。</p>
      {/* 入口(2026-09-26):未登录 → 登录;已登录但还没有任何授权 → 怎么开通;
          已开通或还没查到授权状态时不放按钮 */}
      {me !== "loading" && !authed && (
        <div className={`${styles.btnRow} ${styles.entryRow}`}>
          <Link className={styles.btnPrimary} href="/login?next=/reco">
            登录
          </Link>
        </div>
      )}
      {authed && noAccess && (
        <div className={`${styles.btnRow} ${styles.entryRow}`}>
          <Link className={styles.btnPrimary} href="/pricing#how-to-unlock">
            怎么开通
          </Link>
        </div>
      )}

      <nav className={styles.tabs} aria-label="精选内容切换">
        <Link
          href="/reco?tab=public"
          className={tab === "public" ? styles.tabActive : styles.tab}
          aria-current={tab === "public" ? "page" : undefined}
        >
          每日公推
        </Link>
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
      ) : tab === "record" ? (
        <TrackRecordPanel
          summary={summary ?? null}
          slips={track?.slips ?? []}
          total={track?.total ?? 0}
          loading={!track && !trackErr}
          error={trackErr}
        />
      ) : (
        <section>
          {pubErr && <p className={styles.errText}>{pubErr}</p>}
          <h2 className={styles.sectionTitle}>近 {pub?.window_days ?? 7} 天公推</h2>
          {!pub && !pubErr ? (
            <div className={styles.card} aria-busy="true">
              <div className={styles.skeleton} />
            </div>
          ) : pub && pub.slips.length === 0 ? (
            <p className={styles.empty}>这 {pub?.window_days ?? 7} 天还没发过公推。</p>
          ) : (
            pub?.slips.map((s) => <SlipCard key={s.id} slip={s} />)
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
