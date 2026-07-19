"use client";

/**
 * SpecChart + 文字摘要(宪法 §11.2:图表必须有文字摘要)。
 * summarize 是 client 模块函数,不能在 server component 里直接调用,
 * 故用本客户端包装组件承接(详情页 server 端只传可序列化的 spec)。
 */

import { SpecChart, summarize, type ChartSpec } from "@/components/charts/SpecCharts";

export function ChartWithSummary({
  spec,
  titleClassName,
  summaryClassName,
}: {
  spec: ChartSpec;
  titleClassName?: string;
  summaryClassName?: string;
}) {
  return (
    <>
      <figcaption className={titleClassName}>{spec.title}</figcaption>
      <SpecChart spec={spec} />
      <p className={summaryClassName}>{summarize(spec)}</p>
    </>
  );
}
