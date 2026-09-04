/**
 * 首页战绩 banner(2026-09,经站长明确决定的**择优展示**)。
 *
 * 后端 `/api/v1/reco/highlight` 从数十个统计口径候选里挑最好看的一个;
 * 完整背景、与记录面(/reco?tab=record 全样本)的分工、以及"这不是 bug、
 * 不要修复成全样本"的说明,见 backend/queries/reco_highlight.py 模块头注。
 *
 * 2026-09 改版为横条形态(参照 miaomiaodi.cc 的 VipPromoBanner):板块短标签
 * 做灰色前缀、口径与计数用强调色,一行放下两个板块,靠 flex-wrap 在窄屏
 * 折成两行,「全部 →」始终钉在右侧不参与折行。
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
    <Link
      href="/reco?tab=record"
      className={styles.recordStrip}
      aria-label="推荐战绩,查看完整记录"
    >
      <span className={styles.recordStripBody}>
        <span className={styles.recordChip}>
          <span className={styles.recordChipDot} aria-hidden />
          战绩
        </span>
        {rows.map((r) => (
          <span key={r.board} className={styles.recordItem}>
            {r.lines.boardShort}{" "}
            {/* 渲染 parts 而不是 value:连中数要放大成大号 Oswald 数字,
                回报段要退成次级灰。两者都只能靠结构化分段——三个 kind 的
                数字位置完全不同,正则切分是错的。parts 拼起来逐字节等于
                value(有测试守着),所以 value 仍然是那两条文案不变量的
                合法断言对象。 */}
            <span
              className={styles.recordItemValue}
              data-emphasize={r.lines.emphasize ? "1" : undefined}
            >
              {r.lines.parts.map((part, i) => (
                <span
                  key={i}
                  className={
                    part.big
                      ? styles.recordBigNum
                      : part.muted
                        ? styles.recordItemMuted
                        : undefined
                  }
                >
                  {part.text}
                </span>
              ))}
            </span>
          </span>
        ))}
      </span>
      <span className={styles.recordStripMore}>全部 →</span>
    </Link>
  );
}
