/**
 * 首页战绩 banner(2026-09,经站长明确决定的**择优展示**)。
 *
 * 后端 `/api/v1/reco/highlight` 从数十个统计口径候选里挑最好看的一个;
 * 完整背景、与记录面(/reco?tab=record 全样本)的分工、以及"这不是 bug、
 * 不要修复成全样本"的说明,见 backend/queries/reco_highlight.py 模块头注。
 *
 * **与公推 banner 的结构差异(不要照抄成三段式)**:公推 banner 需要一个
 * "use client" 组件,是因为它有"开球 +2h 精确撤下"的时间判定,必须由各客户端
 * 按自己的当前时间算。本 banner 没有任何时间撤下逻辑——内容只在某张单结算时
 * 变化,服务端算完缓存 5 分钟无害且自愈,所以**纯服务端渲染,不需要 client
 * 组件**。为了对称硬造一个 "use client" 文件反而多此一举。
 */

import { cache } from "react";
import Link from "next/link";
import { serverGetOptional, type GetJson } from "@/lib/api-v1";
import { highlightLines } from "@/lib/reco-highlight";
import styles from "@/app/page.module.css";

type HighlightResp = GetJson<"/api/v1/reco/highlight">;

const getHighlight = cache(async (): Promise<HighlightResp | null> =>
  // 优雅降级:接口挂了就不显示战绩条,绝不能拖垮整个首页
  // (同 app/page.tsx::getRecoOverview 与 PublicPicksBanner 的既有写法)。
  serverGetOptional<HighlightResp>("/api/v1/reco/highlight", {
    revalidate: 300,
  }).catch(() => null),
);

export async function RecordHighlightBanner() {
  const data = await getHighlight();
  if (!data) return null;

  // flatMap 天然收窄:highlightLines 返回 null 的板块(无已结算样本)直接消失,
  // 不需要额外的类型谓词。
  const rows = data.boards.flatMap((b) => {
    const lines = highlightLines(b);
    return lines ? [{ board: b.board, lines }] : [];
  });
  if (rows.length === 0) return null;

  return (
    <Link href="/reco?tab=record" className={styles.recordHighlightBanner}>
      <div className={styles.picksHead}>
        <h2>推荐战绩</h2>
      </div>
      {/* 2026-09 站长要求:只留主行,去掉细行与 CTA 文案。整块仍然是 <Link>,
          点任意位置进 /reco?tab=record 的全样本记录面——入口没丢,只是不再
          有一行文字标注它。 */}
      {rows.map((r) => (
        <div key={r.board} className={styles.recordHighlightRow}>
          <span
            className={styles.recordHighlightMain}
            data-emphasize={r.lines.emphasize ? "1" : undefined}
          >
            {r.lines.main}
          </span>
        </div>
      ))}
    </Link>
  );
}
