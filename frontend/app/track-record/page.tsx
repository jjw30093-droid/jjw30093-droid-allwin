import type { Metadata } from "next";
import Link from "next/link";
import { serverGet, type GetJson } from "@/lib/api-v1";
import { TrackRecordPanel } from "@/components/reco/TrackRecordPanel";
import styles from "@/app/reco/reco.module.css";

/**
 * /track-record — 每日精选与公推的历史战绩,独立页面。
 *
 * 2026-09-16 新增。此前战绩只是 /reco 的第三个标签页(`?tab=record`),
 * 没有自己的路由:不能分享、不能收藏、不在 sitemap、桌面导航里也没有入口。
 * 站长收到的真实用户反馈就是"看不到战绩,推荐入口不明确"——内容其实一直
 * 都在,而且是全量的(命中/未中/走水/作废都留档),纯粹是找不到。
 *
 * 这里是**服务端组件**:战绩匿名公开,不受 /reco 那条"未授权内容不能先
 * 发送再隐藏"的约束,所以可以服务端取数、直接出 HTML,搜索引擎能收录。
 * 渲染逻辑与 /reco?tab=record 共用 TrackRecordPanel,两处不会各写一遍。
 *
 * /reco?tab=record 保留不动:既有 e2e 断言登录用户在 /reco 能看到战绩归档,
 * 而且两个入口并存对用户没有坏处。
 */

export const metadata: Metadata = {
  title: "历史战绩",
  description: "每日精选与每日公推的全部结算记录:命中、未中、走水与作废单,不挑选、不隐藏。",
};

// 与后端 /reco/track-record 的 PUBLIC_CACHE(300s)同一档位。
export const revalidate = 300;

type TrackResp = GetJson<"/api/v1/reco/track-record">;

export default async function TrackRecordPage() {
  let data: TrackResp | null = null;
  let error: string | null = null;
  try {
    // limit 上限由后端钳在 100;这里取满,战绩页就是要一次看全。
    data = await serverGet<TrackResp>("/api/v1/reco/track-record?limit=100", {
      revalidate: 300,
    });
  } catch {
    // 取数失败不能白屏:标题与说明照常渲染,面板里给一句可读的错误。
    error = "战绩暂时取不到,稍后再看看。";
  }

  return (
    <main className={styles.page}>
      <h1 className={styles.title}>历史战绩</h1>
      <p className={styles.subtitle}>
        每日精选和每日公推结算完的单子都在这儿。中了没中都留着，作废的也单列出来，不挑选、不隐藏。
      </p>

      <TrackRecordPanel
        summary={data?.summary ?? null}
        slips={data?.slips ?? []}
        total={data?.total ?? 0}
        error={error}
      />

      <p className={styles.archiveNote}>
        想看今天发了什么,去 <Link className={styles.inlineLink} href="/reco">每日精选</Link>。
      </p>
    </main>
  );
}
