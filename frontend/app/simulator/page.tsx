import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { stepForQuery } from "@/features/simulator/wizard";
import { fixtureIndex, isStale, leagueOf, loadParams, sliceForLeague } from "./loadParams";
import { SimulatorClient } from "./SimulatorClient";
import styles from "./simulator.module.css";

// 访问开关 SIMULATOR_ENABLED 三态(docs/simulator-launch-plan.md §1,运行时读取,改完重启 allwin-web 即生效):
//   未设置 → 404(未上线,不暴露存在);"0" → "暂时下线"说明页;"1" → 正常。
export const dynamic = "force-dynamic";

type Mode = "absent" | "paused" | "on";

function mode(): Mode {
  const v = process.env.SIMULATOR_ENABLED;
  if (v === "1") return "on";
  if (v === "0") return "paused";
  return "absent";
}

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

const SHARE_TITLE = "比赛模拟器:自己排首发,模拟一场比赛";
const SHARE_DESCRIPTION = "选首发、阵型和战术侧重点,模拟一场五大联赛比赛:比分、进球、xG 赛跑和 1000 次模拟的胜平负。模拟结果,仅供娱乐。";

// 用 generateMetadata 而不是静态 metadata:静态导出即使页面 notFound() 也会把标题写进生产响应。
// 索引(§6):测试版期间一律 noindex;带任何查询参数的 /simulator?…(分享链接、比赛页入口)永远 noindex;canonical 指向 /simulator。
// 分享卡片:标题、描述、示意缩略图(静态图,不含真实比分);微信缩略图按正方形裁切,另备一张正方形。
export async function generateMetadata(): Promise<Metadata> {
  const m = mode();
  if (m === "absent") return { robots: { index: false, follow: false } };
  const robots = { index: false, follow: false };
  if (m === "paused") return { title: "比赛模拟器", robots };
  const images = [
    { url: "/brand/simulator-share-card.png", width: 1200, height: 630, alt: "比赛模拟器示意图(模拟比赛,非真实比分)" },
    { url: "/brand/simulator-share-square.png", width: 600, height: 600, alt: "比赛模拟器示意图(模拟比赛,非真实比分)" },
  ];
  return {
    title: "比赛模拟器(原型)",
    description: SHARE_DESCRIPTION,
    robots,
    alternates: { canonical: "/simulator" },
    openGraph: {
      type: "website",
      url: "/simulator",
      siteName: "喵弟数据研究室",
      locale: "zh_CN",
      title: SHARE_TITLE,
      description: SHARE_DESCRIPTION,
      images,
    },
    twitter: { card: "summary_large_image", title: SHARE_TITLE, description: SHARE_DESCRIPTION, images: [images[0].url] },
  };
}

function Notice({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className={styles.page}>
      <section className={styles.card} data-testid="simulator-notice">
        <h1 className={styles.title}>{title}</h1>
        <p className={styles.muted} style={{ marginTop: 12 }}>
          {children}
        </p>
        <p style={{ marginTop: 16 }}>
          <Link href="/" className={styles.linkBtn}>
            返回首页
          </Link>
        </p>
      </section>
    </main>
  );
}

export default async function SimulatorPage({ searchParams }: { searchParams: SearchParams }) {
  const m = mode();
  if (m === "absent") notFound();
  if (m === "paused") {
    return <Notice title="模拟器暂时下线">比赛模拟器正在维护,暂时无法使用。其它页面不受影响。</Notice>;
  }
  const loaded = await loadParams({ dir: process.env.SIMULATOR_PARAMS_DIR, path: process.env.SIMULATOR_PARAMS_PATH });
  if (!loaded) {
    return <Notice title="模拟器参数暂不可用">参数正在更新或暂时无法读取,请稍后再试。</Notice>;
  }
  const sp = await searchParams;
  const leagueId = leagueOf(sp.lg, loaded.params);
  // 链接里带了本联赛两支球队(分享设定 / 只带两队)→ 服务端就决定进第 2 步,页面不会先闪第 1 步
  const search = new URLSearchParams(
    Object.entries(sp).flatMap(([k, v]) => (v == null ? [] : Array.isArray(v) ? v.map((x) => [k, x] as [string, string]) : [[k, v] as [string, string]])),
  ).toString();
  const sliced = sliceForLeague(loaded.params, leagueId);
  return (
    <SimulatorClient
      key={leagueId}
      params={sliced}
      leagueId={leagueId}
      fixtureIndex={fixtureIndex(loaded.params)}
      paramsStale={isStale(loaded.params)}
      initialStep={stepForQuery(`?${search}`, sliced, leagueId)}
    />
  );
}
